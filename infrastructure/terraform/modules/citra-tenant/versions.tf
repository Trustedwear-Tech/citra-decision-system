# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

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
