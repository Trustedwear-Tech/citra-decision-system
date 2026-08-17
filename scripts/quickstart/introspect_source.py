#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
introspect_source.py — scan a live data source → a sources.json registry entry.

Point it at a database you already have and it writes the MCP's source registry
for you: datasets, columns, types, primary keys, foreign-key relationships, plus
enum/range enrichment for low-cardinality and numeric/date columns. No
hand-authoring. Optionally (--describe) an LLM fills the descriptions, semantic
types and PII flags, which is what the query planner actually reasons over.

Connectors: PostgreSQL, MySQL, SQL Server, MongoDB, OData/SAP, Salesforce, REST.

WHERE THE OUTPUT GOES (changed 2026-07-10)
------------------------------------------
The MCP is FILE-DEFINED. This script merges into the sources.json that the MCP
mounts at /app/sources.json via SOURCES_FILE.

Its ancestor (register_source.py, June 2026) upserted into the Mongo
`dept_sources` collection instead. That load mode was REMOVED from
source-mcp-template/config.py -- not deprecated -- and the MCP now refuses to
boot without SOURCES_FILE or SOURCES_JSON. Writing `dept_sources` today
populates a collection nothing reads, so every Mongo path is gone from here.

The registry model is `extra="forbid"`, so the entry this builds is validated
against source-mcp-template/schema/sources.schema.json before you are told it
worked. An unknown key is a hard MCP boot failure, not a warning.

Credentials are passed only to introspect. The emitted entry stores a
`connection.env_prefix` naming the env vars the MCP reads creds from -- never
the secret itself.

Usage
-----
    python scripts/quickstart/introspect_source.py \\
        --conn "postgresql://user:pw@host:5432/db" \\
        --org acme-bank --dept lending --source loan_origination \\
        --tables loan_applications,applicants \\
        --name "Loan Origination" \\
        --env-prefix ACME_BANK_SQL \\
        --describe \\
        --out demo-data/tenants/acme-bank/mcp/sources.json

Then restart the MCP that mounts that file. See docs/change-the-demo.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import sys
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("register_source")

# Distinct-value count at/under which a string column is treated as an enum.
ENUM_MAX_CARDINALITY = 25
# Sample rows fetched per table (kept for future LLM phases; not stored here).
SAMPLE_ROWS = 5


# ── SQL type → Citra dataset column type ────────────────────────────────────
def _citra_type(sql_type: str) -> str:
    t = sql_type.upper()
    if any(k in t for k in ("CHAR", "TEXT", "STRING", "UUID", "JSON", "ENUM", "CLOB")):
        return "string"
    if any(k in t for k in ("BOOL",)):
        return "boolean"
    if any(k in t for k in ("TIMESTAMP", "DATETIME")):
        return "datetime"
    if "DATE" in t:
        return "date"
    if "TIME" in t:
        return "time"
    if any(k in t for k in ("INT", "SERIAL")):
        return "integer"
    if any(k in t for k in ("NUMERIC", "DECIMAL", "FLOAT", "REAL", "DOUBLE", "MONEY", "NUMBER")):
        return "number"
    return "string"


def _humanize(name: str) -> str:
    """consumer_id → 'Consumer Id'; bill_amt → 'Bill Amt'."""
    return re.sub(r"[_\-]+", " ", name).strip().title()


# ── Introspection dispatch by source kind ───────────────────────────────────
# Every "SQL kind" is just a SQLAlchemy dialect — including the warehouses. Point
# --conn at the right connection string and (if needed) `pip install` the dialect;
# the same introspection runs. Warehouses that don't enforce keys simply report no
# PK/FK (columns/types still introspect).
_SQL_KINDS = {"sql", "postgres", "mysql", "mssql", "oracle",
              "bigquery", "snowflake", "redshift", "databricks", "trino"}
_KIND_TYPE = {k: k for k in _SQL_KINDS} | {
    "sql": "postgres", "mongo": "mongo", "odata": "odata", "sap": "odata",
    "salesforce": "salesforce", "soql": "salesforce",
    "rest": "rest", "api": "rest", "openapi": "rest", "swagger": "rest",
}
# Top-level `type` the MCP's catalogue._source_kind dispatches on. SQL/warehouses +
# odata/salesforce ride "structured" (sub-dispatched by connection.type); Mongo, REST,
# and BigQuery need their own top-level type or they fall through to the SQL path.
_TOP_TYPE = {
    "mongo": "mongodb",
    "rest": "rest_api", "api": "rest_api", "openapi": "rest_api", "swagger": "rest_api",
    "bigquery": "bigquery",
}
# pip hint per dialect when the package isn't installed.
_DIALECT_PKG = {"bigquery": "sqlalchemy-bigquery", "snowflake": "snowflake-sqlalchemy",
                "redshift": "sqlalchemy-redshift", "databricks": "databricks-sqlalchemy",
                "trino": "trino[sqlalchemy]"}


def introspect(kind: str, conn_str: str, wanted: List[str]) -> List[Dict[str, Any]]:
    kind = (kind or "sql").lower()
    if kind in _SQL_KINDS:
        return introspect_sql(conn_str, wanted)
    if kind in ("mongo", "mongodb", "nosql"):
        return introspect_mongo(conn_str, wanted)
    if kind in ("odata", "sap"):
        return introspect_odata(conn_str, wanted)
    if kind in ("salesforce", "soql"):
        return introspect_salesforce(conn_str, wanted)
    if kind in ("rest", "api", "openapi", "swagger"):
        return introspect_openapi(conn_str, wanted)
    raise SystemExit(
        f"unknown --kind {kind!r} — supported: sql | bigquery | snowflake | redshift | "
        "databricks | trino | mongo | odata/sap | salesforce | rest"
    )


