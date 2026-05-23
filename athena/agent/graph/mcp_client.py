"""
Tool-calling adapter. The LLM sees abstract tools; we route to the right MCP
server and run the args through OPA first for writes.
"""
from __future__ import annotations

import json
import os
import time
import logging
from typing import Any

import httpx
from opentelemetry import trace
from prometheus_client import Counter

from .state import ToolCall

log = logging.getLogger(__name__)

OPA_URL  = os.getenv("OPA_URL",  "http://opa.agent:8181")
PROM_MCP = os.getenv("PROM_MCP", "http://prom-mcp.agent:8080")
LOKI_MCP = os.getenv("LOKI_MCP", "http://loki-mcp.agent:8080")
K8S_MCP  = os.getenv("K8S_MCP",  "http://k8s-mcp.agent:8080")

TOOL_CALLS = Counter("agent_tool_calls_total", "tool calls", ["tool", "result"])
WRITE_TOOLS = {"k8s_restart_deployment", "k8s_scale_deployment",
               "k8s_rollback_argocd_app", "k8s_cordon_node"}
_tracer = trace.get_tracer("athena.mcp")


def _route(tool: str) -> str:
    if tool.startswith("prom_"): return PROM_MCP
    if tool.startswith("loki_"): return LOKI_MCP
    if tool.startswith("k8s_"):  return K8S_MCP
    raise ValueError(f"unknown tool namespace: {tool}")


def check_policy(tool: str, args: dict) -> tuple[bool, str]:
    """OPA decision. Returns (allow, reason)."""
    try:
        r = httpx.post(f"{OPA_URL}/v1/data/athena/tools/decision",
                       json={"input": {"tool": tool, "args": args}},
                       timeout=3)
        r.raise_for_status()
        decision = r.json().get("result", {})
        return bool(decision.get("allow", False)), decision.get("reason", "")
    except Exception as e:
        log.exception("OPA error; failing closed")
        return False, f"opa error: {e}"


def call(tool: str, args: dict, node: str) -> ToolCall:
    """Make a tool call. Writes are gated by OPA. All calls traced with OTel."""
    t0 = time.time()
    decision = None

    with _tracer.start_as_current_span(f"tool.{tool}") as span:
        span.set_attribute("tool.name", tool)
        span.set_attribute("tool.node", node)
        span.set_attribute("tool.is_write", tool in WRITE_TOOLS)

        if tool in WRITE_TOOLS:
            allowed, reason = check_policy(tool, args)
            decision = f"{'allow' if allowed else 'deny'}: {reason}"
            span.set_attribute("tool.opa_decision", decision)
            if not allowed:
                TOOL_CALLS.labels(tool, "policy_denied").inc()
                span.set_attribute("tool.result", "policy_denied")
                return ToolCall(
                    ts=t0, node=node, tool=tool, args=args,
                    result_summary=f"DENIED by policy: {reason}",
                    success=False, policy_decision=decision,
                )

        url = f"{_route(tool)}/tools/{tool.split('_', 1)[1]}"
        timeout = float(os.getenv("MCP_TIMEOUT_S", "5"))
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                r = httpx.post(url, json=args, timeout=timeout)
                r.raise_for_status()
                result = r.json()
                summary = json.dumps(result, default=str)[:1500]
                TOOL_CALLS.labels(tool, "ok").inc()
                span.set_attribute("tool.result", "ok")
                return ToolCall(ts=t0, node=node, tool=tool, args=args,
                                result_summary=summary, success=True,
                                policy_decision=decision)
            except Exception as e:
                if attempt < max_attempts - 1:
                    time.sleep(1)
                    continue
                TOOL_CALLS.labels(tool, "error").inc()
                span.set_attribute("tool.result", "error")
                span.record_exception(e)
                return ToolCall(ts=t0, node=node, tool=tool, args=args,
                                result_summary=f"error: {e}", success=False,
                                policy_decision=decision)
