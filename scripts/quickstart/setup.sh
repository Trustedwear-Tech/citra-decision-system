#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

#
# PHASE 1 - SETUP. Prepares the local environment so the platform can start:
#   1. generate .env from .env.example with fresh random secrets
#   2. bring up the data stores only (Mongo, Redis x2, Milvus, MinIO, Postgres)
#   3. create the DB-side resources (replica set, acme_bank database, bucket)
#
# Idempotent - safe to re-run. After this, run:  ./scripts/quickstart/start.sh
# Prereqs on host: docker, curl, openssl (optional).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
ENV_FILE="$REPO_ROOT/.env"

setup_usage() {
  cat >&2 <<'USAGE'
Phase 1 - generate .env, start the data stores, create database resources.

  ./scripts/quickstart/setup.sh [options]

Options:
  -h, --help   Show this and exit.

Takes no other arguments. For a guided first run use wizard.sh, which calls
this; for phase 2 use start.sh.
USAGE
}
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) setup_usage; exit 0 ;;
    *)         echo "unknown option: $1" >&2; echo >&2; setup_usage; exit 2 ;;
  esac
  shift
done
COMPOSE="docker compose -f docker-compose.quickstart.yml"

# Check the host BEFORE writing .env. Without this the first failure was
# "docker: command not found" from line 77, after secrets had been generated.
. "$REPO_ROOT/scripts/quickstart/preflight.sh"
preflight || exit 1

# citra-network is created by docker-compose.infra.yml (name: pinned, not
# external), so nothing needs to pre-create it here. It used to be declared
# external and created by hand at this point — see the infra networks block.

# Read one key from .env, tolerating absence.
#
# NOT `VAR="$(grep ... | cut ...)"`. Under `set -euo pipefail` a grep that
# matches nothing returns 1, the assignment inherits that status, and the script
# exits — BEFORE the ${VAR:-default} on the next line can supply the fallback.
# The whole point of those defaults is that the key may be missing, so the
# pattern defeated itself. MONGODB_USER is in neither .env nor .env.example, so
# every clean install died silently right after "waiting for Mongo", with no
# message and exit 1.
envget() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true; }

rand() { openssl rand -hex "$1" 2>/dev/null || head -c "$((${1}*2))" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
setkv() { # key value file - replace KEY=... line in place (value may contain anything)
  local k="$1" v="$2" f="$3"
  if grep -qE "^$k=" "$f"; then
    awk -v k="$k" -v v="$v" 'BEGIN{FS="="} $1==k{print k"="v; next} {print}' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  else
    printf '\n%s=%s\n' "$k" "$v" >> "$f"
  fi
}

# -- 1. Generate .env with fresh secrets --------------------------------------
if [ -f "$ENV_FILE" ]; then
  echo "-> .env already exists - keeping it (delete it to regenerate)."
else
  echo "-> generating .env from .env.example with fresh secrets"
  cp .env.example "$ENV_FILE"
  MCP_KEY="demo-mcp-$(rand 8)"
  setkv JWT_SECRET "$(rand 48)" "$ENV_FILE"
  setkv MCP_API_KEY "$MCP_KEY" "$ENV_FILE"
  # smart-app -> MCP auth: MUST equal MCP_API_KEY. When these drift, the
  # catalogue crawler sends the caller's user token as the service guard, the
  # MCP answers 401, and the catalogue stays SILENTLY empty.
  setkv MCP_SERVICE_API_KEY "$MCP_KEY" "$ENV_FILE"
  setkv SMART_APP_INTERNAL_SIGNING_KEY "$(rand 32)" "$ENV_FILE"
  setkv CONNECTION_ENCRYPTION_KEY "$(rand 32)" "$ENV_FILE"
  setkv ADMIN_PASSWORD "$(rand 6)" "$ENV_FILE"   # printed by start.sh
  # Was shipped as `citra-local-admin-key-change-me` in .env.example -- a
  # credential published in a public repository, identical on every install
  # that never read that line. Per install, like every other secret here.
  setkv ADMIN_API_KEY "$(rand 24)" "$ENV_FILE"
  echo "   [ok] secrets generated (JWT, MCP key + service key, signing + connection keys, admin pw)"
  echo "   [!]  set LLM_API_KEY in .env before start.sh - recommendations need it"
fi

# -- 2. Bring up the data stores only -----------------------------------------
echo "-> starting data stores (Mongo, Redis x2, Milvus, MinIO, Postgres)"
# mongodb-init-rs MUST be named explicitly. `up -d mongodb` starts what mongodb
# depends ON, not what depends on IT, so the one-shot rs.initiate() container
# never runs and the replica set never forms -- every later Mongo call then
# hangs until this script's own 180s timeout expires and it exits 1.
DATA_STORES="mongodb mongodb-init-rs redis queue-redis milvus-etcd milvus-minio milvus minio postgres"
$COMPOSE up -d $DATA_STORES

# A container whose networking setup FAILED (most often "port is already
# allocated", from a second Citra stack publishing the same host port) is left
# half-created: running, but attached to no network and publishing nothing.
# The next `up -d` sees a container matching the config hash and merely STARTS
# it, so the breakage survives a re-run and surfaces three steps later as a
# baffling DNS error ("lookup citra-minio ... no such host"). Detect it here,
# where the cause is still visible, and force a clean recreate.
for svc in $DATA_STORES; do
  cid="$($COMPOSE ps -q "$svc" 2>/dev/null || true)"
  [ -z "$cid" ] && continue
  # mongodb-init-rs is a one-shot that exits; it has no lasting network.
  [ "$svc" = "mongodb-init-rs" ] && continue
  nets="$(docker inspect "$cid" --format '{{len .NetworkSettings.Networks}}' 2>/dev/null || echo 0)"
  if [ "$nets" = "0" ]; then
    echo "   [!] $svc came up attached to no network (its networking setup failed);"
    echo "       recreating it -- if this repeats, another stack holds one of its ports."
    $COMPOSE rm -fs "$svc" >/dev/null 2>&1 || true
    $COMPOSE up -d "$svc"
  fi
done

# -- 3. Wait for the stateful stores, then create resources -------------------
echo "-> waiting for Mongo (replica set) + Postgres"
MONGO_PW="$(envget MONGODB_PASSWORD)"; MONGO_PW="${MONGO_PW:-citradev}"
MONGO_USER="$(envget MONGODB_USER)"; MONGO_USER="${MONGO_USER:-root}"
MONGO_OK=false
for _ in $(seq 1 60); do
  # rs.status() requires auth once the root user exists - authenticate, or this
  # check never confirms and silently burns the full timeout on every run.
  # Capture the output rather than discarding it: an auth failure is a
  # DIFFERENT problem from a replica set that has not formed yet, and only the
  # output tells them apart.
  out="$($COMPOSE exec -T mongodb mongosh --quiet -u "$MONGO_USER" -p "$MONGO_PW" \
          --authenticationDatabase admin --eval "rs.status().ok" 2>&1 || true)"
  case "$out" in
    *1*) case "$out" in *Error*) ;; *) echo "   [ok] Mongo replica set ready"; MONGO_OK=true; break ;; esac ;;
  esac
  case "$out" in
    *"Authentication failed"*)
      # MONGO_INITDB_ROOT_PASSWORD is honoured ONLY when mongod initialises an
      # EMPTY data directory. A mongodb_data volume left over from an earlier
      # install keeps its ORIGINAL root password, so the fresh one this script
      # just generated into .env is silently ignored. Waiting the full 180s and
      # then blaming the replica set points at the wrong thing entirely -- the
      # replica set is fine; the credentials do not match.
      echo "   [FAIL] Mongo rejected the password in .env." >&2
      echo "          A data volume from an earlier install is still present, and" >&2
      echo "          it keeps the root password it was FIRST created with -- the" >&2
      echo "          value generated into .env just now is ignored." >&2
      echo "          Either wipe it and start clean:" >&2
      echo "              $COMPOSE down -v" >&2
      echo "          or set MONGODB_PASSWORD in .env back to the original value." >&2
      exit 1 ;;
  esac
  sleep 3
