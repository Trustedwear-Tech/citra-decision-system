# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Dept MCP — Catalogue & runtime dispatcher
==========================================
Implements the five-tool contract every Citra MCP shim must expose:

    list_datasets()            → /datasets
    describe_dataset(id)       → /datasets/{id}
    sample_dataset(id, n)      → /datasets/{id}/sample
    run_query(...)             → /run_query
    execute_action(...)        → /execute_action

Design notes
------------
* The in-memory `_sources` map (loaded from the central ``dept_sources``
  Mongo collection by router.py) is the single source of truth for what
  this deployment serves. Catalogue calls read from that map; they do
  NOT re-query Mongo or Milvus on each request.

* For SQL sources, the dataset list comes from one of two places only:
      1. ``options.tables`` on the source document — explicit allow-list,
         the recommended path for prod (small surface, predictable cost).
      2. Live ``extract_table_names`` autodiscovery — used when no
         allow-list is set, so dev / first-run still works.
  ``describe_dataset`` live-introspects every kind that has an
  introspection strategy (see below) and falls back to a declared
  ``columns[]`` block on the dataset when there is none. The
  data-discovery-service crawler is the owner of the *enriched*
  catalogue (PII tagging, semantic naming) — the MCP only exposes
  physical truth.

* Column schema — ``describe_dataset`` resolves columns in this order:
      1. live introspection, for kinds that support it (see the table
         below). Always-accurate, zero-maintenance.
      2. a declared ``columns[]`` block on the dataset — authoritative
         for kinds with no introspection (sap_rfc), and a graceful
         fallback when a reachable backend was momentarily unavailable.

