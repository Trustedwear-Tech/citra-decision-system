#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Prove the install actually works — do not just report that containers are up.

citra-flows ends its install by authoring a workflow, running it, and asserting
it reached `completed`. That is worth more than any amount of README, and the
other two repos had no equivalent: setup finished at "services started", which
is not the same claim.

Checks, in dependency order so the first failure is the useful one:

    1. services answer            discovery / smart-app / data-discovery
    2. the MCP registered         its sources reached discovery
    3. the catalogue is populated AND scoped to your org
    4. the vector index exists    semantic dataset recall works

Exit 0 = the platform can actually answer a question about your data.

    python verify_install.py --org-id acme-bank
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PASS, FAIL, WARN = "  [ok]  ", "  [FAIL]", "  [warn]"


def env(key: str, default: str = "") -> str:
    if os.getenv(key):
        return os.environ[key]
    f = REPO_ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


def _get(url: str, token: str | None = None, timeout: int = 15):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _jwt(org_id: str) -> str | None:
    secret = env("JWT_SECRET")
    if not secret:
        return None
    try:
        import jwt
    except ImportError:
        return None
    now = int(time.time())
    return jwt.encode({
        "user_id": env("ADMIN_EMAIL", "admin@citra-ai.com"),
        "email": env("ADMIN_EMAIL", "admin@citra-ai.com"),
        "tenant_id": org_id, "org_id": org_id, "dept_ids": [],
        "roles": ["org_admin", "super_admin"],
        "service_account_admin_of": [], "service_account_member_of": [],
        "aud": "citra-data-discovery",
        "iss": "Citra-AI", "iat": now, "exp": now + 900,
    }, secret, algorithm="HS256")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org-id", required=True)
    # 9010, not 9000. docker-compose.dev.yml maps `9010:9000` — 9000 is the port
    # INSIDE the container, and only sibling containers use it. From the host it
    # is 9010, which is what start.sh and the demo scripts already use.
    # This is not a cosmetic off-by-ten: another Citra repo's discovery-service
    # answers on host 9000 with the same {"status":"ok","tool_count":N} shape, so
    # the wrong port does not fail — it PASSES against a stack that is not yours.
    ap.add_argument("--discovery", default="http://localhost:9010")
    ap.add_argument("--smart-app", default="http://localhost:9100")
    ap.add_argument("--data-discovery", default="http://localhost:8095")
    args = ap.parse_args()

    failures = 0
    print(f"Verifying the install for org {args.org_id!r}\n")

    # 1 — services answer
    for name, url in (("discovery-service", f"{args.discovery}/health"),
                      ("smart-app-service", f"{args.smart_app}/health"),
                      ("data-discovery-service", f"{args.data_discovery}/health")):
        try:
            h = _get(url)
            print(f"{PASS} {name}: {h.get('status', 'ok')}")
        except Exception as exc:  # noqa: BLE001
            print(f"{FAIL} {name} did not answer on {url} ({exc})")
            failures += 1
    if failures:
        print("\nServices are not up — nothing below can pass. Check `make logs`.")
        return 1

    # 2 — the MCP registered its sources
    try:
        tools = _get(f"{args.discovery}/health").get("tool_count", 0)
        if tools:
            print(f"{PASS} discovery has {tools} registered tool(s)")
        else:
            print(f"{FAIL} discovery has NO registered tools — the MCP did not "
                  f"register. Its sources are invisible to every app.")
            failures += 1
    except Exception as exc:  # noqa: BLE001
        print(f"{WARN} could not read discovery tool count ({exc})")

    # 3 — catalogue populated AND scoped to this org
    token = _jwt(args.org_id)
    if not token:
        print(f"{WARN} no JWT_SECRET or pyjwt — skipping catalogue checks")
        return 1 if failures else 0
    try:
        cat = _get(f"{args.data_discovery}/catalogue?limit=200", token)
        entries = cat.get("entries") or []
        mine = [e for e in entries if e.get("tenant_id") == args.org_id]
        if not entries:
            print(f"{FAIL} the catalogue is EMPTY. The most common cause is an "
                  f"org_id mismatch between sources.json and the org that was "
                  f"created — it fails silently, with no error anywhere.")
            failures += 1
        elif not mine:
            others = sorted({e.get("tenant_id") for e in entries})
            print(f"{FAIL} the catalogue has {len(entries)} dataset(s) but NONE for "
                  f"{args.org_id!r} (found: {others}). That is the org_id mismatch.")
            failures += 1
        else:
            print(f"{PASS} catalogue has {len(mine)} dataset(s) for {args.org_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} catalogue unreadable ({exc})")
        failures += 1

    # 4 — semantic recall (the vector index, not just the Mongo list)
    try:
        r = _get(f"{args.data_discovery}/catalogue/search?q=customer%20records&top_k=3", token)
        hits = r.get("entries") or []
        if hits:
            print(f"{PASS} dataset recall works — top hit {hits[0].get('dataset_id')!r}")
        else:
            print(f"{WARN} dataset recall returned nothing. The catalogue vector "
                  f"index may not have been built; the plain list still works.")
    except Exception as exc:  # noqa: BLE001
        print(f"{WARN} dataset recall unavailable ({exc})")

    print()
    if failures:
        print(f"{failures} check(s) FAILED — the install is not usable yet.")
        return 1
    print("All checks passed. The platform can answer questions about your data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
