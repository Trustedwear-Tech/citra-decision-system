#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

#
# Seed one demo tenant end-to-end against the running local stack:
#   org + users + departments -> Postgres data -> demo MCP container
#   -> RAG documents -> data catalogue -> published Decision Apps.
#
# NOTE ON THE SOURCE REGISTRY (changed 2026-07-10):
#   The June 2026 draft of this script ran `mongoimport` to push
#   tenants/<t>/mcp/sources.json into the Mongo `dept_sources` collection. That
#   step is GONE. The MCP is now FILE-DEFINED: the same sources.json is mounted
#   read-only at /app/sources.json and read via SOURCES_FILE. The central-Mongo
#   load mode was REMOVED from source-mcp-template/config.py, not deprecated -
#   the MCP now refuses to boot without SOURCES_FILE or SOURCES_JSON. Re-adding
#   a mongoimport here would write a collection nothing reads.
#   See docs/change-the-demo.md.
#
# Prereqs on host: docker, curl, python3 (a venv is created automatically).
# The main stack must already be up:  make start
#
# Usage:  ./scripts/quickstart/seed-demo.sh acme-bank
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RED="$(printf '\033[31m')"; C_AMB="$(printf '\033[33m')"; C_OFF="$(printf '\033[0m')"
else
  C_RED=""; C_AMB=""; C_OFF=""
fi
red()   { printf '%s%s%s\n' "$C_RED" "$*" "$C_OFF" >&2; }
amber() { printf '%s%s%s\n' "$C_AMB" "$*" "$C_OFF" >&2; }

seed_usage() {
  cat >&2 <<'USAGE'
Re-seed a demo tenant: its Postgres system of record, SOP corpus and apps.

  ./scripts/quickstart/seed-demo.sh [tenant]

  tenant       Directory name under demo-data/tenants/ (default: acme-bank).
  -h, --help   Show this and exit.

Destructive for that tenant's seeded data; other tenants are untouched.
USAGE
}
case "${1:-}" in
  -h|--help) seed_usage; exit 0 ;;
  -*)        echo "unknown option: $1" >&2; echo >&2; seed_usage; exit 2 ;;
esac
TENANT="${1:-acme-bank}"
COMPOSE="docker compose -f docker-compose.quickstart.yml"

# -- Per-tenant Postgres wiring -----------------------------------------------
# The system of record is the tenant's OWN Postgres, brought up by
# demo-data/tenants/<tenant>/mcp/docker-compose.yml — NOT the shared
# citra-postgres on 5432. The MCP connector reads ACME_BANK_SQL_HOST from that
# compose file and resolves it to that container, so seeding 5432 put the 16
# tables somewhere nothing ever reads: every tool call came back
#   (psycopg2.errors.UndefinedTable) relation "customers" does not exist
# and the agent correctly refused to decide rather than invent an answer.
#
# The host port comes from ACME_BANK_PG_PORT in .env rather than a literal.
# The private and public trees publish that container on different ports (and
# under different container names), so any literal here is wrong in one of
# them. This script stays byte-identical across both repos; the deployment
# value lives in .env, which is where the trees are allowed to differ.
case "$TENANT" in
  acme-bank) PG_ENV="ACME_BANK_PG_CONN" ;;
  *) echo "Unknown tenant '$TENANT' (this tree ships: acme-bank)" >&2; exit 1 ;;
esac

TENANT_DIR="demo-data/tenants/$TENANT"
[ -d "$TENANT_DIR" ] || { echo "Tenant dir not found: $TENANT_DIR" >&2; exit 1; }

# `|| true` is load-bearing: these scripts run `set -euo pipefail`, so a grep
# that matches nothing returns 1 and kills the script at the assignment —
# before any ${VAR:-default} can apply. Every key read here happens to exist
# in .env.example today; the first one that does not would fail silently.
# setup.sh hit exactly that on MONGODB_USER.
getenv() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
JWT_SECRET="$(getenv JWT_SECRET)"
ADMIN_EMAIL="$(getenv ADMIN_EMAIL)"; ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
[ -n "$JWT_SECRET" ] || { echo "JWT_SECRET missing from .env" >&2; exit 1; }

# Read the Postgres port through getenv, NOT as a shell variable. ${VAR:?...}
# expands a variable in the ENVIRONMENT, and this script never sources .env —
# so a value sitting correctly in .env still read as unset and the seed aborted
# with "ACME_BANK_PG_PORT: set ACME_BANK_PG_PORT in .env", pointing at the file
# that already had it. Fails loud, but on the real condition.
PG_PORT="$(getenv ACME_BANK_PG_PORT)"
[ -n "$PG_PORT" ] || {
  echo "ACME_BANK_PG_PORT missing from .env - it is the host port that" >&2
  echo "demo-data/tenants/$TENANT/mcp/docker-compose.yml publishes for the" >&2
  echo "tenant's Postgres. Add it (15444 in this tree) and re-run." >&2
  exit 1
}
PG_CONN="postgresql://acme_bank:acme_bank_demo_pw@localhost:${PG_PORT}/acme_bank"

