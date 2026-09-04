#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

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

# Before the FIRST question. The wizard asks for an org, an admin password
# and an API key before it ever reaches setup.sh, so a host that cannot run
# the stack used to discover that only after the whole interview was over.
. "$REPO_ROOT/scripts/quickstart/preflight.sh"
preflight || exit 1

b()  { printf '\033[1m%s\033[0m' "$1"; }
hr() { printf '\n------------------------------------------------------------\n'; }
ask() {
  local q="$1" def="${2:-}" ans
  if [ -n "$def" ]; then printf '%s [%s]: ' "$q" "$def" >&2; else printf '%s: ' "$q" >&2; fi
  read -r ans || true; printf '%s' "${ans:-$def}"
}
ask_secret() { local q="$1" ans; printf '%s: ' "$q" >&2; read -rs ans || true; printf '\n' >&2; printf '%s' "$ans"; }

# Ask until there is an answer, and until it is a plausible one.
#
# The wizard used to fill these in itself -- an email on the vendor's domain, an
# org id inherited from .env.example, a random password nobody chose. Every one
# of those is a value the operator did not pick and will not remember, sitting
# in the identity layer of a system that decides things about money. Accepting
# an invented answer is worse than asking again.
#
# A value ALREADY IN .env is still offered as the default. That is not a guess:
# it is what this deployment is currently configured with, and re-running the
# wizard should not force it to be retyped.
ask_required() {
  local q="$1" def="${2:-}" validator="${3:-}" ans
  while :; do
    ans="$(ask "$q" "$def")"
    if [ -z "$ans" ]; then printf '  ! required\n' >&2; continue; fi
    if [ -n "$validator" ] && ! "$validator" "$ans"; then continue; fi
    printf '%s' "$ans"; return 0
  done
}

# Deliberately looser than the server's regex: the job here is to catch a
# typo or an empty Enter, not to adjudicate RFC 5322.
valid_email() {
  case "$1" in
    *@*.*) return 0 ;;
    *) printf '  ! that is not an email address\n' >&2; return 1 ;;
  esac
}
valid_slug() {
  case "$1" in
    *[!a-z0-9-]*) printf '  ! lowercase letters, digits and hyphens only\n' >&2; return 1 ;;
    *) return 0 ;;
  esac
}

# Never echoed, never defaulted, always confirmed. MIN_PASSWORD_LENGTH in
# Citra-User-Service is 8; asking for less here only moves the rejection later.
ask_password() {
  local p1 p2
  while :; do
    p1="$(ask_secret "Super-admin password (at least 8 characters)")"
    if [ "${#p1}" -lt 8 ]; then printf '  ! at least 8 characters\n' >&2; continue; fi
    p2="$(ask_secret "Confirm password")"
    if [ "$p1" != "$p2" ]; then printf '  ! they do not match\n' >&2; continue; fi
    printf '%s' "$p1"; return 0
  done
}
yes_no() { local q="$1" def="${2:-y}" a; a="$(ask "$q (y/n)" "$def")"; case "$a" in y|Y|yes|YES) return 0;; *) return 1;; esac; }

