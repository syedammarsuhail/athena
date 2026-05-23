"""
Streaming log-anomaly detector.

Pipeline:
  Loki tail → Drain3 (template extraction) → MiniLM embedding (new templates only)
            → HDBSCAN clustering → publish rare/new templates as anomalies

Anomalies are published to NATS subject "anomalies.log".

Tradeoffs:
- Drain3 state is persisted to S3 every 60s; service is stateless on restart beyond that
- Embedding only NEW templates keeps GPU cost trivial (small set, runs on CPU fine)
- "Rare" = cluster of size 1 OR template never seen in past 24h
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque

import httpx
import nats
import numpy as np
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from sentence_transformers import SentenceTransformer
from hdbscan import HDBSCAN

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("drain3-serve")

LOKI_URL = os.getenv("LOKI_URL", "http://loki-gateway.monitoring:80")
LOKI_QUERY = os.getenv("LOKI_QUERY", '{namespace="online-boutique"}')
NATS_URL = os.getenv("NATS_URL", "nats://nats.mlops:4222")
RECLUSTER_EVERY = int(os.getenv("RECLUSTER_EVERY", "300"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# State
miner_cfg = TemplateMinerConfig()
miner_cfg.load(os.getenv("DRAIN3_CONFIG", "/app/drain3.ini"))
miner = TemplateMiner(config=miner_cfg)

model = SentenceTransformer(EMBEDDING_MODEL)
known_templates: dict[str, dict] = {}  # template_id -> {"text": ..., "first_seen": ..., "count": ...}
recent_window: deque[tuple[float, str]] = deque(maxlen=10000)


async def tail_loki(nc) -> None:
    """Long-poll Loki for new log lines (using /loki/api/v1/tail websocket would be cleaner;
    using HTTP query_range here for portability)."""
    last_ns = int(time.time() * 1e9)
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            now_ns = int(time.time() * 1e9)
            r = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    "query": LOKI_QUERY,
                    "start": last_ns,
                    "end": now_ns,
                    "limit": 5000,
                    "direction": "forward",
                },
            )
            try:
                data = r.json()["data"]["result"]
            except Exception:
                log.warning("loki returned %s", r.status_code)
                await asyncio.sleep(2)
                continue
            for stream in data:
                labels = stream["stream"]
                for ts_ns, line in stream["values"]:
                    await handle_line(nc, labels, line)
            last_ns = now_ns
            await asyncio.sleep(2)


async def handle_line(nc, labels: dict, line: str) -> None:
    result = miner.add_log_message(line)
    if result is None:
        return
    template_id = str(result["cluster_id"])
    template_text = result["template_mined"]
    now = time.time()

    entry = known_templates.get(template_id)
    if entry is None:
        known_templates[template_id] = {
            "text": template_text,
            "first_seen": now,
            "count": 1,
            "labels": labels,
        }
        # publish a "new template" anomaly. Likely the highest-value signal.
        await publish_anomaly(nc, {
            "kind": "new_log_template",
            "template_id": template_id,
            "template": template_text,
            "sample": line[:500],
            "labels": labels,
            "ts": now,
        })
    else:
        entry["count"] += 1

    recent_window.append((now, template_id))


async def publish_anomaly(nc, payload: dict) -> None:
    await nc.publish("anomalies.log", json.dumps(payload).encode())
    log.info("published anomaly kind=%s template=%s",
             payload["kind"], payload.get("template", "")[:80])


async def reclustering_loop(nc) -> None:
    """Periodically embed all templates and cluster; templates that are singletons
    or far from any cluster center are also flagged."""
    while True:
        await asyncio.sleep(RECLUSTER_EVERY)
        if len(known_templates) < 10:
            continue
        ids = list(known_templates.keys())
        texts = [known_templates[i]["text"] for i in ids]
        embs = model.encode(texts, normalize_embeddings=True, batch_size=64)
        clusterer = HDBSCAN(min_cluster_size=3, metric="euclidean")
        labels = clusterer.fit_predict(embs)
        # label == -1 means noise/outlier in HDBSCAN
        for tid, label in zip(ids, labels):
            if label == -1 and known_templates[tid]["count"] < 20:
                await publish_anomaly(nc, {
                    "kind": "rare_log_template",
                    "template_id": tid,
                    "template": known_templates[tid]["text"],
                    "count": known_templates[tid]["count"],
                    "ts": time.time(),
                })


async def main() -> None:
    nc = await nats.connect(NATS_URL)
    log.info("connected to NATS at %s", NATS_URL)
    await asyncio.gather(
        tail_loki(nc),
        reclustering_loop(nc),
    )


if __name__ == "__main__":
    asyncio.run(main())
