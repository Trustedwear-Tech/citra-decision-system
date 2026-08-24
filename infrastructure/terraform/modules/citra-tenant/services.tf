# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# ── Platform service catalogue ───────────────────────────────────────────────
# The docker-compose services that run on the tenant boxes. Mirrors the CI build
# matrix (.github/workflows/deploy.yml) MINUS the parked/for-future services:
#   • action-chat-service        — PARKED (removed here)
#   • citra-action-chat-sandbox  — PARKED (removed here)
# collaboration-server is gone entirely (2026-08-09), not just excluded here.
# and KEEPS action-sandbox-host (shared — the smart-app builder sandboxes depend
# on it).
#
# Per service:
#   replicas    default instance count (hot-path services = 2, else 1). Override
#               per tenant via var.service_replicas; 0 disables the service.
#   port        container port (0 = no HTTP listener, e.g. workers)
#   public      true ⇒ routed by the ALB/Traefik on `path`; false ⇒ internal only
#   path        ALB/Traefik path prefix when public
locals {
  # NB: object keys are quoted — bare hyphenated keys parse as subtraction in HCL.
  service_catalogue = {
    # ── hot-path, run 2 by default ──
    "citra-service"     = { replicas = 2, port = 7001, public = true,  path = "/api" }
    "smart-app-service" = { replicas = 2, port = 9100, public = true,  path = "/smart-app" }
    "citra-app-runtime" = { replicas = 2, port = 3000, public = true,  path = "/apps" }
    "citra-workflow"    = { replicas = 2, port = 0,    public = false, path = "" }
    "citra-worker"      = { replicas = 2, port = 0,    public = false, path = "" }

    # ── singletons ──
    "citra-user-service"        = { replicas = 1, port = 7004, public = true,  path = "/api/auth" }
    "data-discovery-service"    = { replicas = 1, port = 9000, public = false, path = "" }
    "discovery-service"         = { replicas = 1, port = 8000, public = false, path = "" }
    "reranker-service"          = { replicas = 1, port = 7302, public = false, path = "" }
    "duckdb-query-service"      = { replicas = 1, port = 8100, public = false, path = "" }
    "citra-mcp-service"         = { replicas = 1, port = 8300, public = false, path = "" }
    "playwright-render-service" = { replicas = 1, port = 3001, public = false, path = "" }
    "monitoring-service"        = { replicas = 1, port = 9400, public = false, path = "" }
    "quick-chat-sandbox"        = { replicas = 1, port = 0,    public = false, path = "" }
    # KEPT (used by smart-app-service builder sandboxes) — NOT action-chat.
    "action-sandbox-host"       = { replicas = 1, port = 8500, public = false, path = "" }
  }

  # citra-ui served from the box only when enable_ui_on_box (else Firebase/CDN).
  ui_service = var.enable_ui_on_box ? {
    "citra-ui" = { replicas = 1, port = 80, public = true, path = "/" }
  } : {}

  _catalogue = merge(local.service_catalogue, local.ui_service)

  # Resolve replicas: catalogue default, overridden by var.service_replicas.
  # Services resolved to 0 are dropped entirely for this tenant.
  resolved_services = {
    for name, cfg in local._catalogue : name => merge(cfg, {
      replicas = lookup(var.service_replicas, name, cfg.replicas)
    }) if lookup(var.service_replicas, name, cfg.replicas) > 0
  }

  # Public (ALB/Traefik-routed) subset.
  public_services = {
    for name, cfg in local.resolved_services : name => cfg if cfg.public
  }
}
