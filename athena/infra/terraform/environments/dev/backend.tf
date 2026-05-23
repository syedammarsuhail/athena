terraform {
  required_version = ">= 1.7"

  backend "s3" {
    bucket         = "athena-tfstate-339713148266"
    key            = "dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "athena-tfstate-lock"
    encrypt        = true
  }

  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
}
