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

# Arguments. This script used to ignore argv completely -- `wizard.sh --help`
# ran the entire wizard, and a mistyped flag was accepted in silence. An
# unrecognised option now stops, because guessing what someone meant by a flag
# that installs software is not a favour.
FRESH=0
usage() {
  cat >&2 <<'USAGE'
Citra Decision System - setup wizard

  ./scripts/quickstart/wizard.sh [options]

Options:
  --fresh     Delete this deployment's local state first, then set up from
              scratch: .env, the Docker volumes, and my-source/sources.json.
              Lists exactly what it will remove and asks once before doing it.
  -h, --help  Show this and exit.

Without --fresh an existing .env is kept and reconciled: keys added to
.env.example since it was written are added, and values that were defaults in
an older release are cleared so you are asked for them rather than inheriting
them. Your API key, organisation and password are preserved.

Related:
  ./scripts/quickstart/setup.sh      phase 1 only (data stores, DB resources)
  ./scripts/quickstart/start.sh      phase 2 only (services, admin, demo)
  make help                          every target
USAGE
}
while [ $# -gt 0 ]; do
  case "$1" in
    --fresh)    FRESH=1 ;;
    -h|--help)  usage; exit 0 ;;
    *)          echo "unknown option: $1" >&2; echo >&2; usage; exit 2 ;;
  esac
  shift
done
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
  CITRA_SETUP_LOG="$REPO_ROOT/logs/wizard-$(date +%Y%m%d-%H%M%S).log"
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

# python3 does not exist on Windows (it is `python`); the READMEs list python3
# as a prereq but the binary name is not portable. Resolve it once.
PY="$(command -v python3 || command -v python || true)"
ENV_FILE="$REPO_ROOT/.env"
# ── Checkpoints ──────────────────────────────────────────────────────────────
# A re-run used to repeat everything: re-paste the key, re-interview the
# schema, re-seed 211,000 rows, re-embed every SOP. On a slow connection that
# is most of an hour to arrive back where you already were, and it is why an
# install that fails at minute 35 gets abandoned rather than resumed.
#
# Two things are recorded, and BOTH must hold before a step is skipped:
#   the checkpoint  -- this step reported success once, and when
#   the artifact    -- the thing it produced is still there NOW
# A checkpoint alone is a claim about the past. `docker compose down -v` makes
# every one of them a lie, so the artifact is what is trusted; a checkpoint
# whose artifact is gone is dropped and the step runs again.
# ── Colour ───────────────────────────────────────────────────────────────────
# Failures used to be the same white as the 2,000 INFO lines around them. The
# one line that mattered -- the re-run command for a failed catalogue crawl --
# scrolled past unread, and the install carried on to produce four confusing
# 422s instead. Built with printf rather than $'..' so the escapes survive any
# editor, and switched off when stderr is not a terminal or NO_COLOR is set.
if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RED="$(printf '\033[31m')"; C_AMB="$(printf '\033[33m')"
  C_GRN="$(printf '\033[32m')"; C_OFF="$(printf '\033[0m')"
else
  C_RED=""; C_AMB=""; C_GRN=""; C_OFF=""
fi
red()   { printf '%s%s%s\n' "$C_RED" "$*" "$C_OFF" >&2; }
amber() { printf '%s%s%s\n' "$C_AMB" "$*" "$C_OFF" >&2; }
green() { printf '%s%s%s\n' "$C_GRN" "$*" "$C_OFF"; }

