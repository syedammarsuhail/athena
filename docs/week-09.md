# Week 9 — Chaos Engineering + Game Days

**Goal:** Continuous low-grade chaos in dev, scheduled high-impact chaos in stage. Agent is in the loop for both.

## Why chaos?

Three reasons that matter for this project:
1. It generates **real incidents** for the agent to handle (otherwise the agent is just code, not a system).
2. It lets you measure **MTTR with and without the agent** — that's the kill metric for your portfolio.
3. It validates the observability stack (you can't react to what you can't see).

## What you'll build

```
Chaos Mesh CRDs (chaos/experiments/*.yaml)
  ├─ memory-leak    → PodChaos: stress-ng eats memory in cartservice
  ├─ network-delay  → NetworkChaos: 200ms latency on cartservice → cart-db
  ├─ falco-trigger  → simulated terminal-exec for the security scenario
  ├─ pod-kill       → routine, runs every hour in dev
  └─ schedule.yaml  → declarative cron of the above
```

## Day-by-day

### Day 1: Chaos Mesh

```bash
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm install chaos-mesh chaos-mesh/chaos-mesh \
  --namespace=chaos-mesh --create-namespace \
  --set dashboard.create=true
kubectl -n chaos-mesh port-forward svc/chaos-dashboard 2333:2333
```

### Day 2: Scenario A — memory leak

```bash
kubectl apply -f chaos/experiments/memory-leak.yaml
```

Watch the loop:
- `kubectl -n online-boutique top pods -l app=cartservice` → memory climbs
- `kubectl -n monitoring logs -l app=isoforest-poller` → anomaly published
- `kubectl -n agent logs -l app=athena-agent -f` → graph runs
- `kubectl -n online-boutique describe deploy cartservice` → annotation update (restart)

Record the timestamps. The Reporter posts a postmortem to your Slack.

### Day 3: Scenario B — network delay (HITL path)

```bash
kubectl apply -f chaos/experiments/network-delay.yaml
```

This triggers cart latency anomalies. The agent's hypothesis correlates with a recent deploy, so the Remediator picks `rollback_argocd_app` → **requires approval**. The Slack-bot posts an interactive message; you approve from your phone; the graph resumes.

### Day 4: Scenario C — security event

```bash
kubectl apply -f chaos/experiments/falco-trigger.yaml
```

A Job exec's into the productcatalog pod. Falco fires `Terminal shell in container`. The agent (subscribed to `alerts.firing` too) treats it differently: instead of remediating, it cordons the node and pages a human. (Demonstrates that the agent has different playbooks per signal type.)

### Day 5: Scheduled chaos

Run the schedule manifest (`chaos/experiments/schedule.yaml`) so that random low-impact chaos runs every hour in dev. The dashboards now have real signal to render.

### Day 6: MTTR measurement

Build a Grafana panel:
```promql
# avg time from anomaly event to "resolved=true"
avg(agent_incident_seconds_total / agent_incidents_total)
```
Compare against your baseline (do an "agent-disabled" hour by scaling agent to 0, let chaos run, measure manual MTTR).

### Day 7: Write up

Add `docs/runbooks/demos.md` with the exact replays of the three scenarios — including what to click, what to expect, and the gotchas. This is your demo cheat sheet.

## Verification checklist

- [ ] All three scenarios run end-to-end without intervention (except HITL for Scenario B)
- [ ] MTTR-with-agent measured and recorded
- [ ] Schedule runs continuously in dev
- [ ] At least one chaos run discovered a real observability gap (and you fixed it)

## What "done" looks like

A 3-minute video stitching together Scenarios A, B, C in sequence, with overlaid timestamps and a final slide:
> "MTTR baseline: 18 min. MTTR with Athena: 2.1 min average across 31 chaos runs."

That number is your hire-me sentence.
