# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# ── Tenant identity ──────────────────────────────────────────────────────────
variable "customer_id" {
  description = "Short slug for this single-tenant deployment (e.g. \"acme-power\"). Names every resource."
  type        = string
}

variable "environment" {
  description = "Deployment environment label (prod | staging | demo)."
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region for this tenant's dedicated environment."
  type        = string
  default     = "ap-south-1"
}

variable "domain" {
  description = "Base domain served by the ALB (e.g. \"acme.citra.ai\"). Route53 zone must be delegated."
  type        = string
}

variable "route53_zone_id" {
  description = "Hosted-zone id for `domain`. Empty ⇒ skip DNS/ACM wiring (bring-your-own cert)."
  type        = string
  default     = ""
}

# ── Network ──────────────────────────────────────────────────────────────────
variable "vpc_cidr" {
  description = "CIDR for the tenant VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to spread public/private subnets across (2+ for multi-AZ ALB)."
  type        = number
  default     = 2
}

variable "admin_ingress_cidrs" {
  description = "CIDRs allowed to reach SSH / the Traefik dashboard (operator access only)."
  type        = list(string)
  default     = []
}

# ── Compute (the boxes) ──────────────────────────────────────────────────────
variable "box_count" {
  description = <<-EOT
    Number of EC2 boxes running the compose stack behind the ALB. 1 = the
    current demo shape (single box, a SPOF). 2..3 = HA across AZs (prod shape).
    Service replicas (var.service_replicas) are spread across these boxes by the
    deploy step; this is the box fleet size, not the per-service replica count.
  EOT
  type        = number
  default     = 3
}

variable "instance_type" {
  description = "EC2 instance type for the boxes."
  type        = string
  default     = "m6i.2xlarge"
}

variable "root_volume_gb" {
  description = "Root EBS size per box (GB)."
  type        = number
  default     = 200
}

variable "ami_id" {
  description = "Base AMI (Ubuntu 22.04+). Empty ⇒ resolve the latest Canonical Ubuntu LTS via data source."
  type        = string
  default     = ""
}

variable "ssh_key_name" {
  description = "EC2 key pair name for break-glass SSH (SSM is the primary access path)."
  type        = string
  default     = ""
}

# ── Service instance counts (the configurable bit) ───────────────────────────
variable "service_replicas" {
  description = <<-EOT
    Per-service replica overrides, merged over the catalogue defaults in
    services.tf. The default catalogue already runs the hot-path services at 2
    (citra-service, smart-app-service, citra-app-runtime, citra-workflow,
    citra-worker) and everything else at 1. Override here per customer, e.g.
    { "citra-service" = 3, "reranker-service" = 2 }. A value of 0 disables a
    service for this tenant.
  EOT
  type        = map(number)
  default     = {}
}

variable "enable_ui_on_box" {
  description = "Serve citra-ui from the box via Traefik (true) vs. Firebase/CDN edge hosting (false)."
  type        = bool
  default     = false
}

# ── Storage ──────────────────────────────────────────────────────────────────
variable "buckets" {
  description = "Logical S3 buckets to create for this tenant (suffix → purpose). Names are prefixed with customer_id."
  type        = map(string)
  default = {
    "media"         = "SoR media streamed via MCP + document handoffs"
    "memory-export" = "customer-owned memory export (JSONL.gz)"
    "artifacts"     = "generated artifacts (presentations/reports/printables)"
  }
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
