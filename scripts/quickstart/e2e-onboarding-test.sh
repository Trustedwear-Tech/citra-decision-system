#!/usr/bin/env bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

#
# End-to-end onboarding test for the CUSTOM-source flow, run against the live
# local stack (`docker compose up -d` must already be running):
#
#   seed org + dept + user  ->  register a real source (introspect schema)
#   ->  generate + start a custom MCP (self-registers with discovery)
#   ->  build the catalogue (crawl)  ->  verify each stage  ->  clean up.
#
# Uses the demo Postgres (citra-postgres / acme_bank db) as a stand-in customer
# source: introspected from the host via localhost:5432, served to the MCP via the
# in-network hostname citra-postgres. No LLM key required (schema = introspection,
# catalogue = crawl; the optional LLM describe / NL-query steps are skipped).
#
#   ./scripts/quickstart/e2e-onboarding-test.sh
#
set -uo pipefail
cd "$(dirname "$0")/../.."
PY="$([ -x .venv-seed/bin/python ] && echo .venv-seed/bin/python || echo .venv-seed/Scripts/python)"

ORG=e2ecorp; DEPT=ops; SRC=billing; PFX=E2ECORP_SQL; PORT=8588
BA_EMAIL="ba@${ORG}.test"
PG_HOST_INTROSPECT="localhost:5432"
PG_HOST_SERVE="citra-postgres"
PG_DB=acme_bank; PG_USER=acme_bank; PG_PASS=acme_bank_demo_pw
COMPOSE="deployments/${ORG}/mcp/docker-compose.yml"
FAILED=0

step(){ echo; echo "──────────────────────────────────────────────"; echo "> $1"; }
ok(){   echo "  [PASS] $1"; }
bad(){  echo "  [FAIL] $1"; FAILED=1; }
mongo_eval(){ docker compose -f docker-compose.quickstart.yml exec -T mongodb mongosh --quiet -u root -p citradev --authenticationDatabase admin --eval "$1" 2>/dev/null; }

export JWT_SECRET="$(grep -E '^JWT_SECRET=' .env | cut -d= -f2-)"

cleanup(){
  step "CLEANUP"
  [ -f "$COMPOSE" ] && docker compose -f "$COMPOSE" down >/dev/null 2>&1 && echo "  MCP container removed"
  mongo_eval "c=db.getSiblingDB('citra'); c.data_catalogue.deleteMany({tenant_id:'${ORG}'}); c.users.deleteMany({org_id:'${ORG}'}); c.orgs.deleteOne({id:'${ORG}'}); print('  db cleaned');" | tail -1
  rm -rf "deployments/${ORG}"; rmdir deployments 2>/dev/null
  echo "  deployment files removed"
}
trap cleanup EXIT

step "0. Preflight - stack health"
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:7004/health 2>/dev/null); [ "$code" = 200 ] && ok "user-service up" || bad "user-service ($code)"
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8095/health 2>/dev/null); [ "$code" = 200 ] && ok "data-discovery up" || bad "data-discovery ($code)"
mongo_eval "db.runCommand({ping:1}).ok" | grep -q 1 && ok "mongo up" || bad "mongo"
[ "$FAILED" = 1 ] && { echo "Preflight failed - is 'docker compose up -d' running?"; exit 1; }

step "1. Seed org + dept + BA user (user-service)"
"$PY" scripts/seed_org_user.py --org "$ORG" --org-name "E2E Corp" --dept "$DEPT" --dept-name "Operations" \
  --user-email "$BA_EMAIL" --roles "dept_admin" | sed 's/^/  /'
