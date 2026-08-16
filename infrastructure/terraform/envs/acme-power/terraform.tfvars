region          = "ap-south-1"
domain          = "acme.citra.ai"
route53_zone_id = "" # set when the hosted zone is delegated (enables ACM + HTTPS)
box_count       = 3  # 1 = demo single-box (SPOF); 2-3 = HA across AZs (prod)

# Per-service instance counts — overrides on top of the catalogue defaults in
# modules/citra-tenant/services.tf. Defaults already run the hot path at 2
# (citra-service, smart-app-service, citra-app-runtime, citra-workflow,
# citra-worker) and everything else at 1. Examples:
service_replicas = {
  # "reranker-service"   = 2   # bump for a heavy-RAG tenant
  # "citra-service"      = 3   # extra core capacity
  # "quick-chat-sandbox" = 0   # disable a service for this tenant
}
