variable "region" {
  type    = string
  default = "us-east-1"
}
variable "environment" {
  type    = string
  default = "dev"
}
variable "cluster_name" {
  type    = string
  default = "athena-dev"
}
variable "domain_name" {
  type    = string
  default = "example.com"
}
variable "letsencrypt_email" { type = string }
