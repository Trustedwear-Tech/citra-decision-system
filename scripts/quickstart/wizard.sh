#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

#
# Guided first-run setup:
#   1. .env with fresh secrets
#   2. AI provider (required - nothing recommends without a model)
#   3. super-admin
#   4. demo or your own database
#
# Image generation (Runware) and web reading (Serper) are NOT asked for here:
# neither is needed to see the product work, and one of them costs money per
# image. Both remain supported — set IMAGE_GEN_* / SERPER_API_KEY in .env.
#
# Re-runnable: reads and updates the existing .env, so run it again to change a
# key. Prereqs: docker, curl, python3.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$REPO_ROOT"
# python3 does not exist on Windows (it is `python`); the READMEs list python3
# as a prereq but the binary name is not portable. Resolve it once.
PY="$(command -v python3 || command -v python || true)"
ENV_FILE="$REPO_ROOT/.env"

b()  { printf '\033[1m%s\033[0m' "$1"; }
hr() { printf '\n------------------------------------------------------------\n'; }
ask() {
  local q="$1" def="${2:-}" ans
  if [ -n "$def" ]; then printf '%s [%s]: ' "$q" "$def" >&2; else printf '%s: ' "$q" >&2; fi
  read -r ans || true; printf '%s' "${ans:-$def}"
}
ask_secret() { local q="$1" ans; printf '%s: ' "$q" >&2; read -rs ans || true; printf '\n' >&2; printf '%s' "$ans"; }
yes_no() { local q="$1" def="${2:-y}" a; a="$(ask "$q (y/n)" "$def")"; case "$a" in y|Y|yes|YES) return 0;; *) return 1;; esac; }

rand()  { openssl rand -hex "$1" 2>/dev/null || head -c "$((${1}*2))" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
getkv() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- ; }
setkv() {
  local k="$1" v="$2"
  if grep -qE "^$k=" "$ENV_FILE" 2>/dev/null; then
    awk -v k="$k" -v v="$v" 'BEGIN{FS="="} $1==k{print k"="v; next} {print}' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"
  fi
}

clear 2>/dev/null || true
echo "$(b "Citra Decision System - setup wizard")"
echo "Self-improving Decision Apps. Your infrastructure, your models, your data."

# -- 1. .env ------------------------------------------------------------------
hr; echo "$(b "Step 1/4 - environment file")"
if [ -f "$ENV_FILE" ]; then
  echo "Found an existing .env - keeping it (values you set are preserved)."
else
  echo "Generating .env from .env.example with fresh random secrets..."
  cp .env.example "$ENV_FILE"
  MCP_KEY="demo-mcp-$(rand 8)"
  setkv JWT_SECRET "$(rand 48)"
  setkv MCP_API_KEY "$MCP_KEY"
  # Must equal MCP_API_KEY. If these drift, the catalogue crawler sends the
  # caller's user token as the service guard, the MCP answers 401, and the
  # catalogue stays SILENTLY empty.
  setkv MCP_SERVICE_API_KEY "$MCP_KEY"
  setkv SMART_APP_INTERNAL_SIGNING_KEY "$(rand 32)"
  setkv CONNECTION_ENCRYPTION_KEY "$(rand 32)"
  setkv ADMIN_PASSWORD "$(rand 6)"
  echo "  [ok] secrets generated (JWT, MCP key + service key, signing + connection keys, admin pw)"
fi

# -- 2. AI provider -----------------------------------------------------------
# ONE provider, deliberately. A single OpenRouter key covers reasoning,
# embeddings, vision and search, so there is one key to paste and one thing
# that can be wrong. Both defaults are open-weights and swappable later by
# editing .env — self-hosting is the same edit, pointing *_BASE_URL at your own
# vLLM. Offering four providers here only multiplied the ways a first run could
# half-work (e.g. an OpenAI key that reasons but was never wired to embeddings).
hr; echo "$(b "Step 2/4 - AI provider (required)")"
echo "Citra calls an LLM for recommendations and NL->SQL, and an embedding"
echo "model to ground answers in your SOPs. One OpenRouter key covers both."
echo
echo "  Get a key: $(b "https://openrouter.ai/keys")"
key="$(ask_secret "Paste your OpenRouter API key (input hidden)")"

