provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "athena"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
