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
# Everything that scrolls past is also written to a file. A first run is 20-40
# minutes of build output; when it fails at minute 30 the useful part has left
# the terminal's scrollback, and the operator is asked to paste something
# nobody can get back. Placed AFTER argument parsing so `--help` does not
# create a transcript, and before everything else so it captures all of it.
#
# CITRA_SETUP_LOG is exported, so setup.sh and start.sh called from here write
# into the SAME transcript rather than opening their own -- and still get one
# of their own when run directly.
#
# chmod is attempted and is a NO-OP on Windows, where this is most often run --
# the file lands 644 whatever we ask for. So the password is not written at
# all rather than written and hopefully protected: fd 3 is opened on the real
# terminal BEFORE stdout is teed, and secret_line() prints there only.
TTY_FD=""
if [ -z "${CITRA_SETUP_LOG:-}" ]; then
  mkdir -p "$REPO_ROOT/logs"
  CITRA_SETUP_LOG="$REPO_ROOT/logs/start-$(date +%Y%m%d-%H%M%S).log"
  : > "$CITRA_SETUP_LOG"
  chmod 600 "$CITRA_SETUP_LOG" 2>/dev/null || true
  export CITRA_SETUP_LOG
  exec 3>&1
  TTY_FD=3
  exec > >(tee -a "$CITRA_SETUP_LOG") 2>&1
  echo "Transcript: $CITRA_SETUP_LOG"
fi
# On screen, never in the transcript. A child script inherits an already-teed
# stdout and no fd 3, so it falls back -- only the wizard prints secrets.
secret_line() {
  if [ -n "$TTY_FD" ]; then printf '%s\n' "$*" >&3; else printf '%s\n' "$*"; fi
}

DEMO="acme-bank"
start_usage() {
  cat >&2 <<'USAGE'
Phase 2 - start every service, create the super-admin, optionally seed a demo.

  ./scripts/quickstart/start.sh [options]

Options:
  --demo <tenant>   Seed this demo tenant after start-up (default: acme-bank).
  --no-demo         Start the services and create the admin, seed nothing.
  -h, --help        Show this and exit.

Reads ADMIN_EMAIL, ADMIN_PASSWORD and ORG_ID from .env; run wizard.sh if they
are not set. Requires SOURCES_FILE for a non-demo install.
USAGE
}
# The case below had no `*)` arm, so an unrecognised flag was DISCARDED and the
# default applied: `--nodemo`, a plausible typo, silently seeded the demo --
# the exact opposite of what was asked for, with nothing printed.
while [ $# -gt 0 ]; do
  case "$1" in
    --no-demo)  DEMO="none" ;;
    --demo)     DEMO="${2:-acme-bank}"; shift ;;
    -h|--help)  start_usage; exit 0 ;;
    *)          echo "unknown option: $1" >&2; echo >&2; start_usage; exit 2 ;;
  esac
  shift
done

# `|| true` is load-bearing: these scripts run `set -euo pipefail`, so a grep
# that matches nothing returns 1 and kills the script at the assignment —
# before any ${VAR:-default} can apply. Every key read here happens to exist
# in .env.example today; the first one that does not would fail silently.
# setup.sh hit exactly that on MONGODB_USER.
getenv() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
# NO fallback. These used to default to admin@example.com / ChangeMe!123 --
# a real super-admin account, with a password published in this repository,
# created on any install that reached here with them unset. A guessable
# credential is worse than a stopped install, so this stops and says what to do.
ADMIN_EMAIL="$(getenv ADMIN_EMAIL)"
ADMIN_PASSWORD="$(getenv ADMIN_PASSWORD)"
if [ -z "$ADMIN_EMAIL" ] || [ -z "$ADMIN_PASSWORD" ]; then
  echo "[FAIL] ADMIN_EMAIL and ADMIN_PASSWORD must be set in .env." >&2
  echo "       They are the super-admin of this deployment, so there is no safe" >&2
  echo "       default: anything shipped here would be the same account, with the" >&2
  echo "       same password, on every install that never changed it." >&2
  echo "       Set both in .env, or run: ./scripts/quickstart/wizard.sh" >&2
  exit 1
