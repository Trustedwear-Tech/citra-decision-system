#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

#
# PHASE 2 - START. Brings up the full platform and seeds it:
#   1. docker compose up -d   (14 application services)
#   2. wait for the core services to answer /health
#   3. Milvus vector-search collection
#   4. sandbox / builder images (spawned at runtime, not by compose)
#   5. super-admin
#   6. the acme-bank demo
#
# Run AFTER ./scripts/quickstart/setup.sh. Idempotent.
# Prereqs on host: docker, curl, python3.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
[ -f .env ] || { echo "No .env - run ./scripts/quickstart/setup.sh first." >&2; exit 1; }

COMPOSE="docker compose -f docker-compose.quickstart.yml"

DEMO="acme-bank"
case "${1:-}" in
  --no-demo) DEMO="none" ;;
  --demo)    DEMO="${2:-acme-bank}" ;;
esac

# `|| true` is load-bearing: these scripts run `set -euo pipefail`, so a grep
# that matches nothing returns 1 and kills the script at the assignment —
# before any ${VAR:-default} can apply. Every key read here happens to exist
# in .env.example today; the first one that does not would fail silently.
# setup.sh hit exactly that on MONGODB_USER.
getenv() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
ADMIN_EMAIL="$(getenv ADMIN_EMAIL)";       ADMIN_EMAIL="${ADMIN_EMAIL:-admin@citra-ai.com}"
ADMIN_PASSWORD="$(getenv ADMIN_PASSWORD)"; ADMIN_PASSWORD="${ADMIN_PASSWORD:-ChangeMe!123}"

if [ -z "$(getenv LLM_API_KEY)" ]; then
  echo "[FAIL] LLM_API_KEY is empty in .env." >&2
  echo "       Decision Apps cannot produce a recommendation without a model." >&2
  echo "       Get an OpenRouter key at https://openrouter.ai/keys, set it in" >&2
  echo "       .env, then re-run. (Later, point the same variable at your own" >&2
  echo "       in-house vLLM endpoint - see docs/change-the-demo.md.)" >&2
  exit 1
fi

# -- 1. Start everything ------------------------------------------------------
echo "-> starting all services (docker compose up -d)"
$COMPOSE up -d

# -- 2. Wait for the core services --------------------------------------------
wait_for() {
  printf "%s" "-> waiting for $1 "
  for _ in $(seq 1 60); do
    if curl -fsS "$2" >/dev/null 2>&1; then echo "OK"; return 0; fi
    printf "."; sleep 5
  done
  echo " TIMEOUT"
  echo "   $1 not healthy at $2 - check: $COMPOSE logs $1" >&2
  exit 1
}
wait_for "citra-user-service"    "http://localhost:7004/health"
wait_for "smart-app-service"     "http://localhost:9100/health"
wait_for "citra-service"         "http://localhost:8085/health"
wait_for "discovery-service"     "http://localhost:9010/health"
wait_for "data-discovery-service" "http://localhost:8095/health"

# -- 3. Milvus vector-search collection ---------------------------------------
# citra-service does NOT auto-create its Milvus collection; without it, document
# semantic search degrades to Mongo-only. The helper exits non-zero when the
# collection already exists, so parse its output rather than the exit code.
echo "-> ensuring the Milvus vector-search collection"
milvus_out="$($COMPOSE exec -T citra-service python scripts/setup_milvus_schema.py 2>&1 || true)"
if printf '%s' "$milvus_out" | grep -qiE "Schema setup successful"; then
  $COMPOSE restart citra-service >/dev/null 2>&1 || true
  echo "   [ok] Milvus collection created"
elif printf '%s' "$milvus_out" | grep -qiE "already exists"; then
  echo "   [ok] Milvus collection already present"
else
  echo "   [FAIL] could not create the Milvus collection. SOP retrieval will be" >&2
  echo "          Mongo-only, so grounded answers will be wrong rather than absent." >&2
  printf '%s\n' "$milvus_out" | tail -5 >&2
  exit 1
fi

# -- 4. Sandbox / builder images (spawned at runtime, NOT by compose) ----------
# action-sandbox-host spawns citra-app-builder (the Decision App builder);
# citra-service spawns quick-chat-sandbox for tools_v2 kind=code_exec. The image
# is still named quick-chat-sandbox after the Quick Chat surface was removed -
# the pool outlived the feature that named it.
if ! docker image inspect citra-app-builder:latest quick-chat-sandbox:latest >/dev/null 2>&1; then
  echo "-> building sandbox images (app-builder, code-exec) - first run pulls the base, several minutes"
  "$REPO_ROOT/scripts/quickstart/build-sandboxes.sh" \
    || echo "   [!] sandbox build had errors - the app BUILDER and code-exec will not work until you re-run scripts/quickstart/build-sandboxes.sh (running Decision Apps is unaffected)"
else
  echo "-> sandbox images present"
fi

# -- 5. Super-admin -----------------------------------------------------------
echo "-> creating super-admin $ADMIN_EMAIL"
$COMPOSE exec -T citra-user-service \
  node src/scripts/create-admin.js "$ADMIN_EMAIL" "$ADMIN_PASSWORD" "Citra Admin" --role=super_admin

# -- 6. Demo tenant -----------------------------------------------------------
if [ "$DEMO" != "none" ]; then
  echo "-> seeding the $DEMO demo"
  "$REPO_ROOT/scripts/quickstart/seed-demo.sh" "$DEMO"
  DEMO_NOTE="Demo org      ${DEMO}  (sign in as admin, or dev-login as a persona)"
else
  echo "-> no demo seeded"
  DEMO_NOTE="Your data     see docs/change-the-demo.md to point the MCP at your own source"
fi

cat <<EOF

----------------------------------------------------------------------------
Citra Decision System is running.

   Web UI        http://localhost:8081
   Sign in       ${ADMIN_EMAIL}  /  ${ADMIN_PASSWORD}
   ${DEMO_NOTE}

   Your own data   docs/change-the-demo.md   (edit sources.json, restart, done)
   Validate first  make validate-sources FILE=path/to/sources.json
   Guide           README.md  (Quickstart, Configuration, Troubleshooting)
----------------------------------------------------------------------------
EOF
