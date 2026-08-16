#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Which databases can this repo actually connect to — derived, never typed.

A hand-written list goes stale the first time a connector is added. Everything
below is read from the code that decides it:

  * `_SQL_KINDS` / `_DIALECT_PKG` in introspect_source.py  -> what can be SCANNED
  * `source-mcp-template/connectors/*.py`                  -> what can be QUERIED
  * `SourceType` / `DatasetKind` in registry_models.py     -> what can be DECLARED

The interesting tier is computed, not listed: a source that is declarable and
queryable but has no introspection path can be hand-authored and never scanned.
Add an introspector for it and it promotes itself here with no edit.

    python list_sources.py           # tiered, for humans
    python list_sources.py --json    # machine-readable
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP = REPO_ROOT / "source-mcp-template"
INTROSPECT = REPO_ROOT / "scripts" / "quickstart" / "introspect_source.py"

#: Drivers that ship pinned, so these work with no extra install. Read from the
#: requirements file rather than assumed.
_DRIVER_FOR = {
    "postgres": "psycopg2", "mysql": "pymysql", "mongo": "pymongo",
}
_PRETTY = {
    "postgres": "PostgreSQL", "mysql": "MySQL", "mssql": "SQL Server",
    "oracle": "Oracle", "bigquery": "BigQuery", "snowflake": "Snowflake",
    "redshift": "Redshift", "databricks": "Databricks", "trino": "Trino",
    "mongo": "MongoDB", "odata": "OData / SAP", "salesforce": "Salesforce",
    "rest": "REST / OpenAPI", "duckdb": "DuckDB", "sap_rfc": "SAP RFC",
    "file": "Local files", "gcs": "Google Cloud Storage", "sql": "Generic SQL",
}


def _literal(name: str, text: str):
    """Pull a set/dict literal out of introspect_source.py without importing it
    (importing drags in optional DB drivers we may not have)."""
    m = re.search(rf"^{name}\s*=\s*([\{{\[].*?[\}}\]])\s*$", text, re.S | re.M)
    if not m:
        return None
    try:
        return eval(m.group(1), {"__builtins__": {}}, {})  # noqa: S307 — our own source
    except Exception:  # noqa: BLE001
        return None


def collect() -> dict:
    text = INTROSPECT.read_text(encoding="utf-8") if INTROSPECT.exists() else ""
    sql_kinds = _literal("_SQL_KINDS", text) or set()
    dialect_pkg = _literal("_DIALECT_PKG", text) or {}

    # non-SQL introspectors are functions, not a table
    non_sql = {k for k in ("mongo", "odata", "salesforce", "rest")
               if f"def introspect_{'openapi' if k == 'rest' else k}(" in text}
    introspectable = set(sql_kinds) | non_sql

    queryable = {p.stem.replace("_connector", "")
                 for p in (MCP / "connectors").glob("*_connector.py")}

    declarable: set = set()
    spec = importlib.util.spec_from_file_location("rm", MCP / "registry_models.py")
    if spec and spec.loader:
        try:
            sys.path.insert(0, str(MCP))
            rm = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rm)
            declarable = {e.value for e in rm.DatasetKind} | {e.value for e in rm.SourceType}
        except Exception as exc:  # noqa: BLE001 — degrade, do not crash the wizard
            print(f"  ! could not read registry_models ({exc})", file=sys.stderr)
        finally:
            sys.path[:0] = []

    reqs = ""
    for f in MCP.glob("requirements*.txt"):
        reqs += f.read_text(encoding="utf-8")

    ready, needs_install = [], []
    for k in sorted(sql_kinds | non_sql):
        drv = _DRIVER_FOR.get(k)
        pinned = bool(drv and re.search(rf"^{drv}", reqs, re.M | re.I))
        if pinned:
            ready.append({"kind": k, "name": _PRETTY.get(k, k)})
        elif k in dialect_pkg:
            needs_install.append({"kind": k, "name": _PRETTY.get(k, k),
                                  "install": dialect_pkg[k]})
        elif k in ("mssql", "oracle"):
            needs_install.append({"kind": k, "name": _PRETTY.get(k, k),
                                  "install": "pyodbc" if k == "mssql" else "oracledb"})
        elif k in non_sql:
            ready.append({"kind": k, "name": _PRETTY.get(k, k), "hand_tune": True})

    # Same thing wears different names in different layers (a Salesforce source
    # is `salesforce` to the introspector and `soql` to the connector and the
    # DatasetKind enum). Normalise, or the tiers disagree with themselves.
    ALIAS = {"soql": "salesforce", "mongodb": "mongo", "rest_api": "rest",
             "sap": "odata"}
    def norm(s):
        return {ALIAS.get(x, x) for x in s}

    # Not databases — they are the shape of a source, not a backend.
    NOT_A_BACKEND = {"semantic", "structured", "sql"}

    introspectable_n = norm(introspectable)
    declarable_n = norm(declarable) - NOT_A_BACKEND
    queryable_n = norm(queryable) - NOT_A_BACKEND

    # THE computed tier: the platform accepts it and can serve it, but nothing
    # here can read its schema — so it must be hand-authored. duckdb is the
    # live example: a valid SourceType AND DatasetKind with no introspector.
    runtime_only = sorted((declarable_n | queryable_n) - introspectable_n)

    return {
        "ready": [r for r in ready if not r.get("hand_tune")],
        "hand_tune": [r for r in ready if r.get("hand_tune")],
        "needs_install": needs_install,
        "runtime_only": [{"kind": k, "name": _PRETTY.get(k, k)} for k in runtime_only],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    d = collect()
    if args.json:
        print(json.dumps(d, indent=2))
        return 0

    print("  Ready now - the wizard can scan these and build end to end:")
    print("      " + ", ".join(r["name"] for r in d["ready"]) or "      (none)")
    if d["needs_install"]:
        print()
        print("  Also supported, one install first:")
        for r in d["needs_install"]:
            print(f"      {r['name']:<14} pip install {r['install']}")
    if d["hand_tune"]:
        print()
        print("  Connectable and scannable, but expect to hand-tune the result:")
        print("      " + ", ".join(r["name"] for r in d["hand_tune"]))
    if d["runtime_only"]:
        print()
        print("  Connectable but NOT scannable - hand-author these:")
        print("      " + ", ".join(r["name"] for r in d["runtime_only"]))
        print("      -> source-mcp-template/docs/sources-file.md   (field reference)")
        print("      -> source-mcp-template/connectors/            (what each can do)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
