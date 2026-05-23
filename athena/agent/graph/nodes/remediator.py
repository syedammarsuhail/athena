"""
Remediator node: hypothesis → RemediationPlan.

NEVER executes. Just picks an action + parameters + classifies risk.
Execution happens in execute.py after policy/HITL gates.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import llm
from ..llm import extract_text, parse_json_block
from ..state import IncidentState, RemediationPlan

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent.parent / "prompts" / "remediator.md").read_text()

# Hard mapping of (action, risk) so the LLM can't classify a rollback as low-risk.
RISK = {
    "restart_deployment":  "high",     # set to high for HITL demo; change back to "low" after testing
    "scale_deployment":    "low",      # OPA further caps the magnitude
    "rollback_argocd_app": "high",
    "cordon_node":         "high",
    "no_action":           "low",
    "escalate_human":      "low",
}


def plan_remediation(state: IncidentState) -> dict:
    hyp = state["hypothesis"]
    sev = state["severity"]

    if hyp.confidence < 0.3:
        plan = RemediationPlan(
            action="escalate_human", params={}, risk="low",
            requires_approval=False,
            rationale=f"low confidence ({hyp.confidence:.2f}); human investigation needed",
        )
    else:
        user = (
            f"Severity: {sev.value}\n"
            f"Hypothesis: {hyp.model_dump_json(indent=2)}\n\n"
            "Choose ONE action from: "
            "restart_deployment, scale_deployment, rollback_argocd_app, cordon_node, "
            "no_action, escalate_human.\n\n"
            "Respond with JSON only:\n"
            "{\"action\": str, \"params\": {...}, \"rationale\": str}\n\n"
            "params schema by action:\n"
            "- restart_deployment: {namespace, name}\n"
            "- scale_deployment:   {namespace, name, replicas}\n"
            "- rollback_argocd_app: {app_name, revision}\n"
            "- cordon_node:        {node_name}\n"
            "- no_action / escalate_human: {}"
        )
        resp = llm.chat(node="plan", system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user}], max_tokens=600)
        try:
            payload = parse_json_block(extract_text(resp))
            action = payload["action"]
            risk = RISK[action]
            plan = RemediationPlan(
                action=action,
                params=payload.get("params", {}),
                risk=risk,
                requires_approval=(risk == "high"),
                rationale=payload.get("rationale", ""),
            )
        except Exception:
            log.exception("remediator parse failed; escalating")
            plan = RemediationPlan(
                action="escalate_human", params={}, risk="low",
                requires_approval=False,
                rationale="planner output was unparseable",
            )

    return {
        "plan": plan,
        "decision_trail": [
            f"remediator: action={plan.action} risk={plan.risk} approval={plan.requires_approval}"
        ],
    }
