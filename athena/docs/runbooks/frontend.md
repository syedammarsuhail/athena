# Runbook — frontend service

Linked from the SLO alert annotation. This is what an on-call human (or the agent) reads when `FrontendAvailabilitySLO` fires.

## What this alert means

The frontend SLO burn rate has exceeded our budget. Either:
- a fast burn (small window, huge spike) → user impact now
- a slow burn (long window, sustained degradation) → user impact accumulating

## First 5 minutes

1. **Confirm user impact:**
   - Grafana: `Golden signals → frontend` — error rate >1%?
   - Is the homepage loading? `curl -I https://shop.example.com`

2. **Recent change?**
   ```bash
   argocd app history online-boutique-dev | head
   ```
   Anything synced in the last 30 minutes?

3. **Check dependencies:**
   - cartservice, productcatalogservice, currencyservice — any of them red?

## Common causes

| Symptom | Likely cause | First fix |
|---------|--------------|-----------|
| 5xx spike right after deploy | Bad release | `argocd app rollback online-boutique-dev` |
| Latency p99 climbing slowly | Memory leak or noisy neighbor | `kubectl rollout restart deploy/frontend -n online-boutique` |
| All requests timing out | Upstream service down | Check cartservice / productcatalog logs |
| Connection refused | Pod not ready / liveness failing | `kubectl describe pod -l app=frontend -n online-boutique` |

## Escalation

If still bleeding after 15 minutes:
- Page the platform team (Slack `@platform-oncall`)
- Open incident channel `#inc-<timestamp>`
- Drop the agent's postmortem URL when it lands

## Agent context

If the agent is handling this, look at:
```bash
kubectl -n agent logs -l app=athena-agent --tail=200 | grep <incident_id>
```

You can override its decisions:
- Reject HITL prompts in Slack to stop high-risk actions
- Disable the agent entirely: `kubectl -n agent scale deploy athena-agent --replicas=0`
