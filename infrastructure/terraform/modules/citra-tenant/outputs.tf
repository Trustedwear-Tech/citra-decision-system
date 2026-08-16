# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

output "alb_dns_name" {
  description = "Public DNS of the tenant ALB (point the customer domain here, or use the Route53 alias)."
  value       = aws_lb.this.dns_name
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "box_instance_ids" {
  description = "EC2 instance ids of the compose fleet (targets for the SSM deploy)."
  value       = aws_instance.box[*].id
}

output "bucket_names" {
  value = { for k, b in aws_s3_bucket.this : k => b.bucket }
}

output "service_replicas_resolved" {
  description = "Effective per-service replica counts for this tenant (catalogue defaults ⊕ overrides)."
  value       = { for name, cfg in local.resolved_services : name => cfg.replicas }
}
