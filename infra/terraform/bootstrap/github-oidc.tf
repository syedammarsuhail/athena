# Run this once, separately. Outputs the IAM Role ARN for GitHub Actions.

variable "github_org"  { type = string }
variable "github_repo" { type = string  default = "athena" }
variable "region"      { type = string  default = "us-east-1" }

provider "aws" { region = var.region }

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github" {
  name               = "github-actions-athena"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Scope this tighter in production. AdministratorAccess used here only for the bootstrap demo.
resource "aws_iam_role_policy_attachment" "admin" {
  role       = aws_iam_role.github.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "github_role_arn" {
  value       = aws_iam_role.github.arn
  description = "Set as AWS_ROLE_ARN secret in the GitHub repo"
}
