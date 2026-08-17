#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.
#
# Friendly front door to scripts/quickstart/build_ontology.py.
#
# It exists to do the three things the raw tool does not: ask for what it needs
# in plain English, check everything BEFORE the first paid model call, and print
# the exact command it runs so you learn the tool instead of depending on this.
#
# Run with no arguments for an interactive walkthrough, or pass flags to script
# it. --help explains every option.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TOOL="$REPO_ROOT/scripts/quickstart/build_ontology.py"
MCP="$REPO_ROOT/source-mcp-template"

b()   { printf '\033[1m%s\033[0m' "$1"; }
hr()  { printf '%s\n' "------------------------------------------------------------"; }
die() { printf '\n  [FAIL] %s\n' "$1" >&2; exit 1; }

ORG=""; DEPT=""; KIND=""; CONN=""; OUT=""; MODEL=""; ROUNDS=""
API_KEY="${LLM_API_KEY:-}"; BASE_URL="${LLM_BASE_URL:-}"; ENV_FILE=""
ASSUME_YES=0; CHECK_ONLY=0

usage() {
  cat <<'HELP_TEXT'
make-ontology.sh — build a sources.json for a database, by interview

WHAT IT DOES
  Runs an agent that connects to your database, reads its structure itself, and
  asks you about the things a scan cannot tell it — which table records past
  decisions, what each document column IS, which column is money. It then writes
  a sources.json and refuses to leave one behind that would not boot.

  Structure it reads. MEANING it asks about. A confident wrong guess there is
  worse than no answer, because screening runs on the wrong document and still
  looks correct.

WHAT IT WILL ASK YOU
  · decision_history  — which table records decisions already made, and which
                        column IS the decision
  · artifact_role     — for each document/image column: evidence / identity /
                        payment_proof / supporting  (the costliest field to get
                        wrong)
  · value_semantics   — which column is money, and what it means
  · domain            — asked last, and optional

USAGE
  ./scripts/quickstart/make-ontology.sh                 # interactive
  ./scripts/quickstart/make-ontology.sh --org acme-bank --dept claims \
      --kind postgres --conn "postgresql://user:pw@localhost:15432/acme"

OPTIONS
  --org ID           organisation id, e.g. acme-bank        (asked if omitted)
  --dept ID          department id, e.g. claims             (asked if omitted)
  --kind KIND        postgres | mysql | mssql | oracle | mongo | odata |
                     salesforce | rest | bigquery | snowflake | ...
  --conn STRING      connection string. Stays on this machine and is NEVER
                     sent to the model — the agent asks for schema via tools.
  --out PATH         where to write. Default:
                     demo-data/tenants/<org>/mcp/sources.json
  --model NAME       override the model (default: deepseek/deepseek-v4-pro)
  --rounds N         tool-round cap, protects your credit (default: 20)
  --api-key KEY      LLM key. Default: $LLM_API_KEY
  --env-file PATH    read LLM_API_KEY / LLM_BASE_URL from this file
  --check            run the preflight checks and stop. No model calls, no cost.
  --yes              skip the final confirmation (scripted runs)
  -h, --help         this text

COST
  Real money — a few cents to a few tens of cents per run, depending on how big
  your schema is and how much back-and-forth it takes. --rounds caps it.

CONFIG
  This script does not read or write any service .env file. It needs exactly two
  values, from the environment or --api-key / --env-file:
      LLM_API_KEY      required
      LLM_BASE_URL     optional, defaults to https://openrouter.ai/api/v1

AFTER IT RUNS
  The file is validated against source-mcp-template/validate_sources.py before
  the script exits non-zero or zero. To re-check it later by hand:
      make validate-sources FILE=<path>
HELP_TEXT
}

while [ $# -gt 0 ]; do
  case "$1" in
    --org)      ORG="${2:-}"; shift 2 ;;
    --dept)     DEPT="${2:-}"; shift 2 ;;
    --kind)     KIND="${2:-}"; shift 2 ;;
    --conn)     CONN="${2:-}"; shift 2 ;;
    --out)      OUT="${2:-}"; shift 2 ;;
    --model)    MODEL="${2:-}"; shift 2 ;;
    --rounds)   ROUNDS="${2:-}"; shift 2 ;;
    --api-key)  API_KEY="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --check)    CHECK_ONLY=1; shift ;;
    --yes|-y)   ASSUME_YES=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    *)          die "unknown option '$1' — try --help" ;;
  esac
done

# --env-file is explicit on purpose. Hunting through service .env files for a
# key would reintroduce exactly the config coupling this repo does not want.
if [ -n "$ENV_FILE" ]; then
  [ -f "$ENV_FILE" ] || die "no such env file: $ENV_FILE"
  v="$(grep -E '^LLM_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  [ -n "$v" ] && API_KEY="$v"
  v="$(grep -E '^LLM_BASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  [ -n "$v" ] && BASE_URL="$v"
fi

PY=""
for c in python3 python; do command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }; done
[ -n "$PY" ] || die "no python interpreter on PATH"

ask() {  # ask <prompt> <default>
  local p="$1" d="${2:-}" a
  if [ -n "$d" ]; then read -r -p "  $p [$d]: " a || true; echo "${a:-$d}"
  else read -r -p "  $p: " a || true; echo "$a"; fi
}

if [ -z "$ORG$DEPT$KIND$CONN" ]; then
  clear 2>/dev/null || true
  echo "$(b "Build an ontology (sources.json) from a live database")"
  echo
  echo "An agent will read your schema and interview you about what it means."
  echo "Your connection string stays on this machine — it is never sent to the"
  echo "model. Press Ctrl-C at any point to stop."
  hr
