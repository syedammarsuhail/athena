# Cluster Rebuild Runbook

Full platform up from scratch. Takes ~20 minutes total.

## Step 1 — Provision infrastructure (~15 min)

```bash
cd infra/terraform/environments/dev
terraform init
terraform apply -auto-approve
```

This creates: VPC, EKS 1.32, node group, ECR repos, EBS CSI driver + IAM role,
gp2→gp3 StorageClass, security group rules for ports 80/443, and admin access entry
for the AWS root account. No manual AWS console steps needed.

## Step 2 — Connect kubectl (~1 min)

```bash
aws eks update-kubeconfig --region us-east-1 --name athena-dev
kubectl get nodes   # should show 3 Ready nodes
```

## Step 3 — Install ArgoCD and bootstrap platform (~4 min)

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=120s
kubectl apply -f platform/bootstrap/root-app.yaml
```

ArgoCD will now auto-deploy everything: observability, security, MLOps, Online Boutique, agent.

## Step 4 — Set the Anthropic API key (required for agent)

```bash
kubectl -n agent create secret generic anthropic \
  --from-literal=api-key="sk-ant-YOUR-KEY-HERE"
```

## Step 5 — Get your URLs

```bash
# Online Boutique (wait ~3 min for ingress-nginx LB to provision)
kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'

# Grafana (admin / Athena@2026!)
kubectl -n monitoring patch svc kube-prometheus-stack-grafana \
  -p '{"spec":{"type":"LoadBalancer"}}'
kubectl -n monitoring get svc kube-prometheus-stack-grafana \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

## What auto-provisions now (no manual steps)

- EKS auth mode API_AND_CONFIG_MAP
- kubectl access for AWS root account
- EBS CSI driver + IAM role (Prometheus, Tempo, NATS get storage)
- gp3 StorageClass as default (via ArgoCD storage-app)
- Security group ports 80/443 open for NLB
- Online Boutique ingress works with any LB hostname

## ArgoCD (optional, port-forward only)

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443 &
# open https://localhost:8080
# password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```
