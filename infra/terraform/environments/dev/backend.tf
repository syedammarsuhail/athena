terraform {
  required_version = ">= 1.7"

  # Using local state for dev. To migrate to S3:
  # 1. Create bucket + DynamoDB table
  # 2. Re-add backend "s3" block and run terraform init -migrate-state

  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.60" }
    helm       = { source = "hashicorp/helm", version = "~> 2.13" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" }
    tls        = { source = "hashicorp/tls", version = "~> 4.0" }
  }
}
