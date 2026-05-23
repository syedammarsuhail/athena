# Athena AIOps — End-to-End Demo Runbook

## What Is Athena?

Athena is an autonomous AIOps platform that detects, diagnoses, and remediates
Kubernetes infrastructure incidents automatically. It runs a 7-node AI graph:

```
detector → diagnostician → remediator → hitl → executor → verifier → reporter
```

- **detector** — decides if the anomaly is real
- **diagnostician** — queries Prometheus, Loki, K8s to find root cause
- **remediator** — proposes a fix with a risk score
- **hitl** — posts a Slack message asking a human to Approve or Reject
- **executor** — runs the fix (e.g. restart deployment) gated by OPA policy
- **verifier** — re-checks Prometheus 30s later to confirm recovery
- **reporter** — posts postmortem to Slack, opens GitHub issue

---

## Prerequisites

Install these tools before starting:

| Tool | Purpose | Download |
|---|---|---|
| AWS CLI | Interact with AWS | https://aws.amazon.com/cli/ |
| kubectl | Manage Kubernetes | https://kubernetes.io/docs/tasks/tools/ |
| Terraform | Provision EKS infrastructure | https://developer.hashicorp.com/terraform/install |
| Docker Desktop | Build and push images | https://www.docker.com/products/docker-desktop/ |
| ngrok (local only) | Expose local Slack bot | https://ngrok.com/download |
| Python 3.11+ | Run test scripts | https://www.python.org/downloads/ |

Configure AWS CLI:
```powershell
aws configure
# Enter: Access Key ID, Secret Access Key, Region (us-east-1), Output (json)
```

---

## Part 1 — Local Development (Docker Compose)

### 1.1 Clone the repo and set up environment

```powershell
cd C:\Users\FAUZIA SHAHRYAR\Desktop\athena\athena\agent
```

Create `.env` file:
```
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

Get a free Groq API key at: https://console.groq.com

### 1.2 Start all services

```powershell
docker compose up -d
```

This starts: NATS, Redis, OPA, agent, slack-bot, OTel Collector, Tempo, Grafana.

Verify everything is running:
```powershell
docker compose ps
```

All services should show `running`.

### 1.3 Set up ngrok for Slack HITL (local only)

In a separate terminal:
```powershell
ngrok http 8000
```

Copy the HTTPS URL (e.g. `https://abc123.ngrok.io`).

Go to https://api.slack.com/apps → your app → **Interactivity & Shortcuts**
Set Request URL to: `https://abc123.ngrok.io/slack/actions`

### 1.4 Fire a test incident

```powershell
# Save the script
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

# Run it
python "$env:TEMP\fire-local.py"
```

### 1.5 Watch logs

```powershell
docker compose logs -f agent
```

### 1.6 Approve in Slack

Go to `#incidents` channel → click **Approve** on the HITL message.

Watch the logs complete:
```
executor  → OPA approved → restart ran
verifier  → confirmed recovery
reporter  → postmortem posted to Slack
incident complete: resolved=True
```

---

## Part 2 — EKS Deployment

### 2.1 Provision EKS with Terraform

```powershell
cd C:\Users\FAUZIA SHAHRYAR\Desktop\athena\infra\terraform\environments\dev
terraform init
terraform apply -auto-approve
```

This takes ~15 minutes. It creates:
- VPC with public/private subnets
- EKS cluster (2x t3.medium nodes)
- IRSA roles for pod-level AWS permissions
- EBS CSI driver for persistent storage

Update kubeconfig:
```powershell
aws eks update-kubeconfig --name athena-dev --region us-east-1
```

Verify cluster:
```powershell
kubectl get nodes
```

Both nodes should show `Ready`.

### 2.2 Log in to ECR

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

### 2.3 Create Kubernetes secrets

```powershell
# Slack credentials
kubectl create secret generic slack -n agent \
  --from-literal=webhook="https://hooks.slack.com/services/YOUR/WEBHOOK/URL" \
  --from-literal=signing_secret="your_signing_secret"

# Groq API key
kubectl create secret generic groq-secret -n agent \
  --from-literal=api-key="your_groq_api_key"

# ArgoCD token (get from ArgoCD UI)
kubectl create secret generic argocd-api-token -n agent \
  --from-literal=token="your_argocd_token"
```

### 2.4 Patch agent deployment with Groq key

```powershell
$patch = '[{"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"GROQ_API_KEY","valueFrom":{"secretKeyRef":{"name":"groq-secret","key":"api-key"}}}}]'
$patch | Out-File -FilePath "$env:TEMP\patch.json" -Encoding utf8 -NoNewline
kubectl patch deployment athena-agent -n agent --type=json --patch-file "$env:TEMP\patch.json"
```