# ── Where we are, and what to say if we stop here ────────────────────────────
# Every sub-script runs under `set -e`, so one failing step takes the whole
# wizard down. That is correct -- carrying on after a failed catalogue crawl
# only produces confusing errors later -- but it exited to the shell prompt
# with NOTHING said: no cause, no state, no way back in. The trap makes the
# ending always print, and the checkpoints make it able to say what survived.
STEP="starting up"
step() { STEP="$1"; }
finish() {
  rc=$?
  [ "$rc" = "0" ] && return 0
  echo >&2
  red "════════════════════════════════════════════════════════════════"
  red " SETUP STOPPED during: $STEP"
  red "════════════════════════════════════════════════════════════════"
  echo >&2
  if [ -f "$STATE_FILE" ]; then
    echo "  Completed before this, and kept:" >&2
    while IFS='=' read -r _n _t; do
      [ -n "$_n" ] && printf '    %s%-14s%s %s\n' "$C_GRN" "$_n" "$C_OFF" "$_t" >&2
    done < "$STATE_FILE"
  else
    echo "  Nothing had completed yet." >&2
  fi
  echo >&2
  echo "  $(b "Run the wizard again.") It resumes: every step above is skipped" >&2
  echo "  after checking that what it produced is still on this machine." >&2
  echo "    ./scripts/quickstart/wizard.sh" >&2
  echo >&2
  echo "  If the same step fails again, the cause is above this banner --" >&2
  echo "  scroll up to the first $(red_word) line, not the last." >&2
  [ -n "${CITRA_SETUP_LOG:-}" ] && echo "  Full transcript: $CITRA_SETUP_LOG" >&2
  echo >&2
  return 0
}
red_word() { printf '%sred%s' "$C_RED" "$C_OFF"; }
trap finish EXIT
STATE_FILE="$REPO_ROOT/logs/.setup-state"
ck_mark()  { mkdir -p "$(dirname "$STATE_FILE")"; ck_drop "$1"; printf '%s=%s\n' "$1" "$(date +%Y-%m-%dT%H:%M:%S)" >> "$STATE_FILE"; }
ck_has()   { [ -f "$STATE_FILE" ] && grep -q "^$1=" "$STATE_FILE" 2>/dev/null; }
ck_when()  { [ -f "$STATE_FILE" ] && grep "^$1=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2; }
ck_drop()  { [ -f "$STATE_FILE" ] || return 0; grep -v "^$1=" "$STATE_FILE" > "$STATE_FILE.tmp" 2>/dev/null || true; mv "$STATE_FILE.tmp" "$STATE_FILE"; }

# The live checks. Cheap ones first -- a container listing costs nothing, a
# psql count costs a second, and neither is worth skipping to save that.
_running() { docker ps --format '{{.Names}}' 2>/dev/null | grep -q "$1"; }
have_llm_key()  { [ -n "$(getkv LLM_API_KEY)" ]; }
have_admin()    { [ -n "$(getkv ADMIN_EMAIL)" ] && [ -n "$(getkv ADMIN_PASSWORD)" ]; }
have_stores()   { _running '^citra-mongodb$'; }
have_services() { _running 'citra-service'; }
have_ontology() { [ -s "$REPO_ROOT/my-source/sources.json" ]; }
# The MCP that fronts YOUR database. Checked by container rather than by the
# generated compose file existing: the file is written before `up`, so a
# generate that then failed to start would otherwise read as done and the
# catalogue would be crawled against an MCP that is not there.
have_source_mcp() { _running "^mcp-${MCP_SAFE_ORG:-__unset__}$"; }
have_demo()     {
  docker exec citra-ds-acme-bank-postgres psql -U acme_bank -d acme_bank -tAc \
    'select count(*) from customers' 2>/dev/null | tr -d '[:space:]' | grep -qE '^[1-9]'
}
# SOPs live in Mongo (the library record) and Milvus (the chunks). Mongo is the
# cheaper of the two to ask and cannot be right while Milvus is empty, because
# the ingest writes the folder LAST. Checked because `down -v` wipes both: a
# sops checkpoint with no library behind it would skip ingestion and leave an
# install whose recommendations cite nothing -- the exact failure this guards.
have_sops()     {
  local _pw _db
  _pw="$(getkv MONGODB_PASSWORD)"; [ -n "$_pw" ] || return 1
  _db="$(getkv MONGODB_DATABASE)"; _db="${_db:-citra}"
  docker exec citra-mongodb mongosh --quiet -u root -p "$_pw" \
      --authenticationDatabase admin "$_db" \
      --eval 'db.folders.countDocuments({folder_kind:"sop_library",deleted:{$ne:true}})' \
      2>/dev/null | tr -d '[:space:]' | grep -qE '^[1-9]'
}
# done <name> <live-check>: has this step's RESULT already been achieved?
#
# The artifact decides, not the checkpoint. Requiring both was wrong in one
# direction: the first run after these checkpoints existed had no state file,
# so a .env with a working key, a seeded Postgres and an ingested SOP library
# were all ignored and every step ran again. Nobody upgrading got any benefit
# until their second run.
#
#   artifact present, no checkpoint -> ADOPT. An install that predates this,
#                                      or a step run by hand. Both are done.
#   artifact gone, checkpoint present -> the checkpoint is a lie. Drop it,
#                                      run the step, and say why.
#   both -> skip, and report when it was done.
done_already() {
  if "$2"; then
    if ! ck_has "$1"; then
      ck_mark "$1"
      echo "  [ok] '$1' was already in place - adopting it (not recorded by an" >&2
      echo "       earlier run of this wizard)." >&2
    fi
    return 0
  fi
  if ck_has "$1"; then
    echo "  [!] '$1' was completed $(ck_when "$1") but what it produced is gone." >&2
    echo "      Running it again." >&2
    ck_drop "$1"
  fi
  return 1
}

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
# Echo one `*` per character, so a paste is visibly RECEIVED.
#
# This used to be a bare `read -rs`: nothing appeared, at all, at the very first
# question after the preflight. The commonest reaction was to sit there
# wondering whether the paste had landed -- and the only way to find out was to
# press Enter and see whether the wizard exited. Every password field ever
# built shows dots for exactly this reason.
#
# Backspace is handled, control characters (arrow keys, escape sequences) are
# dropped rather than smuggled into the value, and a non-interactive stdin --
# a pipe, a CI runner -- falls back to the plain read.
ask_secret() {
  local q="$1" ans="" ch
  printf '%s: ' "$q" >&2
  if [ -t 0 ]; then
    # One `*` per character AS IT ARRIVES, so a paste is visibly received.
    # A bare `read -rs` showed nothing at all until Enter, at the very first
    # question after the preflight -- and printing the asterisks afterwards
    # was no better, because the doubt is at the moment of pasting.
    #
    # Enter must match CR as well as LF. MinTTY on Windows sends CR, and an
    # earlier version of this matched only LF, so CR fell through to the
    # control-character case, was DROPPED, and the prompt hung with no way out
    # but Ctrl-C. Any terminal that ends a line with either now ends the read.
    while IFS= read -rsn1 ch; do
      case "$ch" in
        ''|$'\r'|$'\n') break ;;
        $'\177'|$'\b')  if [ -n "$ans" ]; then ans="${ans%?}"; printf '\b \b' >&2; fi ;;
        [[:cntrl:]])    : ;;
        *)              ans="$ans$ch"; printf '*' >&2 ;;
      esac
    done
  else
    read -rs ans || true
  fi
  # A terminal left in bracketed-paste mode wraps a paste in ESC[200~ … ESC[201~.
  # The ESC is a control character and is dropped above, but the `[200~` that
  # follows is ordinary text and would otherwise be pasted INTO the secret.
  ans="${ans#'[200~'}"; ans="${ans%'[201~'}"
  printf '\n' >&2
  printf '%s' "$ans"
}

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
# On launch, not only behind --help. A flag nobody knows about is a flag that
# does not exist, and the one that matters here -- starting from a clean slate
# -- is exactly what someone re-running this wants and would never guess.
echo "$(b "Options:")  --fresh   wipe .env, volumes and sources.json first"
echo "          --help    what each one does"
echo
# What a previous run got through. Printed before the first question so the
# operator knows what is about to be skipped rather than discovering it.
if [ -f "$STATE_FILE" ] && [ "$FRESH" != "1" ]; then
  echo "$(b "Resuming.") Completed by an earlier run:"
  while IFS='=' read -r _n _t; do
    [ -n "$_n" ] && printf '    %-14s %s\n' "$_n" "$_t"
  done < "$STATE_FILE"
  echo "  Each is re-checked against what is actually on this machine, and"
  echo "  re-run if what it produced has gone. $(b "--fresh") ignores all of it."
  echo