# -- Python venv with the seed-script deps (cross-platform) -------------------
PYBIN="$(command -v python3 || command -v python || true)"
[ -n "$PYBIN" ] || { echo "python3/python not found on PATH - install Python 3." >&2; exit 1; }
VENV="$REPO_ROOT/.venv-seed"
# The DIRECTORY existing is not the same as the venv working. A run that died
# partway -- most commonly `python3 -m venv` failing on Debian for want of
# python3-venv -- leaves a .venv-seed with a bin/python in it and no pip, and
# testing only for the directory then reuses that corpse on every retry:
#
#   .venv-seed/bin/python: No module named pip
#
# which reads like a seeding bug rather than leftover state from the failure
# before it. Test for a working pip, and rebuild when it is not there.
venv_ok() {
  local py
  if   [ -x "$VENV/bin/python" ];      then py="$VENV/bin/python"
  elif [ -x "$VENV/Scripts/python" ];  then py="$VENV/Scripts/python"
  else return 1; fi
  "$py" -m pip --version >/dev/null 2>&1
}
if ! venv_ok; then
  [ -e "$VENV" ] && { echo "-> discarding an unusable seed venv ($VENV)"; rm -rf "$VENV"; }
  echo "-> creating seed venv ($VENV)"
  "$PYBIN" -m venv "$VENV" || {
    echo "   Could not create a virtualenv with $PYBIN." >&2
    echo "   Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  }
fi
if [ -d "$VENV/bin" ]; then PY="$VENV/bin/python"; else PY="$VENV/Scripts/python"; fi
"$PY" -m pip -q install --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip -q install requests pyjwt faker "psycopg2-binary>=2.9" "pymilvus>=2.4" openai boto3 >/dev/null 2>&1 || \
  "$PY" -m pip install requests pyjwt faker "psycopg2-binary>=2.9" "pymilvus>=2.4" openai boto3

# -- 0. Validate the source registry BEFORE anything else ---------------------
# RegistrySource is extra="forbid": one unknown key and the MCP hard-fails at
# boot, several minutes into the seed. Catch it here instead.
echo "-> [0/6] validating $TENANT_DIR/mcp/sources.json"
"$PY" source-mcp-template/validate_sources.py "$TENANT_DIR/mcp/sources.json" \
  || { echo "   [FAIL] sources.json is invalid - the MCP would refuse to boot." >&2; exit 1; }

# -- Mint a super-admin JWT for the seed_tenant admin API ---------------------
echo "-> minting super-admin token"
# tr -d '\r': on Windows $PY is the venv's Scripts/python, which
# writes CRLF. Command substitution strips the trailing newline but leaves
# the CR -- which turns a slug into a malformed URL and puts a stray CR in
# the Authorization header.
ADMIN_JWT="$("$PY" - "$JWT_SECRET" "$ADMIN_EMAIL" <<'PYEOF' | tr -d '\r'
import sys, time, jwt
secret, email = sys.argv[1], sys.argv[2]
print(jwt.encode({
    "user_id": email, "email": email, "org_id": "citra-ai",
    "roles": ["super_admin"], "dept_ids": [],
    "iss": "Citra-AI", "iat": int(time.time()), "exp": int(time.time()) + 3600,
}, secret, algorithm="HS256"))
PYEOF
)"

# -- 1. Org + departments + demo users ----------------------------------------
echo "-> [1/6] seeding org + users ($TENANT)"
"$PY" demo-data/scripts/seed_tenant.py --tenant "$TENANT" \
    --admin-token "$ADMIN_JWT" --user-service-url http://localhost:7004

# -- 2. Postgres system-of-record data ----------------------------------------
# The tenant's Postgres is defined in the MCP compose file, which step 3 brings
# up -- so it has to be started HERE, before anything tries to write to it.
# Previously this step pointed at the SHARED citra-postgres, which setup.sh has
# already started, so the ordering was never exercised; correcting the target to
# the tenant's own database exposed it as "connection refused on 15444".
echo "-> [2/6] starting $TENANT's Postgres, then seeding it"
PG_SVC="citra-ds-$TENANT-postgres"
docker compose --env-file "$REPO_ROOT/.env" -f "$TENANT_DIR/mcp/docker-compose.yml" up -d "$PG_SVC"

PG_READY=false
for _ in $(seq 1 30); do
  if docker compose --env-file "$REPO_ROOT/.env" -f "$TENANT_DIR/mcp/docker-compose.yml"        exec -T "$PG_SVC" pg_isready -U acme_bank -d acme_bank >/dev/null 2>&1; then
    PG_READY=true; break
  fi
  sleep 2
