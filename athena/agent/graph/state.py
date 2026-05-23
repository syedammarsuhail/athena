"""
Typed state for the incident-handling graph.

LangGraph passes this object between nodes. Each node returns a partial dict
that LangGraph merges into the state. Reducers on list fields let us *append*
instead of replace (so the audit trail accumulates).
"""
from __future__ import annotations

import operator
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyEvent(BaseModel):
    """What arrives from NATS / Alertmanager."""
    kind: Literal["metric_anomaly", "log_anomaly", "alert"]
    service: str
    namespace: str = "online-boutique"
    metric: Optional[str] = None
    template: Optional[str] = None
    score: Optional[float] = None
    severity: Severity = Severity.MEDIUM
    ts: float
    raw: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """Audit record of every tool the agent invoked."""
    ts: float
    node: str            # which graph node triggered it
    tool: str            # e.g. "k8s.get_events"
    args: dict[str, Any]
    result_summary: str  # truncated, for the postmortem
    success: bool
    policy_decision: Optional[str] = None  # OPA "allow" / "deny" + reason


class Hypothesis(BaseModel):
    """Output of the Diagnostician."""
    root_cause: str
    confidence: float    # 0.0–1.0
    supporting_evidence: list[str]
    likely_action: str   # natural-language remediation idea (not the actual command)


class RemediationPlan(BaseModel):
    """Output of the Remediator before any execution."""
    action: Literal[
        "restart_deployment",
        "scale_deployment",
        "rollback_argocd_app",
        "cordon_node",
        "no_action",
        "escalate_human",
    ]
    params: dict[str, Any]
    risk: Literal["low", "high"]
    requires_approval: bool
    rationale: str


class IncidentState(TypedDict, total=False):
    """The complete graph state. Annotated lists use add-reducers so each node
    can *append* without clobbering previous entries."""
    incident_id: str
    started_at: float
    event: AnomalyEvent
    severity: Severity
    is_real: bool                 # set by Detector
    hypothesis: Hypothesis
    plan: RemediationPlan
    approval: Optional[Literal["approved", "rejected", "timeout"]]
    verification_attempt: int     # 0-indexed loop guard
    resolved: bool
    postmortem_md: str

    tool_calls:     Annotated[list[ToolCall], operator.add]
    decision_trail: Annotated[list[str], operator.add]   # human-readable steps
    llm_traces:     Annotated[list[dict], operator.add]   # for the cost dashboard
