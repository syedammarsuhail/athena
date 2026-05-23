"""
MCP-style HTTP server for Kubernetes + Argo CD tools.

Read tools: get_pod, get_events, describe_deployment
Write tools: restart_deployment, scale_deployment, rollback_argocd_app, cordon_node

The write tools here also enforce a Redis-backed cooldown as defense-in-depth
beyond OPA. (OPA decides 'may'; the MCP server decides 'actually do'.)
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time

import httpx
import redis
from fastapi import FastAPI, HTTPException
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("k8s-mcp")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis.agent:6379/0")
ARGOCD_URL = os.getenv("ARGOCD_URL", "https://argocd.argocd.svc")
ARGOCD_TOKEN = os.getenv("ARGOCD_TOKEN", "")
COOLDOWN_S = int(os.getenv("RESTART_COOLDOWN_S", "300"))

_r = redis.from_url(REDIS_URL, decode_responses=True)

try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()
app = FastAPI(title="k8s-mcp")


@app.get("/healthz")
def healthz(): return {"ok": True}


# ---------------------- READ TOOLS ----------------------------------------

class NS(BaseModel):
    namespace: str
    involved_object: str | None = None


class NSName(BaseModel):
    namespace: str
    name: str


@app.post("/tools/get_pod")
async def get_pod(body: NSName):
    try:
        pod = core_v1.read_namespaced_pod(body.name, body.namespace)
    except ApiException as e:
        raise HTTPException(e.status, e.reason)
    return {
        "name": pod.metadata.name,
        "phase": pod.status.phase,
        "containers": [
            {"name": c.name, "ready": c.ready, "restart_count": c.restart_count,
             "state": next(iter(c.state.to_dict().keys()))}
            for c in (pod.status.container_statuses or [])
        ],
        "node": pod.spec.node_name,
        "start_time": pod.status.start_time.isoformat() if pod.status.start_time else None,
    }


@app.post("/tools/get_events")
async def get_events(body: NS):
    field_selector = f"involvedObject.name={body.involved_object}" if body.involved_object else None
    evs = core_v1.list_namespaced_event(body.namespace, field_selector=field_selector, limit=50)
    return [
        {
            "type": e.type, "reason": e.reason, "message": e.message,
            "involved": f"{e.involved_object.kind}/{e.involved_object.name}",
            "count": e.count, "last_seen": e.last_timestamp.isoformat() if e.last_timestamp else None,
        }
        for e in evs.items
    ]


@app.post("/tools/describe_deployment")
async def describe_deployment(body: NSName):
    try:
        d = apps_v1.read_namespaced_deployment(body.name, body.namespace)
    except ApiException as e:
        raise HTTPException(e.status, e.reason)
    return {
        "name": d.metadata.name,
        "replicas": d.spec.replicas,
        "available": d.status.available_replicas,
        "image": d.spec.template.spec.containers[0].image if d.spec.template.spec.containers else None,
        "conditions": [{"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                       for c in (d.status.conditions or [])],
    }


# ---------------------- WRITE TOOLS ---------------------------------------

class Restart(BaseModel):
    namespace: str
    name: str


class Scale(BaseModel):
    namespace: str
    name: str
    replicas: int


class Rollback(BaseModel):
    app_name: str
    revision: int | None = None


class Cordon(BaseModel):
    node_name: str


def _check_cooldown(key: str) -> None:
    if _r.exists(key):
        ttl = _r.ttl(key)
        raise HTTPException(429, f"cooldown active, {ttl}s remaining")
    _r.setex(key, COOLDOWN_S, "1")


@app.post("/tools/restart_deployment")
async def restart_deployment(body: Restart):
    _check_cooldown(f"cooldown:restart:{body.namespace}:{body.name}")
    patch = {"spec": {"template": {"metadata": {"annotations":
            {"kubectl.kubernetes.io/restartedAt": dt.datetime.utcnow().isoformat()}}}}}
    try:
        apps_v1.patch_namespaced_deployment(body.name, body.namespace, patch)
    except ApiException as e:
        raise HTTPException(e.status, e.reason)
    return {"restarted": f"{body.namespace}/{body.name}", "at": dt.datetime.utcnow().isoformat()}


@app.post("/tools/scale_deployment")
async def scale_deployment(body: Scale):
    _check_cooldown(f"cooldown:scale:{body.namespace}:{body.name}")
    try:
        current = apps_v1.read_namespaced_deployment_scale(body.name, body.namespace)
        current.spec.replicas = body.replicas
        apps_v1.replace_namespaced_deployment_scale(body.name, body.namespace, current)
    except ApiException as e:
        raise HTTPException(e.status, e.reason)
    return {"scaled": f"{body.namespace}/{body.name}", "to": body.replicas}


@app.post("/tools/rollback_argocd_app")
async def rollback_argocd_app(body: Rollback):
    if not ARGOCD_TOKEN:
        raise HTTPException(500, "ARGOCD_TOKEN not configured")
    headers = {"Authorization": f"Bearer {ARGOCD_TOKEN}"}
    async with httpx.AsyncClient(verify=False, timeout=15) as c:
        if body.revision is None:
            # fetch history; rollback to previous successful sync
            r = await c.get(f"{ARGOCD_URL}/api/v1/applications/{body.app_name}", headers=headers)
            r.raise_for_status()
            history = r.json().get("status", {}).get("history", [])
            if len(history) < 2:
                raise HTTPException(400, "no previous revision in history")
            target_id = history[-2]["id"]
        else:
            target_id = body.revision
        r = await c.post(
            f"{ARGOCD_URL}/api/v1/applications/{body.app_name}/rollback",
            headers=headers,
            json={"id": target_id, "prune": False},
        )
        r.raise_for_status()
    return {"rolled_back": body.app_name, "to_history_id": target_id}


@app.post("/tools/cordon_node")
async def cordon_node(body: Cordon):
    try:
        node = core_v1.read_node(body.node_name)
        node.spec.unschedulable = True
        core_v1.patch_node(body.node_name, {"spec": {"unschedulable": True}})
    except ApiException as e:
        raise HTTPException(e.status, e.reason)
    return {"cordoned": body.node_name}
