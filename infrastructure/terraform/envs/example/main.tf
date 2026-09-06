# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# One directory per deployment. Copy this one, rename it after the customer,
# and set customer_id to the same slug - it names every resource the module
# creates, so two deployments in one account cannot collide.

terraform {
  required_version = ">= 1.5.0"
  # Per-tenant remote state (recommended - one state file per deployment):
  # backend "s3" {
  #   bucket = "citra-tf-state"
  #   key    = "example/prod.tfstate"
  #   region = "ap-south-1"
  # }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "domain" {
  type = string
}

variable "route53_zone_id" {
  type    = string
  default = ""
}

variable "box_count" {
  type    = number
  default = 3
}

variable "service_replicas" {
  type    = map(number)
  default = {}
}

module "example" {
  source           = "../../modules/citra-tenant"
  customer_id      = "example"
  environment      = "prod"
  region           = var.region
  domain           = var.domain
  route53_zone_id  = var.route53_zone_id
  box_count        = var.box_count
  service_replicas = var.service_replicas
}

output "alb_dns_name" {
  value = module.example.alb_dns_name
}

output "service_replicas_resolved" {
  value = module.example.service_replicas_resolved
}
