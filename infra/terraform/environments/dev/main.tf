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
  cluster_version = "1.30"
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
  image_scanning_configuration { scan_on_push = true }
}

# Argo CD — installed via Helm so we can manage version in code
resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "7.4.0"

  values = [file("${path.module}/values/argocd-values.yaml")]

  depends_on = [module.eks]
}