# ── SQL introspection (SQLAlchemy — same engine the workflow SQLSource uses) ──
def introspect_sql(conn_str: str, wanted: List[str]) -> List[Dict[str, Any]]:
    import sqlalchemy
    from sqlalchemy import text

    try:
        engine = sqlalchemy.create_engine(conn_str, pool_pre_ping=True)
    except sqlalchemy.exc.NoSuchModuleError as exc:
        scheme = (conn_str.split("://", 1)[0] or "").split("+")[0]
        pkg = _DIALECT_PKG.get(scheme, f"the SQLAlchemy dialect for '{scheme}'")
        raise SystemExit(f"SQLAlchemy dialect '{scheme}' not installed — `pip install {pkg}` and retry. ({exc})")
    insp = sqlalchemy.inspect(engine)
    table_names = wanted or insp.get_table_names()
    datasets: List[Dict[str, Any]] = []
    # Dialect-correct identifier quoting (Postgres/BigQuery/Snowflake/… differ —
    # BigQuery uses backticks, not double-quotes) so the count/sample/enrich SQL
    # works across warehouses, not just Postgres.
    q = engine.dialect.identifier_preparer.quote

    with engine.connect() as conn:
        for tname in table_names:
            try:
                cols_meta = insp.get_columns(tname)
            except Exception as exc:  # noqa: BLE001
                log.warning("skip table %s — %s", tname, exc)
                continue

            try:
                pk_cols = set(insp.get_pk_constraint(tname).get("constrained_columns") or [])
            except Exception:  # noqa: BLE001
                pk_cols = set()

            columns: List[Dict[str, Any]] = []
            for c in cols_meta:
                cname = c["name"]
                ctype = _citra_type(str(c.get("type", "TEXT")))
                col: Dict[str, Any] = {
                    "name": cname,
                    "physical_name": cname,
                    "type": ctype,
                    # PLACEHOLDER — filled by the LLM-describe phase of the wizard.
                    "description": f"(undocumented) {_humanize(cname)}",
                }
                if cname in pk_cols:
                    col["is_primary_key"] = True
                # Best-effort enrichment: enum values / numeric-date range.
                _enrich_column(conn, text, q, tname, cname, ctype, col)
                columns.append(col)

            relationships: List[Dict[str, Any]] = []
            try:
                for fk in insp.get_foreign_keys(tname):
                    cc = fk.get("constrained_columns") or []
                    rt = fk.get("referred_table")
                    rc = fk.get("referred_columns") or []
                    if cc and rt and rc:
                        relationships.append(
                            {"columns": cc, "references_table": rt, "references_columns": rc}
                        )
            except Exception:  # noqa: BLE001
                pass

            row_count: Optional[int] = None
            try:
                row_count = int(conn.execute(text(f'SELECT COUNT(*) FROM {q(tname)}')).scalar() or 0)
            except Exception as exc:  # noqa: BLE001
                log.warning("row count for %s failed (%s)", tname, exc)

            # A few sample rows — used only by the --describe LLM pass, then
            # stripped from the emitted document (see build_dept_source).
            sample_rows: List[Dict[str, Any]] = []
            try:
                res = conn.execute(text(f'SELECT * FROM {q(tname)} LIMIT {SAMPLE_ROWS}'))
                keys = list(res.keys())
                for r in res.fetchall():
                    sample_rows.append({k: (str(v)[:120] if v is not None else None) for k, v in zip(keys, r)})
            except Exception as exc:  # noqa: BLE001
                log.warning("sample rows for %s failed (%s)", tname, exc)

            datasets.append(
                {
                    "physical_name": tname,
                    "name": _humanize(tname),
                    "kind": "sql",
                    # PLACEHOLDER — the LLM-describe phase rewrites this.
                    "description": f"(undocumented) auto-introspected table '{tname}'"
                    + (f" — ~{row_count} rows" if row_count is not None else ""),
                    "columns": columns,
                    **({"relationships": relationships} if relationships else {}),
                    **({"row_count_approx": row_count} if row_count is not None else {}),
                    "_sample_rows": sample_rows,  # transient
                }
            )
            log.info("introspected %s — %d columns", tname, len(columns))

    return datasets


def _enrich_column(conn, text, q, tname: str, cname: str, ctype: str, col: Dict[str, Any]) -> None:
    """Add enum values (low-card strings) or a min/max range (numbers/dates).

    `q` is the dialect identifier quoter so this works across SQL warehouses.
    """
    t, c = q(tname), q(cname)
    try:
        if ctype == "string":
            n = conn.execute(text(f'SELECT COUNT(DISTINCT {c}) FROM {t}')).scalar()
            if n is not None and 0 < int(n) <= ENUM_MAX_CARDINALITY:
                vals = conn.execute(
                    text(f'SELECT DISTINCT {c} FROM {t} '
                         f'WHERE {c} IS NOT NULL LIMIT {ENUM_MAX_CARDINALITY}')
                ).fetchall()
                col["distinct_values"] = sorted(str(v[0]) for v in vals)
        elif ctype in ("integer", "number", "date", "datetime"):
            row = conn.execute(text(f'SELECT MIN({c}), MAX({c}) FROM {t}')).fetchone()
            if row and row[0] is not None and row[1] is not None:
                col["range"] = {"min": str(row[0]), "max": str(row[1])}
    except Exception:  # noqa: BLE001 — enrichment is best-effort, never fatal
        pass


