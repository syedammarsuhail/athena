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
SLACK_WEBHOOK = os.getenv("SLACK_INCIDENT_WEBHOOK", "")
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO   = os.getenv("GITHUB_REPO", "")   # e.g. "myorg/athena"


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


def _post_slack(summary: str, md_link: str) -> None:
    if not SLACK_WEBHOOK:
        log.info("no SLACK_INCIDENT_WEBHOOK set; skipping Slack")
        return
    try:
        httpx.post(SLACK_WEBHOOK, json={
            "text": f"*Incident resolved* — {summary}\nFull postmortem: {md_link}",
        }, timeout=5)
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
    md = _render_markdown(state)
    issue_url = _file_github_issue(state, md)
    summary = _summarize_with_llm(md)
    _post_slack(summary, issue_url or "(no issue URL)")
    return {
        "postmortem_md": md,
        "decision_trail": [f"reporter: posted summary; issue={issue_url or 'n/a'}"],
    }
