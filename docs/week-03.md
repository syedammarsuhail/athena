# Week 3 — DevSecOps

**Goal:** Every image is signed, SBOM'd, scanned, and admission-controlled. Runtime suspicious behavior is detected and routed.

## What you'll build

```
PR → GitHub Actions (build.yml):
   ├─ docker buildx → push to ECR (immutable tag)
   ├─ Syft → SBOM (uploaded as artifact)
   ├─ Trivy → vuln scan (fails on HIGH/CRITICAL)
   ├─ Cosign → sign image with keyless (Fulcio) OIDC
   └─ Cosign → attest the SBOM
                    │
                    ▼
            Kyverno (in-cluster admission):
              - blocks unsigned images
              - blocks images without SBOM attestation
              - blocks :latest tag
              - blocks privileged pods, runAsRoot, hostNetwork
                    │
                    ▼
            Running pods
                    │
                    ▼
            Falco DaemonSet → falcosidekick → Slack + Loki
```

## Day-by-day

### Day 1: Build/sign/scan pipeline

`.github/workflows/build.yml` — see file. Key points:
- OIDC auth, no static keys (already in week 1)
- `cosign sign --yes <image>@<digest>` uses GitHub's OIDC token → Fulcio short-lived cert
- Trivy fails the build on CRITICAL/HIGH unless a `.trivyignore` justifies it

### Day 2: Push a real image

Pick the simplest workload — the **agent** service (we'll build it later, but the Dockerfile is already there). PR a no-op change to trigger the workflow. Verify in ECR:
```bash
aws ecr describe-images --repository-name athena/agent
# tags include the sha + a cosign signature artifact
```

Verify the signature manually:
```bash
COSIGN_EXPERIMENTAL=1 cosign verify \
  --certificate-identity-regexp 'https://github.com/CHANGEME/athena' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  <image>@<digest>
```

### Day 3: Kyverno

```bash
git add platform/security/kyverno-app.yaml platform/security/kyverno-policies/
git commit -m "feat(security): kyverno admission control" && git push
```

Test it: try to deploy a pod with `:latest`:
```bash
kubectl run nope --image=nginx:latest
# Error from server: admission webhook "validate.kyverno.svc-fail" denied the request:
# require-image-tag: validation failure: image tag ':latest' is not allowed
```

### Day 4: Image signature verification in-cluster

The `verify-images` policy uses Kyverno's `verifyImages` rule. Deploy an unsigned image:
```bash
kubectl run unsigned --image=docker.io/library/nginx:1.27
# blocked: image not signed by trusted identity
```

### Day 5: Falco runtime detection

```bash
git add platform/security/falco-app.yaml
git commit -m "feat(security): falco runtime detection" && git push
```

Trigger a rule by `exec`-ing a shell in a container — Falco fires a `Terminal shell in container` event, falcosidekick forwards it to Slack and pushes the event into Loki labeled `falco_priority="critical"`.

### Day 6: External Secrets

Stop storing secrets in YAML even encrypted. External-Secrets Operator pulls from AWS Secrets Manager via IRSA.

```bash
# Create a secret in AWS SM
aws secretsmanager create-secret \
  --name athena/dev/slack-webhook \
  --secret-string '{"url":"https://hooks.slack.com/services/..."}'
```

Apply the IRSA role (Terraform `irsa-role` module). The `ExternalSecret` CR (`platform/security/external-secret-slack.yaml`) syncs it to a K8s secret automatically.

### Day 7: Audit + harden

Run `kube-bench` and `kube-hunter` as a one-off Job. Fix the criticals. The point isn't a perfect score — it's that you *ran them*, *understood the findings*, and *made deliberate decisions*. Document the decisions as ADRs.

## Verification checklist

- [ ] An unsigned image is blocked from running by Kyverno
- [ ] An image with a CRITICAL CVE fails CI
- [ ] Running `kubectl exec` into a pod fires a Falco alert in Slack within 10s
- [ ] No Kubernetes Secret in Git contains a real value (all are ExternalSecret references)
- [ ] kube-bench score documented in `docs/security-baseline.md`

## What "done" looks like

A 90-second screen recording: PR a Dockerfile that adds a vulnerable package → CI fails on the Trivy step → fix the package → CI passes, image is signed → deployed via Argo. That's the supply chain story in one clip.