rand()  { openssl rand -hex "$1" 2>/dev/null || head -c "$((${1}*2))" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
  # .env has two zones split by the FINE-TUNING marker. Everything below it has a
  # code default and may have been hand-tuned, so the wizard must not write
  # there: getkv/setkv both stop at the marker, and a key that does not yet
  # exist is inserted just above it rather than appended to the file end.
  FT_MARK='^# =+ FINE-TUNING =+$'
  getkv() {
    awk -v k="$1" -v m="$FT_MARK" '$0 ~ m {exit} index($0, k"=")==1 {sub("^" k "=",""); print; exit}' "$ENV_FILE" 2>/dev/null
  }
  setkv() {
    local k="$1" v="$2"
    awk -v k="$k" -v v="$v" -v m="$FT_MARK" '
      BEGIN { done=0; intune=0 }
      $0 ~ m { if (!done) { print k "=" v; done=1 } intune=1 }
      !intune && index($0, k "=")==1 { if (!done) { print k "=" v; done=1 } next }
      { print }
      END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  }

clear 2>/dev/null || true
echo "$(b "Citra Decision System - setup wizard")"
echo "Self-improving Decision Apps. Your infrastructure, your models, your data."
echo
echo "$(b "What this wizard is.") A quick start for the common case: SQL or Mongo"
echo "tables, read access, one write action, and your SOPs. It gets you to a"
echo "working deployment in one sitting."
echo
echo "$(b "What it is not.") A replacement for authoring the ontology by hand."
echo "Sources that stream documents or images, REST/API sources, fraud"
echo "screening and multi-source departments are richer than an interview can"
echo "reach. They are all supported - they are declared in sources.json, and"
echo "this wizard tells you exactly what it left for you when it finishes."

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
setkv LLM_MODEL    "deepseek/deepseek-v4-pro:nitro"
setkv LLM_API_KEY  "$key"
# The tiered clients are NOT optional overrides of LLM_API_KEY — each tier
# resolves its own key, and an empty one is sent as no auth header at all.
# Agents declare model_tier in their spec (the acme-bank loan-triage agent is
# "large"), so leaving these blank meant every real decision died with
#   LLM endpoint returned 401: Missing Authentication header
# All three tiers already point at OpenRouter, so the one key covers them.
setkv LLM_LARGE_API_KEY  "$key"
setkv LLM_MEDIUM_API_KEY "$key"
setkv LLM_SMALL_API_KEY  "$key"
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
# RERANK too. reranker-service ships with RERANKER_PROVIDER=remote and refuses
# to boot without this key — it crash-looped on every clean install because the
# key sat in the FINE-TUNING zone, which the wizard deliberately never writes.
# RERANK_API_URL already points at OpenRouter, so the same key covers it.
setkv RERANK_API_KEY  "$key"
echo "  [ok] one key configured for reasoning, embeddings, vision, search and rerank"

# -- 3. Which starting point --------------------------------------------------
# Two genuinely different audiences: "show me what this does" and "point it at
# my data". The old flow silently assumed the first and left the second as a doc
# link, so a user finished with a bank on their laptop and no idea what it was.
#
# Asked BEFORE the super-admin, because the organisation question below only has
# a sensible answer once we know which of the two this is.
hr; echo "$(b "Step 3/4 - what do you want to start with?")"
echo
echo "  $(b "1) The acme-bank demo")   see the whole decision loop in ~10 minutes"
echo "  $(b "2) My own database")      connect a SQL source and build it up"
echo
start_choice="$(ask "Choose 1-2" "1")"

# -- 4. Super-admin + organisation --------------------------------------------
hr; echo "$(b "Step 4/4 - super-admin")"
echo "The first user. Created as an admin OF the organisation below, which is"
echo "what makes the apps, sources and queues in it visible on sign-in."
echo
# No default unless .env already carries one. This used to offer
# admin@citra-ai.com, so anyone who pressed Enter ran their deployment on an
# account branded with the vendor's domain, unrelated to the org they are
# asked for a few lines later.
adm_email="$(ask_required "Super-admin email" "$(getkv ADMIN_EMAIL)" valid_email)"
setkv ADMIN_EMAIL "$adm_email"

# ORG_ID is what start.sh passes to create-admin.js as --org. Leaving it at the
# .env.example default put the admin in an org with nothing in it: everything
# was installed correctly and the screen was empty anyway.
if [ "$start_choice" = "2" ]; then
  # Whatever is in .env is offered, EXCEPT the value .env.example ships:
  # inheriting that silently is what the comment above describes, an admin of
  # an org with nothing in it.
  cur_org="$(getkv ORG_ID)"; [ "$cur_org" = "citra-ai" ] && cur_org=""
  org_id="$(ask_required "Organisation id (lowercase, no spaces, e.g. acme-bank)" "$cur_org" valid_slug)"
else
  # Not a free choice on the demo path: the demo's data, apps and officer
  # personas are all seeded into acme-bank, so an admin of any other org would
  # sign in to an empty screen.
  org_id="acme-bank"
  echo "  Organisation: $(b "acme-bank")  (fixed - the demo's data, apps and"
  echo "  personas all live in it, so the super-admin is created there)"
fi
setkv ORG_ID "$org_id"

# Asked, never generated. The old prompt defaulted to "n" and minted a random
# hex string instead, so almost nobody chose their own password and the one
# they got existed only in .env and in the closing banner -- which is how you
# end up locked out of a deployment with no mail provider to reset it.
if [ -n "$(getkv ADMIN_PASSWORD)" ] && yes_no "Keep the existing super-admin password?" "y"; then
  :
else
  setkv ADMIN_PASSWORD "$(ask_password)"
