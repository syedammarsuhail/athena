# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Athena is an autonomous AIOps platform: a production-grade Kubernetes system where a multi-agent AI (LangGraph + MCP) autonomously detects, diagnoses, and remediates infrastructure incidents on EKS. The demo workload is Google's Online Boutique microservices app.

## Development Commands

### Agent (local dev)
```bash
cd agent
docker compose up -d          # Start NATS, Redis, OPA, slack-bot, and the agent
docker compose logs -f agent  # Follow agent logs
```

### Run tests
```bash
cd agent
pytest tests/                              # All tests
pytest tests/test_full_incident.py         # End-to-end incident flow
pytest tests/test_opa_gating.py            # OPA policy validation
pytest tests/ -k "test_memory_leak"        # Single test by name
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
- `.github/workflows/build.yml` — matrix build (agent, ml-isoforest, ml-drain3, prom-mcp, loki-mcp, k8s-mcp): Trivy scan → Docker buildx → ECR push → Kustomize image bump; only builds components whose paths changed
- `.github/workflows/terraform.yml` — plan/apply with OIDC auth (no long-lived AWS keys)
- `.github/workflows/destroy.yml` — full teardown

## Architecture

### Agent Graph (LangGraph state machine)

Incidents flow through 7 nodes in `agent/graph/nodes/`:

```
detector → diagnostician → remediator → [hitl?] → executor → verifier → reporter
                               ↑__________________|  (max 3 retries)