fi
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
step "the environment file"
hr; echo "$(b "Step 1/4 - environment file")"

# --fresh: say what will be destroyed, in full, before destroying any of it.
if [ "$FRESH" = "1" ]; then
  echo "$(b "--fresh") will permanently delete:"
  echo "    .env                        every secret and setting, including your API key"
  echo "    Docker volumes              Postgres, Mongo, Milvus, MinIO - all seeded data"
  echo "    my-source/sources.json      the ontology built by a previous run"
  echo "    deployments/<org>/mcp       the MCP generated for your own database"
  echo "    the demo tenant             acme-bank's Postgres and its MCP"
  echo "  Apps, decisions, memory and uploaded SOPs all live in those volumes."
  echo
  echo "  It also REBUILDS every service image, so the clean install is not"
  echo "  running yesterday's code. That is the slow part of --fresh."
  echo
  if yes_no "Delete them and start from scratch?" "n"; then
    # ORDER MATTERS: containers and volumes first, .env LAST.
    #
    # compose interpolates ${VAR} from .env while PARSING the file, so a `down`
    # run after .env has been deleted dies with
    #   required variable MONGODB_PASSWORD is missing a value
    # before it removes anything. .env used to be deleted on the line above
    # this one, and the error went to /dev/null under a `|| true` -- so --fresh
    # printed "local state removed", removed nothing, and the next run found the
    # data still there and adopted it as already seeded. Measured: a --fresh
    # answered `y` left every container running with 5 hours of uptime.
    _wipe_failed=0
    # --env-file, because the per-tenant and per-org composes live in their OWN
    # directories: compose looks for .env NEXT TO the file it was given, finds
    # none there, and dies interpolating MCP_API_KEY. seed-demo.sh passes it for
    # exactly this reason. The quickstart compose does not need it -- .env is in
    # the working directory -- but passing it everywhere is one less rule.
    #
    # Guarded on existence: --env-file pointing at a missing file is itself an
    # error, and a FIRST --fresh, before any .env has been written, is a normal
    # thing to do.
    _envarg=()
    [ -f "$ENV_FILE" ] && _envarg=(--env-file "$ENV_FILE")
    _down() {
      local _out
      if _out="$(docker compose ${_envarg[@]+"${_envarg[@]}"} -f "$1" down -v --remove-orphans 2>&1)"; then return 0; fi
      red "  [!] could not bring down $1"
      printf '%s\n' "$_out" | tail -3 | sed 's/^/      /' >&2
      _wipe_failed=1
    }
    _down docker-compose.quickstart.yml
    # Every MCP is its OWN compose project, so the `down` above -- which names
    # only docker-compose.quickstart.yml -- never touched any of them. For the
    # DEMO that is not untidiness: have_demo() counts rows in the surviving
    # Postgres, so a later run adopts it as seeded while Mongo, and with it the
    # org, the users and the apps, went with the wipe.
    for _d in "$REPO_ROOT"/deployments/*/mcp/docker-compose.yml               "$REPO_ROOT"/demo-data/tenants/*/mcp/docker-compose.yml; do
      [ -f "$_d" ] || continue
      _down "$_d"
    done
    if [ "$_wipe_failed" = "1" ]; then
      echo >&2
      red "  [FAIL] --fresh could not remove everything, so it removed nothing."
      echo "         Carrying on would install over live data and report success," >&2
      echo "         which is the failure this check exists to prevent. Fix the" >&2
      echo "         error above, or bring the stack down by hand:" >&2
      echo "           docker compose -f docker-compose.quickstart.yml down -v" >&2
      exit 1
    fi
    # Only now, once the containers that need it are gone.
    rm -f "$ENV_FILE" "$REPO_ROOT/my-source/sources.json" "$STATE_FILE"
    rm -rf "$REPO_ROOT/deployments"
    # `down -v` takes containers and volumes; images survive it. start.sh reads
    # this and adds --build, so a clean install is not running old code.
    export CITRA_COMPOSE_BUILD=1
    echo "  [ok] local state removed; images will be rebuilt"
  else
    echo "  keeping what is there; continuing as a normal run"
  fi
  echo
fi

