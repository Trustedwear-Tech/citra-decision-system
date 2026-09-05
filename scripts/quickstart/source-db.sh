#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
#
# ─────────────────────────────────────────────────────────────────────────
# Stand up a database that Citra did NOT install, to test "connect my own
# database" against.
#
# It carries the acme-bank generator's data because that data is already
# realistic and FK-consistent -- but in its own container, on its own port,
# under its own database name, in its own compose project. The demo tenant's
# Postgres is untouched and the two never meet, so `--fresh` on either leaves
# the other alone.
#
#   ./scripts/quickstart/source-db.sh              up, seed if empty, print DSN
#   ./scripts/quickstart/source-db.sh --fresh      destroy and rebuild from zero
#   ./scripts/quickstart/source-db.sh --down       stop, keep the data
#   ./scripts/quickstart/source-db.sh --dsn        print the DSN and nothing else
#
# Override any of SOURCE_DB_NAME / _USER / _PASSWORD / _PORT / _CONTAINER to
# run more than one.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$REPO_ROOT"
COMPOSE_FILE="$REPO_ROOT/demo-data/source-db/docker-compose.yml"
SEEDER="$REPO_ROOT/demo-data/tenants/acme-bank/scripts/seed_postgres.py"

DB_NAME="${SOURCE_DB_NAME:-northwind}"
DB_USER="${SOURCE_DB_USER:-northwind}"
DB_PASS="${SOURCE_DB_PASSWORD:-northwind_demo_pw}"
DB_PORT="${SOURCE_DB_PORT:-15544}"
DB_CTR="${SOURCE_DB_CONTAINER:-citra-source-db-postgres}"
export SOURCE_DB_NAME="$DB_NAME" SOURCE_DB_USER="$DB_USER" \
       SOURCE_DB_PASSWORD="$DB_PASS" SOURCE_DB_PORT="$DB_PORT" \
       SOURCE_DB_CONTAINER="$DB_CTR"

DSN="postgresql://${DB_USER}:${DB_PASS}@localhost:${DB_PORT}/${DB_NAME}"

MODE=up
for a in "$@"; do
  case "$a" in
    --fresh) MODE=fresh ;;
    --down)  MODE=down ;;
    --dsn)   MODE=dsn ;;
    -h|--help) sed -n '10,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a" >&2; echo "try --help" >&2; exit 2 ;;
  esac
done

C_RED=""; C_B=""; C_OFF=""
if [ -t 1 ]; then C_RED="$(printf '\033[31m')"; C_B="$(printf '\033[1m')"; C_OFF="$(printf '\033[0m')"; fi
b()   { printf '%s%s%s' "$C_B" "$*" "$C_OFF"; }
red() { printf '%s%s%s\n' "$C_RED" "$*" "$C_OFF" >&2; }

# No MSYS_NO_PATHCONV here. That flag exists for arguments that only LOOK like
# paths (a URL, a `-c "select ..."`), and setting it on a real -f path stops Git
# Bash rewriting /c/Github/... into C:\Github\..., so docker was handed a path
# it read as C:\c\Github\... and could not open. seed-demo.sh passes its
# compose path bare for the same reason.
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

if [ "$MODE" = dsn ]; then printf '%s\n' "$DSN"; exit 0; fi

if [ "$MODE" = down ]; then
  echo "-> stopping $DB_CTR (data kept in the volume)"
  dc down
  echo "   [ok] stopped. Bring it back with: $0"
  exit 0
fi

# -- a python with psycopg2, the same one seed-demo.sh builds ----------------
PY="python"
if [ -x "$REPO_ROOT/.venv-seed/bin/python" ];        then PY="$REPO_ROOT/.venv-seed/bin/python"
elif [ -x "$REPO_ROOT/.venv-seed/Scripts/python.exe" ]; then PY="$REPO_ROOT/.venv-seed/Scripts/python.exe"
fi
if ! "$PY" -c "import psycopg2" >/dev/null 2>&1; then
  red "[FAIL] $PY cannot import psycopg2, so the seed would die after the"
  echo "       database was already up -- half a setup is worse than none." >&2
  echo "       Run the wizard once (it builds .venv-seed), or:" >&2
  echo "         python -m pip install psycopg2-binary" >&2
  exit 1
fi

if [ "$MODE" = fresh ]; then
  echo "-> $(b "--fresh"): destroying $DB_CTR and its volume"
  dc down -v --remove-orphans 2>/dev/null || true
  echo "   [ok] gone"
fi

echo "-> starting $(b "$DB_CTR") on port $(b "$DB_PORT")  (database '$DB_NAME')"
dc up -d

echo "-> waiting for it to accept connections"
READY=false
for _ in $(seq 1 40); do
  if dc exec -T source-db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then READY=true; break; fi
  sleep 2
done
if [ "$READY" != true ]; then
  red "[FAIL] $DB_CTR did not accept connections within 80s."
  echo "       docker compose -f $COMPOSE_FILE logs" >&2
  exit 1
fi
echo "   [ok] accepting connections"

# Seed only when there is nothing there. Re-seeding rewrites ~211,000 rows,
# and doing that silently on every run would make this script slow enough
# that people stop using it -- and would quietly discard anything they had
# changed while testing. --fresh is how you ask for a rewrite.
ROWS="$(dc exec -T source-db psql -U "$DB_USER" -d "$DB_NAME" -tAc \
        "select coalesce((select count(*) from information_schema.tables
                           where table_schema='public'),0)" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [ "${ROWS:-0}" -gt 0 ]; then
  echo "-> already seeded: $ROWS table(s) present, leaving them alone"
  echo "   (use --fresh to rebuild from zero)"
else
  echo "-> seeding (~211,000 rows across 16 tables; takes a minute)"
  if ! "$PY" "$SEEDER" --conn "$DSN"; then
    red "[FAIL] seeding failed. The database is up but EMPTY, so pointing the"
    echo "       wizard at it now would build an ontology over nothing." >&2
    exit 1
  fi
  echo "   [ok] seeded"
fi

TABLES="$(dc exec -T source-db psql -U "$DB_USER" -d "$DB_NAME" -tAc \
          "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null | tr -d '[:space:]')"
echo
echo "──────────────────────────────────────────────────────────────────────"
echo "$(b "Your own database is ready") - $TABLES tables in '$DB_NAME'."
echo
echo "Paste this at the wizard's $(b "Connection string") prompt:"
echo
echo "    $(b "$DSN")"
echo
echo "Then pick an organisation id that is $(b "not") acme-bank - that one belongs to"
echo "the demo tenant, and two sources answering for one org is not a test of"
echo "anything. 'northwind' matches this database."
echo
echo "This database is NOT on citra-network, on purpose: the MCP reaches it"
echo "through the published port, the way it would reach a real customer's."
echo "The wizard rewrites 'localhost' to host.docker.internal for that hop."
echo
echo "  stop, keep data   $0 --down"
echo "  rebuild from zero $0 --fresh"
echo "──────────────────────────────────────────────────────────────────────"
