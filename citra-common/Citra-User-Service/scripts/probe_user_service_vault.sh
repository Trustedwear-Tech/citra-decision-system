#!/bin/bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Probe Vault to find the real mount + path the user-service uses.
# Prior attempts assumed mount=kv but Vault returned "no handler for route kv/data/...",
# so the mount is named something else (likely 'prod' as a top-level mount).
set -e
VT="$VAULT_ROOT_TOKEN"

echo "=== 0. Vault mounts (secret engines) ==="
curl -sS -H "X-Vault-Token: $VT" http://127.0.0.1:8200/v1/sys/mounts \
  | python3 -c 'import sys,json
d = json.load(sys.stdin)
for k, v in sorted(d.items()):
    if k.startswith(("cubbyhole","identity","sys")): continue
    opts = v.get("options") or {}
    print(f"  {k:24s} type={v.get(\"type\"):10s} kv-version={opts.get(\"version\",\"-\")}")'

echo
echo "=== 1. user-service container VAULT_* env (lengths only) ==="
sudo docker exec citra-ai-user-service-prod-1 sh -c \
  'cat /proc/1/environ | tr "\0" "\n" | grep ^VAULT' \
  | awk -F= '{print $1 "  len=" length($2)}'

echo
echo "=== 2. read prod/citra-ai-user-service via the 'prod' mount (kv-v2) ==="
curl -sS -H "X-Vault-Token: $VT" \
  http://127.0.0.1:8200/v1/prod/data/citra-ai-user-service \
  | python3 -c 'import sys,json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print("  not-json:", e); sys.exit(0)
if "errors" in d:
    print("  errors:", d["errors"])
else:
    data = d.get("data",{}).get("data",{}) or {}
    print(f"  keys at prod/data/citra-ai-user-service ({len(data)}):")
    for k in sorted(data.keys()):
        v = data[k]
        ln = len(str(v)) if v is not None else 0
        print(f"    {k:38s} len={ln}")'

echo
echo "=== 3. list paths at the prod kv-v2 mount ==="
curl -sS -H "X-Vault-Token: $VT" -X LIST \
  http://127.0.0.1:8200/v1/prod/metadata \
  | python3 -c 'import sys,json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print("  not-json:", e); sys.exit(0)
if "errors" in d:
    print("  errors:", d["errors"])
else:
    for k in d.get("data",{}).get("keys",[]):
        print(" ", k)'

echo
echo "=== 4. also try kv-v1 style at prod/citra-ai-user-service ==="
curl -sS -o /tmp/v1.json -w "HTTP %{http_code}\n" -H "X-Vault-Token: $VT" \
  http://127.0.0.1:8200/v1/prod/citra-ai-user-service
if [ -s /tmp/v1.json ]; then
  python3 -c 'import json
with open("/tmp/v1.json") as f: d = json.load(f)
if "errors" in d:
    print("  errors:", d["errors"])
else:
    data = d.get("data",{}) or {}
    print(f"  keys at prod/citra-ai-user-service v1 ({len(data)}):")
    for k in sorted(data.keys()):
        print(f"    {k:38s} len={len(str(data[k]))}")'
fi
