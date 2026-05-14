output "cluster_name" { value = module.eks.cluster_name }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "oidc_provider_arn" { value = module.eks.oidc_provider_arn }
output "vpc_id" { value = module.vpc.vpc_id }
output "ecr_repositories" { value = { for k, v in aws_ecr_repository.this : k => v.repository_url } }