fi

[ -n "$ORG"  ] || ORG="$(ask 'Organisation id (lowercase, e.g. acme-bank)' 'acme-bank')"
[ -n "$DEPT" ] || DEPT="$(ask 'Department id (e.g. claims, ops, underwriting)' 'ops')"
[ -n "$KIND" ] || KIND="$(ask 'Database kind (postgres | mysql | mssql | oracle | mongo | odata | salesforce | rest)' 'postgres')"
if [ -z "$CONN" ]; then
  echo
  echo "  The connection string is used locally to read your schema."
  echo "  Example: postgresql://user:password@localhost:15432/dbname"
  CONN="$(ask 'Connection string')"
fi
[ -n "$CONN" ] || die "a connection string is required — there is nothing to read without one"
[ -n "$OUT"  ] || OUT="$REPO_ROOT/demo-data/tenants/$ORG/mcp/sources.json"

# ── Preflight — everything checkable before the first paid call ──────────────
hr; echo "$(b "Preflight")"
[ -f "$TOOL" ] || die "generator not found at $TOOL"
echo "  [ok] generator            $(basename "$TOOL")"

for f in "schema/sources.schema.json" "templates/README.md" \
         "templates/banking-loan_recovery-IN.sources.json" "validate_sources.py"; do
  [ -f "$MCP/$f" ] || die "missing $MCP/$f — the agent builds its prompt from these"
done
echo "  [ok] ontology schema      $(wc -c < "$MCP/schema/sources.schema.json" | tr -d ' ') bytes"

if [ -z "$API_KEY" ]; then
  die "LLM_API_KEY is not set.
         Provide it one of three ways:
           export LLM_API_KEY=sk-...
           --api-key sk-...
           --env-file /path/to/.env
         This script deliberately does not go looking through service .env files."
fi
echo "  [ok] LLM key              ${API_KEY:0:10}… (${#API_KEY} chars)"

# Connection check. Cheap, and finding out the database is unreachable AFTER
# paying for a model call is the worst possible ordering.
if ! "$PY" - "$KIND" "$CONN" <<'PYCHK'
import sys
kind, conn = sys.argv[1], sys.argv[2]
sys.path.insert(0, __import__("os").path.join(__import__("os").getcwd(), "scripts", "quickstart"))
try:
    import introspect_source as ins
except Exception as e:
    print(f"  [warn] could not import introspect_source ({e}); skipping the connection check")
    sys.exit(0)
if kind not in getattr(ins, "_SQL_KINDS", set()):
    print(f"  [--] connection check skipped for kind '{kind}' — the first tool round will surface any problem")
    sys.exit(0)
try:
    import sqlalchemy
    sqlalchemy.create_engine(conn).connect().close()
    print("  [ok] database             reachable")
except Exception as e:
    msg = str(e).splitlines()[0][:160]
    pkg = getattr(ins, "_DIALECT_PKG", {}).get(kind)
    print(f"  [FAIL] cannot connect: {msg}", file=sys.stderr)
    if pkg:
        print(f"         this kind needs: pip install {pkg}", file=sys.stderr)
    sys.exit(1)
PYCHK
then
  die "fix the connection before spending a model call on it"
fi

mkdir -p "$(dirname "$OUT")"
echo "  [ok] output directory     $(dirname "$OUT")"

if [ "$CHECK_ONLY" = "1" ]; then
  hr; echo "Preflight only — nothing was called and nothing was written."; exit 0
fi

# ── Show the real command, then run it ──────────────────────────────────────
ARGS=(--kind "$KIND" --conn "$CONN" --org-id "$ORG" --dept "$DEPT" --out "$OUT")
[ -n "$MODEL"  ] && ARGS+=(--model "$MODEL")
[ -n "$ROUNDS" ] && ARGS+=(--rounds "$ROUNDS")
[ "$ASSUME_YES" = "1" ] && ARGS+=(--yes)

hr; echo "$(b "About to run")"
echo
echo "  LLM_API_KEY=… $PY scripts/quickstart/build_ontology.py \\"
echo "      --kind $KIND --conn '<hidden>' \\"
echo "      --org-id $ORG --dept $DEPT \\"
echo "      --out ${OUT#"$REPO_ROOT/"}"
echo
echo "  This makes paid model calls. The agent will ask you questions —"
echo "  answer them in your own words."
echo

if [ "$ASSUME_YES" != "1" ]; then
  read -r -p "  Go? (y/n) [y]: " go || true
  case "${go:-y}" in y|Y|yes|YES) ;; *) echo "  Stopped."; exit 1 ;; esac
fi

hr
export LLM_API_KEY="$API_KEY"
[ -n "$BASE_URL" ] && export LLM_BASE_URL="$BASE_URL"
cd "$REPO_ROOT"
set +e
"$PY" "$TOOL" "${ARGS[@]}"
rc=$?
set -e

hr
if [ $rc -eq 0 ]; then
  echo "$(b "Done.")  $OUT"
  echo "  It has been validated against source-mcp-template/validate_sources.py."
  echo
  echo "  Next:"
  echo "    1. Read it. The agent states what it inferred — check those lines."
  echo "    2. Point an MCP at it and restart that container."
  echo "    3. Re-crawl so the catalogue picks up the new datasets."
else
  echo "$(b "Not completed") (exit $rc)."
  echo "  If a file was written it did NOT validate and was left in place so you"
  echo "  can read it. Do not boot an MCP against it — the registry is strict and"
  echo "  would fail at startup."
  echo "  Re-check by hand:  make validate-sources FILE=$OUT"
fi
exit $rc
