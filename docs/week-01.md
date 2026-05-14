# Week 1 — Foundation

**Goal:** End of week, `terraform apply` plus a `git push` results in the Online Boutique app running on HTTPS, managed by Argo CD via GitOps.

## What you'll build

```
GitHub (this repo)
    │ push
    ▼
GitHub Actions (OIDC → AWS, no static keys)
    │ runs terraform
    ▼
AWS (VPC, EKS, IRSA, ECR)
    │ provisions
    ▼
EKS cluster
    │ Argo CD installed via Helm (in Terraform)
    │ root-app points at this repo
    ▼
Argo CD deploys:
    - ingress-nginx
    - cert-manager + Let's Encrypt issuer
    - external-dns
    - Online Boutique (workloads/online-boutique)
```

## Day-by-day

### Day 1–2: AWS account prep + Terraform backend

1. **Create an S3 bucket + DynamoDB table** for Terraform state (one-time, manual):
   ```bash
   aws s3 mb s3://athena-tf-state-$RANDOM_SUFFIX --region us-east-1
   aws dynamodb create-table \
     --table-name athena-tf-locks \
     --attribute-definitions AttributeName=LockID,AttributeType=S \
     --key-schema AttributeName=LockID,KeyType=HASH \
     --billing-mode PAY_PER_REQUEST \
     --region us-east-1
   ```

2. **Create the GitHub OIDC provider in AWS** — `infra/terraform/bootstrap/github-oidc.tf` (run once, separately from the main stack):
   ```bash
   cd infra/terraform/bootstrap
   terraform init && terraform apply
   # Outputs the role ARN — paste into .github/workflows/terraform.yml as AWS_ROLE_ARN
   ```

3. **Update `infra/terraform/environments/dev/backend.tf`** with your bucket name.

### Day 3–4: VPC + EKS

```bash
cd infra/terraform/environments/dev
terraform init
terraform plan   # review carefully
terraform apply  # takes ~15 min
```

This creates:
- A VPC (3 AZs, public + private subnets, single NAT gateway for cost)
- An EKS cluster (v1.30) with managed node group (2× t3.large spot)
- IRSA OIDC provider
- ECR repos for your images
- Argo CD installed via Helm

Verify:
```bash
aws eks update-kubeconfig --name athena-dev --region us-east-1
kubectl get nodes
kubectl -n argocd get pods
```

### Day 5: Platform bootstrap

```bash
# Get the Argo admin password
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d

# Port-forward and log in
kubectl -n argocd port-forward svc/argocd-server 8080:443 &
argocd login localhost:8080 --insecure --username admin

# Apply the root app — Argo will discover and deploy everything else
kubectl apply -f platform/bootstrap/root-app.yaml
```

### Day 6: Deploy Online Boutique

The root-app has already pointed at `workloads/online-boutique/overlays/dev`. Check it's synced:

```bash
argocd app list
argocd app sync online-boutique-dev
kubectl -n online-boutique get pods
kubectl -n online-boutique get ingress
```

Point your DNS at the ingress address (or use the auto-generated `*.nip.io` URL for demos), and you should see the shop running on HTTPS via Let's Encrypt.

### Day 7: Wire up CI/CD

The `.github/workflows/terraform.yml` workflow runs `terraform plan` on PRs and `apply` on merges to main. Push a trivial change to verify the loop works.

## Verification checklist

- [ ] `terraform apply` is idempotent (run twice, no changes)
- [ ] `kubectl get nodes` shows 2+ ready nodes
- [ ] Argo CD UI loads at `https://argocd.<your-domain>` with TLS
- [ ] Online Boutique loads at `https://shop.<your-domain>` with TLS
- [ ] You can push a change to `workloads/online-boutique/overlays/dev/kustomization.yaml` (e.g. bump replicas) and Argo auto-syncs it within 3 minutes
- [ ] GitHub Actions PR workflow comments the `terraform plan` output on PRs
- [ ] No long-lived AWS access keys exist in GitHub secrets — only the role ARN

## Common gotchas

- **EKS subnet tagging:** the EKS module needs subnets tagged with `kubernetes.io/role/elb=1` (public) and `kubernetes.io/role/internal-elb=1` (private). The VPC module here handles this.
- **NAT gateway cost:** the dev environment uses a single NAT for cost. Prod environments should have one per AZ.
- **IRSA OIDC URL:** must be created *after* the EKS cluster exists. The module does this — don't try to create it in a separate stack.
- **Argo CD initial password:** if you lose it, delete the `argocd-initial-admin-secret` and the operator regenerates it.

## What "done" looks like

Recorded screencap of:
1. Opening a PR that bumps the cart-service replica count from 2 to 4
2. CI showing the plan diff in PR comments
3. Merging the PR
4. Argo CD UI showing the sync happening
5. `kubectl -n online-boutique get pods` showing 4 cart pods

That's your week 1 demo asset.
