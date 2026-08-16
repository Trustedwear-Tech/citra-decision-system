# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# ── The boxes (Traefik + docker-compose stack) ───────────────────────────────
data "aws_ami" "ubuntu" {
  count       = var.ami_id == "" ? 1 : 0
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

locals {
  ami = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu[0].id
}

# IAM: SSM (the primary deploy/admin channel — matches the existing SSM flow) +
# read of this tenant's buckets.
resource "aws_iam_role" "box" {
  name = "${local.name}-box"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.base_tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.box.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "box" {
  name = "${local.name}-box"
  role = aws_iam_role.box.name
}

# Rendered compose override that pins per-service replica counts (the
# configurable instance counts). The deploy step lays down the base compose
# stack and applies this override; Traefik load-balances across the replicas.
locals {
  compose_override = templatefile("${path.module}/templates/compose.override.yml.tftpl", {
    services = local.resolved_services
  })

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    customer_id      = var.customer_id
    environment      = var.environment
    compose_override = local.compose_override
    public_services  = local.public_services
  })
}

resource "aws_instance" "box" {
  count                  = var.box_count
  ami                    = local.ami
  instance_type          = var.instance_type
  subnet_id              = element([for s in aws_subnet.public : s.id], count.index)
  vpc_security_group_ids = [aws_security_group.box.id]
  iam_instance_profile   = aws_iam_instance_profile.box.name
  key_name               = var.ssh_key_name != "" ? var.ssh_key_name : null
  user_data              = local.user_data

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.base_tags, {
    Name = "${local.name}-box-${count.index + 1}"
    Role = "compose-box"
  })
}