done
if [ "$MONGO_OK" != true ]; then
  echo "   [FAIL] Mongo replica set did not come up within 180s." >&2
  echo "          Check: $COMPOSE logs mongodb" >&2
  exit 1
fi

PG_USER="$(envget POSTGRES_USER)"; PG_USER="${PG_USER:-citra}"
PG_OK=false
for _ in $(seq 1 40); do
  if $COMPOSE exec -T postgres pg_isready -U "$PG_USER" >/dev/null 2>&1; then
    echo "   [ok] Postgres ready (acme_bank database created by the init script)"; PG_OK=true; break
  fi
  sleep 3
done
if [ "$PG_OK" != true ]; then
  echo "   [FAIL] Postgres did not become ready within 120s." >&2
  echo "          Check: $COMPOSE logs postgres" >&2
  exit 1
fi

BUCKET_NAME="$(envget BUCKET_NAME)"; BUCKET_NAME="${BUCKET_NAME:-citra-documents}"
echo "-> creating MinIO bucket '$BUCKET_NAME'"
# Port 9000, not 9002. This runs INSIDE citra-network, so it must use the
# container's own listening port -- MinIO serves its API on 9000 and the
# compose file publishes that to 9002 on the HOST. Using the host-side
# number here fails with "connection refused" and the bucket is never made.
if ! docker run --rm --network citra-network --entrypoint sh minio/mc:latest -c \
  "mc alias set local http://citra-minio:9000 minioadmin minioadmin >/dev/null 2>&1 && \
   mc mb -p local/$BUCKET_NAME >/dev/null 2>&1"; then
  echo "   [FAIL] could not create the MinIO bucket '$BUCKET_NAME'." >&2
  echo "          Every document upload will fail until it exists. Create it in" >&2
  echo "          the MinIO console at http://localhost:9001 (minioadmin/minioadmin)," >&2
  echo "          then re-run this script." >&2
  exit 1
fi
echo "   [ok] bucket '$BUCKET_NAME' present"

cat <<EOF

----------------------------------------------------------------------------
Setup complete. Data stores are up and initialised.

   Next:  set LLM_API_KEY in .env, then run  ./scripts/quickstart/start.sh
          (or just: make start)
----------------------------------------------------------------------------
EOF
