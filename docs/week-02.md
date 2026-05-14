# Week 2 — Observability

**Goal:** A unified observability stack — metrics, logs, traces — with at least one service instrumented end-to-end, SLOs defined in code, and a working burn-rate alert.

## What you'll build

```
Online Boutique pods
   │
   ├─ Prometheus annotations  ──► Prometheus ──► Thanos (long-term storage)
   ├─ stdout/stderr           ──► Promtail   ──► Loki
   └─ OpenTelemetry SDK       ──► OTel Collector ──► Tempo
                                              ▼
                                          Grafana
                                              ▲
                                              │
                                          (queries all three)
```

## Day-by-day

### Day 1: kube-prometheus-stack

Apply the Argo app:
```bash
git add platform/observability/kube-prometheus-stack-app.yaml
git commit -m "feat(o11y): add kube-prometheus-stack"
git push
# Argo auto-syncs in ~3 min
```

Verify:
```bash
kubectl -n monitoring get pods
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
# open localhost:3000 — admin / prom-operator
```

You should see pre-built dashboards for the cluster, nodes, and workloads.

### Day 2: Loki

```bash
git add platform/observability/loki-app.yaml
git commit -m "feat(o11y): add Loki + Promtail" && git push
```

In Grafana, add Loki as a data source: `http://loki-gateway.monitoring.svc:80`. Run a query like `{namespace="online-boutique"}` — you should see logs streaming.

### Day 3: Tempo + OpenTelemetry

```bash
git add platform/observability/tempo-app.yaml platform/observability/otel-collector-app.yaml
git commit -m "feat(o11y): add Tempo + OTel Collector" && git push
```

### Day 4: Instrument one service

Pick `checkoutservice` (Go). Add the OTel SDK, export to the collector. PR walks through:
1. Add OTel deps to `go.mod`
2. Init the tracer provider pointing at `otel-collector.monitoring.svc:4317`
3. Wrap the gRPC server with `otelgrpc.NewServerHandler()`

You now have traces flowing. Click any span in Tempo, then in the Grafana Explore view you can hop directly to the matching logs via TraceID (configured in `tempo-datasource.yaml`).

### Day 5: SLOs with Sloth

Sloth converts SLO YAML into Prometheus recording rules + multi-window burn-rate alerts. See `platform/observability/slos/`.

```bash
kubectl apply -f platform/observability/slos/frontend-availability.yaml
```

Verify alerts exist:
```bash
kubectl -n monitoring exec -it prometheus-kube-prometheus-stack-prometheus-0 -- \
  promtool query instant http://localhost:9090 'ALERTS'
```

### Day 6: Trigger the burn-rate alert

```bash
# Hammer the frontend with bad requests to burn the error budget fast
kubectl run loadgen --image=alpine -- sh -c \
  'apk add curl; while true; do curl -s shop.example.com/i-do-not-exist; done'
```

Within a few minutes, the **fast-burn** alert should fire and route to Alertmanager.

### Day 7: Grafana provisioning

Move the Grafana dashboards to a ConfigMap so they're version-controlled, not click-ops'd. See `platform/observability/dashboards/`.

## Verification checklist

- [ ] In Grafana: a metric → drill to log → drill to trace, all correlated by TraceID
- [ ] At least one SLO defined in `slos/` with both fast-burn and slow-burn alerts
- [ ] Triggered the fast-burn alert once and routed it to a Slack channel
- [ ] Grafana dashboards live in Git, not in the Grafana DB
- [ ] All scrape targets `UP`: `count(up==0)` returns 0

## What "done" looks like

A screenshot in your README of the **service map** in Grafana (Tempo → Service Graph) showing the full Online Boutique microservice topology with real traffic and latency edges. This is the visual that sells.
