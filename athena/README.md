# Athena — Autonomous AIOps Platform

> Production-grade Kubernetes platform with a multi-agent AI system (LangGraph + MCP) that autonomously detects, diagnoses, and remediates incidents.

![architecture](docs/images/architecture.png)

---

## What's in this repo

```
athena/
├── docs/                 ← architecture, ADRs, runbooks, demo scripts
├── infra/terraform/      ← AWS VPC + EKS + IRSA + OIDC for GitHub
├── platform/             ← Argo CD app-of-apps: o11y, security, mesh, mlops
├── workloads/            ← demo microservices (Online Boutique) via Kustomize
├── ml/                   ← anomaly detection models (training + KServe serving)
├── agent/                ← LangGraph multi-agent + MCP servers + OPA policies
├── chaos/                ← Chaos Mesh experiments
└── .github/workflows/    ← OIDC-auth CI: build → SBOM → sign → scan → push
```

---

## Prerequisites

Install locally:

```
- aws-cli v2          # with a role that can create EKS clusters
- terraform >= 1.7
- kubectl >= 1.29
- helm >= 3.14
- argocd-cli
- cosign
- docker
- python 3.11+
- node 20+ (for some MCP servers)
- ollama (optional, for local LLM) OR an Anthropic API key
```

Estimated cloud cost while running: ~$150–250/month on AWS (EKS control plane + small node group + RDS). Spin down with `terraform destroy` between sessions.

---

## Week-by-week build plan

Each week is a self-contained milestone. Finish week N before starting N+1.

| Week | Theme | Deliverable | Doc |
|------|-------|-------------|-----|
| **1** | Foundation — Terraform, EKS, Argo CD, GitOps | `terraform apply` + `git push` deploys Online Boutique on HTTPS | [docs/week-01.md](docs/week-01.md) |
| **2** | Observability — Prom/Thanos, Loki, Tempo, Grafana, SLOs | Golden-signals dashboard + burn-rate alert firing on demand | [docs/week-02.md](docs/week-02.md) |
| **3** | DevSecOps — Cosign, SBOM, Trivy, Kyverno, Falco | Unsigned image PR is blocked; runtime exec triggers Slack alert | [docs/week-03.md](docs/week-03.md) |
| **4–5** | MLOps — anomaly-detection models on KServe | Memory leak detected by ML before threshold alert fires | [docs/week-04-05.md](docs/week-04-05.md) |
| **6–8** | **AI Agent** — LangGraph + MCP + OPA + Slack HITL | Chaos pod-kill → agent diagnoses → restarts → posts postmortem | [docs/week-06-08.md](docs/week-06-08.md) |
| **9** | Chaos + game days | Scheduled experiments running, agent in the loop | [docs/week-09.md](docs/week-09.md) |
| **10** | FinOps + multi-cluster + polish | OpenCost dashboards, second GKE cluster federated | [docs/week-10.md](docs/week-10.md) |

---

## Quick-start (after Week 1 is built)

```bash
# 1. Provision infra
cd infra/terraform/environments/dev
terraform init && terraform apply

# 2. Bootstrap Argo CD (auto-bootstrapped by Terraform helm release;
#    but if doing manually:)
kubectl apply -f platform/bootstrap/argocd-bootstrap.yaml

# 3. Point Argo at this repo (root-app pattern)
kubectl apply -f platform/bootstrap/root-app.yaml

# 4. Wait for Argo to reconcile everything
argocd app wait root-app --health

# 5. Get the boutique URL
kubectl -n online-boutique get ingress

# 6. (Week 6+) Start the agent
cd agent && docker compose up -d
```

---

## Demo scenarios (rehearse for interviews)

1. **Memory leak — auto-remediated** (Scenario A) — `chaos/experiments/memory-leak.yaml`
2. **DB connection storm — HITL approval** (Scenario B) — `chaos/experiments/network-delay.yaml`
3. **Suspicious runtime — security containment** (Scenario C) — `chaos/experiments/falco-trigger.yaml`

Full scripts in [docs/runbooks/demos.md](docs/runbooks/demos.md).

---

## Architecture decisions worth reading

- [ADR-001: Why a multi-agent topology instead of a single LLM call](docs/adr/001-multi-agent.md)
- [ADR-002: OPA between the LLM and kubectl](docs/adr/002-opa-tool-gating.md)
- [ADR-003: Isolation Forest + Drain3 instead of "just use the LLM"](docs/adr/003-ml-detection.md)
- [ADR-004: GitOps for agent prompts and policies](docs/adr/004-agent-as-code.md)

---

## What's NOT in this repo (intentional)

- Cluster autoscaler config (we use Karpenter, defined in Terraform)
- Manual `kubectl apply` — everything goes through Argo CD
- Hardcoded secrets — see External-Secrets + AWS Secrets Manager setup
- "Demo" shortcuts that wouldn't work in production

This is built like a real platform, not a tutorial.
