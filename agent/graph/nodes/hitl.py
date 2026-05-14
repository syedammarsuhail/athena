"""
Human-in-the-loop node: blocks until a Slack approval signal arrives.

Implementation: posts an interactive Slack message with Approve/Reject buttons,
then long-polls a Redis key the slack_bot writes when the user clicks. Times
out after AWAIT_TIMEOUT_S; treats timeout as rejection.
"""
from __future__ import annotations

import logging
import os
import time

import httpx
import redis

from ..state import IncidentState

log = logging.getLogger(__name__)

SLACK_BOT_API = os.getenv("SLACK_BOT_API", "http://slack-bot.agent:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis.agent:6379/0")
AWAIT_TIMEOUT_S = int(os.getenv("AWAIT_TIMEOUT_S", "600"))   # 10 minutes
POLL_INTERVAL_S = 5

_r = redis.from_url(REDIS_URL, decode_responses=True)


def await_approval(state: IncidentState) -> dict:
    plan = state["plan"]
    incident_id = state["incident_id"]
    key = f"approval:{incident_id}"

    # ask slack-bot to post the message
    try:
        httpx.post(f"{SLACK_BOT_API}/post-approval", json={
            "incident_id": incident_id,
            "service": state["event"].service,
            "namespace": state["event"].namespace,
            "action": plan.action,
            "params": plan.params,
            "rationale": plan.rationale,
            "hypothesis": state["hypothesis"].model_dump() if state.get("hypothesis") else {},
        }, timeout=5)
    except Exception:
        log.exception("failed to post approval message; defaulting to reject")
        return {"approval": "rejected",
                "decision_trail": ["hitl: slack-bot unreachable; auto-reject"]}

    # poll redis for the answer
    deadline = time.time() + AWAIT_TIMEOUT_S
    while time.time() < deadline:
        val = _r.get(key)
        if val in ("approved", "rejected"):
            _r.delete(key)
            return {
                "approval": val,
                "decision_trail": [f"hitl: human {val} after {int(time.time() - state['started_at'])}s"],
            }
        time.sleep(POLL_INTERVAL_S)

    return {
        "approval": "timeout",
        "decision_trail": [f"hitl: timed out after {AWAIT_TIMEOUT_S}s, treating as reject"],
    }
