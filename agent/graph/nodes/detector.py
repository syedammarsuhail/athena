"""
Detector node: cheapest decision in the graph.

Input:  AnomalyEvent from NATS
Output: is_real, severity

Strategy: small prompt, no tools, fast model would be ideal (but we use the
same model for code-simplicity). Filters out:
- known noisy services in maintenance windows
- single-sample blips that haven't persisted
- duplicate events for an incident the agent is already handling
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .. import llm
from ..llm import extract_text, parse_json_block
from ..state import IncidentState, Severity

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent.parent / "prompts" / "detector.md").read_text()


def detect(state: IncidentState) -> dict:
    event = state["event"]

    user_msg = (
        f"Event: {event.model_dump_json()}\n\n"
        "Respond with a JSON object: "
        "{\"is_real\": bool, \"severity\": \"low|medium|high|critical\", \"reason\": str}"
    )
    resp = llm.chat(
        node="detect",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=400,
    )
    text = extract_text(resp)
    try:
        result = parse_json_block(text)
        is_real = bool(result.get("is_real", True))
        severity = Severity(result.get("severity", "medium"))
    except Exception:
        log.warning("detector parse failed; defaulting to real/medium\n%s", text)
        is_real, severity = True, Severity.MEDIUM

    return {
        "incident_id": state.get("incident_id") or f"inc-{int(time.time())}",
        "started_at": state.get("started_at") or time.time(),
        "is_real": is_real,
        "severity": severity,
        "decision_trail": [f"detector: is_real={is_real} severity={severity.value}"],
        "llm_traces": [{"node": "detect", "input_tokens": resp["usage"]["input_tokens"],
                        "output_tokens": resp["usage"]["output_tokens"]}],
    }
