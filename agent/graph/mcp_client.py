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
from prometheus_client import Counter

from .state import ToolCall

log = logging.getLogger(__name__)

OPA_URL  = os.getenv("OPA_URL",  "http://opa.agent:8181")
PROM_MCP = os.getenv("PROM_MCP", "http://prom-mcp.agent:8080")
LOKI_MCP = os.getenv("LOKI_MCP", "http://loki-mcp.agent:8080")
K8S_MCP  = os.getenv("K8S_MCP",  "http://k8s-mcp.agent:8080")

TOOL_CALLS = Counter("agent_tool_calls_total", "tool calls", ["tool", "result"])
WRITE_TOOLS = {"k8s.restart_deployment", "k8s.scale_deployment",
               "k8s.rollback_argocd_app", "k8s.cordon_node"}


def _route(tool: str) -> str:
    if tool.startswith("prom."): return PROM_MCP
    if tool.startswith("loki."): return LOKI_MCP
    if tool.startswith("k8s."):  return K8S_MCP
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
    """Make a tool call. Writes are gated by OPA."""
    t0 = time.time()
    decision = None
    if tool in WRITE_TOOLS:
        allowed, reason = check_policy(tool, args)
        decision = f"{'allow' if allowed else 'deny'}: {reason}"
        if not allowed:
            TOOL_CALLS.labels(tool, "policy_denied").inc()
            return ToolCall(
                ts=t0, node=node, tool=tool, args=args,
                result_summary=f"DENIED by policy: {reason}",
                success=False, policy_decision=decision,
            )

    url = f"{_route(tool)}/tools/{tool.split('.', 1)[1]}"
    try:
        r = httpx.post(url, json=args, timeout=30)
        r.raise_for_status()
        result = r.json()
        summary = json.dumps(result, default=str)[:1500]
        TOOL_CALLS.labels(tool, "ok").inc()
        return ToolCall(ts=t0, node=node, tool=tool, args=args,
                        result_summary=summary, success=True,
                        policy_decision=decision)
    except Exception as e:
        TOOL_CALLS.labels(tool, "error").inc()
        return ToolCall(ts=t0, node=node, tool=tool, args=args,
                        result_summary=f"error: {e}", success=False,
                        policy_decision=decision)