```

1. **detector** — fast triage, ~400 tokens; decides `is_real` + `severity`; if not real, graph ends immediately
2. **diagnostician** — root cause + confidence using read-only Prometheus/Loki/K8s tool calls (max 8 tool calls per run)
3. **remediator** — proposes action plan with risk score; confidence < 0.3 → auto-escalates to reporter without executing
4. **hitl** (`slack_bot.py`) — posts Slack interactive message; polls Redis `approval:{incident_id}` key; 600s timeout → auto-reject
5. **executor** — runs the plan via MCP tools, gated by OPA policy; maps action names to specific tools
6. **verifier** — waits 30s settle time, re-queries original signal; loops back to diagnostician up to `MAX_VERIFY_LOOPS=3` times on failure
7. **reporter** — writes postmortem markdown, posts Slack summary, opens GitHub issue; handles both resolved and max-retries-exceeded cases

Shared state across all nodes is `IncidentState` (TypedDict) in `agent/graph/state.py`. Audit-trail fields (`tool_calls`, `decision_trail`, `llm_traces`) use `Annotated[list, operator.add]` reducers — they accumulate across every node invocation and are never overwritten.

### LLM Integration

`agent/graph/llm.py` holds a single global Anthropic SDK client instance. Nodes call it directly (not through LangChain). Each call records `{node, input_tokens, output_tokens}` into `llm_traces` for cost tracking. Token budgets are enforced by the node prompts themselves (`agent/prompts/*.md`): detector is instructed to stop after triage, diagnostician has a full budget with an 8-call tool loop cap.

### Tool Gating (OPA → MCP)

Every **write** tool call goes through `agent/graph/mcp_client.py`:
1. POST to `OPA_URL/v1/data/athena/tools/decision` with `{input: {tool, args}}`
2. If denied, return `ToolCall(success=False, policy_decision="deny: reason")` — no execution
3. If allowed, HTTP POST to the MCP server; result is truncated to 1500 chars before entering the audit trail

Read tools (all Prometheus, Loki, K8s reads) bypass OPA entirely.

**Write tools:** `k8s.restart_deployment`, `k8s.scale_deployment`, `k8s.rollback_argocd_app`, `k8s.cordon_node`

**OPA rules** (`agent/policies/tools.rego` — deny-by-default):
- `restart_deployment`: allowed namespaces + not in Redis cooldown
- `scale_deployment`: allowed namespaces + `replicas ≤ 20` absolute AND `≤ 2× current_replicas` AND `≥ 1`
- `rollback_argocd_app`: only whitelisted apps (`online-boutique-dev`, `agent-dev`)
- `cordon_node`: denied if node name starts with `ip-control` (protect control plane)

**Defense-in-depth:** OPA enforces policy logic; each MCP server *also* enforces a Redis cooldown (`COOLDOWN_S=300s`) independently. Both layers must pass.

**MCP servers** (`agent/mcp-servers/k8s-mcp/`, `prom-mcp/`, `loki-mcp/`) are plain FastAPI HTTP servers — not MCP-over-stdio. They run as Kubernetes Deployments in-cluster, not in docker-compose.

### HITL (Slack + Redis)

`agent/slack_bot.py` is a separate FastAPI service in docker-compose:
- `/post-approval` — agent calls this to post an interactive Slack message with Approve/Reject buttons
- `/slack/actions` — Slack webhook; writes decision to Redis key `approval:{incident_id}` (values: `"approved"`, `"rejected"`)
- HITL node polls Redis every 5s up to 600s; timeout → `"rejected"`, which routes to reporter (escalation)

### Event-Driven Entry Point

`agent/main.py` subscribes to NATS topics `anomalies.metric` and `anomalies.log` (configurable via `NATS_SUBJECTS`). Each message constructs an `IncidentState` and invokes the graph with a unique `thread_id` for LangGraph checkpointing. An `asyncio.Semaphore(MAX_CONCURRENT)` (default 5) caps concurrency.

**`AnomalyEvent` schema** (`agent/graph/state.py`):
```python
kind: "metric_anomaly" | "log_anomaly" | "alert"
service, namespace, metric, template, score, severity, ts, raw
```

### Anomaly Detection (ML tier)

Two models feed NATS before the agent sees an event:
- **Isolation Forest** (`ml/training/metric-isoforest/`): Trains per (namespace, deployment, metric) on 30 days of Prometheus data with 17 features (value, rolling stats, derivatives, cyclical time). Served via KServe (`/v1/predict`).
- **Drain3** (`ml/training/log-drain3/`): Streams Loki → Drain3 template extraction → SentenceTransformers embedding → HDBSCAN clustering. A template is "anomalous" if cluster size = 1 or unseen in past 24h. Drain3 state is persisted to S3 every 60s.

`ml/serving/poller.py` polls Prometheus every 30s, calls the IsoForest service, and publishes anomalies to NATS `anomalies.metric`.

### GitOps (Argo CD)

`platform/bootstrap/root-app.yaml` is the Argo CD app-of-apps root. Reconciles:
- Observability stack (Prometheus + Thanos, Loki, Tempo, Grafana, SLOs)
- Security (External Secrets, Kyverno policies, Falco runtime detection)
- MLOps (MLflow, KServe, VPA, NATS, OpenCost)

Agent prompts (`agent/prompts/`) and OPA policies (`agent/policies/`) are GitOps-deployed — edit them via git, not in-cluster.

### Infrastructure (Terraform)

`infra/terraform/modules/`: `vpc/`, `eks-cluster/`, `irsa-role/` (maps K8s ServiceAccounts to IAM roles via IRSA). `infra/terraform/bootstrap/github-oidc.tf` configures OIDC for keyless Cosign signing and CI.

## Key Configuration

Agent is configured entirely via environment variables:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required |
| `AGENT_MODEL` | Defaults to `claude-opus-4-7` |
| `NATS_URL`, `REDIS_URL`, `OPA_URL` | Service endpoints |
| `PROM_MCP`, `LOKI_MCP`, `K8S_MCP` | MCP server endpoints |
| `MAX_CONCURRENT` | Semaphore cap (default 5) |
| `MAX_VERIFY_LOOPS` | Verifier retry cap (default 3) |
| `COOLDOWN_S` | MCP server restart/scale cooldown in seconds (default 300) |

## Prometheus Metrics Emitted by Agent

| Metric | Labels | Source |
|---|---|---|
| `agent_incidents_total` | `kind, resolved` | `main.py` |
| `agent_incidents_inflight` | — | `main.py` (Gauge) |
| `agent_incident_seconds_total` | `kind` | `main.py` |
| `agent_llm_calls_total` | `node, model, result` | `llm.py` |
| `agent_llm_tokens_total` | `node, model, kind` | `llm.py` |
| `agent_llm_seconds` | `node, model` | `llm.py` (Histogram) |
| `agent_tool_calls_total` | `tool, result` | `mcp_client.py` |

`result` on tool calls is one of: `ok`, `error`, `policy_denied`.

## Testing Strategy

Tests in `agent/tests/` use a **fixture-based replay pattern**: `FakeLLM` returns canned structured responses, and `FakeMCPClient` returns canned tool results. This makes tests deterministic and fast but doesn't cover LLM output variance.

- `test_full_incident.py` — three scenarios: full happy-path memory leak, noise short-circuit (detector returns `is_real=False`), OPA denial recorded in audit trail
- `test_opa_gating.py` — invariant check: every action in `executor.ACTION_TO_TOOL` must be in `WRITE_TOOLS` (no tool escapes OPA)

To add a new test scenario: add a fixture dict to `conftest.py` with the canned LLM JSON and tool outputs, then assert on `final_state` fields.

## Key Architectural Decisions (see `docs/adr/`)

- **ADR-001:** 7-node graph over single ReAct loop — per-node cost control, testability, safety
- **ADR-002:** OPA between LLM and kubectl — policy as code, defense in depth
- **ADR-003:** Isolation Forest + Drain3 for detection (not the LLM) — deterministic thresholds, low latency
- **ADR-004:** Agent prompts + OPA policies in Git (GitOps) — auditable changes, rollback capability

## Chaos / Game Days

`chaos/experiments/` contains Chaos Mesh manifests: `pod-kill`, `memory-leak`, `network-delay`, `falco-trigger`. Use these to trigger incidents for the agent to resolve. See `docs/runbooks/demos.md` for scenario scripts.
