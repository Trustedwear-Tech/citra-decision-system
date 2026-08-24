#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
build_via_builder.py — trigger a Citra Smart-App *builder* run and stream it.

This drives the real agentic build pipeline (smart-app-service POST /build),
the same one the SmartApp Builder UI uses: Internship -> Expertise -> Compose
-> Deploy, plus **Phase W (Workflow design)** when 'workflow' is in --kinds.

It does NOT hand-author or publish a JSON fixture — the builder pod discovers
the tenant catalogue, designs the agent + UI (+ workflow), and publishes.

Usage (from anywhere; needs `pip install pyjwt requests`):

  python build_via_builder.py --kinds app,workflow \
      --tenant bihar-gov --org bihar-gov --dept urban_dev \
      --goal "<plain-language goal>"

  # use the built-in Bihar grievance+workflow example goal:
  python build_via_builder.py --kinds app,workflow \
      --tenant bihar-gov --org bihar-gov --dept urban_dev

Env vars:
  SMART_APP_URL   smart-app-service base URL   (default http://127.0.0.1:9100)
  JWT_SECRET      shared HS256 secret          (default = local dev secret)

The build runs ~15-40 min. This script POSTs /build, then opens the build
chat stream and prints phase / message / tool events as they arrive. When the
stream ends it prints the session id; the published app appears in the Power
AI Apps list for the --org you passed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:
    import jwt as pyjwt
except ImportError:
    sys.exit("Missing dependency — run:  pip install pyjwt")

# Local-dev shared secret. MUST match the JWT_SECRET the running
# smart-app-service was started with, or /build returns 401.
_DEFAULT_SECRET = (
    "13ef57121c519b331e31ef2abbb516f4e64cec78b112c947f734ab8a2ce4020240"
    "cb60cea3a8654cdf8c6998395abdcfebd5211d7888fe65b579e96c898971c7"
)

# Built-in example: a Bihar grievance app *paired with a workflow* — the
# workflow auto-triages the clear-cut cases, the app queues the rest.
_EXAMPLE_GOAL = (
    "Build a Civic Grievance Auto-Triage solution for the Urban Development "
    "Department, Government of Bihar, in TWO parts. "
    "(1) A WORKFLOW that runs when a citizen grievance is lodged: it reads "
    "the grievance, and when the category is unambiguous (e.g. road_repair, "
    "street_light, drainage) it auto-routes the grievance to the owning "
    "department and stamps the SLA from the Civic Grievance Redressal "
    "Charter — no human needed. Grievances that are ambiguous, span "
    "departments, or look high-priority/public-safety are left un-routed for "
    "an officer. "
    "(2) A Smart App for the officer: a queue of ONLY the grievances the "
    "workflow could not auto-route, where the officer reviews each one with "
    "AI assistance (cited charter clause) and routes it; routing must be "
    "written back to the source system. "
    "Data source: the urban_grievances dept-MCP. Policy: govt_policy_library. "
    "You have full latitude on UI and workflow design — make sensible "
    "recommendations and proceed without waiting on me; only stop if "
    "genuinely blocked. Build and publish it."
)


def _mint(secret: str, *, user: str, org: str, tenant: str, depts: list[str]) -> str:
    """Mint a builder-caller JWT. Carries the SA-membership claims so the
    same token can also read the build session, and `iss` so downstream
    services (data-discovery) accept it."""
    now = int(time.time())
    sa = "svc:work-" + user.replace("@", "-").replace(".", "-") + f"@{org}.citra.ai"
    return pyjwt.encode(
        {
            "user_id": user,
            "sub": user,
            "tenant_id": tenant,
            "org_id": org,
            "dept_ids": depts,
            "roles": ["org_admin"],
            "work_sa_id": sa,
            "service_account_admin_of": [sa],
            "service_account_member_of": [sa],
            "iss": "Citra-AI",
            "iat": now,
            "exp": now + 10800,
        },
        secret,
        algorithm="HS256",
    )


def _post(url: str, token: str, body: dict, *, timeout: float = 120.0):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", default=_EXAMPLE_GOAL,
                    help="Plain-language build goal (default: the Bihar grievance+workflow example).")
    ap.add_argument("--kinds", default="app,workflow",
                    help="Comma list of build kinds: app, dashboard, workflow. Include 'workflow' to run Phase W.")
    ap.add_argument("--tenant", default="bihar-gov")
    ap.add_argument("--org", default="bihar-gov")
    ap.add_argument("--dept", default="urban_dev",
                    help="Comma list of dept ids the caller belongs to (drives MCP visibility).")
    ap.add_argument("--user", default="rohit@trustedweartech.com")
    ap.add_argument("--smart-app-url", default=os.getenv("SMART_APP_URL", "http://127.0.0.1:9100"))
    ap.add_argument("--jwt-secret", default=os.getenv("JWT_SECRET", _DEFAULT_SECRET))
    args = ap.parse_args()

    base = args.smart_app_url.rstrip("/")
    depts = [d.strip() for d in args.dept.split(",") if d.strip()]
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    token = _mint(args.jwt_secret, user=args.user, org=args.org, tenant=args.tenant, depts=depts)

    # ---- 1. health check -------------------------------------------------
    try:
        urllib.request.urlopen(base + "/health", timeout=10)
    except Exception as exc:  # noqa: BLE001
        return _die(f"smart-app-service unreachable at {base}: {exc}")

    # ---- 2. POST /build — spawns the ephemeral builder pod ---------------
    print(f"→ POST /build   kinds={kinds}  tenant={args.tenant}")
    try:
        resp = _post(base + "/build", token,
                     {"goal": args.goal, "tenant_id": args.tenant, "build_kinds": kinds})
        d = json.load(resp)
    except urllib.error.HTTPError as e:
        return _die(f"/build failed ({e.code}): {e.read().decode()[:400]}")
    sid = d["session_id"]
    print(f"  session_id = {sid}   pod spawned\n")

    # ---- 3. drive the build — stream the chat turn -----------------------
    print("→ streaming build (Internship → Expertise → Compose → Deploy"
          + (" ; Phase W for the workflow" if "workflow" in kinds else "") + ")\n")
    try:
        resp = _post(base + f"/build/{sid}/chat/stream", token,
                     {"message": args.goal}, timeout=6000)
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line or line.startswith(": ") or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except ValueError:
                continue
            t = ev.get("type")
            if t == "message":
                print("  " + (ev.get("text") or "")[:300], flush=True)
            elif t == "tool_call":
                print("    · tool: " + str(ev.get("name")), flush=True)
            elif t in ("done", "error"):
                print(f"  [{t}] {json.dumps(ev)[:300]}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\n(stream ended: {exc!r})", flush=True)

    print(f"\n✔ build session {sid} finished streaming.")
    print(f"  Published app(s) appear in Power AI Apps for org '{args.org}'.")
    print(f"  Inspect the pod workspace if needed:")
    print(f"    docker exec $(docker ps --format '{{{{.Names}}}}' | grep {sid[:18]}) "
          f"sh -c 'ls -1 /workspace/build/'")
    return 0


def _die(msg: str) -> int:
    print("ERROR: " + msg, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