if [ -f "$ENV_FILE" ]; then
  echo "Found an existing .env - keeping it (values you set are preserved)."
  # Reconcile it, every run.
  #
  # An .env written by an older release keeps whatever .env.example shipped
  # THEN, and nothing ever revisited it. That is precisely how ChangeMe!123 and
  # admin@citra-ai.com survived being removed from the example file: the wizard
  # read them back out of .env and offered them as values the operator had
  # chosen. A key added to .env.example since had the same problem in reverse --
  # absent, so every service using it fell back to a code default nobody set.
  _added=0
  for _k in $(grep -oE '^[A-Za-z0-9_]+=' .env.example | tr -d '=' | sort -u); do
    if ! grep -qE "^${_k}=" "$ENV_FILE"; then
      setkv "$_k" "$(awk -v k="$_k" 'index($0, k"=")==1 {sub("^" k "=", ""); print; exit}' .env.example)"
      _added=$((_added + 1))
    fi
  done
  # Values that WERE defaults in an older .env.example and are not any more.
  # Cleared so the interview ASKS, rather than the operator inheriting an
  # answer they never gave. Only values something later asks for belong here.
  _cleared=0
  for _pair in "ADMIN_EMAIL=admin@citra-ai.com" "ADMIN_EMAIL=admin@example.com" \
               "ADMIN_PASSWORD=ChangeMe!123" "ORG_ID=citra-ai"; do
    _k="${_pair%%=*}"; _v="${_pair#*=}"
    if [ "$(getkv "$_k")" = "$_v" ]; then setkv "$_k" ""; _cleared=$((_cleared + 1)); fi
  done

  # GENERATED secrets are a different case, and getting this wrong took a stack
  # down. ADMIN_API_KEY was in the list above, so a reconcile cleared it -- and
  # nothing refills it: setup.sh generates secrets only when it CREATES .env,
  # and .env.example now ships it empty. discovery-service then did exactly the
  # right thing and refused to boot rather than fall back to a default:
  #   RuntimeError: Required environment variable 'ADMIN_API_KEY' is not set
  # A secret nobody asks for must be REGENERATED, never merely cleared.
  _regen=0
  _cur="$(getkv ADMIN_API_KEY)"
  if [ -z "$_cur" ] || [ "$_cur" = "citra-local-admin-key-change-me" ]; then
    setkv ADMIN_API_KEY "$(rand 24)"; _regen=$((_regen + 1))
  fi

  # The remaining secrets are NOT regenerated on sight, deliberately. Rotating
  # CONNECTION_ENCRYPTION_KEY orphans every stored connection secret (they were
  # encrypted with the old one) and rotating JWT_SECRET invalidates every
  # session. Silently "repairing" either destroys data, so an empty one stops
  # here and says so.
  for _k in JWT_SECRET MCP_API_KEY MCP_SERVICE_API_KEY \
            SMART_APP_INTERNAL_SIGNING_KEY CONNECTION_ENCRYPTION_KEY; do
    if [ -z "$(getkv "$_k")" ]; then
      echo "  [FAIL] $_k is empty in .env." >&2
      echo "         It is not regenerated automatically: a new" >&2
      echo "         CONNECTION_ENCRYPTION_KEY cannot decrypt secrets stored under" >&2
      echo "         the old one, and a new JWT_SECRET signs nobody out cleanly." >&2
      echo "         Set it by hand, or start over with: $0 --fresh" >&2
      exit 1
    fi
  done
  echo "  [ok] reconciled with .env.example ($_added added, $_cleared cleared, $_regen regenerated)"
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
  echo "  [ok] secrets generated (JWT, MCP key + service key, signing + connection keys)"
  # NOT the admin password. This line used to claim one, and none was written
  # here -- the value came from .env.example, which shipped ChangeMe!123.
fi

# -- 2. AI provider -----------------------------------------------------------
# ONE provider, deliberately. A single OpenRouter key covers reasoning,
# embeddings, vision and search, so there is one key to paste and one thing
# that can be wrong. Both defaults are open-weights and swappable later by
# editing .env — self-hosting is the same edit, pointing *_BASE_URL at your own
# vLLM. Offering four providers here only multiplied the ways a first run could
# half-work (e.g. an OpenAI key that reasons but was never wired to embeddings).
step "the AI provider key"
hr; echo "$(b "Step 2/4 - AI provider (required)")"
echo "Citra calls an LLM for recommendations and NL->SQL, and an embedding"
echo "model to ground answers in your SOPs. One OpenRouter key covers both."
echo
echo "$(b "Getting a key") - two minutes, no card needed to start:"
echo "  1. Sign up at $(b "https://openrouter.ai")"
echo "  2. Open $(b "https://openrouter.ai/keys") and press Create Key"
echo "  3. Copy it once - OpenRouter shows the key only at creation"
echo
echo "OpenRouter offers free-tier models, which are enough to see the whole"
echo "loop work: build an app, get a recommendation, cite a SOP, approve it."
echo "Heavier use - a real corpus, a full queue, repeated builds - needs paid"
echo "credit topped up in your OpenRouter account. Nothing is billed through"
echo "Citra; the key is yours and the spend is visible in their dashboard."
echo
# Ask until there is a key. This used to EXIT on an empty answer -- fail loud,
# which was the right instinct with the wrong remedy: an empty paste is a slip,
# not a decision, and exiting threw away every answer given so far and made the
# operator start the wizard again. Ctrl-C is always available for a real stop.
#
# And the key is CHECKED here, not discovered to be wrong half an hour later.
# A truncated paste, a trailing space or a revoked key all wrote cleanly into
# eight .env variables and surfaced mid-demo as
#   LLM endpoint returned 401: Missing Authentication header
# with nothing pointing back at this prompt.
# 0 = OpenRouter accepted it, 1 = it rejected it, 2 = could not tell.
# Being unable to REACH OpenRouter says nothing about the key, so 2 is never
# treated as a failure -- it is reported and allowed through.
verify_key() {
  command -v curl >/dev/null 2>&1 || return 2
  case "$(curl -s -o /dev/null -m 12 -w '%{http_code}' \
            -H "Authorization: Bearer $1" \
            https://openrouter.ai/api/v1/key 2>/dev/null || true)" in
    200)     return 0 ;;
    401|403) return 1 ;;
    *)       return 2 ;;
  esac
}