done
[ "$PG_READY" = true ] || {
  echo "   [FAIL] $PG_SVC did not accept connections within 60s." >&2
  echo "          Check: docker compose -f $TENANT_DIR/mcp/docker-compose.yml logs $PG_SVC" >&2
  exit 1
}

echo "   seeding ($PG_CONN)"
env "$PG_ENV=$PG_CONN" "$PY" "$TENANT_DIR/scripts/seed_postgres.py" --conn "$PG_CONN"

# -- 3. Demo MCP container (registers itself with discovery-service) ----------
# --env-file points compose's ${VAR} interpolation at the ROOT .env, so the AI
# keys (LLM_API_KEY, EMBEDDING_*) actually reach the MCP's NL->SQL planner.
# Without it the environment: block's :-defaults win over env_file and the
# user's key never lands in the container.
echo "-> [3/6] starting the demo MCP container (reads sources.json via SOURCES_FILE)"
docker compose --env-file "$REPO_ROOT/.env" -f "$TENANT_DIR/mcp/docker-compose.yml" up -d --build

echo "-> waiting for the MCP to register its sources with discovery"
# "Dept MCP ready" is the unconditional startup banner - it logs even when
# every registration call below it failed, so it is not evidence of success.
# The real per-tool marker is "[REGISTRATION] Registered tool: ..."; a
# "Failed to register" anywhere means the batch is not actually done
# (confirmed live: a cross-network DNS failure logged "ready" while every
# registration failed, and the catalogue silently stayed at 0 datasets).
registered=0
for _ in $(seq 1 40); do
  logs="$(docker compose --env-file "$REPO_ROOT/.env" -f "$TENANT_DIR/mcp/docker-compose.yml" logs 2>&1)"
  if printf '%s' "$logs" | grep -qE "\[REGISTRATION\] Registered tool:" \
       && ! printf '%s' "$logs" | grep -qE "\[REGISTRATION\] Failed to register"; then
    echo "   [ok] MCP sources registered"; registered=1; break
  fi
  sleep 2
done
if [ "$registered" != 1 ]; then
  echo "   [FAIL] the MCP did not register within 80s." >&2
  echo "          Publishing now would fail 'source_not_found' for every app." >&2
  echo "          Check: docker compose -f $TENANT_DIR/mcp/docker-compose.yml logs" >&2
  exit 1
fi

# -- 4. Ingest the tenant's RAG documents (SOP library) into Milvus -----------
if [ -f "$TENANT_DIR/scripts/ingest_docs.py" ] && [ -d "$TENANT_DIR/raw" ]; then
  echo "-> [4/6] ingesting SOP documents into Milvus"
  # Runs INSIDE citra-service, not in the host seed venv. ingest_docs.py imports
  # Citra-Service application code (dept_library_store -> utils), which pulls in
  # fastapi, llama_index and the rest of that service's dependency tree — the
  # seed venv installs none of it, so on a fresh clone this step died with
  # ModuleNotFoundError and RAG grounding was silently absent from every demo.
  # Installing that tree into the venv is whack-a-mole; the container already
  # has it, and reaches Milvus and MinIO by service name.
  #
  # The copy target matters: ingest_docs.py resolves REPO_ROOT as parents[4] of
  # its own path, so landing it at /app/demo-data/tenants/<t>/scripts/ makes
  # that resolve to /app, where /app/Citra-Service is — the script's path math
  # then works unchanged.
  SVC_CID="$($COMPOSE ps -q citra-service 2>/dev/null || true)"
  if [ -z "$SVC_CID" ]; then
    echo "   [!] citra-service is not running - skipping SOP ingestion."
  else
    # MSYS_NO_PATHCONV stops Git Bash on Windows rewriting /app/... into a
    # Windows path before docker sees it. Harmless elsewhere.
    MSYS_NO_PATHCONV=1 docker exec "$SVC_CID" mkdir -p /app/demo-data/tenants
    # Remove the target first. `docker cp SRC CID:DEST` copies SRC *into* DEST
    # when DEST already exists, so a second run lands the tree at
    # .../acme-bank/acme-bank/ and leaves the FIRST run's copy in place -- the
    # command below then executes a stale ingest_docs.py and reports success.
    # Silent on a fresh install, silent on re-run, wrong only in what it ran.
    MSYS_NO_PATHCONV=1 docker exec "$SVC_CID" rm -rf "/app/demo-data/tenants/$TENANT"
    MSYS_NO_PATHCONV=1 docker cp "$TENANT_DIR" "$SVC_CID:/app/demo-data/tenants/$TENANT"
    MSYS_NO_PATHCONV=1 docker exec -w /app/Citra-Service "$SVC_CID" \
      python "/app/demo-data/tenants/$TENANT/scripts/ingest_docs.py" \
      || echo "   [!] SOP ingestion failed - RAG answers will be ungrounded."
  fi