# ── Mongo introspection (sample documents → inferred field schema) ──────────
def _mongo_type(v: Any) -> str:
    import datetime as _d

    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, (_d.datetime, _d.date)):
        return "datetime"
    if isinstance(v, (dict, list)):
        return "string"  # nested doc/array → treated as JSON-ish text
    return "string"


def introspect_mongo(conn_str: str, wanted: List[str]) -> List[Dict[str, Any]]:
    import urllib.parse as _url

    from pymongo import MongoClient

    uri = conn_str
    parsed = _url.urlparse(uri)
    if parsed.hostname in ("localhost", "127.0.0.1"):
        q = _url.parse_qs(parsed.query)
        q.pop("replicaSet", None)
        q["directConnection"] = ["true"]
        uri = _url.urlunparse(parsed._replace(query=_url.urlencode(q, doseq=True)))
    client = MongoClient(uri)
    dbname = (parsed.path or "/").lstrip("/") or None
    if not dbname:
        raise SystemExit("mongo --conn must include a database, e.g. mongodb://host:27017/mydb")
    db = client[dbname]
    colls = wanted or db.list_collection_names()
    datasets: List[Dict[str, Any]] = []

    for cname in colls:
        docs = list(db[cname].aggregate([{"$sample": {"size": 100}}])) or list(db[cname].find().limit(100))
        fields: Dict[str, str] = {}
        for d in docs:
            for k, v in d.items():
                # promote to a concrete type if we previously only saw null/string
                if k not in fields or fields[k] == "string":
                    fields[k] = _mongo_type(v)
        columns: List[Dict[str, Any]] = []
        for fname, ftype in fields.items():
            col: Dict[str, Any] = {
                "name": fname, "physical_name": fname, "type": ftype,
                "description": f"(undocumented) {_humanize(fname)}",
            }
            if fname == "_id":
                col["is_primary_key"] = True
            columns.append(col)
        sample_rows = [{k: str(v)[:120] for k, v in d.items()} for d in docs[:SAMPLE_ROWS]]
        try:
            approx = db[cname].estimated_document_count()
        except Exception:  # noqa: BLE001
            approx = None
        datasets.append(
            {
                "physical_name": cname, "name": _humanize(cname), "kind": "mongo",
                "description": f"(undocumented) auto-introspected collection '{cname}'"
                + (f" — ~{approx} docs" if approx is not None else ""),
                "columns": columns,
                "_sample_rows": sample_rows,
            }
        )
        log.info("introspected collection %s — %d field(s)", cname, len(columns))
    return datasets


# ── OData introspection (SAP / any OData v2|v4 service) ──────────────────────
def _edm_type(edm: str) -> str:
    e = (edm or "").replace("Edm.", "")
    if e in ("Boolean",):
        return "boolean"
    if e in ("Byte", "SByte", "Int16", "Int32", "Int64"):
        return "integer"
    if e in ("Decimal", "Double", "Single"):
        return "number"
    if e in ("DateTimeOffset", "DateTime"):
        return "datetime"
    if e in ("Date",):
        return "date"
    if e in ("Time", "TimeOfDay", "Duration"):
        return "time"
    return "string"


def _odata_samples(base: str, eset: str) -> List[Dict[str, Any]]:
    """Best-effort: a few entities for the LLM to ground on (skips on any error)."""
    import json as _json
    import urllib.request as _u

    try:
        req = _u.Request(f"{base}/{eset}?$top=3&$format=json", headers={"Accept": "application/json"})
        data = _json.loads(_u.urlopen(req, timeout=20).read())
        rows = data.get("value") or (data.get("d", {}) or {}).get("results") or data.get("d") or []
        out = []
        for r in (rows if isinstance(rows, list) else [])[:SAMPLE_ROWS]:
            if isinstance(r, dict):
                out.append({k: str(v)[:120] for k, v in r.items() if not isinstance(v, (dict, list))})
        return out
    except Exception:  # noqa: BLE001
        return []


def introspect_odata(conn_str: str, wanted: List[str]) -> List[Dict[str, Any]]:
    import urllib.request as _u
    import xml.etree.ElementTree as ET

    url = conn_str.rstrip("/")
    meta_url = url if url.endswith("$metadata") else url + "/$metadata"
    base = meta_url[: -len("/$metadata")]
    log.info("fetching OData $metadata: %s", meta_url)
    xml = _u.urlopen(meta_url, timeout=30).read()
    root = ET.fromstring(xml)

    # EntityType name -> {keys:[...], props:[(name, edmtype)]}. `{*}` = any namespace
    # so this handles OData v2 (ado/edm) and v4 (oasis) without branching.
    # NOTE: findall(".//{*}…") supports the any-namespace wildcard (ElementPath);
    # iter("{*}…") does NOT (it does literal tag matching) — so use findall.
    etypes: Dict[str, Dict[str, Any]] = {}
    for et in root.findall(".//{*}EntityType"):
        keys = [pr.get("Name") for pr in et.findall(".//{*}Key/{*}PropertyRef")]
        props = [(p.get("Name"), p.get("Type")) for p in et.findall("{*}Property")]
        etypes[et.get("Name")] = {"keys": keys, "props": props}

    esets: Dict[str, str] = {es.get("Name"): (es.get("EntityType") or "").split(".")[-1]
                             for es in root.findall(".//{*}EntitySet")}
    wanted_set = set(wanted) if wanted else None
    datasets: List[Dict[str, Any]] = []
    for esname, etname in esets.items():
        if wanted_set and esname not in wanted_set:
            continue
        et = etypes.get(etname)
        if not et:
            continue
        columns: List[Dict[str, Any]] = []
        for pname, ptype in et["props"]:
            col: Dict[str, Any] = {
                "name": pname, "physical_name": pname, "type": _edm_type(ptype),
                "description": f"(undocumented) {_humanize(pname)}",
            }
            if pname in et["keys"]:
                col["is_primary_key"] = True
            columns.append(col)
        datasets.append({
            "physical_name": esname, "name": _humanize(esname), "kind": "odata",
            "description": f"(undocumented) OData entity set '{esname}' (type {etname})",
            "columns": columns,
            "_sample_rows": _odata_samples(base, esname),
        })
        log.info("introspected entity set %s — %d propert(ies)", esname, len(columns))
    return datasets