key=""; _key_reused=0
_existing="$(getkv LLM_API_KEY)"
if [ -n "$_existing" ]; then
  # Checked, not assumed. A key already in .env is the common case on a re-run,
  # and asking someone to find and paste it again -- when we can confirm it in
  # one second -- is the kind of friction that makes people stop re-running.
  printf '  A key is already in .env (%s characters). Checking it... ' "${#_existing}"
  # Status captured explicitly. Reading $? in an elif after an if-condition
  # does work, and is one edit away from silently meaning something else.
  # `|| _vk=$?` also keeps a non-zero return from tripping `set -e`.
  _vk=0; verify_key "$_existing" || _vk=$?
  if [ "$_vk" = 0 ]; then
    echo "accepted."
    if yes_no "Use this key?" "y"; then key="$_existing"; _key_reused=1; ck_mark llm_key; fi
  elif [ "$_vk" = 1 ]; then
    echo "rejected."
    amber "  OpenRouter rejected the key in .env. Paste a current one."
  else
    echo "could not be checked."
    amber "  OpenRouter was unreachable, so the key in .env is unverified."
    if yes_no "Use it anyway?" "y"; then key="$_existing"; _key_reused=1; fi
  fi
fi
while [ -z "$key" ]; do
  key="$(ask_secret "Paste your OpenRouter API key")"
  if [ -z "$key" ]; then
    echo "  ! nothing pasted. A model is required -- Decision Apps cannot" >&2
    echo "    produce a recommendation without one. Ctrl-C to stop." >&2
    continue
  fi
  echo "  [ok] received ${#key} characters"
  case "$key" in
    sk-or-*) ;;
    *) echo "  [!] that does not look like an OpenRouter key -- they begin sk-or-v1-." >&2
       echo "      Continuing, in case you are pointing at another gateway." >&2 ;;
  esac
  # Definitive rejection re-asks; anything else is reported and allowed through,
  # because being unable to REACH OpenRouter says nothing about the key.
  if command -v curl >/dev/null 2>&1; then
    _code="$(curl -s -o /dev/null -m 12 -w '%{http_code}' \
              -H "Authorization: Bearer $key" \
              https://openrouter.ai/api/v1/key 2>/dev/null || true)"
    case "$_code" in
      200)     echo "  [ok] key verified with OpenRouter" ;;
      401|403) echo "  [X] OpenRouter rejected that key (HTTP $_code)." >&2
               echo "      Check for a truncated paste or a revoked key, then try again." >&2
               key="" ;;
      *)       echo "  [!] could not verify the key with OpenRouter (HTTP ${_code:-no response})." >&2
               echo "      Continuing -- if recommendations later fail with a 401, start here." >&2 ;;
    esac
  fi
done

# Stamped only when the key was actually checked against OpenRouter on THIS
# run. Re-stamping on the reuse path would put today's date on a verification
# that never happened, and the next run would report it as fresh.
if [ "$_key_reused" = "0" ]; then ck_mark llm_key; fi
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
# Validated. Everything downstream tests for exactly "2", so ANY other answer
# -- a typo, a trailing space, the word "demo" -- silently installed the demo
# over the database the operator meant to connect, with nothing said about it.
while :; do
  start_choice="$(ask "Choose 1-2" "1")"
  case "$start_choice" in
    1|2) break ;;
    *)   echo "  ! type 1 or 2" >&2 ;;
  esac
done

# -- 4. Super-admin + organisation --------------------------------------------
step "the super-admin"
hr; echo "$(b "Step 4/4 - super-admin")"
echo "The first user. Created as an admin OF the organisation below, which is"
echo "what makes the apps, sources and queues in it visible on sign-in."
echo
# No default unless .env already carries one. This used to offer
# admin@citra-ai.com, so anyone who pressed Enter ran their deployment on an
# account branded with the vendor's domain, unrelated to the org they are
# asked for a few lines later.
# Whatever is in .env is offered, EXCEPT the value .env.example used to ship.
# The comment above was written when this line was added and was true of the
# CODE and not of the FILE: .env.example still carried admin@citra-ai.com, step
# 1 copies that file to .env, and getkv duly offered the vendor's address back
# as the default. Same trap the ORG_ID line below already guards against; the
# guard is kept for anyone whose .env predates the .env.example fix.
cur_adm="$(getkv ADMIN_EMAIL)"
case "$cur_adm" in admin@citra-ai.com|admin@example.com) cur_adm="" ;; esac
adm_email="$(ask_required "Super-admin email (e.g. you@yourcompany.com)" "$cur_adm" valid_email)"
setkv ADMIN_EMAIL "$adm_email"

# ORG_ID is what start.sh passes to create-admin.js as --org. Leaving it at the
# .env.example default put the admin in an org with nothing in it: everything
# was installed correctly and the screen was empty anyway.
if [ "$start_choice" = "2" ]; then
  # Whatever is in .env is offered, EXCEPT the value .env.example ships:
  # inheriting that silently is what the comment above describes, an admin of
  # an org with nothing in it.
  cur_org="$(getkv ORG_ID)"; [ "$cur_org" = "citra-ai" ] && cur_org=""
  # NOT "e.g. acme-bank": that is the org the demo seeds into. Anyone who
  # followed the example landed their own database in the demo's organisation,
  # with two MCPs answering for one org and have_demo() keyed on it.
  org_id="$(ask_required "Organisation id (lowercase, no spaces, e.g. northwind)" "$cur_org" valid_slug)"
else
  # Not a free choice on the demo path: the demo's data, apps and officer
  # personas are all seeded into acme-bank, so an admin of any other org would
  # sign in to an empty screen.
  org_id="acme-bank"
  echo "  Organisation: $(b "acme-bank")   (not asked, on the demo path)"
  echo "  The demo's data, apps and officer personas are all seeded into that"
  echo "  organisation, so your super-admin is created there too. An admin of"
  echo "  any other org would sign in to an empty screen."
