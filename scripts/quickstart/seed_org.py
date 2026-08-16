#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Create a REAL organisation — not a demo tenant — and its first admin.

The demo path seeds an org via `seed-demo.sh`; the custom path had no equivalent,
so a bring-your-own-database user ended up with a registered MCP and a populated
catalogue that no organisation could reach. Since the catalogue is `tenant_id`
scoped, an org whose id matches the sources file has to exist or the whole thing
is invisible.

This writes the two fixtures `seed_tenant.py` already consumes and calls it. It
does NOT reimplement seeding — that script is already generic and POSTs only to
public Citra-User-Service admin APIs.

    python seed_org.py                                   # interactive
    python seed_org.py --org-id acme --dept ops \\
                       --admin ops@acme.com --yes         # scripted

Defaults are read from your sources.json where possible, because org_id and
dept_id MUST match it. A mismatch is not an error anywhere — it just produces an
empty catalogue, which is the silent failure this whole flow exists to remove.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TENANTS_ROOT = REPO_ROOT / "tenants"      # NOT demo-data/
SEED_TENANT = REPO_ROOT / "demo-data" / "scripts" / "seed_tenant.py"

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


def _env(key: str, default: str = "") -> str:
    f = REPO_ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


def _from_sources(path: Path) -> tuple[str | None, str | None]:
    """(org_id, dept_id) as the registry declares them — the values that must match."""
    if not path or not path.exists():
        return None, None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a broken sources file is not this tool's problem
        return None, None
    srcs = doc if isinstance(doc, list) else (doc.get("sources") or [])
    org = next((s.get("org_id") for s in srcs if s.get("org_id")), None)
    dept = next((s.get("dept_id") for s in srcs if s.get("dept_id")), None)
    return org, dept


def _ask(q: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{q}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def _mint_admin_jwt(secret: str, email: str) -> str:
    """Same claims seed-demo.sh mints — reused, not reinvented."""
    import jwt
    now = int(time.time())
    return jwt.encode({
        "user_id": email, "email": email, "org_id": "citra-ai",
        "roles": ["super_admin"], "dept_ids": [],
        "iss": "Citra-AI", "iat": now, "exp": now + 3600,
    }, secret, algorithm="HS256")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org-id")
    ap.add_argument("--org-name")
    ap.add_argument("--dept")
    ap.add_argument("--dept-name")
    ap.add_argument("--admin", help="email of the organisation's first admin")
    ap.add_argument("--sources", default=str(REPO_ROOT / "my-source" / "sources.json"),
                    help="sources.json to take org_id/dept_id defaults from")
    ap.add_argument("--tenants-root", default=str(DEFAULT_TENANTS_ROOT))
    ap.add_argument("--user-service-url", default="http://localhost:7004")
    ap.add_argument("--yes", action="store_true", help="no prompts; requires the flags")
    args = ap.parse_args()

    src_org, src_dept = _from_sources(Path(args.sources))
    if src_org:
        print(f"  sources.json declares org_id={src_org!r} dept_id={src_dept!r}")

    org_id = args.org_id or (src_org if args.yes else _ask("Organisation id", src_org or ""))
    if not org_id or not _SLUG.match(org_id):
        print(f"  organisation id {org_id!r} must be a lowercase slug "
              f"(letters, digits, hyphens), e.g. 'acme-bank'.", file=sys.stderr)
        return 2
    org_name = args.org_name or (org_id if args.yes else _ask("Organisation name", org_id.replace("-", " ").title()))
    dept_id = args.dept or (src_dept if args.yes else _ask("First department id", src_dept or "ops"))
    if not dept_id or not _SLUG.match(dept_id):
        print(f"  department id {dept_id!r} must be a lowercase slug.", file=sys.stderr)
        return 2
    dept_name = args.dept_name or (dept_id if args.yes else _ask("Department name", dept_id.replace("-", " ").title()))
    admin = args.admin or ("" if args.yes else _ask("Admin email for this org", f"admin@{org_id}.local"))
    if not admin or "@" not in admin:
        print("  an admin email is required.", file=sys.stderr)
        return 2

    # FAIL LOUD on divergence — this is the silent-empty-catalogue trap.
    problems = []
    if src_org and src_org != org_id:
        problems.append(f"sources.json says org_id={src_org!r}, you gave {org_id!r}")
    if src_dept and src_dept != dept_id:
        problems.append(f"sources.json says dept_id={src_dept!r}, you gave {dept_id!r}")
    if problems:
        print("\n  These MUST match your sources.json or the catalogue will be scoped to")
        print("  an organisation that does not exist — no error, just nothing there:")
        for p in problems:
            print(f"    - {p}")
        if args.yes or _ask("  Continue anyway? (y/n)", "n").lower() not in ("y", "yes"):
            return 2

    # -- write the fixtures seed_tenant.py consumes ---------------------------
    root = Path(args.tenants_root) / org_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "tenant.json").write_text(json.dumps({
        "org": {"id": org_id, "name": org_name,
                "domain": f"{org_id}.local", "entity_type": "corporate",
                "industry": "", "is_demo": False},      # a real org, not a demo
        "depts": [{"id": dept_id, "name": dept_name, "parent_id": None}],
    }, indent=2), encoding="utf-8")
    (root / "users.json").write_text(json.dumps([{
        "email": admin, "name": "Organisation Admin",
        "entity_type": "corporate", "dept_ids": [dept_id],
        "roles": ["org_admin"], "user_type": "paid", "is_demo": False,
        "default_password": _env("ADMIN_PASSWORD") or "ChangeMe@Citra1",
    }], indent=2), encoding="utf-8")
    print(f"  wrote {root / 'tenant.json'}")
    print(f"  wrote {root / 'users.json'}")

    # -- call the existing generic seeder -------------------------------------
    secret = _env("JWT_SECRET")
    if not secret:
        print("  JWT_SECRET not found in .env — run the wizard first.", file=sys.stderr)
        return 2
    token = _mint_admin_jwt(secret, _env("ADMIN_EMAIL", "admin@citra-ai.com"))
    cmd = [sys.executable, str(SEED_TENANT), "--tenant", org_id,
           "--admin-token", token, "--user-service-url", args.user_service_url,
           "--tenants-root", str(Path(args.tenants_root))]
    print(f"  seeding via {SEED_TENANT.name} ...")
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"  seed_tenant.py exited {rc} — the org was NOT created.", file=sys.stderr)
        return rc
    print(f"\n  organisation {org_id!r} ready, department {dept_id!r}, admin {admin}")
    print(f"  password: the ADMIN_PASSWORD from .env (change it on first sign-in)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
