# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Probe the LIVE embed endpoints against a running smart-app-service.

Not a stub in sight: real HTTP, real Mongo, real middleware, real auth.

    SAS=http://127.0.0.1:9177 python scripts/probe_embed_e2e.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt

SAS = os.getenv("SAS", "http://127.0.0.1:9177")
SLUG = "embed-e2e-loan"
TEST_KEY = "emb_test_e2e0000000000001"
LIVE_KEY = "emb_live_e2e0000000000002"

env = (Path(__file__).resolve().parents[1] / ".env").read_text(
    encoding="utf-8", errors="ignore")
SECRET = re.search(r"^JWT_SECRET=(.*)$", env, re.M).group(1).strip()

TOKEN = jwt.encode(
    {
        "sub": "officer@acme-bank.com", "user_id": "officer@acme-bank.com",
        "email": "officer@acme-bank.com", "tenant_id": "acme-bank",
        "org_id": "acme-bank", "roles": ["user"], "dept_ids": ["lending"],
        "iat": int(time.time()), "exp": int(time.time()) + 3600,
        "iss": "Citra-AI",
    },
    SECRET, algorithm="HS256",
)

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def call(path, headers=None, method="GET"):
    req = urllib.request.Request(f"{SAS}{path}", method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


print("\n— spec resolution by key —")
s, b = call(f"/embed/{LIVE_KEY}/spec")
check("live key resolves", s == 200, f"HTTP {s} {b}")
check("environment is prod", isinstance(b, dict) and b.get("environment") == "prod", str(b)[:120])
check("slug returned", isinstance(b, dict) and b.get("slug") == SLUG)
check("embed page identified", isinstance(b, dict) and b.get("page_id") == "card")
check("agent_spec included", isinstance(b, dict) and bool(b.get("agent_spec")))

s, b = call(f"/embed/{TEST_KEY}/spec")
check("test key resolves", s == 200, f"HTTP {s}")
check("environment is test", isinstance(b, dict) and b.get("environment") == "test", str(b)[:120])

s, _ = call(f"/embed/{SLUG}/spec")
check("a SLUG is not an embed key (404)", s == 404, f"HTTP {s}")
s, _ = call("/embed/emb_live_doesnotexist00/spec")
check("unknown key is 404", s == 404, f"HTTP {s}")

print("\n— environment binding through the middleware —")
# THE defect: a promoted app resolves prod by slug. The test key must flip it.
s, b = call(f"/apps/{SLUG}/embed/snippet")
check("snippet without key binds prod", s == 200 and b.get("environment") == "prod",
      f"HTTP {s} {str(b)[:140]}")
base_env = b.get("environment") if isinstance(b, dict) else None

s2, b2 = call(f"/apps/{SLUG}/embed/snippet", {"X-Citra-Embed-Key": TEST_KEY})
check("snippet WITH test key binds test", s2 == 200 and b2.get("environment") == "test",
      f"HTTP {s2} {str(b2)[:140]}")
check("the header actually changed the answer",
      base_env == "prod" and isinstance(b2, dict) and b2.get("environment") == "test",
      "if these matched, the probe would prove nothing")

s3, b3 = call(f"/apps/{SLUG}/embed/snippet",
              {"X-Citra-Embed-Key": "emb_test_0000000000000000"})
check("a key naming no app is ignored", s3 == 200 and b3.get("environment") == "prod",
      f"HTTP {s3} {str(b3)[:120]}")

print("\n— the snippet a BA copies —")
s, b = call(f"/apps/{SLUG}/embed/snippet")
snip = b.get("snippet", "") if isinstance(b, dict) else ""
check("key is prefilled", LIVE_KEY in snip)
check("script url is prefilled", b.get("script_url", "") in snip and "/v1/citra.js" in snip)
check("uses the public API", "Citra.init" in snip and "citra.mount" in snip)

print("\n— auth —")
req = urllib.request.Request(f"{SAS}/embed/{LIVE_KEY}/spec")
try:
    urllib.request.urlopen(req, timeout=20)
    check("unauthenticated spec is refused", False, "got 200")
except urllib.error.HTTPError as e:
    check("unauthenticated spec is refused", e.code in (401, 403), f"HTTP {e.code}")

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(1 if failed else 0)
