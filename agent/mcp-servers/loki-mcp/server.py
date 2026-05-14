"""
MCP-style HTTP server for Loki tools.
"""
from __future__ import annotations

import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("loki-mcp")

LOKI_URL = os.getenv("LOKI_URL", "http://loki-gateway.monitoring:80")
app = FastAPI(title="loki-mcp")


class QueryLogs(BaseModel):
    query: str
    minutes_back: int = 15
    limit: int = 200


@app.get("/healthz")
def healthz(): return {"ok": True}


@app.post("/tools/query_logs")
async def query_logs(body: QueryLogs):
    end_ns = int(time.time() * 1e9)
    start_ns = int(end_ns - body.minutes_back * 60 * 1e9)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{LOKI_URL}/loki/api/v1/query_range", params={
            "query": body.query, "start": start_ns, "end": end_ns,
            "limit": body.limit, "direction": "backward",
        })
    if r.status_code != 200:
        raise HTTPException(502, f"loki returned {r.status_code}: {r.text[:200]}")
    return _summarize(r.json())


@app.post("/tools/list_streams")
async def list_streams():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{LOKI_URL}/loki/api/v1/labels")
    return r.json()


def _summarize(data: dict) -> dict:
    """Compress Loki's response. The agent doesn't need raw bytes — give it
    grouped templates and counts so the diagnostician can reason about volume."""
    streams = data.get("data", {}).get("result", [])
    out = []
    for s in streams[:20]:
        lines = [v[1] for v in s.get("values", [])]
        out.append({
            "labels": s.get("stream", {}),
            "count": len(lines),
            "sample": lines[:5],          # first 5 actual log lines
            "tail":   lines[-5:],         # most recent 5
        })
    return {"streams": out}
