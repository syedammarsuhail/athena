"""
Executor node: actually invokes the write tool for the plan.

Idempotent. Treats every tool error as a non-fatal event recorded in the audit
trail; the Verifier decides whether to loop back.
"""
from __future__ import annotations

import logging

from .. import mcp_client
from ..state import IncidentState

log = logging.getLogger(__name__)


# Maps plan.action to the actual MCP tool name + required arg keys.
ACTION_TO_TOOL = {
    "restart_deployment":  ("k8s_restart_deployment",  ("namespace", "name")),
    "scale_deployment":    ("k8s_scale_deployment",    ("namespace", "name", "replicas")),
    "rollback_argocd_app": ("k8s_rollback_argocd_app", ("app_name", "revision")),
    "cordon_node":         ("k8s_cordon_node",         ("node_name",)),
}


def execute(state: IncidentState) -> dict:
    plan = state["plan"]
    if plan.action not in ACTION_TO_TOOL:
        return {
            "decision_trail": [f"executor: nothing to execute for action={plan.action}"],
        }

    tool, required = ACTION_TO_TOOL[plan.action]
    missing = [k for k in required if k not in plan.params]
    if missing:
        log.error("executor missing params %s for %s", missing, tool)
        return {
            "decision_trail": [f"executor: missing params {missing}; aborting"],
        }

    tc = mcp_client.call(tool, plan.params, node="execute")
    msg = (
        f"executor: ran {tool} args={plan.params} "
        f"-> success={tc.success} policy={tc.policy_decision or 'n/a'}"
    )
    return {
        "tool_calls": [tc],
        "decision_trail": [msg],
        "verification_attempt": state.get("verification_attempt", 0) + 1,
    }
