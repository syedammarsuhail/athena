"""
Slack approval bot.

Two endpoints:
  POST /post-approval   - called by the HITL node to post an interactive message
  POST /slack/actions   - called by Slack when a user clicks Approve/Reject

On click, writes the decision to Redis at key `approval:<incident_id>`. The
HITL node is polling that key and resumes the graph.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import httpx
import redis
import urllib.parse
from fastapi import FastAPI, HTTPException, Request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("slack-bot")

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "").encode()
REDIS_URL = os.getenv("REDIS_URL", "redis://redis.agent:6379/0")
SLACK_CHANNEL = os.getenv("SLACK_APPROVAL_CHANNEL", "#incidents")

_r = redis.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="athena-slack-bot")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/post-approval")
async def post_approval(body: dict):
    """Called by the agent HITL node."""
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"Approval needed: {body['action']}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Incident:*\n`{body['incident_id']}`"},
            {"type": "mrkdwn", "text": f"*Service:*\n{body['namespace']}/{body['service']}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": f"*Hypothesis:* {body['hypothesis'].get('root_cause', 'n/a')} "
                                             f"(confidence {body['hypothesis'].get('confidence', 0):.2f})"}},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": f"*Rationale:* {body['rationale']}"}},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": f"*Params:*\n```{json.dumps(body['params'], indent=2)}```"}},
        {"type": "actions", "elements": [
            {"type": "button", "style": "primary",
             "text": {"type": "plain_text", "text": "Approve"},
             "value": f"{body['incident_id']}|approved",
             "action_id": "approve"},
            {"type": "button", "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"},
             "value": f"{body['incident_id']}|rejected",
             "action_id": "reject"},
        ]},
    ]
    if not SLACK_WEBHOOK:
        log.warning("SLACK_WEBHOOK_URL not set; storing AUTO-APPROVE for dev")
        _r.setex(f"approval:{body['incident_id']}", 600, "approved")
        return {"posted": False, "dev_auto": "approved"}
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(SLACK_WEBHOOK, json={"channel": SLACK_CHANNEL, "blocks": blocks})
        r.raise_for_status()
    return {"posted": True}


def _verify_slack_sig(body: bytes, ts: str, sig: str) -> bool:
    if not SLACK_SIGNING_SECRET:
        return True  # dev mode
    if abs(time.time() - int(ts)) > 60 * 5:
        return False
    base = b"v0:" + ts.encode() + b":" + body
    digest = "v0=" + hmac.new(SLACK_SIGNING_SECRET, base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, sig)


@app.post("/slack/actions")
async def slack_actions(request: Request):
    raw = await request.body()
    ts = request.headers.get("x-slack-request-timestamp", "")
    sig = request.headers.get("x-slack-signature", "")
    if not _verify_slack_sig(raw, ts, sig):
        raise HTTPException(401, "bad signature")

    form = urllib.parse.parse_qs(raw.decode())
    payload = json.loads(form["payload"][0])
    action = payload["actions"][0]
    value = action["value"]                       # "<incident_id>|approved"
    incident_id, decision = value.split("|", 1)
    user = payload["user"]["username"]

    _r.setex(f"approval:{incident_id}", 600, decision)
    log.info("incident %s %s by %s", incident_id, decision, user)

    return {
        "response_action": "update",
        "text": f"Action {decision} by @{user}.",
        "replace_original": True,
    }