fi
setkv ORG_ID "$org_id"

# Asked, never generated. The old prompt defaulted to "n" and minted a random
# hex string instead, so almost nobody chose their own password and the one
# they got existed only in .env and in the closing banner -- which is how you
# end up locked out of a deployment with no mail provider to reset it.
#
# It then became "Keep the existing super-admin password? [y]" -- which on a
# first run asked the operator to keep a password they had never seen and did
# not set. .env.example shipped ChangeMe!123, step 1 copies that file, so the
# value was always "existing" and pressing Enter accepted a credential
# published in this repository. Never treat the shipped value as a choice.
cur_pw="$(getkv ADMIN_PASSWORD)"
case "$cur_pw" in 'ChangeMe!123') cur_pw="" ;; esac
if [ -n "$cur_pw" ]; then
  # A real one, set on an earlier run. Show it -- start.sh prints it in the
  # closing banner anyway -- so "keep or change" is a decision about something
  # the operator can actually see.
  echo "  This deployment already has a super-admin password:"
  echo "    $(b "$cur_pw")"
  while :; do
    new_pw="$(ask_secret "New password (blank keeps the one above)")"
    [ -z "$new_pw" ] && { echo "  [ok] keeping the existing password"; break; }
    if [ "${#new_pw}" -lt 8 ]; then printf '  ! at least 8 characters
' >&2; continue; fi
    confirm="$(ask_secret "Confirm password")"
    if [ "$new_pw" != "$confirm" ]; then printf '  ! they do not match
' >&2; continue; fi
    setkv ADMIN_PASSWORD "$new_pw"; echo "  [ok] password changed"; break
  done
else
  setkv ADMIN_PASSWORD "$(ask_password)"
fi
echo "  [ok] super-admin = $adm_email   admin of = $org_id"

step "bringing up the data stores"
hr; echo "$(b "Bringing up the data stores")"
if done_already stores have_stores; then
  echo "  [ok] already running (since $(ck_when stores)) - skipping setup."
  echo "      Re-run it yourself with: ./scripts/quickstart/setup.sh"
elif yes_no "Run setup now (data stores + database resources)?" "y"; then
  "$REPO_ROOT/scripts/quickstart/setup.sh"
  ck_mark stores
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

  step "building the ontology"
  hr; echo "$(b "Building the ontology")"
  echo "A model reads your schema and asks what it cannot infer."
  echo "Nothing is written until you confirm it."
  echo
  _skip_ontology=0
  if done_already ontology have_ontology; then
    echo "  An ontology from $(ck_when ontology) is already at my-source/sources.json."
    echo "  Rebuilding calls a model once per table and asks the same questions again."
    if ! yes_no "Build it again?" "n"; then _skip_ontology=1; fi
  fi
  if [ "$_skip_ontology" = "1" ]; then
    echo "  [ok] keeping the existing ontology"
  elif ! "$PY" "$REPO_ROOT/scripts/quickstart/build_ontology.py" \
        --kind "$db_kind" --conn "$db_conn" \
        --org-id "$org_id" --dept "$dept_id" \
        --out "$REPO_ROOT/my-source/sources.json"; then
    echo
    echo "  Ontology not written. Retry, or author it by hand:" >&2
    echo "    cp source-mcp-template/templates/<cell>.sources.json my-source/sources.json" >&2
    echo "    python source-mcp-template/validate_sources.py my-source/sources.json" >&2
    exit 1
  else
    ck_mark ontology
    # The MCP copies the registry in at generate time and loads it once at boot.
    # A rebuilt ontology is therefore NOT picked up by a container that is
    # already running -- and the step below would see it running and skip,
    # leaving the builder reading the tables from before the rebuild while the
    # wizard reported success. Recorded here so that step knows to recreate it.
    _ontology_rebuilt=1
  fi

  hr; echo "$(b "Starting the platform")"
  SOURCES_FILE="$REPO_ROOT/my-source/sources.json" \
    "$REPO_ROOT/scripts/quickstart/start.sh" --demo none

  step "creating your organisation"
  hr; echo "$(b "Creating your organisation")"
  # Without this the catalogue is scoped to an org that does not exist - which
  # fails SILENTLY, so it is a hard failure here rather than a warning.
  "$PY" "$REPO_ROOT/scripts/quickstart/seed_org.py" \
      --org-id "$org_id" --dept "$dept_id" --admin "$admin_email" \
      --sources "$REPO_ROOT/my-source/sources.json" --yes \
    || { echo "  [FAIL] organisation not created - the catalogue would be unreachable." >&2; exit 1; }

  # ---- An MCP in front of YOUR database -------------------------------------
  # sources.json describes the database; it does not SERVE it. Every read the
  # builder and the agents make goes through a source MCP, and until one is
  # running and registered with discovery, the catalogue is empty -- the
  # ontology looks built and nothing can query it.
  step "starting the MCP for your database"
  hr; echo "$(b "Putting an MCP in front of your database")"
  echo
  echo "The ontology describes your database. It does not serve it. Reads go"
  echo "through an MCP: one container, built from source-mcp-template, that"
  echo "loads the ontology you just approved and registers itself with the"
  echo "discovery service so the builder can find your tables."
  echo
  echo "$(b "Your credentials go to the .env on this machine") - never into"
  echo "sources.json, which stays reviewable and secret-free."
  echo
  MCP_SAFE_ORG="$(printf '%s' "$org_id" | tr '[:upper:]_' '[:lower:]-')"
  if [ "${_ontology_rebuilt:-0}" != "1" ] && done_already source_mcp have_source_mcp; then
    echo "  [ok] mcp-$MCP_SAFE_ORG is already running"
  else
    if [ "${_ontology_rebuilt:-0}" = "1" ] && have_source_mcp; then
      echo "  The ontology changed, so mcp-$MCP_SAFE_ORG is rebuilt rather than left"
      echo "  running - it loads its registry once, at boot."
      ck_drop source_mcp
    fi
    # A REST source was introspected from a spec URL, not a connection string --
    # passing that as --conn would parse a hostname out of a document location
    # and write it as a database host.
    mcp_args=(--org "$org_id" --depts "$dept_id"
              --sources "$REPO_ROOT/my-source/sources.json"
              --env-file "$ENV_FILE")
    case "$db_kind" in
      rest|api|openapi|swagger)
        echo "  A REST source has no connection string. The MCP is generated now;"
        echo "  set its ${db_kind}_* auth vars in .env before it can call anything." ;;
      *) mcp_args+=(--conn "$db_conn") ;;
    esac
    if ! "$PY" "$REPO_ROOT/scripts/quickstart/make_mcp.py" "${mcp_args[@]}" --up; then
      echo >&2
      red "  [FAIL] the MCP for your database did not start."
      echo "         Without it the catalogue stays empty and the builder has" >&2
      echo "         nothing to build against. Check the compose it wrote:" >&2
      echo "           deployments/$org_id/mcp/docker-compose.yml" >&2
      exit 1
    fi

    # Registration is what makes the source reachable, and the MCP logs a
    # startup banner whether or not it succeeded -- so the banner is not the
    # signal. Wait for the per-tool marker, and treat any failure line as fatal
    # (seed-demo.sh learned this from a cross-network DNS failure that logged
    # "ready" while every registration failed and the catalogue stayed at 0).
    echo "  waiting for it to register its sources with discovery"
    _mcp_compose="$REPO_ROOT/deployments/$org_id/mcp/docker-compose.yml"
    _reg=0
    for _ in $(seq 1 40); do
      _logs="$(docker compose --env-file "$ENV_FILE" -f "$_mcp_compose" logs 2>&1)"
      if printf '%s' "$_logs" | grep -qE "\[REGISTRATION\] Registered tool:" \
           && ! printf '%s' "$_logs" | grep -qE "\[REGISTRATION\] Failed to register"; then
        echo "  [ok] sources registered with discovery"; _reg=1; break
      fi
      sleep 2
    done
    if [ "$_reg" != 1 ]; then
      echo >&2
      red "  [FAIL] the MCP did not register within 80s."
      echo "         Its tables would be invisible to the builder, and every app" >&2
      echo "         built against them would fail 'source_not_found'." >&2
      echo "           docker compose -f $_mcp_compose logs" >&2
      exit 1
    fi
    ck_mark source_mcp
  fi
  # Read back rather than remembered from the run above: on a resume the block
  # is skipped entirely and the port still has to appear in the summary.
  MCP_PORT="$(grep -oE '"[0-9]+:8090"'       "$REPO_ROOT/deployments/$org_id/mcp/docker-compose.yml" 2>/dev/null       | head -1 | cut -d'"' -f2 | cut -d: -f1)"
  # The env_prefix the interview chose -- it names the .env keys holding this
  # source's credentials, and it is not guessable from the org id.
  MCP_PFX="$("$PY" -c "import json,sys
