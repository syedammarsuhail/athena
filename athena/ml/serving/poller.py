"""
Poll Prometheus every 30s, call the isoforest service, publish anomalies to NATS.
This is the closed-loop component — ML drives the agent.
"""
from __future__ import annotations

import asyncio, json, logging, os, time
import httpx, nats, numpy as np

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("poller")

PROM = os.getenv("PROM_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090")
ISO  = os.getenv("ISOFOREST_URL", "http://isoforest.mlops")
NATS_URL = os.getenv("NATS_URL", "nats://nats.mlops:4222")
INTERVAL = int(os.getenv("INTERVAL_S", "30"))

# Same metrics the trainer used. In production, discover from MLflow registry.
TARGETS = [
    ("container_memory_working_set_bytes", '{namespace="online-boutique",pod!=""}'),
    ("container_cpu_usage_seconds_total",  '{namespace="online-boutique",pod!=""}'),
]


async def featurize_series(client: httpx.AsyncClient, metric: str, selector: str):
    # Pull the last 6 minutes of 30s-resolution data
    end = time.time()
    start = end - 6 * 60
    r = await client.get(f"{PROM}/api/v1/query_range", params={
        "query": f'{metric}{selector}',
        "start": start, "end": end, "step": "30s",
    })
    r.raise_for_status()
    for series in r.json()["data"]["result"]:
        pod = series["metric"].get("pod", "unknown")
        vals = np.array([float(v[1]) for v in series["values"]], dtype=np.float32)
        if len(vals) < 5:
            continue
        v = vals[-1]
        mean5m = float(vals[-10:].mean()) if len(vals) >= 10 else float(vals.mean())
        std5m  = float(vals[-10:].std())  if len(vals) >= 10 else float(vals.std())
        ratio  = float(v / mean5m) if mean5m else 1.0
        d1     = float(vals[-1] - vals[-2])
        d2     = float(d1 - (vals[-2] - vals[-3])) if len(vals) >= 3 else 0.0
        hour = (time.gmtime().tm_hour + time.gmtime().tm_min / 60)
        dow  = time.gmtime().tm_wday
        feats = {
            "value": float(v),
            "mean5m": mean5m, "std5m": std5m, "ratio": ratio,
            "d1": d1, "d2": d2,
            "hod_sin": float(np.sin(2*np.pi*hour/24)),
            "hod_cos": float(np.cos(2*np.pi*hour/24)),
            "dow_sin": float(np.sin(2*np.pi*dow/7)),
            "dow_cos": float(np.cos(2*np.pi*dow/7)),
        }
        yield pod, metric, feats


async def main():
    nc = await nats.connect(NATS_URL)
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                for metric, sel in TARGETS:
                    model_name = f"isoforest-online-boutique-*-{metric}"  # TODO: refine
                    async for pod, m, feats in featurize_series(client, metric, sel):
                        r = await client.post(f"{ISO}/v1/predict", json={
                            "model_name": model_name, "features": feats,
                        })
                        if r.status_code == 404:
                            continue
                        body = r.json()
                        if body["is_anomaly"]:
                            event = {
                                "kind": "metric_anomaly",
                                "service": pod, "metric": m,
                                "score": body["score"], "value": feats["value"],
                                "ts": time.time(),
                            }
                            await nc.publish("anomalies.metric", json.dumps(event).encode())
                            log.info("anomaly: %s %s score=%.3f", pod, m, body["score"])
            except Exception:
                log.exception("poll cycle failed")
            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