# Fail loud rather than continuing to a stack that cannot recommend anything.
if [ -z "$key" ]; then
  echo
  echo "  [FAIL] no key entered. Decision Apps cannot produce a recommendation" >&2
  echo "         without a model, so the demo would come up unable to do the one" >&2
  echo "         thing it exists to show. Re-run once you have a key." >&2
  exit 1
fi

setkv LLM_BASE_URL "https://openrouter.ai/api/v1"
setkv LLM_MODEL    "deepseek/deepseek-chat-v3.1"
setkv LLM_API_KEY  "$key"
# bge-m3 is open-weights, and the client sends `dimensions` on every call so it
# returns 768 rather than its native 1024 — matching the Milvus collection.
setkv EMBEDDING_BASE_URL  "https://openrouter.ai/api/v1"
setkv EMBEDDING_MODEL     "baai/bge-m3"
setkv EMBEDDING_DIMENSION "768"
setkv EMBEDDING_API_KEY   "$key"
setkv VISION_BASE_URL "https://openrouter.ai/api/v1"
setkv VISION_MODEL    "qwen/qwen3-vl-32b-instruct"
setkv VISION_API_KEY  "$key"
setkv SEARCH_BASE_URL "https://openrouter.ai/api/v1"
setkv SEARCH_API_KEY  "$key"
echo "  [ok] one key configured for reasoning, embeddings, vision and search"

# -- 3. Super-admin -----------------------------------------------------------
hr; echo "$(b "Step 3/4 - super-admin")"
cur_email="$(getkv ADMIN_EMAIL)"; cur_email="${cur_email:-admin@citra-ai.com}"
adm_email="$(ask "Super-admin email" "$cur_email")"; setkv ADMIN_EMAIL "$adm_email"
if yes_no "Set a super-admin password now? (otherwise a random one is generated and printed)" "n"; then
  apw="$(ask_secret "Super-admin password")"; [ -n "$apw" ] && setkv ADMIN_PASSWORD "$apw"
fi
echo "  [ok] super-admin = $adm_email"

# -- 4. Which starting point --------------------------------------------------
# Two genuinely different audiences: "show me what this does" and "point it at
# my data". The old flow silently assumed the first and left the second as a doc
# link, so a user finished with a bank on their laptop and no idea what it was.
hr; echo "$(b "Step 4/4 - what do you want to start with?")"
echo
echo "  $(b "1) The acme-bank demo")   see the whole decision loop in ~10 minutes"
echo "  $(b "2) My own database")      connect a SQL source and build it up"
echo
start_choice="$(ask "Choose 1-2" "1")"

hr; echo "$(b "Bringing up the data stores")"
if yes_no "Run setup now (data stores + database resources)?" "y"; then
  "$REPO_ROOT/scripts/quickstart/setup.sh"
fi