raw=json.load(open(sys.argv[1],encoding='utf-8'))
for d in (raw['sources'] if isinstance(raw,dict) else raw):
    p=((d.get('connection') or {}).get('env_prefix') or '').strip()
    if p: print(p); break" "$REPO_ROOT/my-source/sources.json" 2>/dev/null || true)"

  # ---- The catalogue -------------------------------------------------------
  # Registration tells discovery the MCP EXISTS; the crawl is what reads its
  # datasets into the catalogue the builder actually queries. data-discovery
  # crawls at startup, which for this path is BEFORE the MCP existed, so
  # without this the catalogue is empty however healthy everything looks.
  step "building the data catalogue"
  hr; echo "$(b "Reading your tables into the catalogue")"
  if ! JWT_SECRET="$(getkv JWT_SECRET)" \
        "$PY" "$REPO_ROOT/scripts/quickstart/build_catalogue.py" --org "$org_id"; then
    echo >&2
    red "  [FAIL] the catalogue crawl failed."
    echo "         The builder binds every app to a catalogue dataset, so with an" >&2
    echo "         empty catalogue nothing can be built. Re-run just this step:" >&2
    echo "           JWT_SECRET=... $PY scripts/quickstart/build_catalogue.py --org $org_id" >&2
    exit 1
  fi

  # ---- SOPs: the rules half of the product ----------------------------------
  # Connecting a database gives the agent facts. SOPs give it RULES -- what your
  # team already does with those facts. Without them a recommendation cannot
  # cite anything, which is most of the difference between this and a chatbot
  # over your database.
  step "ingesting your SOPs"
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
  if done_already sops have_sops; then
    echo "  SOPs were ingested on $(ck_when sops). Re-ingesting re-embeds every"
    echo "  document, which costs time and tokens for a corpus that has not changed."
    if ! yes_no "Ingest a folder of SOPs again?" "n"; then sop_dir=""; else sop_dir="__ask__"; fi
  else
    sop_dir="__ask__"
  fi
  if [ "$sop_dir" = "__ask__" ]; then
    sop_dir="$(ask "Folder of SOP documents (blank to upload from the UI later)" "")"
  fi

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
          && ck_mark sops \
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
  step "starting services and seeding the demo"
  hr; echo "$(b "What the acme-bank demo is")"
  echo
  echo "A retail bank and general insurer, invented for this demo. Postgres"
  echo "system-of-record: $(b "16 tables, ~211,000 rows") - customers, loan"
  echo "applications, disbursements, repayment schedules, delinquencies,"
  echo "collection activities, policies, claims, surveyor reports, leads."
  echo "$(b "Five departments, fourteen officer personas") you can sign in as."
  echo
  echo "Four Decision Apps come pre-built - $(b "so you have something to open on")"
  echo "$(b "minute one"), not because apps arrive with the product:"
  echo "  - $(b "Loan triage")           approve or decline against credit policy"
  echo "  - $(b "Collections priority")  who to chase today, grounded in what worked"
  echo "  - $(b "Claims triage")         settle or reject, with fraud screening"
  echo "  - $(b "Sales performance")     a dashboard app - no decisions"
  echo
  echo "$(b "The product is the builder, not these four.") Each was made the way you"
  echo "will make yours: someone described the work in a sentence on the home"
  echo "screen and answered questions while it was built. Open the builder and"
  echo "ask for a fifth - the ontology this demo seeds is what it reads to know"
  echo "which tables exist, what they mean, and which columns may be written."
  echo
  echo "Plus a SOP library in Milvus, so recommendations cite the actual policy."
  echo
  echo "$(b "Try this first:") open Claims triage, read the recommendation AND its"
  echo "citations, override one with a reason, then watch the governed write land"
  echo "in Postgres and the outcome fold into memory for the next case."
  echo
  _demo_flag="--demo acme-bank"
  if done_already demo have_demo; then
    echo "  The acme-bank data was seeded on $(ck_when demo) and is still in Postgres."
    echo "  Re-seeding rewrites all 16 tables - about 211,000 rows."
    if ! yes_no "Seed it again?" "n"; then
      _demo_flag="--no-demo"
      echo "  [ok] keeping the seeded data; starting services only"
    fi
  fi
  if yes_no "Start all services?" "y"; then
    # shellcheck disable=SC2086
    "$REPO_ROOT/scripts/quickstart/start.sh" $_demo_flag
    ck_mark services
    # NOT `[ ... ] && ck_mark demo`: a false test there returns non-zero as the
    # last command of the block, and `set -e` takes the whole wizard down.
    if [ "$_demo_flag" = "--demo acme-bank" ]; then ck_mark demo; fi
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
green "════════════════════════════════════════════════════════════════"
green " READY"
green "════════════════════════════════════════════════════════════════"
echo
# The whole block goes to the terminal by the same route, or it arrives out of
# order: fd 3 writes straight through while everything else queues behind tee,
# so mixing the two put the password line above the heading it belongs under.
secret_line "$(b "1. Open it")"
secret_line "     $(b "http://localhost:8081")"
secret_line "     email     $adm_email"
secret_line "     password  $adm_pw"
secret_line "     org       $org_id   (you are an admin of it, so you see everything)"
if [ -n "$TTY_FD" ]; then
  echo "$(b "1. Open it")   -- shown on screen only, kept out of this transcript"
