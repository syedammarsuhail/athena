# Demo runbook — three scenarios

Three rehearsable demos. Each takes ~5 minutes. Practice them; record the best take.

## Pre-flight (run once before any demo)

```bash
# 1. Open these tabs in your browser:
#    - Grafana (golden-signals dashboard pinned)
#    - Argo CD (root-app)
#    - Slack #incidents channel
#    - Chaos dashboard

# 2. Open three terminal panes:
kubectl -n agent logs -l app=athena-agent -f       # pane 1: agent
kubectl -n online-boutique top pods -w             # pane 2: workload
nats sub "anomalies.>"                             # pane 3: event bus

# 3. Confirm baseline is healthy
kubectl -n online-boutique get pods | grep -v Running && echo "NOT READY" || echo "ready"
```

## Scenario A — Memory leak, auto-remediated (low-stakes, no HITL)

**The story:** A new release of `cartservice` has a leak. The agent catches it before the 95th percentile latency SLO burns out.

```bash
kubectl apply -f chaos/experiments/memory-leak.yaml
```

**Talk through, as it unfolds:**
1. (60s) "isoforest sees memory climbing 4× over baseline — anomaly published."
2. (5s) "Detector classifies as real, severity high."
3. (15s) "Diagnostician pulls metrics, recent events, logs. Three pieces of evidence, confidence 0.82."
4. (3s) "Remediator picks restart_deployment. Low risk — no approval required."
5. (5s) "Executor runs the patch; OPA allowed it."
6. (30s) "Verifier waits 30s, re-queries, sees memory stable."
7. "Reporter posts the postmortem to Slack."

End by showing the Slack thread.

## Scenario B — Bad deploy → rollback (with HITL)

**The story:** A latency spike correlates with a recent deploy. The agent proposes rollback; you approve from Slack.

```bash
kubectl apply -f chaos/experiments/network-delay.yaml
# Then trigger a fake deploy event to make the agent correlate:
kubectl -n online-boutique annotate deployment cartservice deploy.athena/last-applied=$(date -Iseconds) --overwrite
```

Show the Slack Approve / Reject buttons. Click Approve. The graph resumes, runs rollback, verifies, reports.

The key teaching moment: **risk-tiered actions take different paths through the graph.**

## Scenario C — Suspicious shell → contain, not remediate

**The story:** Falco fires on a shell exec'd into a pod. The agent's playbook isn't "restart" — it's "isolate and page."

```bash
kubectl apply -f chaos/experiments/falco-trigger.yaml
```

Watch:
1. Falco event → falcosidekick → Loki + the agent's `alerts.firing` consumer.
2. The agent recognizes it's a security signal, not a performance one.
3. Plan: cordon_node + page (no automatic remediation).
4. Slack message tags `@oncall-security` with the full event context.

This shows **the agent's playbooks vary by signal class** — not a one-size-fits-all responder.

## After each demo

```bash
# Clean up the chaos
kubectl delete stresschaos cartservice-memory-leak -n chaos-mesh --ignore-not-found
kubectl delete networkchaos cart-to-redis-latency -n chaos-mesh --ignore-not-found
kubectl delete job falco-trigger -n chaos-mesh --ignore-not-found

# Reset the agent's cooldowns so the next demo isn't blocked
kubectl -n agent exec deploy/redis -- redis-cli FLUSHDB
```

## Numbers to call out

Have these memorized:
- **Mean time to detect:** _your number_ (typically <90s with ML; >5min with thresholds)
- **Mean time to remediate (Scenario A):** _your number_ (typically 2-3 min agent vs 15-20 min human)
- **Cost per incident:** _your number_ (typically $0.05-0.15 in LLM tokens)
- **False positive rate:** _your number_ (whatever your isoforest tuning gives)
