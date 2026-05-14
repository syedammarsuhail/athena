variable "name"               { type = string }
variable "cidr"               { type = string }
variable "azs"                { type = list(string) }
variable "single_nat_gateway" {
  type    = bool
  default = false
}
variable "cluster_name"       { type = string }

locals {
  public_subnets  = [for i, az in var.azs : cidrsubnet(var.cidr, 4, i)]
  private_subnets = [for i, az in var.azs : cidrsubnet(var.cidr, 4, i + 10)]
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags = { Name = "${var.name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = length(var.azs)
  vpc_id                  = aws_vpc.this.id
  availability_zone       = var.azs[count.index]
  cidr_block              = local.public_subnets[count.index]
  map_public_ip_on_launch = true
  tags = {
    Name                                            = "${var.name}-public-${var.azs[count.index]}"
    "kubernetes.io/role/elb"                        = "1"
    "kubernetes.io/cluster/${var.cluster_name}"     = "shared"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.this.id
  availability_zone = var.azs[count.index]
  cidr_block        = local.private_subnets[count.index]
  tags = {
    Name                                            = "${var.name}-private-${var.azs[count.index]}"
    "kubernetes.io/role/internal-elb"               = "1"
    "kubernetes.io/cluster/${var.cluster_name}"     = "shared"
  }
}

resource "aws_eip" "nat" {
  count  = var.single_nat_gateway ? 1 : length(var.azs)
  domain = "vpc"
}

resource "aws_nat_gateway" "this" {
  count         = var.single_nat_gateway ? 1 : length(var.azs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags = { Name = "${var.name}-nat-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "public" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(var.azs)
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[var.single_nat_gateway ? 0 : count.index].id
  }
}

resource "aws_route_table_association" "private" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

output "vpc_id"             { value = aws_vpc.this.id }
output "public_subnet_ids"  { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
