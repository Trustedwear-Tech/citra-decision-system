#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# PROPRIETARY - all rights reserved. See LICENSE.md. NOT an open-source grant.
# SPDX-License-Identifier: LicenseRef-Citra-AI-Proprietary
"""
Trigger a one-shot data-discovery crawl so the searchable `data_catalogue` (used by
the Decision-App BUILDER to find datasets) is built from the registered MCPs.

Catalogue-building is NOT automatic: data-discovery-service comes up with the stack
but only crawls on `POST /crawl/run`, or on a background timer if `CRAWL_ENABLED=true`.
This mints a short-lived org-scoped token (HS256, JWT_SECRET — the SAME claims the
service's own background crawler uses: org_admin + tenant_id, so MCP visibility lets
it read every source in the org) and POSTs `/crawl/run`. The crawler enumerates the
org's registered MCPs and pulls each one's /datasets schemas into `data_catalogue`.

  python scripts/build_catalogue.py --org acme-power

Note: query execution does NOT need the catalogue (the MCP serves NL->query straight
from dept_sources); this only populates the builder's dataset palette / catalogue search.
Requires `requests` + `pyjwt`, and the MCP(s) for the org must already be running +
registered with discovery-service.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import jwt  # pyjwt
import requests


def _crawler_token(secret: str, org: str, issuer: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "user_id": "discovery-crawler-system", "sub": "discovery-crawler-system",
            "org_id": org, "tenant_id": org,
            "roles": ["user", "org_admin"], "crawler_system": True,
            "iss": issuer, "iat": now, "exp": now + 600,
        },
        secret, algorithm="HS256",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Trigger a data-discovery crawl to build data_catalogue for an org.")
    ap.add_argument("--org", required=True, help="org/tenant to crawl (must match the MCP's ORG_ID)")
    ap.add_argument("--url", default="http://localhost:8095", help="data-discovery-service base URL")
    ap.add_argument("--jwt-secret", default=os.getenv("JWT_SECRET", ""), help="defaults to $JWT_SECRET")
    ap.add_argument("--issuer", default="Citra-AI", help="JWT issuer (must match the service's jwt_issuer)")
    a = ap.parse_args()
    if not a.jwt_secret:
        print("[FAIL] no signing secret — set $JWT_SECRET or pass --jwt-secret")
        return 2

    token = _crawler_token(a.jwt_secret, a.org, a.issuer)
    try:
        r = requests.post(f"{a.url.rstrip('/')}/crawl/run",
                          headers={"Authorization": f"Bearer {token}"}, timeout=180)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] could not reach data-discovery-service at {a.url}: {exc}")
        return 2
    if r.status_code >= 300:
        print(f"[FAIL] crawl/run -> {r.status_code} {r.text[:300]}")
        return 1
    data = r.json()
    total = data.get("total_datasets", "?")
    reports = data.get("reports") or []
    print(f"[ok] crawl complete for org '{a.org}': {total} dataset(s) catalogued across {len(reports)} MCP(s)")
    for rep in reports:
        label = rep.get("mcp_tool_id") or rep.get("source_id") or "?"
        n = rep.get("datasets_written", rep.get("datasets_seen", "?"))
        errs = rep.get("errors") or []
        print(f"     - {label}: {n} dataset(s) catalogued"
              + (f"  [errors: {'; '.join(map(str, errs))}]" if errs else ""))
    if total in (0, "0"):
        print("     ! 0 datasets — is the org's MCP running + registered? (check its logs for 'source(s) registered')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
