"""
Fixture-replay tests for the agent graph.

We mock the LLM with canned responses and the MCP client with canned tool
results, then assert the graph took the expected path and produced the
expected final state. This is what makes the agent trustworthy: deterministic
proof that, given X anomaly + Y evidence, the agent makes Z decision.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agent.graph.graph import build_graph
from agent.graph.state import AnomalyEvent, IncidentState, Severity, ToolCall


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def memory_leak_event() -> AnomalyEvent:
    return AnomalyEvent(
        kind="metric_anomaly",
        service="cartservice",
        namespace="online-boutique",
        metric="container_memory_working_set_bytes",
        score=0.91,
        severity=Severity.HIGH,
        ts=time.time(),
        raw={},
    )


def noise_event() -> AnomalyEvent:
    return AnomalyEvent(
        kind="metric_anomaly",
        service="quiet-batch-job",
        namespace="online-boutique",
        metric="container_cpu_usage_seconds_total",
        score=0.55,
        severity=Severity.LOW,
        ts=time.time(),
        raw={},
    )


def initial_state(event: AnomalyEvent) -> IncidentState:
    return {
        "event": event,
        "severity": event.severity,
        "tool_calls": [],
        "decision_trail": [],
        "llm_traces": [],
        "verification_attempt": 0,
    }


# Canned LLM responses keyed by node
LLM_RESPONSES = {
    "detect": {
        "memory_leak": {
            "content": [{"type": "text", "text":
                '```json\n{"is_real": true, "severity": "high", "reason": "persistent memory growth"}\n```'}],
            "usage": {"input_tokens": 100, "output_tokens": 30},
        },
        "noise": {
            "content": [{"type": "text", "text":
                '```json\n{"is_real": false, "severity": "low", "reason": "scheduled batch spike"}\n```'}],
            "usage": {"input_tokens": 100, "output_tokens": 30},
        },
    },
    "diagnose": {
        "memory_leak_final": {
            "content": [{"type": "text", "text":
                '```json\n'
                '{"root_cause": "cartservice OOM due to memory leak in latest deploy",\n'
                ' "confidence": 0.82,\n'
                ' "supporting_evidence": ["memory grew 8x in 10min", "3 OOMKilled events", "deployed 14min ago"],\n'
                ' "likely_action": "restart while we investigate"}\n```'}],
            "usage": {"input_tokens": 800, "output_tokens": 150},
        },
    },
    "plan": {
        "restart": {
            "content": [{"type": "text", "text":
                '```json\n{"action": "restart_deployment", '
                '"params": {"namespace": "online-boutique", "name": "cartservice"}, '
                '"rationale": "low-risk action that clears leaked state"}\n```'}],
            "usage": {"input_tokens": 300, "output_tokens": 60},
        },
    },
    "report": {
        "any": {
            "content": [{"type": "text", "text":
                "cartservice memory leak resolved by restart. Followup: review latest commits."}],
            "usage": {"input_tokens": 600, "output_tokens": 30},
        },
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class FakeLLM:
    """Stateful fake. Returns canned responses based on which node is calling."""

    def __init__(self):
        self.calls = []

    def chat(self, *, node, system, messages, tools=None, max_tokens=2048):
        self.calls.append({"node": node, "messages_len": len(messages)})
        if node == "detect":
            return LLM_RESPONSES["detect"]["memory_leak"]
        if node == "diagnose":
            return LLM_RESPONSES["diagnose"]["memory_leak_final"]
        if node == "plan":
            return LLM_RESPONSES["plan"]["restart"]
        if node == "report":
            return LLM_RESPONSES["report"]["any"]
        raise ValueError(f"unexpected node: {node}")


def fake_mcp_call(tool, args, node):
    """Canned tool results — pretend the cluster is healthy after the restart."""
    success = True
    summary = "[]"
    if tool == "k8s.restart_deployment":
        summary = '{"restarted": "online-boutique/cartservice"}'
    elif tool == "prom.query_instant":
        summary = '{"result": []}'  # nothing anomalous on re-query
    return ToolCall(
        ts=time.time(), node=node, tool=tool, args=args,
        result_summary=summary, success=success, policy_decision="allow: ok",
    )


def test_memory_leak_full_flow(monkeypatch):
    """Realistic incident: detect → diagnose → plan → execute → verify → report."""
    fake = FakeLLM()
    monkeypatch.setattr("agent.graph.llm.chat", fake.chat)
    monkeypatch.setattr("agent.graph.mcp_client.call", fake_mcp_call)
    # Skip the verifier sleep to keep the test fast
    monkeypatch.setattr("agent.graph.nodes.verifier.SETTLE_SECONDS", 0)

    graph = build_graph()
    final = graph.invoke(initial_state(memory_leak_event()))

    assert final["is_real"] is True
    assert final["severity"] == Severity.HIGH
    assert final["plan"].action == "restart_deployment"
    assert final["plan"].requires_approval is False        # low risk
    assert final["resolved"] is True
    assert "Postmortem" in final.get("postmortem_md", "") or "Incident" in final["postmortem_md"]
    # Sanity: the LLM was called the expected number of times in the expected order
    assert [c["node"] for c in fake.calls] == ["detect", "diagnose", "plan", "report"]


def test_noise_short_circuits(monkeypatch):
    """If detector says is_real=false, the graph ends immediately."""
    fake = FakeLLM()
    fake_responses = {**LLM_RESPONSES}
    monkeypatch.setattr("agent.graph.llm.chat",
                        lambda **kw: LLM_RESPONSES["detect"]["noise"] if kw["node"] == "detect"
                                     else (_ for _ in ()).throw(AssertionError("noise should short-circuit")))
    monkeypatch.setattr("agent.graph.mcp_client.call",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no tool calls expected")))

    graph = build_graph()
    final = graph.invoke(initial_state(noise_event()))

    assert final["is_real"] is False
    assert "plan" not in final
    assert "postmortem_md" not in final


def test_policy_denial_recorded(monkeypatch):
    """When OPA denies a write, the ToolCall is recorded as policy_denied; verifier sees it unresolved."""
    fake = FakeLLM()
    monkeypatch.setattr("agent.graph.llm.chat", fake.chat)
    monkeypatch.setattr("agent.graph.nodes.verifier.SETTLE_SECONDS", 0)

    def mcp_with_denial(tool, args, node):
        if tool == "k8s.restart_deployment":
            return ToolCall(ts=time.time(), node=node, tool=tool, args=args,
                            result_summary="DENIED by policy: cooldown",
                            success=False, policy_decision="deny: cooldown")
        return fake_mcp_call(tool, args, node)
    monkeypatch.setattr("agent.graph.mcp_client.call", mcp_with_denial)

    graph = build_graph()
    final = graph.invoke(initial_state(memory_leak_event()))

    denied = [tc for tc in final["tool_calls"] if "DENIED" in tc.result_summary]
    assert len(denied) == 1
    assert denied[0].policy_decision.startswith("deny:")