* Backends ("kinds") and their introspection strategy:
      sql       — read + write + introspection (SQLAlchemy inspect)
      soql      — read + write + introspection (/sobjects/{name}/describe)
      odata     — read + write + introspection ($metadata parsing)
      bigquery  — read + introspection (google-cloud-bigquery get_table)
      mongodb   — read via /query; WRITE via /execute_action (create /
                  update / upsert / delete, tenant-scoped); introspection
                  by collection sampling
      duckdb    — read + introspection (one-shot DuckDB schema sniff over
                  the parquet/csv file, incl. gs:// / s3://)
      sap_rfc   — read via /run_query; NO introspection — declare columns[]
      semantic  — read maps to existing /query path
      rest      — extension point; raises 501 with a clear contract so
                  dept-specific subclasses can plug in.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from models import (
    ColumnSpec,
    DatasetKind,
    DatasetRef,
    DatasetSchema,
    ExecuteActionRequest,
    ExecuteActionResponse,
    ListDatasetsResponse,
    ReadVia,
    Relationship,
    RunQueryRequest,
    RunQueryResponse,
    SampleResponse,
    WriteAction,
)
from router import _sources, get_source

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — derive datasets from a source's stored config
# ---------------------------------------------------------------------------


def _source_kind(source: Dict[str, Any]) -> DatasetKind:
    """Map a source's ``type`` to a DatasetKind.

    Sources may also override per-dataset by writing ``catalogue.kind``.
    """
    t = str(source.get("type", "semantic")).lower()
    if t == "structured":
        # SQL Server / Postgres / MySQL — declared via connection.type
        conn = source.get("connection") or {}
        sub = str(conn.get("type", "")).lower()
        if sub in {"odata", "sap_odata"}:
            return DatasetKind.odata
        if sub in {"salesforce", "sfdc", "soql"}:
            return DatasetKind.soql
        return DatasetKind.sql
    if t == "rest_api":
        return DatasetKind.rest
    if t == "mongodb":
        return DatasetKind.mongodb
    if t == "bigquery":
        return DatasetKind.bigquery
    if t == "sap_rfc":
        return DatasetKind.sap_rfc
    if t == "duckdb":
        # Cloud-backed DuckDB (GCS / S3 parquet) — dispatched through the
        # same _run_duckdb branch as on-disk files.
        return DatasetKind.duckdb
    return DatasetKind.semantic


def _datasets_for(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the list of dataset config blocks for a source.

    Resolution order:
        1. Explicit ``source.datasets[]`` (top-level on the dept_source
           document) — used by bigquery / sap_rfc / gcs-via-duckdb where
           the dataset's read_via shape isn't derivable from a table name.
        2. Explicit ``source.catalogue.datasets[]`` (legacy nested
           location — kept for back-compat).
        3. Tabular auto-discovery for sql / soql / odata via
           ``options.tables`` or the matching connector's
           ``extract_table_names``.
        4. Non-tabular single-dataset fallback for everything else.
    """
    # (1) + (2) — caller-declared datasets win.
    explicit = (
        list(source.get("datasets") or [])
        or list((source.get("catalogue") or {}).get("datasets") or [])
    )
    if explicit:
        return explicit

    kind = _source_kind(source)

    # (3) Tabular auto-discovery.
    if kind in {DatasetKind.sql, DatasetKind.soql, DatasetKind.odata}:
        options = source.get("options") or {}
        tables = list(options.get("tables") or [])
        if not tables:
            tables = _autodiscover_tables(source, kind)
        return [
            {
                "id": f"{source['source_id']}.{t}",
                "name": t,
                "physical_name": t,
                "kind": kind.value,
                "read_via": {"kind": kind.value, "target": t},
            }
            for t in tables
        ]

    # (4) Non-tabular fallback — one dataset == the source itself
    #     (mongodb / semantic / rest / sap_rfc without a datasets[] block).
    #     Carries the source-level description + any declared columns[] so
    #     describe_dataset can surface a schema even when the source did
    #     not spell out an explicit datasets[] entry.
    return [
        {
            "id": source["source_id"],
            "name": source.get("name") or source["source_id"],
            "physical_name": source.get("physical_name") or source["source_id"],
            "kind": kind.value,
            "description": source.get("description"),
            "columns": list(source.get("columns") or []),
            "write_actions": list(source.get("write_actions") or []),
            "read_via": {"kind": kind.value, "target": source["source_id"]},
        }
    ]


def _autodiscover_tables(source: Dict[str, Any], kind: DatasetKind) -> List[str]:
    """Live autodiscovery via the right connector for `kind`. Errors are
    non-fatal — empty list means the source contributes zero datasets."""
    try:
        connector = _connector_for_kind(kind)
        return list(connector.extract_table_names(source.get("connection") or {}) or [])
    except Exception as exc:
        logger.warning(
            "%s autodiscovery failed for source %s: %s — declare options.tables to fix",
            kind.value, source.get("source_id"), exc,
        )
        return []


# Kinds describe_dataset live-introspects. mongodb + duckdb have dedicated
# strategies in _live_introspect_full; the rest go through a connector.
_INTROSPECTABLE_KINDS = {
    DatasetKind.sql, DatasetKind.soql, DatasetKind.odata,
    DatasetKind.bigquery, DatasetKind.mongodb, DatasetKind.duckdb,
}

# Documents sampled to infer a MongoDB collection's field schema.
_MONGO_SAMPLE_DOCS = 200


def _connector_for_kind(kind: DatasetKind):
    """Return the connector module that owns table-based introspection +
    sample for a kind. Centralised so every kind=foo branch is one if/elif.

    Note: mongodb + duckdb are NOT here — they have dedicated, non-table
    introspection strategies (_introspect_mongo / _introspect_files)."""
    if kind == DatasetKind.sql:
        from connectors import sql_connector  # type: ignore
        return sql_connector
    if kind == DatasetKind.soql:
        from connectors import soql_connector  # type: ignore
        return soql_connector
    if kind == DatasetKind.odata:
        from connectors import odata_connector  # type: ignore
        return odata_connector
    if kind == DatasetKind.bigquery:
        from connectors import bigquery_connector  # type: ignore
        return bigquery_connector
    raise ValueError(f"No table-based connector for kind={kind!r}")


def _find_dataset(source_id: str, dataset_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Returns (source, dataset_block). Raises HTTPException(404) on miss."""
    source = get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {source_id!r}")
    for ds in _datasets_for(source):
        if ds.get("id") == dataset_id:
            return source, ds
    raise HTTPException(
        status_code=404,
        detail=f"Dataset {dataset_id!r} not found on source {source_id!r}",
    )


# ---------------------------------------------------------------------------
# /datasets
# ---------------------------------------------------------------------------


def list_datasets(claims: Optional[Dict[str, Any]] = None) -> ListDatasetsResponse:
    """Flatten every source's datasets into one response.

    When ``claims`` is provided (the verified X-User-JWT body), sources the
    caller can't see are filtered OUT — same visibility matrix as /run_query
    so dataset enumeration can never leak the existence of sources the
    caller couldn't query anyway. Pass ``claims=None`` only for unauthenticated
    contexts (e.g., dev with AUTHZ_ENFORCE=false and no JWT)."""
    from auth import is_visible

    out: List[DatasetRef] = []
    for source in _sources.values():
        if claims is not None:
            allowed, _reason = is_visible(source, claims)
            if not allowed:
                continue
        for ds in _datasets_for(source):
            try:
                out.append(
                    DatasetRef(
                        id=ds["id"],
                        source_id=source["source_id"],
                        name=ds.get("name", ds["id"]),
                        physical_name=ds.get("physical_name") or ds.get("name") or ds["id"],
                        kind=DatasetKind(ds.get("kind", _source_kind(source).value)),
                        description=ds.get("description"),
                        row_count_approx=ds.get("row_count_approx"),
                        has_pii=ds.get("has_pii"),
                        last_refreshed_at=ds.get("last_refreshed_at"),
                        decision_history=ds.get("decision_history"),
                        mandatory_when_used=ds.get("mandatory_when_used"),
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Skipping malformed dataset on source %s: %s",
                    source.get("source_id"),
                    exc,
                )
    return ListDatasetsResponse(datasets=out, total=len(out))


# ---------------------------------------------------------------------------
# /datasets/{id}
# ---------------------------------------------------------------------------


# H1: TTL-cache live introspection per (source, dataset). _live_introspect_full
# hits the source (schema + row-count estimate) on EVERY describe; schema is
# caller-independent and stable, so a re-browse / re-sample within the TTL must
# not re-introspect.
_INTROSPECT_TTL_S = int(os.getenv("CATALOGUE_INTROSPECT_TTL_S", "300"))
_introspect_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}


def _cached_introspect(source: Dict[str, Any], ds: Dict[str, Any], kind) -> Dict[str, Any]:
    sid = str(source.get("source_id") or source.get("id") or "")
    did = str(ds.get("id") or ds.get("dataset_id") or "")
    now = time.time()
    if sid and did:
        # Per-process layer first (cheapest, no network).
        hit = _introspect_cache.get((sid, did))
        if hit and (now - hit[0]) < _INTROSPECT_TTL_S:
            return hit[1]
        # §1/§2-P3 — SHARED Redis layer so N replicas don't each re-introspect
        # the source DB (a herd on mass expiry / scale-out). Schema is caller-
        # independent and stable; fail-open to live introspection. Populate the
        # process layer from a Redis hit so subsequent calls skip even Redis.
        try:
            import plan_cache
            shared = plan_cache.get_introspect(sid, did)
            if shared is not None:
                _introspect_cache[(sid, did)] = (now, shared)
                return shared
        except Exception:  # noqa: BLE001 — cache is best-effort
            pass
    intro = _live_introspect_full(source, ds, kind)
    if sid and did:
        _introspect_cache[(sid, did)] = (now, intro)
        try:
            import plan_cache
            plan_cache.set_introspect(sid, did, intro)
        except Exception:  # noqa: BLE001 — cache is best-effort
            pass
    return intro


def describe_dataset(
    dataset_id: str,
    source_id: Optional[str] = None,
    claims: Optional[Dict[str, Any]] = None,
) -> DatasetSchema:
    """Return full schema for a dataset.

    If `source_id` is omitted, search every loaded source for a matching id.
    When ``claims`` is provided, invisible sources are treated as not-found
    (404, not 403) so existence can't be probed by privilege-less callers.
    """
    from auth import is_visible

    source: Optional[Dict[str, Any]] = None
    ds: Optional[Dict[str, Any]] = None

    if source_id:
        source, ds = _find_dataset(source_id, dataset_id)
    else:
        for s in _sources.values():
            for candidate in _datasets_for(s):
                if candidate.get("id") == dataset_id:
                    source, ds = s, candidate
                    break
            if ds is not None:
                break

    if source is None or ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id!r} not found")

    if claims is not None:
        allowed, _reason = is_visible(source, claims)
        if not allowed:
            raise HTTPException(status_code=404, detail=f"Dataset {dataset_id!r} not found")

    kind = DatasetKind(ds.get("kind", _source_kind(source).value))

    # Live-introspect every kind that has a strategy; the MCP exposes
    # physical truth and the data-discovery-service crawler owns enrichment.
    columns_raw: List[Dict[str, Any]] = []
    relationships_raw: List[Dict[str, Any]] = []
    row_count_approx = ds.get("row_count_approx")

    if kind in _INTROSPECTABLE_KINDS:
        intro = _cached_introspect(source, ds, kind)
        columns_raw = intro["columns"]
        relationships_raw = intro["relationships"]
        if row_count_approx is None:
            row_count_approx = intro["row_count"]

    # Declared columns[] on the dataset block are the authoritative schema
    # for kinds with no live introspection (sap_rfc), and a graceful
    # fallback when a reachable backend was momentarily unavailable.
    if not columns_raw:
        columns_raw = list(ds.get("columns") or [])

    # Declared-semantics overlay for EVERY kind. Introspection (SQL/Mongo/DuckDB
    # alike) only ever yields the physical layer (name/type/nullable/keys) — it
    # NEVER produces the authored semantic layer: descriptions, enums, sensitivity,
    # pii, column_kind/mime_hint (the media contract) or the fraud ontology
    # (artifact_role/reuse_policy). The relational branch overlays most of these in
    # _live_introspect_full, but mongo/duckdb bypass that branch entirely, and even
    # the relational path historically dropped column_kind/mime_hint/pii — so a BA's
    # declared "this column is an image_url" silently no-oped unless the column
    # NAME happened to match the crawler's fallback heuristics. Fill-only: a
    # declared value is applied only where introspection left the field empty;
    # physical truth is never overridden.
    _DECLARED_SEMANTIC_FIELDS = (
        "artifact_role", "reuse_policy",
        "column_kind", "mime_hint",
        "description", "semantic_type", "sensitivity",
        "distinct_values", "range", "pii",
    )
    _declared_by_name = {
        str(dc.get("physical_name") or dc.get("name") or ""): dc
        for dc in (ds.get("columns") or [])
    }
    if _declared_by_name:
        for c in columns_raw:
            decl = _declared_by_name.get(str(c.get("physical_name") or c.get("name") or ""))
            if not decl:
                continue
            for _f in _DECLARED_SEMANTIC_FIELDS:
                if c.get(_f) in (None, "", [], {}) and decl.get(_f) not in (None, "", [], {}):
                    c[_f] = decl[_f]

    columns = [_to_column_spec(c) for c in columns_raw]

    relationships = [
        Relationship(**r) for r in relationships_raw if isinstance(r, dict)
    ]

    # physical_name FIRST. `name` is a human label — "Maintenance orders" — and
    # using it as a read target produces a 404 on every sample for a dataset
    # that is otherwise correctly registered: the MCP boots, /health lists the
    # source, the crawler catalogues it, and only an actual read fails. Hand-
    # authored registries all set read_via explicitly, so the fallback was
    # never exercised until a generated one relied on it.
    read_via_raw = ds.get("read_via") or {
        "kind": kind.value,
        "target": ds.get("physical_name") or ds.get("name") or ds["id"],
    }
    # ReadVia only preserves ``kind``/``target``/``extra`` (extra fields ignored),
    # so a REST mapping authored at the read_via TOP LEVEL would be dropped from
    # the catalogued schema the builder reads. Fold request/response into ``extra``
    # so both authoring styles survive to /datasets → discovery → builder.
    if any(k in read_via_raw for k in ("request", "response")):
        read_via_raw = dict(read_via_raw)
        _extra = dict(read_via_raw.get("extra") or {})
        for _k in ("request", "response"):
            if _k in read_via_raw and _k not in _extra:
                _extra[_k] = read_via_raw.pop(_k)
        read_via_raw["extra"] = _extra
    read_via = ReadVia(**read_via_raw)

    write_actions = [
        WriteAction(**w) for w in (ds.get("write_actions") or []) if isinstance(w, dict)
    ]

    return DatasetSchema(
        id=ds["id"],
        source_id=source["source_id"],
        name=ds.get("name", ds["id"]),
        physical_name=ds.get("physical_name") or read_via_raw.get("target") or ds.get("name") or ds["id"],
        kind=kind,
        description=ds.get("description"),
        columns=columns,
        relationships=relationships,
        read_via=read_via,
        input_schema=dict(ds.get("input_schema") or {}),
        write_actions=write_actions,
        samples_redacted=bool(ds.get("samples_redacted", True)),
        row_count_approx=row_count_approx,
        last_refreshed_at=ds.get("last_refreshed_at"),
        decision_history=ds.get("decision_history"),
        value_semantics=ds.get("value_semantics"),
        fraud_screening=ds.get("fraud_screening"),
        # Effective domain triple: the dataset's own block wins outright (it is
        # a COMPLETE triple by model contract — never a partial merge), else the
        # source's. Normalized through the registry model so the served value
        # always carries the country derivations (currency/date_order) — the
        # raw file dict flows here un-filled.
        domain=_effective_domain(source, ds),
        # Display identity is source-level (no per-dataset override — a company
        # doesn't change per table); boot validation already checked the shape.
        organization=source.get("organization"),
        mandatory_when_used=ds.get("mandatory_when_used"),
    )


def _effective_domain(source: Dict[str, Any], ds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The dataset's effective domain triple, normalized through the registry
    model so derivations (currency / date_order from country) are FILLED in the
    served value. The boot gate already validated the source, so a failure here
    is a real contract break — propagate, never serve a half-filled block."""
    raw = ds.get("domain") or source.get("domain")
    if not raw:
        return None
    from registry_models import Domain as RegistryDomain
    return RegistryDomain.model_validate(raw).model_dump(mode="json")


def _to_column_spec(c: Dict[str, Any]) -> ColumnSpec:
    return ColumnSpec(
        name=str(c.get("name", "")),
        physical_name=c.get("physical_name") or str(c.get("name", "")),
        type=str(c.get("type", "")),
        semantic_type=c.get("semantic_type"),
        # Media contract: a declared column_kind/mime_hint marks URL/object columns
        # for image_analyze / doc_extract / runtime display. Dropping them here
        # silently voided the documented "declare it, the platform does the rest"
        # contract (the crawler's name heuristics were the only survivor path).
        column_kind=c.get("column_kind"),
        mime_hint=c.get("mime_hint"),
        nullable=bool(c.get("nullable", True)),
        is_primary_key=bool(c.get("is_primary_key", False)),
        is_foreign_key=bool(c.get("is_foreign_key", False)),
        foreign_ref=c.get("foreign_ref"),
        pii=bool(c.get("pii", False)),
        sensitivity=c.get("sensitivity"),
        distinct_values=c.get("distinct_values"),
        range=c.get("range"),
        description=c.get("description"),
        artifact_role=c.get("artifact_role"),
        reuse_policy=c.get("reuse_policy"),
    )


def _mongo_type(value: Any) -> str:
    """Map a Python/BSON value to a simple catalogue type label."""
    import datetime
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (datetime.datetime, datetime.date)):
        return "datetime"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _mongo_data_conn(conn: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve ``(uri, db_name)`` for a mongodb source's DATA plane.

    A mongodb source MUST declare its OWN data connection — the MCP never
    falls back to any shared/central cluster (RULE #1: fail loud, no silent
    default). Resolution order:

      1. ``connection.uri`` — explicit per-source override (discouraged:
         it puts credentials inside the registry document).
      2. ``connection.env_prefix`` — reads ``{PFX}_URI`` / ``{PFX}_DB`` from
         the environment, the same credential model as
         ``connectors.mongo_connector``.

    Any source that resolves NEITHER a URI nor a DB name raises — a mongodb
    source with no connection is a misconfiguration, not a request to read
    some default cluster.

    DB name: ``connection.mongo_db`` → ``{PFX}_DB``.
    """
    prefix = (conn.get("env_prefix") or "").upper()
    uri = conn.get("uri")
    if not uri and prefix:
        uri = os.getenv(f"{prefix}_URI")
    if not uri:
        raise RuntimeError(
            "mongodb source declares no data connection: set "
            "connection.env_prefix (→ {PFX}_URI) or connection.uri. The MCP "
            "does NOT fall back to any shared cluster (RULE #1 — no silent "
            f"default). env_prefix={prefix or '(unset)'!r}"
        )
    db_name = conn.get("mongo_db") or (os.getenv(f"{prefix}_DB") if prefix else None)
    if not db_name:
        raise RuntimeError(
            "mongodb source declares no database: set connection.mongo_db or "
            f"{prefix or '{PFX}'}_DB in the MCP environment."
        )
    return uri, db_name


def _introspect_mongo(source: Dict[str, Any]) -> Dict[str, Any]:
    """Catalogue-native MongoDB introspection — sample the collection and
    union field names into a column list.

    Deliberately does NOT use ``connectors.mongo_connector``: that one is
    the ingest-era connector keyed on a per-source ``env_prefix`` + a
    hand-declared ``scalar_fields`` list. Here we want zero-config schema
    for ANY collection, so we sample documents directly. Connection
    resolution: see ``_mongo_data_conn``."""
    out: Dict[str, Any] = {"columns": [], "relationships": [], "row_count": None}
    conn = source.get("connection") or {}
    coll_name = conn.get("collection")
    if not coll_name:
        return out
    try:
        from pymongo import MongoClient

        uri, db_name = _mongo_data_conn(conn)
        client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        try:
            coll = client[db_name][coll_name]
            try:
                sample = list(coll.aggregate([{"$sample": {"size": _MONGO_SAMPLE_DOCS}}]))
            except Exception:
                sample = list(coll.find({}, limit=_MONGO_SAMPLE_DOCS))
            row_count = coll.estimated_document_count()
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("mongo introspect failed for %s: %s", source.get("source_id"), exc)
        return out

    pk = set(conn.get("primary_key") or [])
    field_types: Dict[str, str] = {}
    for doc in sample:
        for k, v in doc.items():
            if k == "_id" or v is None:
                continue
            field_types.setdefault(k, _mongo_type(v))

    out["columns"] = [
        {
            "name": k,
            "physical_name": k,
            "type": field_types[k],
            "nullable": True,
            "is_primary_key": k in pk,
        }
        for k in field_types
    ]
    out["row_count"] = int(row_count) if isinstance(row_count, int) else None
    return out


def _introspect_files(source: Dict[str, Any], ds: Dict[str, Any]) -> Dict[str, Any]:
    """DuckDB-over-files introspection (parquet / csv on gs:// / s3:// /
    on-disk) via gcs_connector's one-shot DuckDB schema sniff."""
    out: Dict[str, Any] = {"columns": [], "relationships": [], "row_count": None}
    rv = ds.get("read_via") or {}
    files = rv.get("files") or (rv.get("extra") or {}).get("files") or []
    if not files:
        return out
    try:
        from connectors import gcs_connector  # type: ignore
        schema = gcs_connector.extract_schema(source.get("connection") or {}, files[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("duckdb/file introspect failed for %s: %s", ds.get("id"), exc)
        return out
    out["columns"] = [
        {
            "name": c.get("name"),
            "physical_name": c.get("name"),
            "type": str(c.get("type", "")),
            "nullable": bool(c.get("nullable", True)),
        }
        for c in (schema.get("columns") or [])
        if c.get("name")
    ]
    rc = schema.get("row_count")
    out["row_count"] = rc if isinstance(rc, int) and rc >= 0 else None
    return out


def _live_introspect_full(
    source: Dict[str, Any],
    ds: Dict[str, Any],
    kind: DatasetKind,
) -> Dict[str, Any]:
    """Live introspection, dispatched per kind to the right strategy:

      • mongodb              → sample the collection (_introspect_mongo)
      • duckdb (files)       → DuckDB schema sniff over the parquet/file
      • sql/soql/odata/bigquery → connector.extract_schema(conn, table) + PK/FK

    Every strategy returns the same normalised shape::

        {
          "columns":       [{name, physical_name, type, nullable,
                             is_primary_key, is_foreign_key, foreign_ref,
                             distinct_values, range, description}, ...],
          "relationships": [{from_column, to_dataset, to_column, inferred}, ...],
          "row_count":     int | None,
        }

    Errors are swallowed and logged — partial / empty schema is fine;
    describe_dataset then falls back to any declared ``columns[]``.
    """
    if kind == DatasetKind.mongodb:
        return _introspect_mongo(source)
    if kind == DatasetKind.duckdb:
        return _introspect_files(source, ds)

    # Relational kinds — sql / soql / odata / bigquery — all share the
    # connector contract extract_schema(conn, table) [+ PK/FK].
    out: Dict[str, Any] = {"columns": [], "relationships": [], "row_count": None}

    table = (ds.get("read_via") or {}).get("target") or ds.get("physical_name") or ds.get("name")
    if not table:
        return out

    try:
        connector = _connector_for_kind(kind)
    except Exception as exc:
        logger.debug("connector lookup failed for kind=%s: %s", kind, exc)
        return out

    conn = source.get("connection") or {}
    try:
        schema = connector.extract_schema(conn, table)
    except Exception as exc:
        logger.debug("%s introspect failed for %s: %s", kind.value, ds.get("id"), exc)
        return out

    raw_cols = list(schema.get("columns") or [])
    out["row_count"] = schema.get("row_count")

    # Primary keys — already inlined by SOQL/OData connectors but SQL needs
    # an extra call. The dispatch is uniform.
    pk_cols: List[str] = []
    try:
        pk_cols = list(connector.extract_primary_keys(conn, table) or [])
    except Exception as exc:
        logger.debug("PK extract failed for %s: %s", table, exc)

    fk_index: Dict[str, str] = {}
    rels: List[Dict[str, Any]] = []
    try:
        for fk in connector.extract_foreign_keys(conn, table) or []:
            from_col = fk.get("from_column")
            to_table = fk.get("to_table") or ""
            to_col = fk.get("to_column") or ""
            if not from_col or not to_table:
                continue
            fk_index[from_col] = f"{to_table}.{to_col}" if to_col else to_table
            rels.append({
                "from_column": from_col,
                "to_dataset": to_table,
                "to_column": to_col,
                "inferred": False,
            })
    except Exception as exc:
        logger.debug("FK extract failed for %s: %s", table, exc)

    # Declared column metadata the live DB cannot provide — description,
    # value enums (distinct_values), numeric/date range, sensitivity,
    # semantic_type — is overlaid onto the physically-introspected columns
    # (matched by physical name). Introspection owns PHYSICAL truth
    # (type / nullable / PK / FK); the declared ``columns[]`` block owns the
    # human/semantic layer. Without this overlay, a source whose backend is
    # reachable loses every hand-authored column description + enum on the way
    # to the catalogue (Postgres carries none unless COMMENT ON is set), so the
    # builder would see bare name+type columns and the table-level description
    # only. Declared values fill ONLY gaps introspection left — never override
    # physical truth.
    declared_by_name: Dict[str, Dict[str, Any]] = {}
    for dc in (ds.get("columns") or []):
        key = str(dc.get("physical_name") or dc.get("name") or "")
        if key:
            declared_by_name[key] = dc

    # NB: artifact_role / reuse_policy (fraud ontology) MUST be overlaid too — a
    # live relational backend never returns them, so without this the declared
    # roles are dropped for every reachable SQL source and the fraud feature
    # silently no-ops (the common case). Same for column_kind / mime_hint (the
    # media contract) and pii — declared-only, never introspected. Fill gaps only.
    _semantic_overlay = ("description", "distinct_values", "range", "sensitivity",
                         "semantic_type", "artifact_role", "reuse_policy",
                         "column_kind", "mime_hint", "pii")

    cols: List[Dict[str, Any]] = []
    for c in raw_cols:
        physical = str(c.get("name") or "")
        decl = declared_by_name.get(physical)
        merged = {
            **c,
            "physical_name": physical,
            # The BA's semantic/display name (docs §6: "defaults to
            # physical_name"). This used to hard-set `physical` with the comment
            # "crawler will overwrite" — the crawler does the OPPOSITE: it carries
            # the declared name through iff name != physical_name, so forcing them
            # equal here made CatalogueColumn.display_name permanently null for
            # every reachable source. Declared wins; physical is the default.
            "name": str((decl or {}).get("name") or "") or physical,
            # Declared keys OR introspected keys. `decl` must be consulted: a
            # VIEW, a Mongo collection, a parquet/DuckDB dataset or a purely
            # logical FK has no DB-level constraint to introspect, which is
            # exactly when the author declares one — and previously the declared
            # value survived ONLY when the backend was unreachable.
            "is_primary_key": (
                bool(c.get("is_primary_key"))
                or bool((decl or {}).get("is_primary_key"))
                or (physical in pk_cols)
            ),
            "is_foreign_key": (
                bool(c.get("is_foreign_key"))
                or bool((decl or {}).get("is_foreign_key"))
                or (physical in fk_index)
            ),
        }
        if not merged.get("foreign_ref"):
            merged["foreign_ref"] = fk_index.get(physical) or (decl or {}).get("foreign_ref")
        if decl:
            for f in _semantic_overlay:
                if merged.get(f) in (None, "", [], {}) and decl.get(f) not in (None, "", [], {}):
                    merged[f] = decl[f]
        cols.append(merged)
    # Fail loud on a declared column that matched NOTHING. Overlay keys on an
    # exact physical-name match, so a typo — or Postgres folding an unquoted
    # identifier to lower case — silently discards the whole declared column:
    # artifact_role, column_kind, description and all. That is a silent no-op of
    # authored intent, and sources.json gets no schema validation to catch it.
    _matched = {str(c.get("physical_name") or "") for c in cols}
    _orphans = sorted(k for k in declared_by_name if k not in _matched)
    if _orphans:
        logger.warning(
            "[CATALOGUE] dataset %r: %d declared column(s) match NO introspected "
            "column and were IGNORED: %s. Introspected: %s. Check spelling/case "
            "(unquoted identifiers fold to lower case on Postgres) — the declared "
            "ontology on these columns (artifact_role/column_kind/description/…) "
            "has NO effect.",
            ds.get("id") or ds.get("physical_name"), len(_orphans), _orphans,
            sorted(_matched)[:25],
        )
    out["columns"] = cols
    out["relationships"] = rels
    return out


# ---------------------------------------------------------------------------
# /datasets/{id}/sample
# ---------------------------------------------------------------------------


def sample_dataset(
    dataset_id: str,
    n: int = 100,
    source_id: Optional[str] = None,
    claims: Optional[Dict[str, Any]] = None,
) -> SampleResponse:
    """Return up to `n` sample rows. Always redacted at the source layer
    when the catalogue marks the dataset as containing PII; un-redaction
    is the caller's responsibility (and is not allowed for the data-
    discovery-service crawler — only authorised user runs un-mask).

    Visibility: filtering happens inside describe_dataset(claims=...)."""
    schema = describe_dataset(dataset_id, source_id=source_id, claims=claims)
    source, ds = _find_dataset(schema.source_id, dataset_id)

    n = max(1, min(int(n), 1000))

    # Connector-backed sampling — kinds whose connector exposes
    # extract_sample_rows(conn, table, n). mongodb / duckdb / sap_rfc have
    # no uniform sample surface yet (their schema still comes through
    # describe_dataset's introspection / declared columns[]).
    if schema.kind in {DatasetKind.sql, DatasetKind.soql,
                       DatasetKind.odata, DatasetKind.bigquery}:
        table = schema.read_via.target or schema.physical_name or schema.name
        rows: List[Dict[str, Any]] = []
        try:
            connector = _connector_for_kind(schema.kind)
            rows = list(
                connector.extract_sample_rows(source.get("connection") or {}, table, n=n) or []
            )
        except Exception as exc:
            logger.warning("sample_dataset %s fetch failed for %s: %s",
                           schema.kind.value, dataset_id, exc)
        return SampleResponse(
            id=dataset_id,
            rows=rows[:n],
            redacted=bool(schema.samples_redacted and any(c.pii for c in schema.columns)),
            truncated=len(rows) > n,
        )

    # Non-tabular kinds — no sample surface yet.
    return SampleResponse(id=dataset_id, rows=[], redacted=True, truncated=False)


# ---------------------------------------------------------------------------
# /run_query
# ---------------------------------------------------------------------------


async def run_query(req: RunQueryRequest) -> RunQueryResponse:
    """Execute a generated read query.

    Dispatches on `kind` through the shared `query_engine` so /query
    (NL→SQL) and /run_query (caller-supplied SQL) share one execution
    path. Backends not handled by the engine raise 501 with a clear
    contract for dept subclasses.
    """
    source = get_source(req.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {req.source_id!r}")

    # All execution flows through the shared query_engine so /query and
    # /run_query produce byte-identical rows for the same SQL/SOQL/OData.
    string_kinds = {DatasetKind.sql, DatasetKind.duckdb, DatasetKind.semantic, DatasetKind.soql}
    if req.kind in string_kinds:
        if not isinstance(req.query, str):
            raise HTTPException(
                status_code=400,
                detail=f"kind={req.kind.value} expects `query` to be a string",
            )
        # For duckdb we need the read_via block from the catalogue document
        # so the engine knows which files to mount and under what view name.
        read_via = None
        if req.kind == DatasetKind.duckdb:
            read_via = _resolve_duckdb_read_via(source, req)

        import query_engine  # type: ignore

        result = await query_engine.execute(
            kind=req.kind.value,
            source=source,
            query=req.query,
            row_limit=req.row_limit,
            read_via=read_via,
        )
        return RunQueryResponse(
            rows=result.rows,
            total=len(result.rows),
            truncated=len(result.rows) >= req.row_limit,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )

    if req.kind == DatasetKind.odata:
        if not isinstance(req.query, dict):
            raise HTTPException(
                status_code=400,
                detail="kind=odata expects `query` to be a dict {entity, $filter, $select, $top, $orderby}",
            )
        # Resolve read_via from the dataset so the connector has the entity set name.
        read_via: Optional[Dict[str, Any]] = None
        if req.dataset_id:
            try:
                _, ds_block = _find_dataset(req.source_id, req.dataset_id)
                read_via = dict(ds_block.get("read_via") or {})
            except HTTPException:
                read_via = None

        import query_engine  # type: ignore

        result = await query_engine.execute(
            kind=req.kind.value,
            source=source,
            query=req.query,
            row_limit=req.row_limit,
            read_via=read_via,
        )
        return RunQueryResponse(
            rows=result.rows,
            total=len(result.rows),
            truncated=len(result.rows) >= req.row_limit,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )

    if req.kind == DatasetKind.bigquery:
        # BigQuery: `query` is a SQL string. The connector wraps with a
        # hard LIMIT and caps maximum_bytes_billed from
        # source.connection.max_bytes_billed.
        if not isinstance(req.query, str):
            raise HTTPException(
                status_code=400,
                detail="kind=bigquery expects `query` to be a SQL string",
            )
        import query_engine  # type: ignore
        result = await query_engine.execute(
            kind=req.kind.value,
            source=source,
            query=req.query,
            row_limit=req.row_limit,
        )
        return RunQueryResponse(
            rows=result.rows,
            total=len(result.rows),
            truncated=len(result.rows) >= req.row_limit,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )

    if req.kind == DatasetKind.sap_rfc:
        # SAP RFC: two shapes.
        #   - Table read: caller supplies read_via via dataset_id (the
        #     dataset's read_via.table is the SAP transparent table).
        #     `query` may also be a dict with extra OPTIONS/FIELDS.
        #   - RFC/BAPI call: `query` is a dict {function, parameters}.
        read_via: Optional[Dict[str, Any]] = None
        if req.dataset_id:
            try:
                _, ds_block = _find_dataset(req.source_id, req.dataset_id)
                read_via = dict(ds_block.get("read_via") or {})
                # Promote dataset's extra.{default_fields,options} into the
                # connector's expected shape so a bare /run_query needn't
                # re-spell the FIELDS list.
                extra = read_via.get("extra") or {}
                if "fields" not in read_via and extra.get("default_fields"):
                    read_via["fields"] = list(extra["default_fields"])
                if "options" not in read_via and extra.get("default_options"):
                    read_via["options"] = list(extra["default_options"])
            except HTTPException:
                read_via = None
        # If query is a dict with WHERE-style OPTIONS, merge into read_via.
        if isinstance(req.query, dict):
            q = req.query
            if read_via is None and q.get("function"):
                # Pure RFC/BAPI call — no table.
                read_via = None
            elif read_via is not None:
                for k in ("fields", "options"):
                    if q.get(k):
                        read_via[k] = list(q[k])
        import query_engine  # type: ignore
        result = await query_engine.execute(
            kind=req.kind.value,
            source=source,
            query=req.query,
            row_limit=req.row_limit,
            read_via=read_via,
        )
        return RunQueryResponse(
            rows=result.rows,
            total=len(result.rows),
            truncated=len(result.rows) >= req.row_limit,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )

    if req.kind == DatasetKind.mongodb:
        # MongoDB structured read. ``query`` is an optional Mongo filter
        # dict ({} = match all). Blocking pymongo work — offload so a
        # slow find can't freeze the event loop.
        return await asyncio.to_thread(_run_mongo, source, req)

    if req.kind == DatasetKind.rest:
        # Schema-driven REST read. The dataset's ``read_via`` holds the
        # request/response mapping (+ input_schema); ``req.query`` is the caller's
        # PARAMS — either flat ({"pan": ...}) or wrapped ({"filters": {...}}) as the
        # keyed-read fast-path sends. The connector does the HTTP work.
        read_via: Optional[Dict[str, Any]] = None
        if req.dataset_id:
            try:
                _, ds_block = _find_dataset(req.source_id, req.dataset_id)
            except HTTPException:
                ds_block = None
            if ds_block is not None:
                rv = dict(ds_block.get("read_via") or {})
                extra = rv.get("extra") or {}
                # Normalise to the flat block the connector expects. The
                # request/response mapping may be authored at read_via top level OR
                # under read_via.extra (the passthrough that survives to the
                # catalogue/builder); input_schema may sit on the dataset or here.
                read_via = {
                    "request": rv.get("request") or extra.get("request") or {},
                    "response": rv.get("response") or extra.get("response") or {},
                    "input_schema": (ds_block.get("input_schema")
                                     or rv.get("input_schema")
                                     or extra.get("input_schema") or {}),
                }
        params: Any = req.query
        if isinstance(params, dict) and isinstance(params.get("filters"), dict):
            params = params["filters"]
        if not isinstance(params, dict):
            params = {}
        import query_engine  # type: ignore
        result = await query_engine.execute(
            kind="rest",
            source=source,
            query=params,
            row_limit=req.row_limit,
            read_via=read_via,
        )
        return RunQueryResponse(
            rows=result.rows,
            total=len(result.rows),
            truncated=len(result.rows) >= req.row_limit,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )
    raise HTTPException(status_code=400, detail=f"Unsupported kind {req.kind!r}")


def _mongo_jsonsafe(value: Any) -> Any:
    """Recursively convert a Mongo document into JSON-serialisable values.

    ObjectId has no JSON encoder — stringify it (and any nested ones).
    datetime is left as-is; pydantic/FastAPI serialise it fine.
    """
    try:
        from bson import ObjectId
    except Exception:  # pragma: no cover — bson ships with pymongo
        ObjectId = ()  # type: ignore
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: _mongo_jsonsafe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mongo_jsonsafe(v) for v in value]
    return value


def _run_mongo(source: Dict[str, Any], req: RunQueryRequest) -> RunQueryResponse:
    """Execute a structured read against a MongoDB-backed dataset.

    ``req.query`` is an optional Mongo filter dict (``{}`` = match all).
    Connection resolution: see ``_mongo_data_conn``.
    """
    started = time.perf_counter()
    conn = source.get("connection") or {}
    coll_name = conn.get("collection")
    if not coll_name:
        return RunQueryResponse(
            error="mongodb source has no connection.collection"
        )
    flt = req.query if isinstance(req.query, dict) else {}
    try:
        from pymongo import MongoClient

        uri, db_name = _mongo_data_conn(conn)
        client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        try:
            coll = client[db_name][coll_name]
            # Fetch one extra so we can report truncation honestly.
            raw = list(coll.find(flt).limit(req.row_limit + 1))
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 — never crash /run_query
        logger.warning("mongo run_query failed for %s: %s",
                        source.get("source_id"), exc)
        return RunQueryResponse(error=f"mongo query failed: {exc}")

    truncated = len(raw) > req.row_limit
    rows = [_mongo_jsonsafe(d) for d in raw[: req.row_limit]]
    return RunQueryResponse(
        rows=rows,
        total=len(rows),
        truncated=truncated,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _resolve_duckdb_read_via(source: Dict[str, Any], req: RunQueryRequest) -> Dict[str, Any]:
    """Look up the dataset's `read_via` block from the source's catalogue.

    Only datasets explicitly catalogued as kind=duckdb expose `files[]`; we
    refuse to fall back to anything else because letting the caller name
    arbitrary file paths would be a path-traversal foot-gun.
    """
    if not req.dataset_id:
        raise HTTPException(
            status_code=400,
            detail="kind=duckdb requires `dataset_id` so the engine can resolve files[]",
        )
    # Honour both the new top-level `datasets` and the legacy nested
    # `catalogue.datasets` (same resolution order as _datasets_for).
    datasets = (
        list(source.get("datasets") or [])
        or list((source.get("catalogue") or {}).get("datasets") or [])
    )
    for ds in datasets:
        if ds.get("id") == req.dataset_id:
            rv = ds.get("read_via") or {}
            if rv.get("kind") != "duckdb":
                raise HTTPException(
                    status_code=400,
                    detail=f"Dataset {req.dataset_id!r} is not catalogued as duckdb",
                )
            # Allow files[] to live either at read_via.files or read_via.extra.files
            files = rv.get("files") or (rv.get("extra") or {}).get("files") or []
            return {
                "kind": "duckdb",
                "table": rv.get("target") or rv.get("table") or "data",
                "files": files,
                "extra": rv.get("extra") or {},
            }
    raise HTTPException(
        status_code=404,
        detail=f"Dataset {req.dataset_id!r} not found on source {req.source_id!r}",
    )


# ---------------------------------------------------------------------------
# /resolve_media — read a record's native media reference (deterministic, by key)
# ---------------------------------------------------------------------------


def declared_mime_hint(source_id: str, dataset_id: str, column: str) -> Optional[str]:
    """The column's declared ``mime_hint``, or None.

    /resolve_media derives content_type from the ref's file EXTENSION, so a ref
    with no recognised extension (a bare object key, a signed URL, an opaque
    id) yielded content_type=None — the one case the hint exists for, and the
    hint was never consulted. Extension still wins: it describes the actual
    stored object, while the hint describes what the author expects.
    """
    try:
        _src, ds = _find_dataset(source_id, dataset_id)
    except Exception:  # unknown source/dataset — the caller's own lookup reports it
        return None
    for c in (ds.get("columns") or []):
        if column in (c.get("name"), c.get("physical_name")):
            hint = c.get("mime_hint")
            return str(hint) if hint else None
    return None


async def read_media_ref(
    *, source_id: str, dataset_id: str, key_field: str, key_value: str, column: str,
) -> str:
    """Read ONE record by key and return the NATIVE media reference stored in
    ``column`` (e.g. ``s3://bucket/key``). Deterministic by-key read — never the NL
    planner. ``column`` and ``key_field`` are validated against the dataset's
    declared columns and the table comes from its read_via, so the only
    caller-controlled value (``key_value``) is escaped per dialect, never spliced
    as an identifier.

    Works for every dataset kind that supports a deterministic by-key read:
    the SQL family (``sql`` / ``bigquery`` / ``duckdb``), ``soql`` and
    ``odata``. It used to be ``sql`` alone, which meant a Salesforce or S/4HANA
    record could carry a document URL that nothing could ever resolve — the
    dataset was fully queryable for rows and 501'd for its own attachment.

    Still refused, with the reason stated: ``rest`` and ``sap_rfc`` have no
    generic by-key form (a REST source's key read is whatever its schema
    declares), ``mongodb`` is not dispatched by query_engine at all, and
    ``semantic`` is a chunk index rather than a record store."""
    source, ds = _find_dataset(source_id, dataset_id)
    _kind = ds.get("kind")
    _kind = _kind.value if hasattr(_kind, "value") else str(_kind)
    _SQL_FAMILY = ("sql", "bigquery", "duckdb")
    if _kind not in _SQL_FAMILY + ("soql", "odata"):
        _why = {
            "rest": "a REST source has no generic by-key read — its key lookup "
                    "is whatever its own schema declares",
            "sap_rfc": "RFC_READ_TABLE has no safe generic by-key projection",
            "mongodb": "mongodb is not dispatched by query_engine",
            "semantic": "a semantic source is a chunk index, not a record store",
        }.get(_kind, "no deterministic by-key read is defined for this kind")
        raise HTTPException(
            status_code=501,
            detail=f"resolve_media cannot read a media reference from a "
                   f"{_kind!r} dataset ({dataset_id!r}): {_why}. Carry the "
                   f"document reference on a sql/bigquery/duckdb/soql/odata "
                   f"dataset instead.",
        )
    # logical→physical column map (accept either name from the caller)
    phys: Dict[str, str] = {}
    for c in (ds.get("columns") or []):
        nm, ph = c.get("name"), (c.get("physical_name") or c.get("name"))
        if nm:
            phys[nm] = ph or nm
        if ph:
            phys[ph] = ph
    if column not in phys:
        raise HTTPException(status_code=400,
                            detail=f"column {column!r} not declared on dataset {dataset_id!r}")
    if key_field not in phys:
        raise HTTPException(status_code=400,
                            detail=f"key_field {key_field!r} not declared on dataset {dataset_id!r}")
    col_phys, key_phys = phys[column], phys[key_field]
    read_via = ds.get("read_via") or {}
    table = read_via.get("table") or ds.get("physical_name") or dataset_id.rsplit(".", 1)[-1]
    # The ONLY caller-controlled value. Identifiers come from the declared
    # columns and the dataset's own read_via, never from the request.
    safe_val = str(key_value).replace("'", "''")

    import query_engine  # type: ignore
    if _kind in _SQL_FAMILY:
        query: Any = (f'SELECT "{col_phys}" FROM {table} '
                      f'WHERE "{key_phys}" = \'{safe_val}\' LIMIT 1')
    elif _kind == "soql":
        # SOQL: no double quotes around identifiers, and LIMIT not TOP. Escaping
        # is BACKSLASH-based, not doubled quotes — SOQL reads '' as an empty
        # string followed by another literal, so the SQL convention would not
        # escape anything here. Backslash first, or the escape escapes itself.
        _soql_val = str(key_value).replace("\\", "\\\\").replace("'", "\\'")
        entity = read_via.get("object") or read_via.get("table") or table
        query = (f"SELECT {col_phys} FROM {entity} "
                 f"WHERE {key_phys} = '{_soql_val}' LIMIT 1")
    else:  # odata — a dict, not a string (see odata_connector.execute_odata)
        entity = read_via.get("entity") or read_via.get("target") or table
        query = {"entity": entity, "$select": col_phys, "$top": 1,
                 "$filter": f"{key_phys} eq '{safe_val}'"}
    result = await query_engine.execute(
        kind=_kind, source=source, query=query, row_limit=1, read_via=read_via)
    if result.error:
        raise HTTPException(status_code=502, detail=f"media row read failed: {result.error}")
    if not result.rows:
        raise HTTPException(status_code=404,
                            detail=f"record {key_value!r} not found in {dataset_id!r}")
    row = result.rows[0]
    ref = row.get(col_phys) if col_phys in row else row.get(column)
    if not ref or not isinstance(ref, str):
        raise HTTPException(status_code=404,
                            detail=f"media column {column!r} empty on record {key_value!r}")
    return ref


# ---------------------------------------------------------------------------
# /execute_action
# ---------------------------------------------------------------------------


async def execute_action(
    req: ExecuteActionRequest,
    claims: Optional[Dict[str, Any]] = None,
) -> ExecuteActionResponse:
    """Run a registered write action on a dataset.

    ``claims`` is the verified X-User-JWT body. When present, the action's
    write-authz gate (check_write_permission) is enforced ON TOP OF the
    read visibility PDP the caller already passed — so a user who can read
    a source may still be refused a write to it. Pass claims=None only in
    dev / AUTHZ_ENFORCE=false contexts.
    """
    started = time.perf_counter()
    # Resolve the dataset block WITHOUT live introspection — execute_action
    # only needs the action definition + kind, not the column schema.
    # describe_dataset would re-sample the Mongo collection / hit BigQuery
    # on every write; _find_dataset just reads the catalogued block.
    source, ds = _find_dataset(req.source_id, req.dataset_id)
    kind = DatasetKind(ds.get("kind", _source_kind(source).value))
    action_dict = next(
        (w for w in (ds.get("write_actions") or [])
         if isinstance(w, dict) and w.get("id") == req.action_id),
        None,
    )
    if action_dict is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Action {req.action_id!r} not registered on {req.dataset_id!r}. "
                "Register it under the source's datasets[].write_actions block "
                "(or, for a non-tabular source, the source-level write_actions[])."
            ),
        )
    action = WriteAction(**action_dict)

    # Write-authz — enforced before validation and before a dry-run, so an
    # unauthorised caller learns nothing about the action's shape.
    if claims:
        from auth import check_write_permission  # local import — avoids cycle
        check_write_permission(
            claims,
            roles_allowed_write=action.roles_allowed_write,
            action_id=action.id,
        )

    # Full pre-flight validation — run identically for dry_run and the real
    # call, so a dry_run is a faithful preview, not a partial check.
    _validate_action(action, req, kind)

    if req.dry_run:
        return ExecuteActionResponse(
            ok=True,
            action_id=req.action_id,
            result={"dry_run": True, "validated": True, "verb": action.verb,
                    "payload": req.payload},
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    # sql + mongodb executors are synchronous/blocking — offload to a worker
    # thread so a slow write can't freeze the MCP event loop. rest / soql /
    # odata executors are already async (async httpx / async connectors).
    if kind == DatasetKind.sql:
        result = await asyncio.to_thread(
            _exec_sql_action, source, action, req, claims or {}
        )
    elif kind == DatasetKind.rest:
        result = await _exec_rest_action(source, action, req)
    elif kind == DatasetKind.soql:
        from connectors import soql_connector  # type: ignore
        result = await soql_connector.execute_action(
            source.get("connection") or {},
            action.model_dump(),
            req.payload,
            idempotency_key=req.idempotency_key,
        )
    elif kind == DatasetKind.odata:
        from connectors import odata_connector  # type: ignore
        result = await odata_connector.execute_action(
            source.get("connection") or {},
            action.model_dump(),
            req.payload,
            idempotency_key=req.idempotency_key,
        )
    elif kind == DatasetKind.mongodb:
        result = await asyncio.to_thread(_exec_mongo_action, source, action, req, claims or {})
    elif kind == DatasetKind.semantic:
        raise HTTPException(
            status_code=501,
            detail=(
                "execute_action for kind='semantic' is an extension point — "
                "a vector store has no write contract. Override "
                "catalogue._exec_semantic_action in a dept subclass if needed."
            ),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported kind {kind!r}")

    # A real write changed the data — bump the source's count-cache version so
    # any cached size estimate (count-probe) is invalidated at once. Best-effort:
    # a cache blip must never fail a committed write.
    try:
        import plan_cache
        plan_cache.bump_data_version((source or {}).get("source_id"))
    except Exception:  # noqa: BLE001
        logger.debug("count-cache version bump skipped", exc_info=True)

    return ExecuteActionResponse(
        ok=True,
        action_id=req.action_id,
        result=result,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _validate_payload(action: WriteAction, payload: Dict[str, Any]) -> None:
    """Lightweight required-fields check using the action's input_schema.

    We deliberately avoid a heavy JSON-Schema dependency here — the smart-app
    builder already validates against the full schema before invoking us.
    """
    schema = action.input_schema or {}
    required = schema.get("required") or []
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields for action {action.id!r}: {missing}",
        )


def _validate_action(action: WriteAction, req: ExecuteActionRequest, kind: DatasetKind) -> None:
    """Full pre-flight validation for a write action.

    Run identically for a dry_run and a real execution, so a dry_run that
    returns ok=True is a faithful guarantee the real call will not 4xx on
    payload shape. Generic checks apply to every kind; mongodb adds its own.
    """
    _validate_payload(action, req.payload)  # generic: input_schema.required

    if kind == DatasetKind.mongodb:
        verb = (action.verb or "").lower()
        if verb not in ("create", "update", "upsert", "delete"):
            raise HTTPException(
                status_code=400,
                detail=f"mongo action {action.id!r}: unsupported verb {verb!r}",
            )
        # A mongo write action MUST declare its field contract.
        props = (action.input_schema or {}).get("properties") or {}
        if not props:
            raise HTTPException(
                status_code=500,
                detail=f"mongo write action {action.id!r} must declare input_schema.properties",
            )
        # Field allow-list — only declared fields may be written.
        unknown = set(req.payload) - set(props.keys())
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"action {action.id!r} does not permit field(s): {sorted(unknown)}",
            )
        # update / upsert / delete need a key filter so a write can never
        # match more than the intended document(s).
        if verb in ("update", "upsert", "delete"):
            if not action.key_fields:
                raise HTTPException(
                    status_code=500,
                    detail=f"mongo action {action.id!r} verb={verb!r} requires key_fields",
                )
            bad = [k for k in action.key_fields if k not in props]
            if bad:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"mongo action {action.id!r} key_fields {bad} are not "
                        "declared in input_schema.properties"
                    ),
                )
            missing_keys = [k for k in action.key_fields if k not in req.payload]
            if missing_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"action {action.id!r} missing key field(s): {missing_keys}",
                )


def _sql_action_http_error(action: WriteAction, exc: Exception) -> HTTPException:
    """Turn a driver exception into a response an OFFICER can be shown.

    The caller here is a bank officer looking at a decision card, not an
    operator. SQLAlchemy's ``str(exc)`` appends ``[SQL: ...]`` and
    ``[parameters: ...]`` — the statement and every bound value — and psycopg2
    adds a ``LINE n:`` fragment of the statement under its own message. All of
    that used to be forwarded verbatim to the browser: schema disclosure, and
    unreadable besides.

    So: keep the driver's FIRST line, which is the diagnosis ("invalid input
    syntax for type timestamp") and carries no SQL, and drop the rest. The
    full thing is already in the log at ERROR with a traceback — this is a
    narrowing of what the UI sees, never a swallow.

    Status follows fault: a rejected value or violated constraint is the
    request's (4xx); an unreachable database is not (503).
    """
    orig = getattr(exc, "orig", None) or exc
    # First line only — everything after it is statement context.
    reason = str(orig).strip().splitlines()[0].strip() or exc.__class__.__name__

    status_code = 500
    try:
        from sqlalchemy import exc as sa_exc  # type: ignore

        if isinstance(exc, (sa_exc.DataError, sa_exc.IntegrityError)):
            status_code = 400
        elif isinstance(exc, (sa_exc.OperationalError, sa_exc.InterfaceError)):
            status_code = 503
            # Connection failures can carry host/port/credential hints.
            reason = "the source database is not reachable"
    except ImportError:
        pass

    return HTTPException(
        status_code=status_code,
        detail=f"action {action.id!r} could not be applied: {reason}",
    )


def _exec_sql_action(
    source: Dict[str, Any],
    action: WriteAction,
    req: ExecuteActionRequest,
    claims: Dict[str, Any],
) -> Dict[str, Any]:
    """Run a parameterised DML statement. Synchronous — execute_action
    invokes it via asyncio.to_thread so the blocking DB round-trip stays
    off the event loop.

    The template uses SQLAlchemy named-parameter syntax (``:field``).
    No string interpolation — all values flow through bound parameters.

    Audit stamping — a field declared ``"x-citra-fill": "actor"`` in the
    action's input_schema is filled from the VERIFIED JWT and overrides
    whatever the caller sent, matching the guarantee the mongo executor
    already gives with created_by/updated_by. The identity of whoever
    committed a write is not something a payload gets to assert.
    """
    from sqlalchemy import text as sqla_text
    from connectors.sql_connector import _get_engine  # type: ignore  # internal helper

    if not action.sql_template:
        raise HTTPException(
            status_code=500,
            detail=f"sql action {action.id!r} has no sql_template",
        )
    engine = _get_engine(source.get("connection") or {})

    # Bind every field the action *declares* in its input_schema, defaulting
    # any the caller omitted to NULL. A single write action is routinely
    # called with partial payloads across an entity's lifecycle (e.g. dispatch
    # sets restoration_crew; restoration sets end_time + saidi_minutes), and
    # SQLAlchemy's text() raises InvalidRequestError if a ``:param`` in the
    # template has no value at all — an absent key is NOT treated as NULL.
    # Templates pair this with COALESCE(:field, field) so a NULL bind PRESERVES
    # the current value rather than wiping it. Required fields are already
    # enforced by _validate_payload, and any ``:param`` the template references
    # but the schema does NOT declare stays absent — so a malformed action
    # definition still fails loud here instead of silently binding NULL.
    declared = (action.input_schema or {}).get("properties") or {}
    params = {field: None for field in declared}
    params.update(req.payload)

    # An EMPTY STRING for a field that cannot hold one is an omission, not a
    # value — treat it as NULL so COALESCE(:field, field) preserves what is
    # already there.
    #
    # The defaulting above only covers keys the caller left OUT. A caller that
    # sends "" instead overwrites the None, and "" then reaches the column:
    #
    #   invalid input syntax for type timestamp: ""
    #   ... decided_by='', decided_at='' WHERE application_id=...
    #
    # Observed on a live embed Apply: the agent proposed the write without
    # filling decided_by/decided_at, the officer approved it, and the whole
    # UPDATE was rejected by Postgres. Nothing committed — correct — but the
    # write is unusable and the officer sees a psycopg2 error.
    #
    # Scoped by DECLARED TYPE, deliberately. A bare `type: string` keeps ""
    # because clearing a text column is a legitimate edit; only formats and
    # types that can never accept "" are coerced. Anything not declared is left
    # exactly as the caller sent it.
    _EMPTY_INVALID_FORMATS = {"date", "date-time", "time", "duration", "uuid"}
    for field, spec in declared.items():
        if params.get(field) != "" or not isinstance(spec, dict):
            continue
        types = spec.get("type")
        types = {types} if isinstance(types, str) else set(types or ())
        fmt = spec.get("format")
        if (types & {"number", "integer", "boolean"}) or fmt in _EMPTY_INVALID_FORMATS:
            logger.info(
                "execute_action %s: field %r arrived as '' but is declared "
                "%s%s — binding NULL so the current value is preserved",
                action.id, field, types or "?",
                f"/{fmt}" if fmt else "",
            )
            params[field] = None

    # Server-filled audit columns. The agent proposes a write long before an
    # officer approves it, so it cannot know who will commit it — and must not
    # guess. A field declared "x-citra-fill": "actor" is bound from the
    # verified caller identity here, at the point of write, overriding any
    # value in the payload. That makes it unforgeable: a client that sends its
    # own decided_by has it replaced, not honoured.
    _actor = (
        claims.get("email")
        or claims.get("user_id")
        or claims.get("sub")
    )
    for field, spec in declared.items():
        if not isinstance(spec, dict) or spec.get("x-citra-fill") != "actor":
            continue
        if not _actor:
            # Fail loud: the action asked for an audited actor and the token
            # carries no identity. Committing an unattributed decision would
            # be worse than refusing it.
            raise HTTPException(
                status_code=403,
                detail=(
                    f"action {action.id!r} declares {field!r} as an audit "
                    "actor but the caller's token carries no identity"
                ),
            )
        if params.get(field) not in (None, "", _actor):
            logger.warning(
                "execute_action %s: payload set %r=%r; overriding with the "
                "verified caller identity",
                action.id, field, params.get(field),
            )
        params[field] = _actor

    # The when, on the same footing as the who. `decided_by` was unforgeable
    # while `decided_at` beside it was whatever the model typed — observed on
    # acme-bank, where a decision proposed today carried "17 Jul 2026, 4:00 pm"
    # because the agent invented a plausible-looking timestamp. A decision time
    # a model guessed is not an audit trail. "x-citra-fill": "now" binds the
    # column to the server clock AT THE POINT OF WRITE, which is also the only
    # moment that is true: the agent proposes long before an officer approves.
    from datetime import datetime as _dt, timezone as _tz
    _now = _dt.now(_tz.utc)
    for field, spec in declared.items():
        if not isinstance(spec, dict) or spec.get("x-citra-fill") != "now":
            continue
        if params.get(field) not in (None, ""):
            logger.warning(
                "execute_action %s: payload set %r=%r; overriding with the "
                "server clock", action.id, field, params.get(field),
            )
        # Match the column: a DATE/TIMESTAMP column takes the datetime, a text
        # column takes ISO-8601. Guessing a format into a typed column is the
        # DataError this stamping exists to avoid.
        _t = str(spec.get("type") or "").lower()
        params[field] = _now.isoformat() if _t == "string" else _now

    # What the SERVER decided, returned so the caller can show what was
    # actually written. Until now the response carried only a rowcount, so the
    # officer's applied-changes card echoed the agent's PROPOSAL — displaying
    # decided_by "credit-officer-assistant" over a row that says
    # credit-manager@acme-bank-demo.citra.ai. On the one screen whose job is to
    # evidence the decision, that is the audit trail disagreeing with itself.
    #
    # ONLY the fields the server itself filled. Echoing every bound parameter
    # would put customer data into a response that flows to logs and browsers
    # for no reason — these two are the ones the caller cannot otherwise know.
    _server_filled = {
        f: (params[f].isoformat() if hasattr(params[f], "isoformat") else params[f])
        for f, spec in declared.items()
        if isinstance(spec, dict)
        and spec.get("x-citra-fill") in ("actor", "now")
        and f in params
    }

    try:
        with engine.begin() as conn:
            result = conn.execute(sqla_text(action.sql_template), params)
            rowcount = getattr(result, "rowcount", None)
        return {"rowcount": rowcount, "verb": action.verb,
                "server_filled": _server_filled}
    except Exception as exc:
        # Fail loud in the LOG — full statement, bound parameters, traceback.
        logger.error(
            "execute_action sql failed: action=%s source=%s: %s",
            action.id, req.source_id, exc, exc_info=True,
        )
        raise _sql_action_http_error(action, exc)


async def _exec_rest_action(
    source: Dict[str, Any],
    action: WriteAction,
    req: ExecuteActionRequest,
) -> Dict[str, Any]:
    """POST/PATCH against the registered REST endpoint."""
    import httpx  # local import keeps cold-start light

    if not action.endpoint:
        raise HTTPException(status_code=500, detail=f"rest action {action.id!r} has no endpoint")

    base = (source.get("connection") or {}).get("base_url") or ""
    url = action.endpoint if action.endpoint.startswith("http") else f"{base.rstrip('/')}/{action.endpoint.lstrip('/')}"
    method = (action.method or "POST").upper()
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if req.idempotency_key:
        headers["Idempotency-Key"] = req.idempotency_key

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, url, json=req.payload, headers=headers)
        # Raise HTTPException with a sanitised body (no upstream credentials).
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"rest action {action.id!r} failed: {resp.text[:500]}",
            )
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        return {"status": resp.status_code, "body": body, "verb": action.verb}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("execute_action rest failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"rest action failed: {exc}")


# ---------------------------------------------------------------------------
# MongoDB write executor
# ---------------------------------------------------------------------------


def _mongo_collection(conn: Dict[str, Any]):
    """Resolve a pymongo Collection for a mongodb source's connection block.

    Connection resolution: see ``_mongo_data_conn``. The caller owns
    closing the client via ``collection.database.client.close()``.
    """
    from pymongo import MongoClient

    coll_name = conn.get("collection")
    if not coll_name:
        raise HTTPException(
            status_code=500, detail="mongodb source has no connection.collection",
        )
    uri, db_name = _mongo_data_conn(conn)
    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    return client[db_name][coll_name]


# Collections whose idempotency index has been ensured this process.
_IDEMPOTENCY_INDEX_DONE: set = set()


def _ensure_idempotency_index(coll, tenant_filter: Dict[str, Any]) -> None:
    """Ensure the partial-unique index that makes idempotent create safe
    UNDER CONCURRENCY.

    Without it, two simultaneous requests carrying the same idempotency
    key can both miss the upsert filter and both insert. The index is
    compound over ``_idempotency_key`` + the tenant fields, so an
    idempotency key is unique *per tenant*. Ensured once per
    (collection, key-shape) per process; create_index is idempotent
    server-side so a redundant call is a cheap no-op.
    """
    keys = ["_idempotency_key"] + sorted(tenant_filter.keys())
    cache_key = f"{coll.database.name}.{coll.name}:{','.join(keys)}"
    if cache_key in _IDEMPOTENCY_INDEX_DONE:
        return
    try:
        coll.create_index(
            [(k, 1) for k in keys],
            unique=True,
            partialFilterExpression={"_idempotency_key": {"$exists": True}},
        )
        _IDEMPOTENCY_INDEX_DONE.add(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not ensure _idempotency_key index on %s: %s", coll.name, exc,
        )


def _exec_mongo_action(
    source: Dict[str, Any],
    action: WriteAction,
    req: ExecuteActionRequest,
    claims: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a write action against a MongoDB-backed source.

    Synchronous (pymongo) — execute_action invokes it via asyncio.to_thread
    so the blocking write stays off the event loop.

    Enterprise guarantees:
      • Tenant scoping   — connection.tenant_filter is ANDed into every
        update/delete filter and merged into every inserted document, so a
        write can never escape the source's tenant partition.
      • Field allow-list — only fields declared in action.input_schema.
        properties may be written; anything else is rejected (422).
      • Audit stamping   — created_by/at + updated_by/at are set from the
        verified JWT; an impersonation id (if any) is recorded on the row.
      • Idempotent create — when idempotency_key is supplied, create is an
        upsert keyed on it AND backed by a per-tenant unique index, so even
        concurrent duplicate requests can never double-insert.

    Verbs: create | update | upsert | delete. update/upsert/delete require
    action.key_fields (the payload fields that identify the target doc).
    Payload / field-contract validation runs upstream in _validate_action.
    """
    from datetime import datetime, timezone

    # Payload shape, field allow-list and key_fields were all checked in
    # _validate_action (before dry_run) — this executor trusts a validated
    # request and only performs the write.
    conn = source.get("connection") or {}
    verb = (action.verb or "").lower()
    tenant_filter = dict(conn.get("tenant_filter") or {})

    now = datetime.now(timezone.utc)
    actor = claims.get("user_id") or claims.get("sub") or "unknown"
    imp = claims.get("impersonation_id")

    coll = _mongo_collection(conn)
    client = coll.database.client
    try:
        if verb == "create":
            doc = {k: v for k, v in req.payload.items()}
            doc.update(tenant_filter)
            doc.update({
                "created_by": actor, "created_at": now,
                "updated_by": actor, "updated_at": now,
            })
            if imp:
                doc["written_via_impersonation"] = imp
            if req.idempotency_key:
                # Idempotent create: upsert keyed on the idempotency key,
                # backed by a unique index so concurrent duplicates can't
                # both insert. The loser of the race gets DuplicateKeyError,
                # which is itself a successful idempotent replay.
                from pymongo.errors import DuplicateKeyError  # type: ignore

                _ensure_idempotency_index(coll, tenant_filter)
                try:
                    res = coll.update_one(
                        {"_idempotency_key": req.idempotency_key, **tenant_filter},
                        {"$setOnInsert": {**doc, "_idempotency_key": req.idempotency_key}},
                        upsert=True,
                    )
                except DuplicateKeyError:
                    return {
                        "verb": verb, "rowcount": 0, "inserted_id": None,
                        "idempotent_replay": True,
                    }
                inserted = res.upserted_id is not None
                return {
                    "verb": verb,
                    "rowcount": 1 if inserted else 0,
                    "inserted_id": str(res.upserted_id) if res.upserted_id else None,
                    "idempotent_replay": not inserted,
                }
            res = coll.insert_one(doc)
            return {"verb": verb, "rowcount": 1, "inserted_id": str(res.inserted_id)}

        # update / upsert / delete — key filter (validated in _validate_action;
        # the guard below is defence-in-depth for a destructive operation).
        key_fields = list(action.key_fields or [])
        if not key_fields:
            raise HTTPException(
                status_code=500,
                detail=f"mongo action {action.id!r} verb={verb!r} requires key_fields",
            )
        filt = {**tenant_filter, **{k: req.payload[k] for k in key_fields}}

        if verb == "delete":
            res = coll.delete_one(filt)
            return {"verb": verb, "rowcount": res.deleted_count}

        if verb in ("update", "upsert"):
            set_fields = {k: v for k, v in req.payload.items() if k not in key_fields}
            set_fields.update({"updated_by": actor, "updated_at": now})
            if imp:
                set_fields["written_via_impersonation"] = imp
            update_doc: Dict[str, Any] = {"$set": set_fields}
            if verb == "upsert":
                update_doc["$setOnInsert"] = {
                    **tenant_filter,
                    **{k: req.payload[k] for k in key_fields},
                    "created_by": actor, "created_at": now,
                }
            res = coll.update_one(filt, update_doc, upsert=(verb == "upsert"))
            upserted = getattr(res, "upserted_id", None)
            return {
                "verb": verb,
                "rowcount": res.modified_count + (1 if upserted else 0),
                "matched": res.matched_count,
                "upserted_id": str(upserted) if upserted else None,
            }

        raise HTTPException(status_code=400, detail=f"mongo action: unsupported verb {verb!r}")
    finally:
        client.close()