# ── Salesforce introspection (simple-salesforce describe) ───────────────────
def _sf_type(t: str) -> str:
    t = (t or "").lower()
    if t == "boolean":
        return "boolean"
    if t in ("int",):
        return "integer"
    if t in ("double", "currency", "percent"):
        return "number"
    if t == "date":
        return "date"
    if t == "datetime":
        return "datetime"
    if t == "time":
        return "time"
    return "string"  # string/textarea/picklist/email/phone/url/id/reference/…


def _parse_sf_conn(conn_str: str) -> Dict[str, str]:
    """salesforce://USER:PASS@login|test?security_token=… (any part may come from
    SF_USERNAME / SF_PASSWORD / SF_SECURITY_TOKEN / SF_DOMAIN env vars)."""
    import os
    import urllib.parse as _u

    p = _u.urlparse(conn_str)
    qs = dict(_u.parse_qsl(p.query))
    return {
        "username": _u.unquote(p.username or "") or os.getenv("SF_USERNAME", ""),
        "password": _u.unquote(p.password or "") or os.getenv("SF_PASSWORD", ""),
        "security_token": qs.get("security_token") or qs.get("token") or os.getenv("SF_SECURITY_TOKEN", ""),
        "domain": (p.hostname or os.getenv("SF_DOMAIN") or "login"),  # 'login' (prod) | 'test' (sandbox)
    }


def _sf_columns_from_describe(desc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map a Salesforce sObject describe() to our column list. Pure (testable)."""
    columns: List[Dict[str, Any]] = []
    for f in desc.get("fields", []):
        fname = f.get("name")
        col: Dict[str, Any] = {
            "name": fname, "physical_name": fname, "type": _sf_type(f.get("type")),
            "description": (f.get("label") or f"(undocumented) {_humanize(fname or '')}"),
        }
        if fname == "Id":
            col["is_primary_key"] = True
        if f.get("type") in ("picklist", "multipicklist"):
            vals = [v.get("value") for v in (f.get("picklistValues") or []) if v.get("active", True)]
            if vals:
                col["distinct_values"] = vals[:ENUM_MAX_CARDINALITY]
        columns.append(col)
    return columns


def introspect_salesforce(conn_str: str, wanted: List[str]) -> List[Dict[str, Any]]:
    try:
        from simple_salesforce import Salesforce
    except ImportError:
        raise SystemExit("Salesforce needs simple-salesforce — `pip install simple-salesforce` and retry.")
    creds = _parse_sf_conn(conn_str)
    if not creds["username"]:
        raise SystemExit("Salesforce: set creds in --conn (salesforce://user:pw@login?security_token=…) or SF_* env.")
    sf = Salesforce(username=creds["username"], password=creds["password"],
                    security_token=creds["security_token"], domain=creds["domain"])
    wanted_set = set(wanted) if wanted else None
    objs = [o["name"] for o in sf.describe()["sobjects"]
            if o.get("queryable") and (not wanted_set or o["name"] in wanted_set)]
    datasets: List[Dict[str, Any]] = []
    for name in objs:
        try:
            desc = getattr(sf, name).describe()
        except Exception as exc:  # noqa: BLE001
            log.warning("skip sObject %s — %s", name, exc)
            continue
        columns = _sf_columns_from_describe(desc)
        datasets.append({
            "physical_name": name, "name": desc.get("label") or _humanize(name), "kind": "salesforce",
            "description": f"(undocumented) Salesforce object '{name}'",
            "columns": columns, "_sample_rows": [],
        })
        log.info("introspected sObject %s — %d field(s)", name, len(columns))
    return datasets


# ── Generic REST introspection (OpenAPI / Swagger schema) ───────────────────
def _json_schema_type(pdef: Dict[str, Any]) -> str:
    t = pdef.get("type")
    fmt = pdef.get("format", "")
    if t == "boolean":
        return "boolean"
    if t == "integer":
        return "integer"
    if t == "number":
        return "number"
    if t in ("array", "object") or "$ref" in pdef:
        return "string"
    if t == "string":
        if fmt == "date":
            return "date"
        if fmt in ("date-time", "datetime"):
            return "datetime"
    return "string"


def _openapi_columns(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map an OpenAPI/Swagger object schema to our column list. Pure (testable)."""
    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    columns: List[Dict[str, Any]] = []
    for pname, pdef in props.items():
        pdef = pdef or {}
        col: Dict[str, Any] = {
            "name": pname, "physical_name": pname, "type": _json_schema_type(pdef),
            "description": pdef.get("description") or f"(undocumented) {_humanize(pname)}",
        }
        if pname.lower() in ("id", "_id"):
            col["is_primary_key"] = True
        if pdef.get("enum"):
            col["distinct_values"] = [str(v) for v in pdef["enum"]][:ENUM_MAX_CARDINALITY]
        if pname in required:
            col["required"] = True
        columns.append(col)
    return columns


def introspect_openapi(conn_str: str, wanted: List[str]) -> List[Dict[str, Any]]:
    import urllib.request as _u

    if conn_str.startswith(("http://", "https://")):
        raw = _u.urlopen(conn_str, timeout=30).read()
    else:
        raw = open(conn_str, "rb").read()
    try:
        spec = json.loads(raw)
    except Exception:  # noqa: BLE001 — fall back to YAML
        try:
            import yaml  # type: ignore
            spec = yaml.safe_load(raw)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"could not parse the OpenAPI spec as JSON or YAML ({exc}). `pip install pyyaml` for YAML.")
    schemas = (spec.get("components", {}) or {}).get("schemas") or spec.get("definitions") or {}
    if not schemas:
        raise SystemExit("no schemas found in the spec (components.schemas / definitions).")
    wanted_set = set(wanted) if wanted else None
    datasets: List[Dict[str, Any]] = []
    for name, schema in schemas.items():
        if wanted_set and name not in wanted_set:
            continue
        if not isinstance(schema, dict) or schema.get("type", "object") not in ("object", None):
            continue
        cols = _openapi_columns(schema)
        if not cols:
            continue
        datasets.append({
            "physical_name": name, "name": _humanize(name), "kind": "rest",
            "description": schema.get("description") or f"(undocumented) REST resource '{name}'",
            "columns": cols, "_sample_rows": [],
        })
        log.info("introspected schema %s — %d field(s)", name, len(cols))
    return datasets


