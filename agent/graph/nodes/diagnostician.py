"""
Diagnostician node: the reasoning engine.

Runs a bounded tool-use loop:
  - LLM proposes which tool(s) to call next given current evidence
  - We execute the read-only tools, append results to the conversation
  - LLM keeps investigating until it produces a Hypothesis, OR we hit MAX_STEPS

Tools available here are READ-ONLY. The Remediator node handles writes later.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .. import llm, mcp_client
from ..llm import extract_text, extract_tool_uses, parse_json_block
from ..state import Hypothesis, IncidentState

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent.parent / "prompts" / "diagnostician.md").read_text()
MAX_STEPS = 8

READ_TOOLS = [
    {
        "name": "prom_query_instant",
        "description": "Run an instant PromQL query. Use for current-state checks.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "prom_query_range",
        "description": "Run a range PromQL query. Use for trends over time.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "minutes_back": {"type": "integer", "default": 30},
            "step_seconds": {"type": "integer", "default": 30},
        }, "required": ["query"]},
    },
    {
        "name": "loki_query_logs",
        "description": "LogQL query. Pull recent logs for a service or pattern.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "minutes_back": {"type": "integer", "default": 15},
            "limit": {"type": "integer", "default": 200},
        }, "required": ["query"]},
    },
    {
        "name": "k8s_get_events",
        "description": "Recent Kubernetes events for a namespace, optionally filtered.",
        "input_schema": {"type": "object", "properties": {
            "namespace": {"type": "string"},
            "involved_object": {"type": "string"},
        }, "required": ["namespace"]},
    },
    {
        "name": "k8s_describe_deployment",
        "description": "Full description of a deployment, including conditions and recent ReplicaSets.",
        "input_schema": {"type": "object", "properties": {
            "namespace": {"type": "string"},
            "name": {"type": "string"},
        }, "required": ["namespace", "name"]},
    },
]


def diagnose(state: IncidentState) -> dict:
    event = state["event"]
    prior_hypothesis = state.get("hypothesis")
    prior_action = state.get("plan")

    # If this is a re-entry after a failed remediation, tell the LLM what failed.
    retry_context = ""
    if state.get("verification_attempt", 0) > 0:
        retry_context = (
            f"\n\nNOTE: a previous remediation was attempted and DID NOT resolve the issue.\n"
            f"Previous hypothesis: {prior_hypothesis.root_cause if prior_hypothesis else 'n/a'}\n"
            f"Previous action: {prior_action.action if prior_action else 'n/a'}\n"
            f"Consider a different root cause."
        )

    conversation: list[dict] = [{
        "role": "user",
        "content": (
            f"An anomaly has been detected. Investigate it.\n\n"
            f"Event: {event.model_dump_json(indent=2)}\n"
            f"Severity (from detector): {state['severity'].value}{retry_context}\n\n"
            "Call tools to gather evidence. When you have enough to form a confident hypothesis, "
            "respond with ONLY a JSON object (no tool calls) matching this schema:\n"
            "{\"root_cause\": str, \"confidence\": float, "
            "\"supporting_evidence\": [str, ...], \"likely_action\": str}"
        ),
    }]

    new_tool_calls = []
    final_hypothesis: Hypothesis | None = None

    for step in range(MAX_STEPS):
        resp = llm.chat(
            node="diagnose",
            system=SYSTEM_PROMPT,
            messages=conversation,
            tools=READ_TOOLS,
            max_tokens=2048,
        )
        tool_uses = extract_tool_uses(resp)
        if not tool_uses:
            # final answer expected
            try:
                payload = parse_json_block(extract_text(resp))
                final_hypothesis = Hypothesis(**payload)
            except Exception as e:
                log.warning("hypothesis parse failed: %s\n%s", e, extract_text(resp))
                # fall through; try one more loop with a hint
                conversation.append({"role": "assistant", "content": resp["content"]})
                conversation.append({"role": "user", "content":
                                     "Please respond with ONLY the JSON hypothesis now."})
                continue
            break

        # Execute every tool use the model requested in this turn.
        conversation.append({"role": "assistant", "content": resp["content"]})
        tool_results_block = []
        for tu in tool_uses:
            tc = mcp_client.call(tu["name"], tu.get("input", {}), node="diagnose")
            new_tool_calls.append(tc)
            tool_results_block.append({
                "type": "tool_result",
                "tool_use_id": tu["id"],
                "content": tc.result_summary if tc.success else f"ERROR: {tc.result_summary}",
            })
        conversation.append({"role": "user", "content": tool_results_block})

    if final_hypothesis is None:
        final_hypothesis = Hypothesis(
            root_cause="UNDETERMINED — exceeded reasoning step budget",
            confidence=0.0,
            supporting_evidence=["agent reached MAX_STEPS without confident conclusion"],
            likely_action="escalate to human",
        )

    return {
        "hypothesis": final_hypothesis,
        "tool_calls": new_tool_calls,
        "decision_trail": [
            f"diagnostician: rc='{final_hypothesis.root_cause}' conf={final_hypothesis.confidence:.2f}"
        ],
    }
