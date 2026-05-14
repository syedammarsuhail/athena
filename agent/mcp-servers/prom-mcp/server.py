"""
MCP-style HTTP server for Prometheus tools.

For simplicity, exposes plain HTTP endpoints under /tools/<tool>. The agent's
mcp_client.call() dispatches by tool namespace. This is intentionally NOT the
full MCP-over-stdio protocol — HTTP is fine inside a cluster, simpler to
operate, and lets us run each server as a normal Deployment.
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("prom-mcp")

PROM_URL = os.getenv("PROM_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090")
app = FastAPI(title="prom-mcp")


class QueryInstant(BaseModel):
    query: str


class QueryRange(BaseModel):
    query: str
    minutes_back: int = 30
    step_seconds: int = 30


@app.get("/healthz")
def healthz(): return {"ok": True}


@app.post("/tools/query_instant")
async def query_instant(body: QueryInstant):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{PROM_URL}/api/v1/query", params={"query": body.query})
    if r.status_code != 200:
        raise HTTPException(502, f"prometheus returned {r.status_code}")
    return _simplify(r.json())


@app.post("/tools/query_range")
async def query_range(body: QueryRange):
    import time
    end = time.time()
    start = end - body.minutes_back * 60
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{PROM_URL}/api/v1/query_range", params={
            "query": body.query,
            "start": start, "end": end,
            "step": f"{body.step_seconds}s",
        })
    if r.status_code != 200:
        raise HTTPException(502, f"prometheus returned {r.status_code}")
    return _simplify(r.json())


@app.post("/tools/list_alerts")
async def list_alerts():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{PROM_URL}/api/v1/alerts")
    return r.json().get("data", {}).get("alerts", [])


def _simplify(data: dict) -> dict:
    """Trim Prometheus response so we don't blow the LLM context window."""
    result = data.get("data", {}).get("result", [])
    # Cap to first 20 series; for ranges, cap to last 60 samples per series
    out = []
    for s in result[:20]:
        if "value" in s:
            out.append({"metric": s["metric"], "value": s["value"]})
        else:
            vals = s.get("values", [])[-60:]
            out.append({"metric": s["metric"], "values": vals})
    return {"result_type": data.get("data", {}).get("resultType"), "result": out}