# ── LLM describe (Phase 2): fill descriptions + semantic types + PII ────────
_SEMANTIC_TYPES = (
    "identifier name email phone address money quantity percentage date "
    "datetime category status boolean geo url free_text other"
)


def _describe_prompt(ds: Dict[str, Any]) -> str:
    """Build the documentation prompt for one table from its schema + samples."""
    cols = []
    for c in ds["columns"]:
        bits = [f'"{c["name"]}" ({c["type"]}'
                + (", primary key" if c.get("is_primary_key") else "") + ")"]
        if "distinct_values" in c:
            bits.append("values=" + ", ".join(c["distinct_values"][:12]))
        if "range" in c:
            bits.append(f'range={c["range"]["min"]}..{c["range"]["max"]}')
        cols.append("  - " + " | ".join(bits))
    samples = json.dumps(ds.get("_sample_rows", [])[:3], default=str)[:1500]
    return (
        f"You are documenting the table `{ds['physical_name']}` for a data catalogue.\n"
        f"Columns:\n" + "\n".join(cols) + "\n\n"
        f"Sample rows: {samples}\n\n"
        "Return ONLY a JSON object, no prose:\n"
        "{\n"
        '  "table_description": "<one sentence: what business entity this table holds>",\n'
        '  "columns": {\n'
        '    "<col>": {"description":"<short, business-meaning>", '
        f'"semantic_type":"<one of: {_SEMANTIC_TYPES}>", '
        '"pii": <true|false>, "uncertain": <true if you cannot confidently describe it>}\n'
        "  }\n"
        "}\n"
        "Infer meaning from the column name, type, enum values, and samples. "
        "Mark pii=true for personal data (names, phones, emails, addresses, account holders). "
        "Mark uncertain=true ONLY when the meaning is genuinely unclear."
    )


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from an LLM response."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


def _apply_descriptions(ds: Dict[str, Any], obj: Dict[str, Any]) -> List[str]:
    """Apply an LLM description object to a dataset. Returns clarifying questions.

    Pure function (no network) so it can be unit-tested with a fixture.
    """
    questions: List[str] = []
    td = (obj or {}).get("table_description")
    if td:
        ds["description"] = td.strip()
    col_descs = (obj or {}).get("columns", {}) or {}
    for c in ds["columns"]:
        info = col_descs.get(c["name"]) or {}
        if info.get("description"):
            c["description"] = info["description"].strip()
        if info.get("semantic_type"):
            c["semantic_type"] = info["semantic_type"]
        if "pii" in info:
            c["pii"] = bool(info["pii"])
        if info.get("uncertain") or not info.get("description"):
            questions.append(
                f"{ds['physical_name']}.{c['name']} ({c['type']}): what does this represent?"
            )
    return questions


def _call_llm(cfg: Dict[str, str], prompt: str) -> str:
    import openai

    client = openai.OpenAI(api_key=cfg["key"], base_url=cfg["base"])
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        extra_body={"reasoning": {"exclude": True}} if "openrouter" in (cfg["base"] or "") else None,
    )
    return resp.choices[0].message.content or ""


def describe_datasets(datasets: List[Dict[str, Any]], cfg: Dict[str, str]) -> List[str]:
    """LLM-document every dataset in place. Returns the merged clarification list."""
    all_q: List[str] = []
    for ds in datasets:
        try:
            obj = _parse_json(_call_llm(cfg, _describe_prompt(ds)))
        except Exception as exc:  # noqa: BLE001
            log.warning("describe failed for %s (%s) — leaving placeholders", ds["physical_name"], exc)
            obj = None
        if obj:
            qs = _apply_descriptions(ds, obj)
            all_q.extend(qs)
            log.info("described %s — %d column(s), %d to clarify",
                     ds["physical_name"], len(ds["columns"]), len(qs))
        else:
            all_q.append(f"{ds['physical_name']}: describe step returned nothing — review manually")
    return all_q


