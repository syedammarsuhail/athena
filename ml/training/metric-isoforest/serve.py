"""
FastAPI inference server for IsolationForest models.
Loads models from MLflow registry on startup. Refreshes every 15 min.

Endpoints:
  POST /v1/predict        - single prediction
  POST /v1/predict/batch  - many at once
  GET  /healthz, /readyz
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List

import mlflow
import mlflow.sklearn
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, make_asgi_app

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("isoforest-serve")

MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlops:5000")
REFRESH_SECONDS = int(os.getenv("MODEL_REFRESH_SECONDS", "900"))

MODELS: Dict[str, object] = {}
LAST_REFRESH = 0.0

REQS = Counter("isoforest_requests_total", "predictions served", ["model", "result"])
LATENCY = Histogram("isoforest_predict_seconds", "prediction latency", ["model"])


class Features(BaseModel):
    value: float
    mean5m: float
    std5m: float
    ratio: float
    d1: float
    d2: float
    hod_sin: float
    hod_cos: float
    dow_sin: float
    dow_cos: float

    def to_array(self) -> np.ndarray:
        return np.array([[self.value, self.mean5m, self.std5m, self.ratio,
                          self.d1, self.d2, self.hod_sin, self.hod_cos,
                          self.dow_sin, self.dow_cos]], dtype=np.float32)


class PredictRequest(BaseModel):
    model_name: str = Field(..., examples=["isoforest-online-boutique-cartservice-container_memory_working_set_bytes"])
    features: Features


class PredictResponse(BaseModel):
    model_name: str
    is_anomaly: bool
    score: float
    threshold: float = -0.0   # decision_function returns negative for outliers


def refresh_models() -> None:
    """Reload all registered isoforest models. Idempotent."""
    global LAST_REFRESH
    mlflow.set_tracking_uri(MLFLOW_URI)
    client = mlflow.MlflowClient()
    loaded = 0
    for rm in client.search_registered_models(filter_string="name LIKE 'isoforest-%'"):
        latest = client.get_latest_versions(rm.name, stages=["None", "Production"])
        if not latest:
            continue
        v = sorted(latest, key=lambda x: int(x.version))[-1]
        try:
            MODELS[rm.name] = mlflow.sklearn.load_model(f"models:/{rm.name}/{v.version}")
            loaded += 1
        except Exception as e:
            log.warning("failed to load %s: %s", rm.name, e)
    LAST_REFRESH = time.time()
    log.info("refreshed %d models", loaded)


async def refresh_loop():
    while True:
        try:
            refresh_models()
        except Exception:
            log.exception("refresh failed")
        await asyncio.sleep(REFRESH_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    refresh_models()
    task = asyncio.create_task(refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="isoforest-serve", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/healthz")
def healthz(): return {"ok": True}


@app.get("/readyz")
def readyz():
    return {"ok": len(MODELS) > 0, "models": len(MODELS)}


@app.post("/v1/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    model = MODELS.get(req.model_name)
    if model is None:
        REQS.labels(req.model_name, "unknown_model").inc()
        raise HTTPException(404, f"unknown model {req.model_name}")
    with LATENCY.labels(req.model_name).time():
        X = req.features.to_array()
        pred = int(model.predict(X)[0])
        score = float(model.decision_function(X)[0])
    is_anom = pred == -1
    REQS.labels(req.model_name, "anomaly" if is_anom else "normal").inc()
    return PredictResponse(
        model_name=req.model_name,
        is_anomaly=is_anom,
        score=score,
    )


class BatchRequest(BaseModel):
    items: List[PredictRequest]


@app.post("/v1/predict/batch")
def predict_batch(req: BatchRequest):
    return [predict(item) for item in req.items]
