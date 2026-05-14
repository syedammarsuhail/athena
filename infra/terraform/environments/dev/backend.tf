terraform {
  required_version = ">= 1.7"

  backend "s3" {
    bucket         = "athena-tf-state-CHANGEME" # set to your bucket
    key            = "environments/dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "athena-tf-locks"
    encrypt        = true
  }

  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.60" }
    helm       = { source = "hashicorp/helm", version = "~> 2.13" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" }
    tls        = { source = "hashicorp/tls", version = "~> 4.0" }
  }
}