fi
echo
echo "$(b "2. What this run did")"
if [ -f "$STATE_FILE" ]; then
  while IFS='=' read -r _n _t; do
    [ -n "$_n" ] && printf '     %s✓%s %-14s %s\n' "$C_GRN" "$C_OFF" "$_n" "$_t"
  done < "$STATE_FILE"
else
  echo "     (no checkpoints recorded)"
fi
echo
echo "$(b "3. Do this first")"
if [ "$start_choice" != "2" ]; then
  echo "     Open $(b "Claims triage") from the home screen. Read the recommendation"
  echo "     AND the citation under it, then override one with a reason. That"
  echo "     override is what the system learns from - three officers agreeing"
  echo "     on the same reason becomes a clause the next case cites."
  echo "     To see it as an officer rather than an admin:"
  echo "       user menu -> Login as User"
else
  echo "     Open the $(b "builder") from the home screen and describe an app in one"
  echo "     sentence. It reads the ontology you just built to know which tables"
  echo "     exist, what they mean, and which columns it may write."
fi
echo
echo "$(b "4. Look at the data while you use it")"
echo "     Mongo     mongodb://127.0.0.1:27017   db citra    apps, decisions, clauses"
echo "     Milvus    localhost:19530                         SOP vectors"
echo "     MinIO     http://localhost:9001                   uploaded files"
# The system-of-record differs by path, and this used to print the DEMO's
# Postgres either way -- so someone who had just connected their own database
# was handed acme-bank's host, port and database name as though it were theirs.
if [ "$start_choice" != "2" ]; then
  echo "     Postgres  localhost:15444             acme_bank   the bank's own records"
else
  echo "     Your own database is where it always was - this install did not"
  echo "     copy it. What it added is the MCP in front of it:"
  echo "       MCP     http://localhost:${MCP_PORT:-<port>}/health   serves your tables to the builder"
  echo "       compose deployments/$org_id/mcp/docker-compose.yml"
  if [ -n "${MCP_PFX:-}" ]; then
    echo "       credentials  ${MCP_PFX}_HOST / _PORT / _DB / _USER / _PASS in $(b ".env")"
  else
    echo "       credentials  the source's *_HOST / _PORT / _DB / _USER / _PASS keys in $(b ".env")"
  fi
fi
echo "     Passwords for all of them are in $(b ".env")."
echo
echo "$(b "5. Useful from here")"
echo "     make ps                              what is running"
echo "     make logs                            tail the core services"
echo "     make down                            stop, keep the data"
echo "     ./scripts/quickstart/wizard.sh       re-run; resumes, changes keys"
echo "     ./scripts/quickstart/wizard.sh --fresh   start over from nothing"
[ -n "${CITRA_SETUP_LOG:-}" ] && echo "     transcript of this run: $CITRA_SETUP_LOG"
echo
if [ "$start_choice" != "2" ]; then
  echo "Point it at your own data later:  $(b "docs/change-the-demo.md")"
fi
