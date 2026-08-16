# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Publish all demo apps to a running smart-app-service.

Each `apps/*.json` is already a full PublishRequest body. We mint a
test JWT with `scope=smart-app-builder` (matches the auth contract from
require_publish_scope) and POST each fixture.

Usage:
    python scripts/publish_apps.py \\
        --smart-app-url http://localhost:9100 \\
        --apps-dir apps/
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict

import jwt as pyjwt
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _mint(secret: str, *, tenant_id: str, user_id: str = "demo-publisher") -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "org_id": tenant_id,
        "dept_ids": ["plant_ops", "quality", "sales_dispatch"],
        "roles": ["org_admin"],
        "scope": "smart-app-builder",
        # Issuer MUST be present — data-discovery (and other services) verify with
        # issuer="Citra-AI"; without it the catalogue lookup during publish is 401'd.
        "iss": "Citra-AI",
        "iat": now,
        "exp": now + 3600,
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smart-app-url", default=os.getenv(
        "SMART_APP_SERVICE_URL", "http://localhost:9100"))
    ap.add_argument("--jwt-secret", default=os.getenv("JWT_SECRET", "test-only-not-for-prod"))
    ap.add_argument("--tenant-id", default="acme-cement",
                    help="Tenant id; apps are read from tenants/<tenant-id>/apps/ unless --apps-dir overrides")
    ap.add_argument("--apps-dir", default=None,
                    help="Override path to apps folder (defaults to tenants/<tenant-id>/apps/)")
    args = ap.parse_args()

    base = args.smart_app_url.rstrip("/")
    try:
        health = requests.get(f"{base}/health", timeout=5)
        health.raise_for_status()
        log.info("smart-app-service: %s", health.json())
    except requests.RequestException as exc:
        log.error("smart-app-service unreachable at %s: %s", base, exc)
        return 3

    token = _mint(args.jwt_secret, tenant_id=args.tenant_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    apps_dir = Path(args.apps_dir) if args.apps_dir else (
        Path(__file__).resolve().parent.parent / "tenants" / args.tenant_id / "apps"
    )
    fixtures = sorted(apps_dir.glob("*.json"))
    log.info("Found %d apps in %s", len(fixtures), apps_dir)

    ok = 0
    failed = 0
    for f in fixtures:
        raw = json.loads(f.read_text(encoding="utf-8"))
        payload = {k: v for k, v in raw.items() if not k.startswith("_")}
        slug = payload["app_spec"]["slug"]
        log.info("→ %s  (slug=%s)", f.name, slug)
        try:
            r = requests.post(f"{base}/publish", json=payload,
                              headers=headers, timeout=30)
            if 200 <= r.status_code < 300:
                body = r.json()
                ok += 1
                log.info("   OK  app_id=%s version=%s", body.get("app_id"), body.get("version"))
            else:
                failed += 1
                log.error("   FAIL  %d  %s", r.status_code, r.text[:500])
        except requests.RequestException as exc:
            failed += 1
            log.error("   FAIL  %s", exc)

    log.info("Done. published=%d / %d", ok, len(fixtures))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
