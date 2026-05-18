module "vpc" {
  source = "../../modules/vpc"

  name               = "${var.cluster_name}-vpc"
  cidr               = "10.0.0.0/16"
  azs                = ["us-east-1a", "us-east-1b", "us-east-1c"]
  single_nat_gateway = true # cost: dev only
  cluster_name       = var.cluster_name
}

module "eks" {
  source = "../../modules/eks-cluster"

  cluster_name    = var.cluster_name
  cluster_version = "1.32"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids

  node_groups = {
    primary = {
      instance_types = ["t3.large"]
      capacity_type  = "SPOT"
      min_size       = 2
      desired_size   = 3
      max_size       = 6
    }
  }
}

# ECR repos for our images
resource "aws_ecr_repository" "this" {
  for_each = toset([
    "athena/agent",
    "athena/ml-isoforest",
    "athena/ml-drain3",
    "athena/prom-mcp",
    "athena/loki-mcp",
    "athena/k8s-mcp",
  ])
  name                 = each.value
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = true }
}

# NOTE: Argo CD is installed via kubectl after the cluster is up.
# See the README or docs/runbooks/demos.md for the bootstrap commands.
