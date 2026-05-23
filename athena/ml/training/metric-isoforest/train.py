"""
Train Isolation Forest anomaly detectors per (service, metric) from Prometheus history.

Run as a CronJob (see cronjob.yaml). One model per (namespace, deployment, metric).
Logs models + metadata to MLflow. KServe loads the latest model from the MLflow registry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
from dataclasses import dataclass, asdict
from typing import Iterable

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_recall_fscore_support
from sklearn.model_selection import train_test_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("isoforest-train")


@dataclass
class Target:
    namespace: str
    deployment: str
    metric: str           # promQL metric name
    label_selector: str   # extra labels

    @property
    def model_name(self) -> str:
        return f"isoforest-{self.namespace}-{self.deployment}-{self.metric}".replace("/", "_")


def query_range(prom_url: str, query: str, start: dt.datetime, end: dt.datetime, step: str = "30s") -> pd.DataFrame:
    """Return a long-form DataFrame: ts | series_labels_hash | value."""
    resp = requests.get(
        f"{prom_url}/api/v1/query_range",
        params={
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]["result"]
    rows = []
    for series in data:
        labels = series["metric"]
        # stable key per timeseries
        series_id = "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
        for ts, val in series["values"]:
            rows.append({"ts": float(ts), "series_id": series_id, "value": float(val), **labels})
    return pd.DataFrame(rows)


def featurize(df: pd.DataFrame) -> np.ndarray:
    """
    Convert a single-series time vector into a feature matrix using sliding
    windows. Features: value, 5m rolling mean, 5m rolling std, value/mean ratio,
    1st/2nd derivatives, hour-of-day (cyclic), day-of-week (cyclic).
    """
    df = df.sort_values("ts").copy()
    df["dt"] = pd.to_datetime(df["ts"], unit="s")
    df = df.set_index("dt")

    # resample uniform; forward-fill short gaps
    s = df["value"].resample("30s").mean().ffill(limit=4)
    if len(s) < 50:
        raise ValueError("not enough samples")

    feats = pd.DataFrame({"v": s.values}, index=s.index)
    feats["mean5m"] = feats["v"].rolling(10, min_periods=3).mean()
    feats["std5m"]  = feats["v"].rolling(10, min_periods=3).std().fillna(0)
    feats["ratio"]  = feats["v"] / (feats["mean5m"].replace(0, np.nan))
    feats["ratio"]  = feats["ratio"].replace([np.inf, -np.inf], np.nan).fillna(1.0)
    feats["d1"]     = feats["v"].diff().fillna(0)
    feats["d2"]     = feats["d1"].diff().fillna(0)

    hours = feats.index.hour + feats.index.minute / 60
    feats["hod_sin"] = np.sin(2 * np.pi * hours / 24)
    feats["hod_cos"] = np.cos(2 * np.pi * hours / 24)
    dows = feats.index.dayofweek
    feats["dow_sin"] = np.sin(2 * np.pi * dows / 7)
    feats["dow_cos"] = np.cos(2 * np.pi * dows / 7)

    return feats.dropna().to_numpy(dtype=np.float32)


def train_one(target: Target, prom_url: str, window_days: int, contamination: float) -> dict:
    end = dt.datetime.utcnow()
    start = end - dt.timedelta(days=window_days)
    promql = f'{target.metric}{{namespace="{target.namespace}",{target.label_selector}}}'

    df = query_range(prom_url, promql, start, end)
    if df.empty:
        log.warning("no data for %s", target.model_name)
        return {"status": "skipped", "reason": "no data"}

    # train one model per series (or a shared one - shared keeps signal cleaner)
    X_parts = []
    for sid, g in df.groupby("series_id"):
        try:
            X_parts.append(featurize(g))
        except ValueError:
            continue
    if not X_parts:
        return {"status": "skipped", "reason": "all series too short"}
    X = np.vstack(X_parts)

    X_train, X_val = train_test_split(X, test_size=0.2, shuffle=False)
    model = IsolationForest(
        n_estimators=200,
        max_samples="auto",
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train)

    # self-evaluation: anomaly rate on the "normal" validation set should be near contamination
    val_pred = model.predict(X_val)  # +1 normal, -1 anomaly
    realised_rate = float((val_pred == -1).mean())

    log.info("trained %s | rows=%d | realised_anomaly_rate=%.3f", target.model_name, len(X), realised_rate)

    with mlflow.start_run(run_name=target.model_name):
        mlflow.log_params({
            "namespace": target.namespace,
            "deployment": target.deployment,
            "metric": target.metric,
            "label_selector": target.label_selector,
            "contamination": contamination,
            "n_estimators": 200,
            "window_days": window_days,
        })
        mlflow.log_metrics({
            "n_rows": float(len(X)),
            "realised_anomaly_rate": realised_rate,
        })
        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            registered_model_name=target.model_name,
        )

    return {"status": "ok", "n_rows": len(X), "realised_rate": realised_rate}


def parse_targets(metrics: str, namespaces: str, label_selector: str) -> Iterable[Target]:
    for ns in namespaces.split(","):
        for m in metrics.split(","):
            # one model per deployment by default
            ns = ns.strip(); m = m.strip()
            # We'd typically discover deployments via the K8s API.
            # For brevity, train cluster-wide-per-namespace; refine later.
            yield Target(namespace=ns, deployment="*", metric=m, label_selector=label_selector)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prom-url", required=True)
    ap.add_argument("--metrics", required=True, help="comma-separated metric names")
    ap.add_argument("--namespaces", required=True)
    ap.add_argument("--label-selector", default='pod=~".+"')
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--contamination", type=float, default=0.01)
    ap.add_argument("--mlflow-uri", default=os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlops:5000"))
    args = ap.parse_args()

    mlflow.set_tracking_uri(args.mlflow_uri)
    mlflow.set_experiment("athena-anomaly-isoforest")

    for t in parse_targets(args.metrics, args.namespaces, args.label_selector):
        try:
            r = train_one(t, args.prom_url, args.window_days, args.contamination)
            log.info("result: %s -> %s", t.model_name, r)
        except Exception as e:
            log.exception("failed %s: %s", t.model_name, e)


if __name__ == "__main__":
    main()
