# Week 10 — FinOps + Multi-cluster + Polish

**Goal:** Cost visibility, a second cluster (different cloud) federated in, and the polish that takes this from "impressive personal project" to "interview talking-point I can speak to for an hour."

## What you'll build

```
Cluster 1 (AWS EKS, dev)            Cluster 2 (GCP GKE, dev)
  - everything from weeks 1-9         - mirror of the platform
  - OpenCost dashboards               - shares Argo CD / Grafana
                          \          /
                           Argo CD (cluster-of-clusters)
                           Grafana (mixed data sources)
                           Agent (multi-cluster MCP)
```

## Day-by-day

### Day 1: OpenCost

```bash
git add platform/mlops/opencost-app.yaml
git commit -m "feat(finops): opencost" && git push
```

OpenCost integrates into the existing Prometheus + Grafana. Dashboards land at `Cost Allocation` in Grafana. Pin a panel: **dollars per incident** = `agent_incident_seconds_total * (eks_node_cost / 3600)`.

### Day 2: Resource right-sizing

Vertical Pod Autoscaler in recommendation mode (don't actually let it mutate yet):

```bash
git add platform/mlops/vpa-app.yaml
git commit -m "feat(finops): VPA recommendations" && git push
```

After 48h, generate a PR that bumps every workload's requests/limits to the recommendation. Document the cost impact.

### Day 3–4: Second cluster

Provision a GKE cluster via Terraform (`infra/terraform/environments/dev-gke`). Register it with Argo CD as an additional destination cluster:

```bash
argocd cluster add gke-context --name dev-gke
```

Re-target the platform Apps to deploy across both clusters using ApplicationSet:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata: { name: platform-multi-cluster, namespace: argocd }
spec:
  generators:
    - clusters: {}            # all registered clusters
  template:
    metadata: { name: 'platform-{{name}}' }
    spec:
      project: default
      source: { repoURL: '...', path: platform, targetRevision: main }
      destination: { server: '{{server}}' }
      syncPolicy: { automated: { prune: true, selfHeal: true } }
```

### Day 5: Federate Prometheus + Grafana

Make Grafana the single pane of glass. Two strategies:
- **Easy:** add the second cluster's Prom as a Grafana data source. Dashboards get a `cluster` variable.
- **Robust:** install Thanos in front of both Prometheuses (sidecar + querier). Skip this on first pass; it's a separate project.

### Day 6: Agent across clusters

The agent already routes by `namespace`. Update the k8s-mcp to load multiple kubeconfigs (one per cluster). Each tool call carries an extra `cluster` arg. Update the OPA policy to gate per-cluster.

### Day 7: Polish

- README with architecture diagram (use the ASCII one, or draw with Excalidraw — `docs/images/architecture.png`)
- Record a 2-minute demo video
- Write a blog post: "How I built an AI-powered SRE assistant" — linkable in your résumé
- Write a `THINGS-I-CUT.md` of features deliberately skipped (interviewers love this)

## Verification checklist

- [ ] OpenCost dashboard shows `$/namespace/day`
- [ ] Both clusters healthy in Argo CD UI
- [ ] One Grafana dashboard shows metrics from both clusters with a `cluster` selector
- [ ] Agent successfully remediates an incident in the GCP cluster (chaos triggered there)
- [ ] Architecture diagram in README

## What "done" looks like

You can give a 5-minute end-to-end demo over Zoom: explain the problem, show the running system, trigger one chaos scenario, narrate what's happening on screen, and answer "why did you pick X over Y?" for any component in the stack. That's the interview-ready state.

## What to talk about in interviews

- **The trust boundary**: "I chose to put OPA between the LLM and kubectl. The LLM can be wrong; the policy enforces what's safe regardless."
- **Cost discipline**: "Average cost per incident was $0.06. Cheaper than the on-call engineer's bandwidth."
- **Failure modes I considered**: "What if NATS is down? Agent stops consuming, alerts keep firing through the normal path. The agent is additive, not a critical path."
- **What I'd build next**: "Drift detection — periodic model-evaluation against labeled past incidents to catch agent quality regression."