# ── Phase 3: decision/history tables + proposed write_actions ───────────────
def tag_decision_history(datasets: List[Dict[str, Any]], decision: str, history: str) -> None:
    """Tag the decision table (app acts on it) and history table (few-shot signal)."""
    for ds in datasets:
        if decision and ds["physical_name"] == decision:
            ds["decision_table"] = True
        if history and ds["physical_name"] == history:
            ds["history_table"] = True


def _pk_of(ds: Dict[str, Any]) -> List[str]:
    pks = [c["name"] for c in ds["columns"] if c.get("is_primary_key")]
    return pks or ([ds["columns"][0]["name"]] if ds["columns"] else [])


def _propose_writes_prompt(ds: Dict[str, Any]) -> str:
    pk = _pk_of(ds)
    cols = []
    for c in ds["columns"]:
        b = f'"{c["name"]}" ({c["type"]}'
        if "distinct_values" in c:
            b += "; values=" + ", ".join(c["distinct_values"][:12])
        b += ")"
        cols.append("  - " + b + (" [identity]" if c["name"] in pk else ""))
    return (
        f"A Decision App reads a case from table `{ds['physical_name']}`, an officer "
        "approves a recommendation, and the app then writes the OUTCOME back to that row.\n"
        f"Identity (key) column(s): {', '.join(pk)}\n"
        "Columns:\n" + "\n".join(cols) + "\n\n"
        "Propose the GOVERNED write-backs the app should be allowed to make — ONLY "
        "updates to outcome/status/assignment/disposition fields. NEVER propose changing "
        "identity, money-of-record, or audit columns, and never delete.\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "actions": [\n'
        '    {"id":"<snake_case>", "verb":"update", "description":"<what it does>",\n'
        '     "set_fields": {"<col>": {"type":"<string|number|date|boolean>", '
        '"enum":[<allowed values, if any>], "required":<bool>}}}\n'
        "  ]\n"
        "}\n"
        "If no safe write-back exists, return {\"actions\": []}."
    )


# A sentinel role that NO user holds. An EMPTY roles_allowed_write would fall
# back to the platform default (dept_admin+ MAY write — see auth.DEFAULT_WRITE_ROLES
# / WriteAction docstring), i.e. NOT locked. IT replaces this with real roles.
_LOCKED_ROLE = "__locked_pending_it_review__"


def _input_schema_from_set_fields(sf: Dict[str, Any], pk: List[str]) -> Dict[str, Any]:
    """Build a JSON-Schema ``input_schema`` ({type, properties, required}) — the shape
    the MCP validator + executors require (``catalogue._validate_payload`` reads
    ``.required``; the sql/mongo executors read ``.properties`` for the field
    allow-list). Key fields are included + required so the row can be located; a
    flat ``{col: spec}`` map (what the LLM returns) is NOT accepted by the MCP."""
    props: Dict[str, Any] = {}
    required: List[str] = []
    for k in pk:                                   # keys locate the row → always required
        props[k] = {"type": "string"}
        required.append(k)
    for col, spec in sf.items():
        if col in props:                           # never let a set-field shadow a key
            continue
        spec = spec or {}
        p: Dict[str, Any] = {"type": spec.get("type") or "string"}
        if spec.get("enum"):
            p["enum"] = spec["enum"]
        props[col] = p
        if spec.get("required"):
            required.append(col)
    return {"type": "object", "properties": props, "required": required}


def _sql_update_template(table: str, set_cols: List[str], pk: List[str]) -> str:
    """A parameterised UPDATE (SQLAlchemy ``:named`` binds, ANSI double-quoted
    identifiers). ``COALESCE(:c, "c")`` preserves a column when its bind is NULL —
    matching the MCP executor's partial-payload contract. IT reviews/adjusts the
    quoting per dialect (e.g. backticks for BigQuery/MySQL) during the role grant."""
    sets = ", ".join(f'"{c}" = COALESCE(:{c}, "{c}")' for c in set_cols)
    where = " AND ".join(f'"{k}" = :{k}' for k in pk)
    return f'UPDATE "{table}" SET {sets} WHERE {where}'


# verb → HTTP method for OData / REST write actions.
_VERB_HTTP = {"update": "PATCH", "create": "POST", "upsert": "PUT", "delete": "DELETE"}


def _apply_write_actions(ds: Dict[str, Any], obj: Dict[str, Any]) -> int:
    """Turn proposed actions into LOCKED WriteAction dicts on the dataset. Pure.

    The executor differs by kind: sql runs a parameterised ``sql_template``; odata/
    rest issue an HTTP write to ``endpoint`` with the verb's ``method``; mongo uses
    ``key_fields`` + ``input_schema`` directly. IT reviews + adjusts before unlock.
    """
    pk = _pk_of(ds)
    kind = (ds.get("kind") or "sql").lower()
    actions: List[Dict[str, Any]] = []
    for a in (obj or {}).get("actions", []) or []:
        sf = a.get("set_fields") or {}
        verb = a.get("verb") or "update"
        if not sf or verb not in ("update", "upsert"):
            continue
        set_cols = [c for c in sf if c not in pk]   # never update identity columns
        if not set_cols:
            continue
        action: Dict[str, Any] = {
            "id": a.get("id") or f"update_{ds['physical_name']}",
            "verb": verb,
            "key_fields": pk,
            "input_schema": _input_schema_from_set_fields(sf, pk),
            "description": a.get("description") or f"Update {ds['physical_name']} outcome fields.",
            # LOCKED via a sentinel role nobody holds (an EMPTY list = dept_admin+ MAY write).
            "roles_allowed_write": [_LOCKED_ROLE],
        }
        if kind == "sql":                           # executes via sql_template
            action["sql_template"] = _sql_update_template(ds["physical_name"], set_cols, pk)
        elif kind in ("odata", "salesforce"):       # HTTP write to the entity/sObject
            action["endpoint"] = ds["physical_name"]
            action["method"] = _VERB_HTTP.get(verb, "PATCH")
        elif kind == "rest":                        # HTTP write to a resource path (IT adjusts)
            action["endpoint"] = f"/{ds['physical_name']}"
            action["method"] = _VERB_HTTP.get(verb, "PATCH")
        # mongo: key_fields + input_schema are enough for _exec_mongo_action.
        actions.append(action)
    if actions:
        ds["write_actions"] = actions
    return len(actions)


