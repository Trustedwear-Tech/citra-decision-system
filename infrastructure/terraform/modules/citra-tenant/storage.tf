# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# ── Per-tenant S3 buckets ────────────────────────────────────────────────────
# One dedicated bucket per logical purpose (media, memory-export, artifacts),
# name-prefixed by customer_id. Memory-export is the customer-owned data-egress
# target (see docs: memory export). All private + encrypted + versioned.
resource "aws_s3_bucket" "this" {
  for_each = var.buckets
  # Include environment: S3 names are GLOBALLY unique, so a `staging` deployment
  # for a customer already running `prod` (same customer_id) would otherwise
  # collide on the identical bucket name (BucketAlreadyExists) or cross-wire two
  # environments onto one bucket.
  bucket = "citra-${var.customer_id}-${var.environment}-${each.key}"
  tags   = merge(local.base_tags, { Purpose = each.value })
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# The boxes may read/write this tenant's buckets (scoped to its own prefix set).
resource "aws_iam_role_policy" "box_s3" {
  name = "${local.name}-s3"
  role = aws_iam_role.box.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
      Resource = concat(
        [for b in aws_s3_bucket.this : b.arn],
        [for b in aws_s3_bucket.this : "${b.arn}/*"],
      )
    }]
  })
}
