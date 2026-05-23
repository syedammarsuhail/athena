# One-time bootstrap

Run this once to create:
- GitHub OIDC provider in AWS
- IAM role GitHub Actions will assume (no long-lived keys)

```bash
terraform init
terraform apply -var github_org=YOUR_GITHUB_ORG
```

Copy the `github_role_arn` output into GitHub repo settings:
**Settings → Secrets and variables → Actions → New repository secret**
- Name: `AWS_ROLE_ARN`
- Value: the role ARN

You also need:
- `AWS_REGION` (e.g. `us-east-1`)
- `TF_STATE_BUCKET` (the bucket you created)
