"""
Reporter node: closes the loop with a human-readable record.

- Renders a postmortem markdown from the full IncidentState
- Posts a concise Slack summary to #incidents
- Opens a GitHub issue with the full postmortem body (so it's searchable and
  future incident retros can compare)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

import httpx

from .. import llm
from ..llm import extract_text
from ..state import IncidentState

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent.parent / "prompts" / "reporter.md").read_text()
SLACK_WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL", os.getenv("SLACK_INCIDENT_WEBHOOK", ""))
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO    = os.getenv("GITHUB_REPO", "")
GRAFANA_URL    = os.getenv("GRAFANA_URL", "")   # e.g. http://grafana:3000


def _render_markdown(state: IncidentState) -> str:
    event = state["event"]
    hyp = state.get("hypothesis")
    plan = state.get("plan")
    started = dt.datetime.utcfromtimestamp(state.get("started_at", 0)).isoformat() + "Z"
    duration = round((__import__("time").time() - state.get("started_at", 0)) / 60, 1)

    lines = [
        f"# Incident {state['incident_id']}",
        f"- **Started:** {started}",
        f"- **Duration:** {duration} min",
        f"- **Service:** {event.service} ({event.namespace})",
        f"- **Severity:** {state['severity'].value}",
        f"- **Resolved:** {'yes' if state.get('resolved') else 'no — escalated'}",
        "",
        "## What happened",
        f"- Trigger: {event.kind} ({event.metric or event.template or 'n/a'})",
    ]
    if hyp:
        lines += [
            f"- Root cause (confidence {hyp.confidence:.2f}): {hyp.root_cause}",
            "- Supporting evidence:",
            *[f"  - {e}" for e in hyp.supporting_evidence],
        ]
    if plan:
        lines += [
            "",
            "## Action taken",
            f"- {plan.action} (risk={plan.risk}, approval={plan.requires_approval})",
            f"- Rationale: {plan.rationale}",
            f"- Params: `{json.dumps(plan.params)}`",
        ]
    lines += ["", "## Decision trail"]
    lines += [f"- {x}" for x in state.get("decision_trail", [])]
    lines += ["", "## Tool calls (audit)"]
    for tc in state.get("tool_calls", []):
        ok = "✓" if tc.success else "✗"
        lines.append(
            f"- {ok} `{tc.tool}` (node={tc.node}) policy={tc.policy_decision or 'n/a'}\n"
            f"    args: `{json.dumps(tc.args)}`\n"
            f"    result: {tc.result_summary[:300]}"
        )
    lines += ["", "## Followups (TODO)"]
    lines += ["- [ ] Review hypothesis accuracy",
              "- [ ] Add detection rule if novel pattern",
              "- [ ] Update runbook"]
    return "\n".join(lines)


def _summarize_with_llm(md: str) -> str:
    """Have the LLM produce a 3-sentence Slack-friendly summary."""
    resp = llm.chat(
        node="report",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content":
                   f"Summarize this incident in 3 sentences for a Slack message:\n\n{md}"}],
        max_tokens=250,
    )
    return extract_text(resp).strip()


def _post_slack(state: IncidentState, summary: str, issue_url: str, trace_id: str) -> None:
    """
    Posts a rich Slack Block Kit message — same pattern used by Shopify, Atlassian, PagerDuty.
    Includes: status, service, root cause, action taken, duration, trace link, postmortem link.
    """
    if not SLACK_WEBHOOK:
        log.info("SLACK_WEBHOOK_URL not set; skipping Slack notification")
        return

    event    = state["event"]
    resolved = state.get("resolved", False)
    hyp      = state.get("hypothesis")
    plan     = state.get("plan")
    duration = round((__import__("time").time() - state.get("started_at", 0)))
    status_emoji = "✅" if resolved else "🚨"
    status_text  = "Resolved" if resolved else "Escalated — needs human"

    # Header section
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_emoji} Incident {status_text}: {event.service}"
            }
        },
        {"type": "divider"},
        # Service + severity + duration
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n`{event.namespace}/{event.service}`"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{state['severity'].value.upper()}"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{duration}s"},
                {"type": "mrkdwn", "text": f"*Incident ID:*\n`{state['incident_id']}`"},
            ]
        },
    ]

    # Root cause section
    if hyp:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Root Cause* (confidence {hyp.confidence:.0%}):\n"
                    f"{hyp.root_cause}\n\n"
                    f"*Evidence:* {', '.join(hyp.supporting_evidence[:3])}"
                )
            }
        })

    # Action taken
    if plan and plan.action not in ("no_action", "escalate_human"):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Action Taken:* `{plan.action}` (risk={plan.risk})\n_{plan.rationale}_"
            }
        })

    # AI summary
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"}
    })

    # Links row
    buttons = []
    if GRAFANA_URL and trace_id:
        buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "🔍 View Trace"},
            "url": f"{GRAFANA_URL}/explore?orgId=1&left=%5B%22now-1h%22,%22now%22,%22Tempo%22,%7B%22query%22:%22{trace_id}%22%7D%5D"
        })
    if issue_url:
        buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "📋 View Postmortem"},
            "url": issue_url
        })

    if buttons:
        blocks.append({"type": "actions", "elements": buttons})

    blocks.append({"type": "divider"})

    try:
        httpx.post(SLACK_WEBHOOK, json={"blocks": blocks}, timeout=5)
        log.info("slack notification posted for incident %s", state["incident_id"])
    except Exception:
        log.exception("slack post failed")


def _file_github_issue(state: IncidentState, md: str) -> str:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.info("github not configured; skipping issue")
        return ""
    try:
        r = httpx.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/issues",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github+json"},
            json={
                "title": f"Postmortem: {state['incident_id']} — {state['event'].service}",
                "body": md,
                "labels": ["postmortem", f"severity:{state['severity'].value}"],
            }, timeout=10,
        )
        r.raise_for_status()
        return r.json().get("html_url", "")
    except Exception:
        log.exception("github issue creation failed")
        return ""


def report(state: IncidentState) -> dict:
    from opentelemetry import trace as otel_trace

    md        = _render_markdown(state)
    issue_url = _file_github_issue(state, md)
    summary   = _summarize_with_llm(md)

    # Extract current OTel trace ID so Slack message links directly to this trace
    ctx      = otel_trace.get_current_span().get_span_context()
    trace_id = format(ctx.trace_id, "032x") if ctx and ctx.is_valid else ""

    _post_slack(state, summary, issue_url or "", trace_id)

    return {
        "postmortem_md": md,
        "decision_trail": [
            f"reporter: posted slack summary; issue={issue_url or 'n/a'}; trace={trace_id or 'n/a'}"
        ],
    }
