#!/usr/bin/env bash
# Week 1 quickstart — wraps the manual steps from docs/week-01.md
# Run from the repo root. Edit the variables block first.
set -euo pipefail

# ───── edit me ─────────────────────────────────────────────────────────
: "${GITHUB_ORG:?set GITHUB_ORG to your GitHub username/org}"
: "${TF_STATE_BUCKET:?set TF_STATE_BUCKET to a unique S3 bucket name}"
: "${AWS_REGION:=us-east-1}"
# ───────────────────────────────────────────────────────────────────────

echo "→ Creating Terraform state bucket + lock table"
aws s3 mb "s3://${TF_STATE_BUCKET}" --region "$AWS_REGION" 2>/dev/null || echo "  (bucket exists)"
aws dynamodb create-table \
  --table-name athena-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_REGION" 2>/dev/null || echo "  (table exists)"

echo "→ Patching backend.tf with your bucket name"
sed -i.bak "s/athena-tf-state-CHANGEME/${TF_STATE_BUCKET}/" \
  infra/terraform/environments/dev/backend.tf

echo "→ Bootstrapping GitHub OIDC (separate state)"
pushd infra/terraform/bootstrap >/dev/null
terraform init
terraform apply -auto-approve -var "github_org=${GITHUB_ORG}"
ROLE_ARN=$(terraform output -raw github_role_arn)
popd >/dev/null
echo
echo "→ Set this in your GitHub repo Secrets:"
echo "   AWS_ROLE_ARN  = $ROLE_ARN"
echo "   AWS_REGION    = $AWS_REGION"
echo "   TF_STATE_BUCKET = $TF_STATE_BUCKET"
echo
echo "→ Then commit, push, and let GitHub Actions apply the dev environment."
echo "   (or run terraform apply locally from infra/terraform/environments/dev/)"