fi

# -- 5. Build the data catalogue ----------------------------------------------
# data-discovery runs a leader-gated crawl at startup, so the catalogue is
# usually already populated by now. This is the explicit refresh.
echo "-> [5/7] refreshing the data catalogue"
# STOPS on failure. This used to warn and carry on, and every step after it was
# then guaranteed to fail: publishing validates each data_source.ref against the
# catalogue, so an empty catalogue rejects all four apps with
#   E_UNKNOWN_DATASET ... ref does not match any catalogue dataset
# The operator saw four 422s and "published=0 / 4" -- a plausible-looking app
# problem -- while the real cause was one warning far above. A step whose
# failure invalidates everything downstream must not be survivable.
if ! JWT_SECRET="$JWT_SECRET" "$PY" scripts/quickstart/build_catalogue.py --org "$TENANT"; then
  red "[FAIL] the catalogue crawl failed, so nothing can be published."
  echo "       Every app binds its data sources to catalogue datasets; with an" >&2
  echo "       empty catalogue all four would be rejected as E_UNKNOWN_DATASET." >&2
  echo >&2
  echo "       Usually data-discovery-service was not reachable. Check it, then" >&2
  echo "       re-run this step alone and the seed will continue from here:" >&2
  echo "         docker ps | grep data-discovery" >&2
  echo "         JWT_SECRET=... $PY scripts/quickstart/build_catalogue.py --org $TENANT" >&2
  echo "         ./scripts/quickstart/seed-demo.sh $TENANT" >&2
  exit 1
fi

# -- 6. Publish the Decision Apps ---------------------------------------------
echo "-> [6/7] publishing Decision Apps"
"$PY" demo-data/scripts/publish_apps.py \
    --smart-app-url http://localhost:9100 \
    --jwt-secret "$JWT_SECRET" \
    --tenant-id "$TENANT" \
    --apps-dir "$TENANT_DIR/apps" \
    --user-id "$ADMIN_EMAIL"

# -- 7. Promote them to prod --------------------------------------------------
# publish_apps.py lands apps in the TEST environment, which is right for the
# builder (it always runs in test). The UI's default lenses read PROD, so
# without this the seed reports "published=4 / 4" and the user signs in to
# "Nothing published to you" -- and has to discover impersonation or the test
# lens to find the demo they just installed.
#
# Promotion can legitimately refuse: an app that learns from historical
# decisions answers grounding_refresh_required until its grounding is rebuilt.
# On a fresh install there is nothing to rebuild it FROM, so that answer
# is retried with promote_ungrounded.
echo "-> [7/7] promoting the Decision Apps to prod"
PROMOTE_JWT="$("$PY" - "$JWT_SECRET" "$ADMIN_EMAIL" "$TENANT" <<'PYEOF' | tr -d '\r'
import sys, time, jwt
secret, email, tenant = sys.argv[1], sys.argv[2], sys.argv[3]
now = int(time.time())
print(jwt.encode({
    "sub": email, "user_id": email, "email": email,
    "org_id": tenant, "tenant_id": tenant,
    "roles": ["super_admin", "org_admin"], "scope": "smart-app-builder",
    "iss": "Citra-AI", "iat": now, "exp": now + 3600,
}, secret, algorithm="HS256"))
PYEOF
)"
for slug in $("$PY" - "$TENANT_DIR/apps" <<'PYEOF' | tr -d '\r'
import json, pathlib, sys
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    print(json.loads(f.read_text(encoding="utf-8"))["app_spec"]["slug"])
PYEOF
); do
  promote() {
    curl -s -m 60 -X POST \
         -H "Authorization: Bearer $PROMOTE_JWT" \
         -H "Content-Type: application/json" -d "$1" \
         "http://localhost:9100/apps/$slug/promote-to-prod" || true
  }
  body="$(promote '{}')"
  case "$body" in
    *grounding_refresh_required*)
      # The app declares it learns from historical decisions, but a
      # fresh install has none yet -- the refresh would load an empty
      # set. Ship it ungrounded so the demo is visible at all; it runs
      # on its base prompt and grounds itself from the decisions made
      # in the demo.
      body="$(promote '{"promote_ungrounded": true}')" ;;
  esac
  case "$body" in
    *'"prod_url"'*)  echo "   [ok] $slug" ;;
    *)               red "   [!!] $slug did not promote: $(printf '%s' "$body" | head -c 160)" ;;
  esac
done

echo ""
echo "Demo '$TENANT' seeded. Sign in at http://localhost:8081 as $ADMIN_EMAIL"
echo "and its Decision Apps are on your home screen -- no impersonation needed."
