# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Athena is an autonomous AIOps platform: a production-grade Kubernetes system where a multi-agent AI (LangGraph + MCP) autonomously detects, diagnoses, and remediates infrastructure incidents on EKS. The demo workload is Google's Online Boutique microservices app.

## Development Commands

### Agent (local dev)
```bash
cd agent
docker compose up -d          # Start NATS, Redis, OPA, and the agent
docker compose logs -f agent  # Follow agent logs
```

### Run tests
```bash
cd agent
pytest tests/                              # All tests
pytest tests/test_full_incident.py         # End-to-end incident flow
pytest tests/test_opa_gating.py            # OPA policy validation
```

### Infrastructure
```bash
cd infra/terraform/environments/dev
terraform init && terraform apply          # Provision EKS + VPC
```

### ML models
```bash
cd ml/training/metric-isoforest
python train.py                            # Train and register to MLflow
python serve.py                            # Run KServe inference server locally
```

### CI (GitHub Actions)
- `.github/workflows/build.yml` — matrix build (agent, ml-isoforest, ml-drain3, mcp-servers): Trivy scan → SBOM → Cosign keyless sign → ECR push
- `.github/workflows/terraform.yml` — plan/apply with OIDC auth

## Architecture

### Agent Graph (LangGraph state machine)

Incidents flow through 7 nodes in `agent/graph/nodes/`:

```
detector → diagnostician → remediator → [hitl?] → executor → verifier → reporter
                               ↑__________________|  (max 3 retries)
```

1. **detector** — fast triage, ~400 tokens; decides if anomaly warrants action
2. **diagnostician** — root cause + confidence using Prometheus/Loki/K8s tool calls
3. **remediator** — proposes action plan with risk score; high-risk routes to HITL
4. **hitl** (`slack_bot.py`) — awaits human Slack approval before proceeding
5. **executor** — runs the plan via MCP tools, gated by OPA policy
6. **verifier** — checks if remediation succeeded; loops back up to 3× on failure
7. **reporter** — writes postmortem markdown, escalates if unresolved

Shared state across all nodes is defined in `agent/graph/state.py` (TypedDict + reducers using `operator.add` for audit trail accumulation).

### Tool Gating (OPA → MCP)

Every write tool call goes through `agent/graph/mcp_client.py`:
1. OPA policy (`agent/policies/tools.rego`) checks permission (restart cooldown 5 min, scale ≤2x current or ≤20 absolute, rollback rules)
2. If allowed, MCP server executes the actual Kubernetes/Prometheus/Loki call

MCP servers live in `agent/mcp-servers/`: `k8s-mcp/`, `prom-mcp/`, `loki-mcp/`.

### Event-Driven Entry Point

`agent/main.py` subscribes to NATS topics (`anomalies.metric`, `anomalies.log`). Each event spawns a LangGraph invocation. Concurrency is capped by an asyncio semaphore (default 5, controlled by `MAX_CONCURRENT` env var).

### Anomaly Detection (ML tier)

Two models feed NATS before the agent ever sees an event:
- **Isolation Forest** (`ml/training/metric-isoforest/`): Prometheus metric anomaly scores, served via KServe
- **Drain3** (`ml/training/log-drain3/`): Log template clustering for log-based anomalies

Scores are polled by `ml/serving/poller.py` and published to NATS.

### GitOps (Argo CD)

`platform/bootstrap/root-app.yaml` is the Argo CD "app-of-apps" root. It reconciles:
- Observability stack (Prometheus + Thanos, Loki, Tempo, Grafana, SLOs)
- Security (External Secrets, Kyverno policies, Falco runtime detection)
- MLOps (MLflow, KServe, VPA, NATS, OpenCost)

Agent prompts (`agent/prompts/`) and OPA policies (`agent/policies/`) are version-controlled and GitOps-deployed — edit them via git, not in-cluster.

### Infrastructure (Terraform)

`infra/terraform/modules/`: `vpc/`, `eks-cluster/`, `irsa-role/` (maps K8s ServiceAccounts to IAM roles). `infra/terraform/bootstrap/github-oidc.tf` sets up OIDC for keyless Cosign signing.

## Key Configuration

Agent is configured entirely via environment variables:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required |
| `AGENT_MODEL` | Defaults to `claude-opus-4-7` |
| `NATS_URL`, `REDIS_URL`, `OPA_URL` | Service endpoints |
| `PROM_MCP`, `LOKI_MCP`, `K8S_MCP` | MCP server endpoints |
| `MAX_CONCURRENT` | Semaphore cap (default 5) |

## Prometheus Metrics Emitted by Agent

`agent/graph/llm.py` and graph nodes emit:
- `agent_incidents_total{kind, resolved}`
- `agent_llm_calls_total{node, model, result}`
- `agent_llm_tokens_total{node, model, kind}`
- `agent_tool_calls_total{tool, result}`

## Key Architectural Decisions (see `docs/adr/`)

- **ADR-001:** 7-node graph over single ReAct loop — per-node cost control (detector uses ~400 tokens, diagnostician uses full budget), testability, safety
- **ADR-002:** OPA between LLM and kubectl — policy as code, defense in depth
- **ADR-003:** Isolation Forest + Drain3 for detection (not the LLM) — deterministic thresholds, low latency
- **ADR-004:** Agent prompts + OPA policies in Git (GitOps) — auditable changes, rollback capability

## Chaos / Game Days

`chaos/experiments/` contains Chaos Mesh manifests: `pod-kill`, `memory-leak`, `network-delay`, `falco-trigger`. Use these to trigger incidents for the agent to resolve. See `docs/runbooks/demos.md` for scenario scripts.