if [ "$start_choice" = "2" ]; then
  # ---- Path 2: your own data ------------------------------------------------
  # This branch used to copy a template and print homework. It now RUNS the
  # whole sequence, because the ordering is the hard part and getting it wrong
  # leaves a catalogue nobody can reach.
  hr; echo "$(b "Connect your own database")"
  echo
  echo "Two halves, and they differ in kind:"
  echo
  echo "  $(b "STRUCTURE") - tables, columns, types, keys.  Scanned for you."
  echo "  $(b "MEANING")   - which table records decisions already made, what a"
  echo "     document column IS, which column is money. A scan cannot infer this."
  echo "     It is what turns a database into something the platform can screen,"
  echo "     ground and measure."
  echo
  if [ -n "$PY" ]; then "$PY" "$REPO_ROOT/scripts/quickstart/list_sources.py" || true; fi
  echo

  db_kind="$(ask "Database kind (postgres | mysql | mongo | mssql | oracle | odata | salesforce | rest)" "postgres")"
  echo "  Your connection string stays on this machine. It is never sent to a model."
  db_conn="$(ask_secret "Connection string")"
  if [ -z "$db_conn" ]; then
    echo
    echo "  [FAIL] no connection string, so there is nothing to build an ontology" >&2
    echo "         from. An empty registry is VALID but useless - it would come up" >&2
    echo "         'working' with no data behind it. Re-run and choose the demo if" >&2
    echo "         you want to see the product first." >&2
    exit 1
  fi

  org_id="$(ask "Organisation id (lowercase, e.g. acme-bank)" "my-org")"
  dept_id="$(ask "First department id (e.g. claims, ops)" "ops")"
  admin_email="$(ask "Admin email for this organisation" "$adm_email")"

  hr; echo "$(b "Building the ontology")"
  echo "A model reads your schema and asks what it cannot infer."
  echo "Nothing is written until you confirm it."
  echo
  if ! "$PY" "$REPO_ROOT/scripts/quickstart/build_ontology.py" \
        --kind "$db_kind" --conn "$db_conn" \
        --org-id "$org_id" --dept "$dept_id" \
        --out "$REPO_ROOT/my-source/sources.json"; then
    echo
    echo "  Ontology not written. Retry, or author it by hand:" >&2
    echo "    cp source-mcp-template/templates/<cell>.sources.json my-source/sources.json" >&2
    echo "    python source-mcp-template/validate_sources.py my-source/sources.json" >&2
    exit 1
  fi

  hr; echo "$(b "Starting the platform")"
  SOURCES_FILE="$REPO_ROOT/my-source/sources.json" \
    "$REPO_ROOT/scripts/quickstart/start.sh" --demo none

  hr; echo "$(b "Creating your organisation")"
  # Without this the catalogue is scoped to an org that does not exist - which
  # fails SILENTLY, so it is a hard failure here rather than a warning.
  "$PY" "$REPO_ROOT/scripts/quickstart/seed_org.py" \
      --org-id "$org_id" --dept "$dept_id" --admin "$admin_email" \
      --sources "$REPO_ROOT/my-source/sources.json" --yes \
    || { echo "  [FAIL] organisation not created - the catalogue would be unreachable." >&2; exit 1; }

  hr; echo "$(b "Verifying")"
  "$PY" "$REPO_ROOT/scripts/quickstart/verify_install.py" --org-id "$org_id" || true
else
  # ---- Path 1: the demo -----------------------------------------------------
  hr; echo "$(b "What the acme-bank demo is")"
  echo
  echo "A retail bank and general insurer, invented for this demo. Postgres"
  echo "system-of-record: $(b "16 tables, ~211,000 rows") - customers, loan"
  echo "applications, disbursements, repayment schedules, delinquencies,"
  echo "collection activities, policies, claims, surveyor reports, leads."
  echo "$(b "Five departments, fourteen officer personas") you can sign in as."
  echo
  echo "Four Decision Apps - the same loop over different data:"
  echo "  - $(b "Loan triage")           approve or decline against credit policy"
  echo "  - $(b "Collections priority")  who to chase today, grounded in what worked"
  echo "  - $(b "Claims triage")         settle or reject, with fraud screening"
  echo "  - $(b "Sales performance")     a dashboard app - no decisions"
  echo
  echo "Plus a SOP library in Milvus, so recommendations cite the actual policy."
  echo
  echo "$(b "Try this first:") open Claims triage, read the recommendation AND its"
  echo "citations, override one with a reason, then watch the governed write land"
  echo "in Postgres and the outcome fold into memory for the next case."
  echo
  if yes_no "Start all services and seed the acme-bank demo?" "y"; then
    "$REPO_ROOT/scripts/quickstart/start.sh" --demo acme-bank
    # Finish at "I ran it and it worked", not at "containers started" — the
    # citra-flows pattern, which is the best of the three installs.
    hr; echo "$(b "Verifying")"
    [ -n "$PY" ] && "$PY" "$REPO_ROOT/scripts/quickstart/verify_install.py" --org-id acme-bank || true
  fi
fi

hr
echo "$(b "Done.")  Open  http://localhost:8081  and sign in as  $adm_email"
if [ "$start_choice" != "2" ]; then
  echo "Impersonate an acme-bank persona: user menu -> Login as User."
  echo "Point it at your own data later:  docs/change-the-demo.md"
fi
echo "Re-run this wizard any time to change keys:  ./scripts/quickstart/wizard.sh"