fi

if [ -z "$(getenv LLM_API_KEY)" ]; then
  echo "[FAIL] LLM_API_KEY is empty in .env." >&2
  echo "       Decision Apps cannot produce a recommendation without a model." >&2
  echo "       Get an OpenRouter key at https://openrouter.ai/keys, set it in" >&2
  echo "       .env, then re-run. (Later, point the same variable at your own" >&2
  echo "       in-house vLLM endpoint - see docs/change-the-demo.md.)" >&2
  exit 1
fi

# -- 1. Start everything ------------------------------------------------------
# CITRA_COMPOSE_BUILD=1 adds --build. Set by `wizard.sh --fresh`, because
# `down -v` removes containers and volumes but NOT images, so a "start over from
# nothing" run came back up on whatever images happened to be on the machine.
#
# That is not only the two services whose source is baked in (citra-ui,
# citra-app-runtime -- the other ten bind-mount theirs). DEPENDENCIES live in
# the image for all twelve, so a changed requirements.txt or package.json is
# invisible to a bind mount: the new code runs against the old libraries, which
# fails further from the cause than a missing file would.
#
# Off by default. A plain `make start` is the everyday path and should not pay
# for a rebuild it does not need.
BUILD_FLAG=""
if [ "${CITRA_COMPOSE_BUILD:-0}" = "1" ]; then
  BUILD_FLAG="--build"
  echo "-> rebuilding images first (--fresh)"
fi
echo "-> starting all services (docker compose up -d${BUILD_FLAG:+ --build})"
# shellcheck disable=SC2086 # BUILD_FLAG is one optional token, not a list
$COMPOSE up -d $BUILD_FLAG

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
# --org matters. Without it the super-admin belongs to no organisation, so their
# Work Service Account is minted for the wrong org and the seeded Decision Apps
# -- owned by the publisher's Work SA in the DEMO org -- are invisible. Someone
# installing this signs in, sees "Nothing published to you", and has to discover
# impersonation to find the demo they just seeded.
#
# Impersonation still exists and is still right for an operator entering a
# customer's org. It should not be the first thing a self-hoster has to learn in
# order to see their own data.
ADMIN_ORG="$DEMO"
[ "$ADMIN_ORG" = "none" ] && ADMIN_ORG="$(getenv ORG_ID)"
# Also no fallback. This defaulted to `citra-ai` -- our organisation, created
# inside someone else's deployment, holding their super-admin.
if [ -z "$ADMIN_ORG" ]; then
  echo "[FAIL] ORG_ID is empty in .env, so there is no organisation to create" >&2
  echo "       the super-admin in. Set ORG_ID, or run the wizard." >&2
  exit 1
fi

# Seeding a demo puts the super-admin in the DEMO org, not the ORG_ID the
# operator was asked for -- deliberately, per the note above, so the seeded
# apps are visible on first sign-in. It was not SAID anywhere, so someone who
# typed their own org and accepted the demo found their admin somewhere else
# with no explanation. The banner below now reports it.
ORG_NOTE=""
_WANTED_ORG="$(getenv ORG_ID)"
if [ -n "$_WANTED_ORG" ] && [ "$_WANTED_ORG" != "$ADMIN_ORG" ]; then
  ORG_NOTE="   (the demo org; your ORG_ID $_WANTED_ORG is unused until you seed it)"
fi
$COMPOSE exec -T citra-user-service \
  node src/scripts/create-admin.js "$ADMIN_EMAIL" "$ADMIN_PASSWORD" "${ADMIN_EMAIL%%@*}" \
       --role=super_admin --org="$ADMIN_ORG"

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
   Admin org     ${ADMIN_ORG}${ORG_NOTE}
   ${DEMO_NOTE}

   Your own data   docs/change-the-demo.md   (edit sources.json, restart, done)
   Validate first  make validate-sources FILE=path/to/sources.json
   Guide           README.md  (Quickstart, Configuration, Troubleshooting)
----------------------------------------------------------------------------
EOF