def propose_writes(datasets: List[Dict[str, Any]], cfg: Dict[str, str]) -> int:
    """Propose write_actions for every dataset tagged decision_table. Returns count."""
    total = 0
    for ds in datasets:
        if not ds.get("decision_table"):
            continue
        try:
            obj = _parse_json(_call_llm(cfg, _propose_writes_prompt(ds)))
        except Exception as exc:  # noqa: BLE001
            log.warning("propose-writes failed for %s (%s)", ds["physical_name"], exc)
            obj = None
        n = _apply_write_actions(ds, obj or {})
        total += n
        log.info("proposed %d write-action(s) on decision table %s (LOCKED — IT must grant roles)",
                 n, ds["physical_name"])
    return total


# ── Assemble ONE sources.json registry entry ────────────────────────────────
def build_registry_source(args, datasets: List[Dict[str, Any]], needs_review: bool = True) -> Dict[str, Any]:
    for ds in datasets:
        ds["id"] = f"{args.source}.{ds['physical_name']}"
        ds.pop("_sample_rows", None)  # transient — never persisted
    _prefix_suffix = {"mongo": "MONGO", "odata": "ODATA", "sap": "ODATA", "salesforce": "SF",
                      "rest": "REST", "api": "REST"}.get(args.kind.lower(), "SQL")
    conn = {"type": args.type, "env_prefix": args.env_prefix or f"{args.source.upper()}_{_prefix_suffix}"}
    if args.kind.lower() in ("odata", "sap", "rest", "api", "openapi", "swagger"):
        conn["endpoint"] = args.conn  # OData/REST URL (no embedded credentials)
    return {
        "org_id": args.org,
        "dept_id": args.dept,
        "source_id": args.source,
        "type": _TOP_TYPE.get(args.kind.lower(), "structured"),
        "is_active": True,
        "name": args.name or _humanize(args.source),
        # PLACEHOLDER — the LLM-describe phase writes the source summary.
        "description": args.description
        or f"(undocumented) {args.type} source '{args.source}' — {len(datasets)} table(s). "
           "Run the describe step of the wizard to document it.",
        "connection": conn,
        # Shape MUST match what the MCP read-authz (auth.py) AND registration.py read:
        # roles_allowed / cross_org_ids / public_within_org. (The old dept_ids/roles/public
        # keys were silently dropped → defaulted to roles_allowed=["user"].) Default here:
        # visible to any role within the source's org/dept; dept scoping is by org_id/dept_id.
        "visibility": {
            "roles_allowed": ["user", "dept_admin", "org_admin", "super_admin"],
            "cross_org_ids": [],
            "public_within_org": False,
        },
        "datasets": datasets,
        # write_actions are added by the governed-writes phase (read-only until then).
        "write_actions": [],
        "query_timeout_seconds": 30,
        # RegistrySource is extra="forbid": ANY key not in the model is a HARD
        # BOOT FAILURE for the MCP, not a warning. The Mongo-era document had two
        # keys the registry model does not define:
        #   introspected_at -> `created_at` (an allowed field)
        #   needs_review    -> dropped; carried in `tags` so a human can still
        #                      see it, because there is nowhere typed to put it.
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "tags": (["needs-review"] if needs_review else []),
    }


def merge_into_sources_file(path: str, doc: dict) -> str:
    """Write `doc` into a sources.json, replacing any entry with the same
    (org_id, dept_id, source_id) and preserving the rest of the file.

    The registry accepts either a top-level ARRAY or a {"sources": [...]}
    envelope; whichever shape the file already uses is preserved, because a
    silent reshape would produce a confusing diff for the human reviewing it.
    """
    import os

    envelope = False
    existing: list = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict) and "sources" in loaded:
            envelope, existing = True, list(loaded["sources"])
        elif isinstance(loaded, list):
            existing = list(loaded)
        else:
            raise SystemExit(f"{path} is neither a source array nor a {{'sources': [...]}} envelope")

    key = (doc["org_id"], doc["dept_id"], doc["source_id"])
    out, replaced = [], False
    for entry in existing:
        if (entry.get("org_id"), entry.get("dept_id"), entry.get("source_id")) == key:
            out.append(doc); replaced = True
        else:
            out.append(entry)
    if not replaced:
        out.append(doc)

    payload = {"sources": out} if envelope else out
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, default=str)
        fh.write("\n")
    return "replaced" if replaced else "added"


