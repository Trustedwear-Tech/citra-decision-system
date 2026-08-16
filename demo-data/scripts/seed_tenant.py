# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Seed a demo tenant (org + depts + users) via Citra-User-Service admin APIs.

This is the generic, pluggable replacement for the old tenant-hardcoded
seed_demo_users.py. To add a new demo company:

    1. Create demo-data/tenants/<tenant-id>/tenant.json with the org +
       depts. Schema:
         {
           "org":   { "id", "name", "domain", "is_demo": true, ... },
           "depts": [ { "id", "name", "parent_id?" }, ... ]
         }
    2. Create demo-data/tenants/<tenant-id>/users.json with persona list.
    3. Run:
         python demo-data/scripts/seed_tenant.py \\
             --tenant <tenant-id> \\
             --admin-token "$(cat ~/.citra-admin-jwt)" \\
             --user-service-url http://localhost:7004

The script only POSTs to public admin APIs (no Mongo writes, no
user-service config edits). It is idempotent — re-running is a no-op
on already-seeded rows.

Admin-API endpoints used:
    POST /api/admin/orgs                 — create the tenant org
    POST /api/admin/depts                — upsert each dept under the org
    POST /api/admin/users                — invite each demo persona

How to get an admin token:
    Sign in to Citra-UI as a citra-ai super_admin
    (ADMIN_EMAILS=<your-email> on Citra-User-Service triggers auto-promote
    on first login). Copy the JWT from browser localStorage.

Demo personas are PLACEHOLDERS without passwords — they cannot log in
directly. Use the Impersonate User screen ("Demo personas" tab) which
groups personas by tenant, so a super_admin can demo any company's
flow by impersonating that company's persona.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DEMO_DATA_ROOT = Path(__file__).resolve().parent.parent  # demo-data/


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ensure_org(base: str, token: str, org: Dict[str, Any]) -> None:
    body = {
        "id": org["id"],
        "name": org.get("name") or org["id"],
        "domain": org.get("domain"),
        "is_demo": bool(org.get("is_demo", True)),
    }
    r = requests.post(f"{base}/api/admin/orgs", json=body, headers=_headers(token), timeout=15)
    if r.status_code == 201:
        log.info("[org] created %s", org["id"])
    elif r.status_code == 409:
        log.info("[org] already exists %s (skip)", org["id"])
    elif r.status_code == 403:
        raise RuntimeError(
            "admin token rejected (403). Confirm token belongs to a super_admin."
        )
    else:
        r.raise_for_status()


def _ensure_dept(base: str, token: str, org_id: str, dept: Dict[str, Any]) -> None:
    body = {
        "org_id": org_id,
        "id": dept["id"],
        "name": dept.get("name") or dept["id"],
        "parent_id": dept.get("parent_id"),
    }
    r = requests.post(f"{base}/api/admin/depts", json=body, headers=_headers(token), timeout=15)
    if r.ok:
        log.info("[dept] upserted %s/%s", org_id, dept["id"])
    elif r.status_code == 403:
        raise RuntimeError("admin token rejected creating dept (403). super_admin required.")
    else:
        log.warning("[dept] %s/%s failed: %s %s", org_id, dept["id"], r.status_code, r.text[:200])


def _ensure_user(base: str, token: str, org_id: str, user: Dict[str, Any]) -> None:
    body = {
        "email": user["email"],
        "name": user.get("name") or user["email"].split("@")[0],
        "mobile": user.get("mobile"),
        "org_id": org_id,
        "entity_type": user.get("entity_type", "company"),
        "dept_ids": user.get("dept_ids", []),
        "roles": user.get("roles", ["user"]),
        "user_type": user.get("user_type", "paid"),
        "activate": True,
    }
    r = requests.post(f"{base}/api/admin/users", json=body, headers=_headers(token), timeout=15)
    if r.ok:
        log.info("[user] upserted %s (roles=%s, depts=%s)", user["email"], body["roles"], body["dept_ids"])
    elif r.status_code == 403:
        raise RuntimeError(f"admin token rejected creating user {user['email']} (403).")
    else:
        log.warning("[user] %s failed: %s %s", user["email"], r.status_code, r.text[:200])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True, help="Tenant id (folder name under demo-data/tenants/)")
    ap.add_argument("--admin-token", required=True, help="super_admin JWT")
    ap.add_argument("--user-service-url", default="http://localhost:7004")
    ap.add_argument("--tenants-root", default=str(DEMO_DATA_ROOT / "tenants"))
    args = ap.parse_args()

    tenant_dir = Path(args.tenants_root) / args.tenant
    if not tenant_dir.is_dir():
        log.error("tenant folder not found: %s", tenant_dir)
        sys.exit(2)

    tenant_path = tenant_dir / "tenant.json"
    users_path  = tenant_dir / "users.json"
    if not tenant_path.exists():
        log.error("missing %s", tenant_path)
        sys.exit(2)
    if not users_path.exists():
        log.error("missing %s", users_path)
        sys.exit(2)

    tenant_doc = json.loads(tenant_path.read_text(encoding="utf-8"))
    users_doc  = json.loads(users_path.read_text(encoding="utf-8"))

    org = tenant_doc.get("org") or {}
    if not org.get("id"):
        log.error("%s: org.id missing", tenant_path)
        sys.exit(2)

    log.info("=== Seeding tenant %s ===", org["id"])
    _ensure_org(args.user_service_url, args.admin_token, org)

    for dept in tenant_doc.get("depts", []):
        _ensure_dept(args.user_service_url, args.admin_token, org["id"], dept)

    users = users_doc if isinstance(users_doc, list) else users_doc.get("users", [])
    for u in users:
        _ensure_user(args.user_service_url, args.admin_token, org["id"], u)

    log.info("=== Done: %s (org + %d depts + %d users) ===",
             org["id"], len(tenant_doc.get("depts", [])), len(users))


if __name__ == "__main__":
    main()
