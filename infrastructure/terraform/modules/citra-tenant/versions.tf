# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Citra per-tenant provisioning — provider + version pins.
#
# Topology (deliberately NOT Kubernetes — see README): one dedicated,
# single-tenant environment = an AWS ALB in front of 1..N EC2 boxes, each
# running Traefik + the docker-compose service stack. Terraform provisions the
# infra and renders the compose stack (with per-service replica counts); the
# existing GHCR image + SSM deploy flow ships the code.
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