def main() -> int:
    ap = argparse.ArgumentParser(description="Introspect a live source into a sources.json registry entry.")
    ap.add_argument("--conn", default="", help="SQLAlchemy connection string (introspection only)")
    ap.add_argument("--org", default="")
    ap.add_argument("--dept", default="")
    ap.add_argument("--source", default="", help="source_id (composite key with org/dept)")
    ap.add_argument("--kind", default="sql", help="source kind: sql | mongo (SAP/Salesforce/BigQuery/REST planned)")
    ap.add_argument("--tables", default="", help="comma-separated tables/collections; blank = all")
    ap.add_argument("--name", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--type", default="", help="connection.type override (defaults from --kind)")
    ap.add_argument("--env-prefix", default="", help="connection.env_prefix the MCP reads creds from")
    ap.add_argument("--out", default="",
                    help="sources.json to merge this source into (created if absent). "
                         "Omit to print the entry to stdout.")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip the schema validation of the written file (not recommended)")
    # Phase 2 — LLM describe (fills column/table descriptions + semantic types + PII).
    import os
    ap.add_argument("--describe", action="store_true",
                    help="LLM-document tables/columns (needs an LLM key)")
    ap.add_argument("--llm-key", default=os.getenv("LLM_API_KEY", ""))
    ap.add_argument("--llm-base", default=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"))
    # The wizard does the hardest reasoning (describe + governed-write proposal),
    # so it pins a top-tier model by default rather than the deployment's runtime
    # LLM_MODEL. Override with WIZARD_LLM_MODEL or --llm-model.
    ap.add_argument("--llm-model", default=os.getenv("WIZARD_LLM_MODEL", "anthropic/claude-opus-latest"))
    # Phase 3 — decision/history tables + governed write_actions.
    ap.add_argument("--decision-table", default="",
                    help="table a Decision App acts on (gets proposed write_actions with --propose-writes)")
    ap.add_argument("--history-table", default="",
                    help="table of past outcomes (feeds the decision-app few-shot learning loop)")
    ap.add_argument("--propose-writes", action="store_true",
                    help="LLM-propose governed write_actions on the decision table (LOCKED until IT grants roles; needs an LLM key)")
    args = ap.parse_args()

    missing = [f"--{f}" for f in ("conn", "org", "dept", "source") if not getattr(args, f)]
    if missing:
        log.error("missing required argument(s): %s", ", ".join(missing))
        return 2

    args.type = args.type or _KIND_TYPE.get(args.kind.lower(), args.kind.lower())
    wanted = [t.strip() for t in args.tables.split(",") if t.strip()]
    log.info("scanning %s source %s (%s)", args.kind, args.source, "all" if not wanted else ", ".join(wanted))
    datasets = introspect(args.kind, args.conn, wanted)
    if not datasets:
        log.error("no tables introspected — nothing to register.")
        return 2

    needs_review = True
    if args.describe:
        if not args.llm_key:
            log.error("--describe needs an LLM key (set LLM_API_KEY or pass --llm-key).")
            return 2
        cfg = {"key": args.llm_key, "base": args.llm_base, "model": args.llm_model}
        log.info("describing %d table(s) with %s …", len(datasets), args.llm_model)
        questions = describe_datasets(datasets, cfg)
        needs_review = bool(questions)  # clean describe with nothing to clarify → review-ready
        if questions:
            log.info("=" * 60)
            log.info("%d item(s) to clarify (answer in review, then re-run):", len(questions))
            for q in questions:
                log.info("  ? %s", q)
            log.info("=" * 60)

    # Phase 3 — tag decision/history tables, propose governed writes.
    if args.decision_table or args.history_table:
        tag_decision_history(datasets, args.decision_table, args.history_table)
        log.info("tagged decision_table=%s history_table=%s",
                 args.decision_table or "-", args.history_table or "-")
    if args.propose_writes:
        if not args.decision_table:
            log.error("--propose-writes needs --decision-table.")
            return 2
        if not args.llm_key:
            log.error("--propose-writes needs an LLM key (set LLM_API_KEY or pass --llm-key).")
            return 2
        cfg = {"key": args.llm_key, "base": args.llm_base, "model": args.llm_model}
        n = propose_writes(datasets, cfg)
        if n:
            needs_review = True  # proposed writes are LOCKED — IT must review + grant roles
            log.info("⚠ %d write-action(s) PROPOSED but LOCKED — review and add roles_allowed_write to enable.", n)

    doc = build_registry_source(args, datasets, needs_review=needs_review)
    log.info("built registry source %s/%s/%s: %d dataset(s), %d column(s), needs_review=%s",
             args.org, args.dept, args.source, len(datasets),
             sum(len(d["columns"]) for d in datasets), needs_review)

    if not args.out:
        json.dump([doc], sys.stdout, indent=2, default=str)
        print()
        log.info("no --out given: nothing written. Re-run with --out <sources.json> to register it.")
        return 0

    action = merge_into_sources_file(args.out, doc)
    log.info("%s %s in %s", action, args.source, args.out)

    # Validate what we just wrote. RegistrySource is extra="forbid", so a shape
    # error here becomes an MCP that refuses to boot -- catch it now, while the
    # person who caused it is still looking at the terminal.
    if not args.no_validate:
        import subprocess
        validator = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "source-mcp-template", "validate_sources.py")
        if os.path.exists(validator):
            rc = subprocess.run([sys.executable, validator, args.out]).returncode
            if rc != 0:
                log.error("the file was written but FAILED validation - the MCP will not boot with it.")
                return 3
            log.info("validated against the registry schema")
        else:
            log.warning("validator not found at %s - skipping validation", validator)

    log.info("")
    log.info("Next: restart the MCP that mounts this file, e.g.")
    log.info("  docker compose -f demo-data/tenants/<org>/mcp/docker-compose.yml up -d --build")
    log.info("See docs/change-the-demo.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
