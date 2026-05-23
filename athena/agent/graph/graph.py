"""
LangGraph wiring: defines nodes, edges, and conditional routing.

Read this top-to-bottom to understand the agent's control flow.
"""
from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END, START, StateGraph

from .state import IncidentState
from .nodes.detector import detect
from .nodes.diagnostician import diagnose
from .nodes.remediator import plan_remediation
from .nodes.executor import execute
from .nodes.verifier import verify
from .nodes.reporter import report
from .nodes.hitl import await_approval

log = logging.getLogger(__name__)

MAX_VERIFY_LOOPS = 3


# --- conditional edge functions -------------------------------------------------

def after_detector(state: IncidentState) -> Literal["diagnose", END]:
    if not state.get("is_real", False):
        return END
    return "diagnose"


def after_plan(state: IncidentState) -> Literal["hitl", "execute", "report"]:
    plan = state.get("plan")
    if plan is None or plan.action == "no_action":
        return "report"
    if plan.action == "escalate_human":
        return "report"
    if plan.requires_approval:
        return "hitl"
    return "execute"


def after_approval(state: IncidentState) -> Literal["execute", "report"]:
    if state.get("approval") == "approved":
        return "execute"
    return "report"


def after_verify(state: IncidentState) -> Literal["report", "diagnose"]:
    if state.get("resolved", False):
        return "report"
    if state.get("verification_attempt", 0) >= MAX_VERIFY_LOOPS:
        # give up; let Reporter escalate
        return "report"
    return "diagnose"


# --- graph builder --------------------------------------------------------------

def build_graph(checkpointer=None):
    g = StateGraph(IncidentState)

    g.add_node("detect",    detect)
    g.add_node("diagnose",  diagnose)
    g.add_node("plan_node", plan_remediation)
    g.add_node("hitl",      await_approval)
    g.add_node("execute",   execute)
    g.add_node("verify",    verify)
    g.add_node("report",    report)

    g.add_edge(START, "detect")
    g.add_conditional_edges("detect", after_detector, {"diagnose": "diagnose", END: END})
    g.add_edge("diagnose", "plan_node")
    g.add_conditional_edges("plan_node", after_plan, {"hitl": "hitl", "execute": "execute", "report": "report"})
    g.add_conditional_edges("hitl", after_approval, {"execute": "execute", "report": "report"})
    g.add_edge("execute", "verify")
    g.add_conditional_edges("verify", after_verify, {"diagnose": "diagnose", "report": "report"})
    g.add_edge("report", END)

    return g.compile(checkpointer=checkpointer if checkpointer is not None else False)