u=$(curl -s -X POST http://localhost:7004/api/auth/local/dev-login -H "Content-Type: application/json" -d "{\"email\":\"$BA_EMAIL\"}")
echo "$u" | "$PY" -c "import sys,json; d=json.load(sys.stdin).get('data',{}).get('user',{}); print('  BA login -> org='+str(d.get('org_id'))+' depts='+str(d.get('dept_ids'))+' roles='+str(d.get('roles')))" 2>/dev/null
echo "$u" | grep -q "\"org_id\":\"$ORG\"" && ok "BA user seeded + can log in" || bad "BA user seed"

step "2. Introspect the Postgres schema -> sources.json"
# The registry is a FILE. register_source.py's Mongo upsert is gone: the
# central-Mongo dept_sources load mode was removed from the MCP on 2026-07-10,
# so writing that collection would populate something nothing reads.
SRC_JSON="deployments/${ORG}/mcp/sources.json"
mkdir -p "$(dirname "$SRC_JSON")"
"$PY" scripts/quickstart/introspect_source.py   --conn "postgresql://${PG_USER}:${PG_PASS}@${PG_HOST_INTROSPECT}/${PG_DB}"   --org "$ORG" --dept "$DEPT" --source "$SRC" --name "Billing"   --tables customers,loan_applications,bureau_pulls --env-prefix "$PFX"   --out "$SRC_JSON" 2>&1 | tail -3 | sed 's/^/  /'

step "3. Verify the registry file is valid for the MCP"
# RegistrySource is extra="forbid": an unknown key here is a HARD BOOT FAILURE,
# not a warning. Validate before we ever start a container.
if "$PY" source-mcp-template/validate_sources.py "$SRC_JSON" >/dev/null 2>&1; then
  ok "sources.json validates against the registry schema"
else
  "$PY" source-mcp-template/validate_sources.py "$SRC_JSON" 2>&1 | tail -5 | sed 's/^/  /'
  bad "sources.json failed schema validation"
fi
n=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); d=d.get('sources',d); print(len(d))" "$SRC_JSON" 2>/dev/null || echo 0)
t=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); d=d.get('sources',d); print(sum(len(x.get('datasets',[])) for x in d))" "$SRC_JSON" 2>/dev/null || echo 0)
echo "  sources=$n datasets=$t"
[ "$n" = 1 ] && [ "${t:-0}" -ge 3 ] && ok "registry written (1 source, $t tables introspected)" || bad "registry (sources=$n datasets=$t)"

step "4. make_mcp -> generate compose + fill creds + start"
"$PY" scripts/quickstart/make_mcp.py --org "$ORG" --port "$PORT" --sources "$SRC_JSON" 2>&1 | tail -2 | sed 's/^/  /'
sed -i "s#${PFX}_HOST: \"\"#${PFX}_HOST: \"${PG_HOST_SERVE}\"#; s#${PFX}_DB: \"\"#${PFX}_DB: \"${PG_DB}\"#; s#${PFX}_USER: \"\"#${PFX}_USER: \"${PG_USER}\"#; s#${PFX}_PASS: \"\"#${PFX}_PASS: \"${PG_PASS}\"#" "$COMPOSE"
docker compose --env-file .env -f "$COMPOSE" up -d --build >/dev/null 2>&1 && echo "  MCP started"

step "5. Verify MCP health + discovery registration"
hcode=""
for i in $(seq 1 30); do hcode=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:${PORT}/health 2>/dev/null); [ "$hcode" = 200 ] && break; sleep 2; done
[ "$hcode" = 200 ] && ok "MCP healthy on :${PORT}" || bad "MCP health ($hcode)"
reg=0
for i in $(seq 1 15); do reg=$(docker logs "mcp-${ORG}" 2>&1 | grep -cE "source\(s\) registered"); [ "${reg:-0}" -ge 1 ] && break; sleep 2; done
[ "${reg:-0}" -ge 1 ] && ok "MCP self-registered with discovery" || bad "MCP registration"

step "6. build_catalogue -> crawl the MCP -> data_catalogue"
"$PY" scripts/quickstart/build_catalogue.py --org "$ORG" 2>&1 | tail -3 | sed 's/^/  /'
cat=$(mongo_eval "print(db.getSiblingDB('citra').data_catalogue.countDocuments({tenant_id:'${ORG}'}))" | tr -d '[:space:]')
mongo_eval "db.getSiblingDB('citra').data_catalogue.find({tenant_id:'${ORG}'},{dataset_id:1,_id:0}).forEach(x=>print('   - '+x.dataset_id))"
[ "${cat:-0}" -ge 3 ] && ok "catalogue built ($cat datasets)" || bad "catalogue ($cat datasets)"

step "RESULT"
if [ "$FAILED" = 0 ]; then echo "  PASSED - full custom-source onboarding works end to end."; else echo "  FAILED - see [FAIL] lines above."; fi
exit $FAILED
