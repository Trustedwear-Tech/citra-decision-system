# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# ── VPC + subnets (multi-AZ) ─────────────────────────────────────────────────
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "citra-${var.customer_id}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  base_tags = merge({
    Project     = "citra"
    Customer    = var.customer_id
    Environment = var.environment
    ManagedBy   = "terraform"
  }, var.tags)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.base_tags, { Name = local.name })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.base_tags, { Name = "${local.name}-igw" })
}

# Public subnets host the ALB (+ the boxes; SSM/egress via IGW keeps the skeleton
# simple — a private-subnet + NAT split is a later hardening).
resource "aws_subnet" "public" {
  for_each                = { for i, az in local.azs : az => cidrsubnet(var.vpc_cidr, 4, i) }
  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = true
  tags                    = merge(local.base_tags, { Name = "${local.name}-public-${each.key}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.base_tags, { Name = "${local.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# ── Security groups ──────────────────────────────────────────────────────────
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public HTTP/HTTPS into the ALB"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "HTTP (redirect to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.base_tags, { Name = "${local.name}-alb" })
}

resource "aws_security_group" "box" {
  name        = "${local.name}-box"
  description = "Tenant compose boxes — ALB to Traefik + operator admin"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "Traefik web from ALB only"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  ingress {
    description = "Operator SSH + Traefik dashboard (restricted)"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.admin_ingress_cidrs
  }
  # Intra-fleet: boxes talk to each other on the docker/service ports.
  ingress {
    description = "Intra-fleet service mesh"
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    self        = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.base_tags, { Name = "${local.name}-box" })
}
