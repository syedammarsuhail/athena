# ADR-003: Isolation Forest + Drain3, not "just use the LLM"

**Status:** Accepted
**Date:** Week 4

## Context

Anomaly detection runs continuously across thousands of metric series and millions of log lines per day. The LLM is good at reasoning, bad at high-volume pattern matching at $/Mtok.

## Decision

Use classical ML for detection:
- **Isolation Forest** per (service, metric) for metric anomalies
- **Drain3 + MiniLM + HDBSCAN** for log anomalies

LLM consumes only the *event*, never the raw stream. Cost stays bounded regardless of cluster size.

## Consequences

### Positive
- **Cost:** detection cost is ~$0 (CPU only) regardless of metric volume.
- **Latency:** classical ML responds in <100ms; an LLM call is 1–3s.
- **Determinism:** the same input gives the same output. Easier to debug, easier to test.
- **Explainability:** Isolation Forest decision_function gives a numeric score we can threshold.

### Negative
- **Concept drift:** models need retraining (nightly CronJob).
- **Cold start:** new services have no baseline for the first 24h.
- **Threshold tuning:** contamination parameter requires per-service calibration over time.

## Alternatives considered

- **LLM-only:** rejected (cost, latency, non-determinism).
- **Prometheus alerting rules:** still in use for *known* problems. ML is additive: it catches *unknown* unknowns.
- **Anomaly Detection in DataDog/NewRelic:** rejected (we want this portable across clouds; ADR-005 covers multi-cluster).
