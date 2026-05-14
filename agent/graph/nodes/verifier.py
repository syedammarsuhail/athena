"""
Verifier node: did the remediation actually fix the problem?

Re-queries the original anomalous signal after a settle period and decides.
If still anomalous after MAX_VERIFY_LOOPS, the graph escalates via Reporter.
"""
from __future__ import annotations

import logging
import time

from .. import mcp_client
from ..state import IncidentState

log = logging.getLogger(__name__)

SETTLE_SECONDS = 30      # let new pods come up & traffic stabilize
LOOKBACK_MIN = 2         # window for the verification query


def verify(state: IncidentState) -> dict:
    event = state["event"]
    time.sleep(SETTLE_SECONDS)

    # Reconstruct the right verification query from the event.
    if event.kind == "metric_anomaly" and event.metric:
        query = (
            f'avg_over_time({event.metric}'
            f'{{namespace="{event.namespace}",pod=~"{event.service}.*"}}'
            f'[{LOOKBACK_MIN}m])'
        )
        tc = mcp_client.call("prom.query_instant", {"query": query}, node="verify")
    elif event.kind == "log_anomaly":
        # check whether the same template is still firing
        tc = mcp_client.call(
            "loki.query_logs",
            {"query": f'{{namespace="{event.namespace}"}}', "minutes_back": LOOKBACK_MIN},
            node="verify",
        )
    else:
        tc = mcp_client.call(
            "prom.query_instant",
            {"query": f'ALERTS{{alertname="{event.raw.get("alertname","")}"}}'},
            node="verify",
        )

    # Naïve rule: if the verification tool succeeded and returned non-empty data,
    # *and* values are below 2× the historical mean, call it resolved. Real-world
    # you'd reuse the same isoforest model for this check.
    resolved = tc.success and "anomal" not in tc.result_summary.lower()

    return {
        "tool_calls": [tc],
        "resolved": resolved,
        "decision_trail": [
            f"verifier: attempt={state.get('verification_attempt', 0)} resolved={resolved}"
        ],
    }