```powershell
$patch = '[{"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"SLACK_WEBHOOK_URL","valueFrom":{"secretKeyRef":{"name":"slack","key":"webhook"}}}}]'
$patch | Out-File -FilePath "$env:TEMP\patch2.json" -Encoding utf8 -NoNewline
kubectl patch deployment athena-agent -n agent --type=json --patch-file "$env:TEMP\patch2.json"
```

### 2.5 Create Slack-bot LoadBalancer for EKS callbacks

ArgoCD manages the slack-bot service and keeps resetting it to ClusterIP.
Create a separate LoadBalancer service:

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

Wait for external IP:
```powershell
kubectl get svc -n agent slack-bot-lb -w
```

Once EXTERNAL-IP appears, copy it. Then update Slack interactivity URL to:
```
http://EXTERNAL-IP/slack/actions
```

### 2.6 Verify all pods are running

```powershell
kubectl get pods -n agent
kubectl get pods -n online-boutique
```

Expected pods in `agent` namespace:
- `athena-agent` (x2)
- `k8s-mcp` (x2)
- `loki-mcp` (x2)
- `prom-mcp` (x2)
- `opa` (x2)
- `redis`
- `slack-bot` (x2)

---

## Part 3 — Run the Full EKS Incident Flow

### 3.1 Save the event script (one time)

I already created this file for you at `C:\Users\FAUZIA SHAHRYAR\AppData\Local\Temp\fire.py`.

If you need to recreate it:
```python
# Contents of fire.py
import nats, asyncio, json, time
async def p():
    nc = await nats.connect("nats://nats.mlops:4222")
    data = {"service":"cartservice","namespace":"online-boutique","metric":"memory","score":0.97,"severity":"high","ts":time.time()}
    await nc.publish("anomalies.metric", json.dumps(data).encode())
    await nc.drain()
    print("published!")
asyncio.run(p())
```

### 3.2 Fire the incident

```powershell
$pod = (kubectl get pods -n agent --no-headers | Select-String "athena-agent" | ForEach-Object { ($_ -split "\s+")[0] } | Select-Object -First 1)
Get-Content "$env:TEMP\fire.py" | kubectl exec -n agent -i $pod -c agent -- python -
```

Expected output: `published!`

### 3.3 Watch the logs (second terminal)

```powershell
kubectl logs -n agent deployment/athena-agent -f
```

### 3.4 Approve in Slack

Within ~10 seconds a message appears in `#incidents` with **Approve** and **Reject** buttons.
Click **Approve**.

### 3.5 Confirm success in logs

```
POST http://opa.agent:8181/.../decision  200 OK        ← OPA approved the action
POST http://k8s-mcp.agent:8080/tools/restart_deployment  200 OK  ← kubectl restart ran
# 30 seconds later...
POST http://prom-mcp.agent:8080/tools/query_instant  200 OK       ← verifier checked
POST https://hooks.slack.com/...  200 OK                           ← postmortem sent
INFO agent incident complete: resolved=True
```

### 3.6 Confirm cartservice restarted

```powershell
kubectl get pods -n online-boutique -l app=cartservice
```

The pod AGE will be very recent (seconds/minutes).

---

## Part 4 — What Each Component Does (Summary)

| Component | Where | What it does |
|---|---|---|
| NATS | mlops namespace | Message queue — anomaly events flow through here |
| Redis | agent namespace | Stores HITL approval decisions |
| OPA | agent namespace | Policy engine — blocks unsafe kubectl actions |
| prom-mcp | agent namespace | Proxies Prometheus queries for the agent |
| loki-mcp | agent namespace | Proxies Loki log queries for the agent |
| k8s-mcp | agent namespace | Executes kubectl commands on behalf of the agent |
| athena-agent | agent namespace | The AI brain — runs the 7-node LangGraph |
| slack-bot | agent namespace | Handles Slack HITL approve/reject buttons |
| Groq | Cloud (free) | Runs the LLM (Llama 3.3 70B) |
| ArgoCD | cluster | GitOps — keeps all deployments in sync with git |

---

## Part 5 — Cleanup

When done, destroy EKS to stop AWS charges (~$0.50/hr):

```powershell
cd "C:\Users\FAUZIA SHAHRYAR\Desktop\athena\infra\terraform\environments\dev"
terraform destroy -auto-approve
```

This takes ~10 minutes and removes all AWS resources.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `published!` not printed | Check pod name: `kubectl get pods -n agent` |
| No Slack message appears | Check Slack interactivity URL is set to the LoadBalancer IP |
| Approval not picked up | Slack URL must point to EKS LoadBalancer, not ngrok |
| Redis timeout error | Redis pod restarted — wait 30s and fire the event again |
| `resolved=None` | HITL timed out (10 min window) — fire the event again and approve faster |
| Loki 500 errors | Loki is not deployed — agent handles this gracefully, safe to ignore |