fi
echo "  [ok] super-admin = $adm_email   admin of = $org_id"

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
  # A REST source is introspected from its OpenAPI/Swagger SPEC, not from a
  # connection string (introspect_source.introspect_openapi). Asking for a
  # "connection string" here guaranteed a failure for anyone who picked `rest`:
  # they had no way to know a spec URL was what was wanted, and the spec is not
  # a secret, so reading it silently was wrong too.
  case "$db_kind" in
    rest|api|openapi|swagger)
      echo "  A REST source is read from its OpenAPI (Swagger) spec - a URL or a"
      echo "  local file. The spec is not a secret, so this is echoed as you type."
      db_conn="$(ask "OpenAPI spec URL or file path" "")"
      echo
      echo "  $(b "Note") - the spec gives the wizard your resources and fields. It"
      echo "  does NOT give it how to CALL the API: base_url, the auth env_prefix"
      echo "  and options.invocation_template are yours to add afterwards, in"
      echo "  sources.json (docs/sources-file.md s5.1). Until they are set, the"
      echo "  builder can see the source and the runtime cannot call it."
      ;;
    *)
      echo "  Your connection string stays on this machine. It is never sent to a model."
      db_conn="$(ask_secret "Connection string")"
      ;;
  esac
  if [ -z "$db_conn" ]; then
    echo
    echo "  [FAIL] nothing to introspect, so there is nothing to build an ontology" >&2
    echo "         from. An empty registry is VALID but useless - it would come up" >&2
    echo "         'working' with no data behind it. Re-run and choose the demo if" >&2
    echo "         you want to see the product first." >&2
    exit 1
  fi

  # org and admin were settled in step 4 -- asking again invited two different
  # answers, and the org seeded here has to be the one the admin belongs to.
  echo "  Organisation: $org_id     admin: $adm_email"
  dept_id="$(ask "First department id (e.g. claims, ops)" "ops")"
  admin_email="$adm_email"

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

  # ---- SOPs: the rules half of the product ----------------------------------
  # Connecting a database gives the agent facts. SOPs give it RULES -- what your
  # team already does with those facts. Without them a recommendation cannot
  # cite anything, which is most of the difference between this and a chatbot
  # over your database.
  hr; echo "$(b "Your SOPs - the rules your apps decide by")"
  echo
  echo "Your database says what IS TRUE. Your SOPs say what to DO about it:"
  echo
  echo "  - when a loan can be approved, and what must be verified first"
  echo "  - when a claim settles, when it needs a surveyor, when it is rejected"
  echo "  - what makes an account NPA, what the KYC steps are"
  echo
  echo "Anything you would hand a new joiner and expect them to follow."
  echo "$(b "PDF, Word, Markdown or text.")"
  echo
  echo "This is what makes a recommendation quotable: $(b "\"approve under §4.2 of")"
  echo "$(b "the credit policy\"") rather than an opinion you cannot check. It is also"
  echo "the layer that always wins -- what the app learns from your officers"
  echo "sits underneath your SOPs, never over them."
  echo
  sop_dir="$(ask "Folder of SOP documents (blank to upload from the UI later)" "")"

  if [ -n "$sop_dir" ]; then
    if [ ! -d "$sop_dir" ]; then
      echo "  [!] $sop_dir is not a folder - skipping. Upload from the UI instead:" >&2
      echo "      Home -> SOP Library -> New library -> Upload SOPs" >&2
    else
      SVC_CID="$($COMPOSE ps -q citra-service 2>/dev/null || true)"
      if [ -z "$SVC_CID" ]; then
        echo "  [!] citra-service is not running - skipping SOP ingestion." >&2
      else
        # Runs inside citra-service: the ingest imports that service's
        # embedding client and Milvus wiring, which the host venv does not have.
        MSYS_NO_PATHCONV=1 docker exec "$SVC_CID" rm -rf /app/_sops_in
        MSYS_NO_PATHCONV=1 docker cp "$sop_dir" "$SVC_CID:/app/_sops_in"
        MSYS_NO_PATHCONV=1 docker exec -w /app/Citra-Service "$SVC_CID" \
          python /app/scripts/quickstart/ingest_sops.py \
            --org "$org_id" --dept "$dept_id" --dir /app/_sops_in \
          || echo "  [!] SOP ingestion failed - your apps will have no rules to cite." >&2
      fi
    fi
  else
    echo
    echo "  No SOPs loaded. Your apps will read your data but will not be able to"
    echo "  cite a rule. Add them any time:"
    echo "      $(b "Home -> SOP Library -> New library -> Upload SOPs")"
  fi

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
# Print the credentials HERE, not only in start.sh. start.sh does print them,
# but that scrolls past long before the wizard finishes -- and if the user
# declined "start now" it never ran at all, leaving a generated password
# readable only by grepping .env.
adm_pw="$(getkv ADMIN_PASSWORD)"; adm_pw="${adm_pw:-<the password you set>}"
echo "$(b "Done.")  Open  $(b "http://localhost:8081")  and sign in:"
echo
echo "    email     $adm_email"
echo "    password  $adm_pw"
echo "    org       $org_id   (you are an admin of it, so you see everything in it)"
echo
if [ "$start_choice" != "2" ]; then
  echo "The four Decision Apps are on your home screen. To see the demo as one of"
  echo "the officer personas instead: user menu -> Login as User."
  echo "Point it at your own data later:  docs/change-the-demo.md"
fi
echo "Credentials live in .env (ADMIN_EMAIL / ADMIN_PASSWORD)."
echo "Re-run this wizard any time to change keys:  ./scripts/quickstart/wizard.sh"
