"""
Agent entrypoint. Subscribes to NATS anomaly subjects, runs each through the
LangGraph state machine, exposes Prom metrics on :9000.

One graph invocation per anomaly. Concurrency cap to avoid stampedes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid

import nats
from prometheus_client import Counter, Gauge, start_http_server

from graph.graph import build_graph
from graph.state import AnomalyEvent, IncidentState, Severity
from graph.dd_tracing import init_datadog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("agent")

NATS_URL = os.getenv("NATS_URL", "nats://nats.mlops:4222")
SUBJECTS = os.getenv("NATS_SUBJECTS", "anomalies.metric,anomalies.log").split(",")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "5"))

INCIDENTS = Counter("agent_incidents_total", "incidents handled", ["kind", "resolved"])
INFLIGHT  = Gauge("agent_incidents_inflight", "in-flight incidents")
LATENCY   = Counter("agent_incident_seconds_total", "cumulative wall time", ["kind"])


async def handle(graph, sem: asyncio.Semaphore, msg) -> None:
    async with sem:
        INFLIGHT.inc()
        t0 = time.time()
        try:
            payload = json.loads(msg.data)
            event = _to_event(payload, msg.subject)
            initial: IncidentState = {
                "event": event,
                "severity": event.severity,
                "tool_calls": [],
                "decision_trail": [],
                "llm_traces": [],
                "verification_attempt": 0,
            }
            log.info("handling incident on %s for %s/%s", msg.subject, event.namespace, event.service)
            # Each graph run gets its own thread_id for the checkpointer
            cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
            final = await asyncio.to_thread(graph.invoke, initial, cfg)
            INCIDENTS.labels(event.kind, str(final.get("resolved", False))).inc()
            log.info("incident complete: resolved=%s", final.get("resolved"))
        except Exception:
            log.exception("incident handler crashed")
            INCIDENTS.labels(msg.subject, "error").inc()
        finally:
            INFLIGHT.dec()
            LATENCY.labels(msg.subject).inc(time.time() - t0)


def _to_event(payload: dict, subject: str) -> AnomalyEvent:
    kind = {"anomalies.metric": "metric_anomaly",
            "anomalies.log":    "log_anomaly",
            "alerts.firing":    "alert"}.get(subject, "alert")

    return AnomalyEvent(
        kind=kind,
        service=payload.get("service", payload.get("labels", {}).get("pod", "unknown")),
        namespace=payload.get("namespace", payload.get("labels", {}).get("namespace", "online-boutique")),
        metric=payload.get("metric"),
        template=payload.get("template"),
        score=payload.get("score"),
        severity=Severity(payload.get("severity", "medium")),
        ts=payload.get("ts", time.time()),
        raw=payload,
    )


async def main() -> None:
    init_datadog()           # no-op if DD_API_KEY not set
    start_http_server(9000)  # Prometheus metrics
    graph = build_graph()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    nc = await nats.connect(NATS_URL, name="athena-agent")
    log.info("connected to NATS at %s", NATS_URL)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    for subject in SUBJECTS:
        async def cb(msg, _s=subject):
            asyncio.create_task(handle(graph, sem, msg))
        await nc.subscribe(subject.strip(), cb=cb, queue="agent-workers")
        log.info("subscribed to %s", subject)

    await stop.wait()
    log.info("shutting down")
    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
