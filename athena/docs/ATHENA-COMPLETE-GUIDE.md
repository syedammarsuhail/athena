# Athena AIOps Platform — Complete Guide

---

## Table of Contents

1. [What is Athena?](#1-what-is-athena)
2. [Architecture](#2-architecture)
3. [How the 7-Node Graph Works](#3-how-the-7-node-graph-works)
4. [Components Explained](#4-components-explained)
5. [Prerequisites](#5-prerequisites)
6. [Part A — Local Development (Docker Compose)](#part-a--local-development-docker-compose)
7. [Part B — EKS Cloud Deployment](#part-b--eks-cloud-deployment)
8. [Part C — Running the Incident Flow](#part-c--running-the-incident-flow)
9. [Understanding the Logs](#9-understanding-the-logs)
10. [Troubleshooting](#10-troubleshooting)
11. [Cleanup](#11-cleanup)

---

## 1. What is Athena?

Athena is an **autonomous AIOps platform** — a system that automatically detects,
diagnoses, and fixes Kubernetes infrastructure incidents without human intervention
(except for optional human approval on high-risk actions).

**The problem it solves:**
- Traditional monitoring sends alerts → humans wake up at 3am → humans fix it
- Athena detects the anomaly → diagnoses root cause → proposes fix → (optionally asks human) → fixes it → confirms it worked → posts postmortem

**The demo workload:** Google's Online Boutique — a 12-microservice e-commerce app
running on AWS EKS.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     AWS EKS Cluster                      │
│                                                          │
│  ┌──────────────┐    ┌─────────────────────────────┐    │
│  │ online-boutique│   │      agent namespace        │    │
│  │  (12 pods)   │    │                             │    │
│  │              │    │  ┌──────────┐  ┌─────────┐  │    │
│  │ cartservice  │◄───┼──│ k8s-mcp  │  │  OPA    │  │    │
│  │ frontend     │    │  │ prom-mcp │  │ (policy)│  │    │
│  │ checkoutsvс  │    │  │ loki-mcp │  └─────────┘  │    │
│  │ ...          │    │  └──────────┘               │    │
│  └──────────────┘    │       │                     │    │
│                      │  ┌────▼─────┐  ┌─────────┐  │    │
│  ┌──────────────┐    │  │  athena  │  │  Redis  │  │    │
│  │   mlops ns   │    │  │  -agent  │  │  (HITL) │  │    │
│  │              │    │  │(LangGraph│  └─────────┘  │    │
│  │    NATS      │───►│  │  7-node) │               │    │
│  │  (messages)  │    │  └──────────┘  ┌─────────┐  │    │
│  │  Prometheus  │    │               │slack-bot │  │    │
│  └──────────────┘    │               └─────────┘  │    │
│                      └─────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
         │                              │
         │ anomaly score                │ postmortem
         ▼                              ▼
    Isolation Forest              Slack #incidents
    (ML model)
```

**Data flow:**
1. ML model (Isolation Forest) detects anomaly in Prometheus metrics
2. Publishes event to NATS message queue
3. Athena agent wakes up, runs 7-node graph
4. Agent queries Prometheus/Loki/K8s via MCP servers
5. If risky action needed, asks human via Slack
6. Executes fix via k8s-mcp (gated by OPA)
7. Verifies fix worked, posts postmortem to Slack

---

## 3. How the 7-Node Graph Works

```
NATS Event arrives
      │
      ▼
┌──────────┐
│ detector │ → Is this anomaly real? (severity: low/medium/high)
└────┬─────┘   If not real → skip, graph ends
     │
     ▼
┌──────────────┐
│ diagnostician│ → Queries Prometheus, Loki, K8s to find root cause
└──────┬───────┘   Uses real data: CPU, memory, error rates, pod events
       │
       ▼
┌───────────┐
│ remediator│ → Proposes fix (e.g. restart deployment)
└─────┬─────┘   Assigns risk score (low/medium/high)
      │         High risk → goes to HITL
      ▼
┌──────┐
│ hitl │ → Posts Slack message with Approve/Reject buttons
└──┬───┘   Agent polls Redis every 5 seconds
   │       Timeout: 10 minutes → auto-reject → escalate
   │ (approved)
   ▼
┌──────────┐
│ executor │ → Checks OPA policy first
└─────┬────┘   If allowed → calls k8s-mcp to run kubectl
      │        If denied → records denial, goes to reporter
      ▼
┌──────────┐
│ verifier │ → Waits 30 seconds (settle time)
└─────┬────┘   Re-queries Prometheus to check if metrics recovered
      │        If not recovered → loops back to diagnostician (max 3 times)
      ▼
┌──────────┐
│ reporter │ → Writes postmortem markdown
└──────────┘   Posts summary to Slack #incidents
               Opens GitHub issue (if configured)
               Records: resolved=True or resolved=None (escalated)
```

---

## 4. Components Explained

| Component | Where Runs | What It Does |
|---|---|---|
| **NATS** | mlops namespace | Message queue — anomaly events flow through here. Like a post office between services. |
| **Redis** | agent namespace | Stores HITL decisions. When you click Approve, slack-bot writes "approved" here. Agent reads it. |
| **OPA** | agent namespace | Open Policy Agent — enforces rules like "only restart allowed namespaces" and "don't scale above 20 replicas". |
| **prom-mcp** | agent namespace | Proxy between agent and Prometheus. Agent asks it for metrics. |
| **loki-mcp** | agent namespace | Proxy between agent and Loki (log aggregation). |
| **k8s-mcp** | agent namespace | Executes actual kubectl commands (restart, scale, rollback) on behalf of the agent. |
| **athena-agent** | agent namespace | The AI brain. Runs LangGraph 7-node state machine using Groq (Llama 3.3 70B). |
| **slack-bot** | agent namespace | Handles Slack interactive button callbacks. Writes decisions to Redis. |
| **Groq** | Cloud (free) | Provides the LLM (Llama 3.3 70B). Free tier: 14,400 requests/day. |
| **ArgoCD** | cluster | GitOps — keeps all K8s deployments in sync with the GitHub repo. |
| **Isolation Forest** | ml namespace | ML model that detects anomalies in Prometheus metrics. |

---

## 5. Prerequisites

Install these tools:

```powershell
# Verify each is installed
aws --version          # AWS CLI
kubectl version        # Kubernetes CLI
terraform --version    # Infrastructure as Code
docker --version       # Container runtime
```

**AWS credentials:**
```powershell
aws configure
# AWS Access Key ID: YOUR_KEY
# AWS Secret Access Key: YOUR_SECRET
# Default region: us-east-1
# Default output format: json
```

**Free accounts needed:**
- Groq API key (free): https://console.groq.com
- Slack workspace with a bot app configured

---

## Part A — Local Development (Docker Compose)

Good for development and testing without any AWS costs.

### A.1 — Set up environment file

```
File: agent/.env
```

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
SLACK_SIGNING_SECRET=your_signing_secret
SLACK_APPROVAL_CHANNEL=#incidents
REDIS_URL=redis://redis:6379/0
NATS_URL=nats://nats:4222
OPA_URL=http://opa:8181
PROM_MCP=http://prom-mcp:8080
LOKI_MCP=http://loki-mcp:8080
K8S_MCP=http://k8s-mcp:8080
GROQ_API_KEY=your_groq_api_key
AGENT_MODEL=llama-3.3-70b-versatile
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=athena-agent
GRAFANA_URL=http://localhost:3000
```

### A.2 — Start all services

```powershell
cd C:\Users\FAUZIA SHAHRYAR\Desktop\athena\athena\agent
docker compose up -d
```

Verify running:
```powershell
docker compose ps
```

### A.3 — Set up ngrok for Slack callbacks

Slack needs a public URL to send button clicks to. ngrok creates a tunnel:

```powershell
ngrok http 8000
```

Copy the HTTPS URL (e.g. `https://abc123.ngrok.io`)

Go to: https://api.slack.com/apps → your app → **Interactivity & Shortcuts**
Set Request URL: `https://abc123.ngrok.io/slack/actions`

### A.4 — Fire a test incident

Save this script once:
```powershell
@'
import nats, asyncio, json, time
async def p():
    nc = await nats.connect("nats://nats:4222")
    data = {"service":"cartservice","namespace":"online-boutique","metric":"memory","score":0.97,"severity":"high","ts":time.time()}
    await nc.publish("anomalies.metric", json.dumps(data).encode())
    await nc.drain()
    print("published!")
asyncio.run(p())
'@ | Out-File -FilePath "$env:TEMP\fire-local.py" -Encoding utf8
```

Run it:
```powershell
python "$env:TEMP\fire-local.py"
```

### A.5 — Watch logs

```powershell
docker compose logs -f agent
```

### A.6 — Approve in Slack

Go to `#incidents` → scroll to the **bottom** → click **Approve** on the newest message.

---

## Part B — EKS Cloud Deployment

Deploys Athena to real AWS infrastructure.

### B.1 — Provision EKS with Terraform

```powershell
cd C:\Users\FAUZIA SHAHRYAR\Desktop\athena\infra\terraform\environments\dev
terraform init
terraform apply -auto-approve
```

Takes ~15 minutes. Creates: VPC, EKS cluster (2x t3.medium nodes), IRSA roles, EBS CSI driver.

Update kubeconfig:
```powershell
aws eks update-kubeconfig --name athena-dev --region us-east-1
```

Verify:
```powershell
kubectl get nodes
# Both nodes should show Ready
```

### B.2 — Log in to ECR (AWS container registry)

```powershell
$TOKEN = aws ecr get-login-password --region us-east-1
$AUTH = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("AWS:$TOKEN"))
$CONFIG = '{"auths":{"339713148266.dkr.ecr.us-east-1.amazonaws.com":{"auth":"' + $AUTH + '"}},"credsStore":"","currentContext":"desktop-linux","plugins":{"-x-cli-hints":{"enabled":"true"}},"features":{"hooks":"true"}}'
[System.IO.File]::WriteAllText(
    "$env:USERPROFILE\.docker\config.json",
    $CONFIG,
    [System.Text.UTF8Encoding]::new($false)
)
```

### B.3 — Create Kubernetes secrets

```powershell
# Slack credentials (webhook + signing secret)
kubectl create secret generic slack -n agent `
  --from-literal=webhook="https://hooks.slack.com/services/YOUR/WEBHOOK/URL" `
  --from-literal=signing_secret="your_signing_secret"

# Groq API key
kubectl create secret generic groq-secret -n agent `
  --from-literal=api-key="your_groq_api_key"

# ArgoCD token
kubectl create secret generic argocd-api-token -n agent `
  --from-literal=token="your_argocd_token"
```

### B.4 — Patch agent with Groq key and Slack webhook

```powershell
# Add GROQ_API_KEY
$patch = '[{"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"GROQ_API_KEY","valueFrom":{"secretKeyRef":{"name":"groq-secret","key":"api-key"}}}}]'
$patch | Out-File -FilePath "$env:TEMP\patch.json" -Encoding utf8 -NoNewline
kubectl patch deployment athena-agent -n agent --type=json --patch-file "$env:TEMP\patch.json"

# Add SLACK_WEBHOOK_URL
$patch2 = '[{"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"SLACK_WEBHOOK_URL","valueFrom":{"secretKeyRef":{"name":"slack","key":"webhook"}}}}]'
$patch2 | Out-File -FilePath "$env:TEMP\patch2.json" -Encoding utf8 -NoNewline
kubectl patch deployment athena-agent -n agent --type=json --patch-file "$env:TEMP\patch2.json"
```

### B.5 — Create LoadBalancer for Slack callbacks

ArgoCD resets the slack-bot service to ClusterIP. Create a separate LoadBalancer:

```powershell
@'
apiVersion: v1
kind: Service
metadata:
  name: slack-bot-lb
  namespace: agent
spec:
  type: LoadBalancer
  selector:
    app: slack-bot
  ports:
    - port: 80
      targetPort: 8000
'@ | Out-File -FilePath "$env:TEMP\slack-bot-lb.yaml" -Encoding utf8
kubectl apply -f "$env:TEMP\slack-bot-lb.yaml"
```

Wait for external IP (takes 2-3 minutes):
```powershell
kubectl get svc -n agent slack-bot-lb -w
```

Once you see an EXTERNAL-IP, go to:
https://api.slack.com/apps → your app → **Interactivity & Shortcuts**
Set Request URL: `http://EXTERNAL-IP/slack/actions`

### B.6 — Verify everything is running

```powershell
kubectl get pods -n agent
kubectl get pods -n online-boutique
```

**Expected in agent namespace:**
```
athena-agent    Running
k8s-mcp         Running  (x2)
loki-mcp        Running  (x2)
opa             Running  (x2)
prom-mcp        Running  (x2)
redis           Running
slack-bot       Running  (x2)
```

**Expected in online-boutique namespace:**
```
adservice, cartservice, checkoutservice, currencyservice,
emailservice, frontend, loadgenerator, paymentservice,
productcatalogservice, recommendationservice, redis-cart,
shippingservice  — all Running
```

---

## Part C — Running the Incident Flow

### C.1 — Save the event script (one time only)

The file already exists at: `C:\Users\FAUZIA SHAHRYAR\AppData\Local\Temp\fire.py`

If you need to recreate it, create a file with these contents:
```python
import nats, asyncio, json, time
async def p():
    nc = await nats.connect("nats://nats.mlops:4222")
    data = {"service":"cartservice","namespace":"online-boutique","metric":"memory","score":0.97,"severity":"high","ts":time.time()}
    await nc.publish("anomalies.metric", json.dumps(data).encode())
    await nc.drain()
    print("published!")
asyncio.run(p())
```

### C.2 — Fire the incident (run separately, line by line)

**Line 1:**
```powershell
$pod = (kubectl get pods -n agent --no-headers | Select-String "athena-agent" | ForEach-Object { ($_ -split "\s+")[0] } | Select-Object -First 1)
```

**Line 2:**
```powershell
Get-Content "$env:TEMP\fire.py" | kubectl exec -n agent -i $pod -c agent -- python -
```

Expected output: `published!`

### C.3 — Watch logs in a second terminal

```powershell
kubectl logs -n agent deployment/athena-agent -f
```

### C.4 — Approve in Slack

1. Go to `#incidents` channel
2. **Scroll to the very bottom**
3. Find the newest message (highest incident number e.g. `inc-17794xxxxx`)
4. Click **Approve**

> **Important:** There will be many old messages with Approve/Reject buttons.
> Always click on the **bottom-most / newest** message only.
> Old messages are expired and clicking them does nothing.

### C.5 — Confirm success

Watch logs for:
```
POST http://opa.agent:8181/.../decision  200 OK         ← OPA approved
POST http://k8s-mcp.agent:8080/tools/restart_deployment 200 OK  ← restarted
# 30 seconds later...
POST http://prom-mcp.agent:8080/tools/query_instant  200 OK     ← verified
POST https://hooks.slack.com/...  200 OK                         ← Slack notified
INFO agent incident complete: resolved=True                       ← DONE
```

Confirm cartservice restarted:
```powershell
kubectl get pods -n online-boutique -l app=cartservice
```
The AGE column will show a very recent pod (seconds/minutes).

---

## 9. Understanding the Logs

Here is a real example of a successful full run with explanation:

```
22:17:25  handling incident on anomalies.metric for online-boutique/cartservice
          → Agent woke up from NATS event

22:17:25  POST https://api.groq.com/...  200 OK
          → detector node called Groq LLM — decided anomaly is real

22:17:26  POST http://prom-mcp.../tools/query_instant  200 OK
22:17:26  POST http://k8s-mcp.../tools/get_events  200 OK
22:17:26  POST http://prom-mcp.../tools/query_range  200 OK
          → diagnostician queried real Prometheus metrics + K8s events

22:17:26  POST http://loki-mcp.../tools/query_logs  500 Error  (x3)
          → Loki not deployed — agent handles gracefully, continues anyway

22:17:28  POST http://k8s-mcp.../tools/describe_deployment  200 OK
          → diagnostician checked deployment status

22:17:29  POST https://api.groq.com/...  200 OK
          → remediator decided: restart cartservice (risk=high → needs HITL)

22:17:29  POST http://slack-bot.agent:8000/post-approval  200 OK
          → HITL message posted to Slack #incidents

22:17:59  POST http://opa.agent:8181/.../decision  200 OK
          → YOU APPROVED → OPA policy check passed

22:17:59  POST http://k8s-mcp.agent:8080/tools/restart_deployment  200 OK
          → kubectl rollout restart ran on cartservice

22:18:29  POST http://prom-mcp.../tools/query_instant  200 OK
          → verifier waited 30s, re-checked Prometheus — metrics recovered

22:18:29  POST https://hooks.slack.com/...  200 OK
          → reporter posted postmortem to #incidents

22:18:29  agent incident complete: resolved=True
          → DONE — full autonomous remediation complete
```

---

## 10. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `published!` not shown | Wrong pod name | Run `kubectl get pods -n agent` and check athena-agent is Running |
| No Slack HITL message | Slack webhook not set | Check SLACK_WEBHOOK_URL is set in agent env |
| Approved but nothing happened | Clicked old Slack message | Scroll to bottom of #incidents, approve the newest message |
| `resolved=None` | HITL timed out (10 min window) | Fire a new event and approve within 10 minutes |
| Redis timeout error | Redis pod restarted | Wait 30s, fire event again |
| Loki 500 errors | Loki not deployed | Safe to ignore — agent continues without logs |
| `resolved=None` after approval | Approval went to wrong Redis | Confirm Slack URL points to EKS LoadBalancer, not ngrok |
| Groq 400 error | Bad request format | Usually a single failed call — agent retries automatically |

---

## 11. Cleanup

**Stop local dev:**
```powershell
cd C:\Users\FAUZIA SHAHRYAR\Desktop\athena\athena\agent
docker compose down
```

**Destroy EKS (stops ~$0.50/hr billing):**
```powershell
cd "C:\Users\FAUZIA SHAHRYAR\Desktop\athena\infra\terraform\environments\dev"
terraform destroy -auto-approve
```

Takes ~10 minutes. Removes: EKS cluster, VPC, load balancers, all AWS resources.

---

## Quick Reference Card

```
FIRE EVENT:
  Line 1: $pod = (kubectl get pods -n agent --no-headers | Select-String "athena-agent" | ForEach-Object { ($_ -split "\s+")[0] } | Select-Object -First 1)
  Line 2: Get-Content "$env:TEMP\fire.py" | kubectl exec -n agent -i $pod -c agent -- python -

WATCH LOGS:
  kubectl logs -n agent deployment/athena-agent -f

CHECK PODS:
  kubectl get pods -n agent
  kubectl get pods -n online-boutique

CHECK CARTSERVICE RESTARTED:
  kubectl get pods -n online-boutique -l app=cartservice

DESTROY EKS:
  cd "C:\Users\FAUZIA SHAHRYAR\Desktop\athena\infra\terraform\environments\dev"
  terraform destroy -auto-approve
```
