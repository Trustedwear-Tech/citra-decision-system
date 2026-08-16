# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# ── ALB → boxes (Traefik terminates per-service routing) ─────────────────────
# The ALB is the single public ingress; it forwards :443 to Traefik on each box
# (:80), and Traefik does the per-service path routing from its dynamic config
# (rendered from local.public_services). One target group over all boxes.
resource "aws_lb" "this" {
  name               = substr("${local.name}-alb", 0, 32)
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [for s in aws_subnet.public : s.id]
  tags               = local.base_tags
}

resource "aws_lb_target_group" "traefik" {
  name        = substr("${local.name}-tf", 0, 32)
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.this.id
  target_type = "instance"

  health_check {
    path                = "/ping" # Traefik ping entrypoint (see infrastructure/traefik/traefik.yml)
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }
  tags = local.base_tags
}

resource "aws_lb_target_group_attachment" "boxes" {
  count            = var.box_count
  target_group_arn = aws_lb_target_group.traefik.arn
  target_id        = aws_instance.box[count.index].id
  port             = 80
}

# HTTPS listener — uses the ACM cert when DNS is delegated, else expects an
# externally-provided cert ARN (skeleton: HTTP-only when route53_zone_id empty).
resource "aws_lb_listener" "https" {
  count             = var.route53_zone_id == "" ? 0 : 1
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.this[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.traefik.arn
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  # Redirect to HTTPS when TLS is configured; otherwise forward (dev/demo).
  default_action {
    type             = var.route53_zone_id == "" ? "forward" : "redirect"
    target_group_arn = var.route53_zone_id == "" ? aws_lb_target_group.traefik.arn : null

    dynamic "redirect" {
      for_each = var.route53_zone_id == "" ? [] : [1]
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

# ── DNS + TLS (only when a zone is delegated) ────────────────────────────────
resource "aws_acm_certificate" "this" {
  count             = var.route53_zone_id == "" ? 0 : 1
  domain_name       = var.domain
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
  tags = local.base_tags
}

resource "aws_route53_record" "cert_validation" {
  for_each = var.route53_zone_id == "" ? {} : {
    for dvo in aws_acm_certificate.this[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }
  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "this" {
  count                   = var.route53_zone_id == "" ? 0 : 1
  certificate_arn         = aws_acm_certificate.this[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

resource "aws_route53_record" "alias" {
  count   = var.route53_zone_id == "" ? 0 : 1
  zone_id = var.route53_zone_id
  name    = var.domain
  type    = "A"
  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}
