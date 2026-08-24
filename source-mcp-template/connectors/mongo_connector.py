# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
MongoDB Connector
=================
Connects to MongoDB, introspects collection schema, and provides row/chunk
iteration for the hybrid ingest pipeline.

A MongoDB source can have TWO categories of fields in each document:
  • scalar_fields  — numeric, boolean, date, short-string IDs → embedded as
                     structured Milvus records (record_type="schema"|"row")
                     so the LLM can generate MongoDB aggregation pipelines.
  • text_fields    — long free-text fields (descriptions, notes, comments) →
                     chunked and embedded as semantic Milvus records.

sources.yaml shape:
  connection:
    type: mongodb
    env_prefix: SURVEY_MONGO       # reads SURVEY_MONGO_URI  (full pymongo URI)
                                   #       SURVEY_MONGO_DB   (database name)
    collection: farmer_surveys     # MongoDB collection name
    scalar_fields:                 # short/structured fields → structured engine
      - district
      - block
      - crop_type
      - year
      - yield_tonnes
      - irrigated
    text_fields:                   # long text fields → semantic engine
      - remarks
      - issues_reported
    categorical_fields:            # scalar_fields to compute distinct values for
      - district
      - block
      - crop_type
    id_field: _id                  # field used as the stable document identifier
    page_size: 500                 # documents per cursor batch

Credentials:
  SURVEY_MONGO_URI  — full pymongo connection string (e.g. mongodb://user:pass@host:27017)
  SURVEY_MONGO_DB   — database name

Design Notes:
  • pymongo (sync) is used here for the connector because schema introspection
    and row iteration are CPU-bound; the async motor client is used only inside
    the async writer/engine layers.
  • Field-type inference samples up to SCHEMA_SAMPLE_DOCS documents.
  • Distinct values for categorical fields are limited to CARDINALITY_THRESHOLD.
"""

import logging
import os
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

SCHEMA_SAMPLE_DOCS   = 200   # documents to sample for field-type inference
CARDINALITY_THRESHOLD = 30    # max distinct values stored for categorical fields


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

# Module-level client cache. A MongoClient owns its own connection pool, so a
# fresh client per call (the old behaviour) defeated pooling entirely and leaked
# sockets. Cache one client per URI and reuse it across all calls.
_MONGO_CLIENT_CACHE: Dict[str, Any] = {}


def _get_mongo_client(conn_config: Dict[str, Any]):
    from pymongo import MongoClient
    from citra_service_utils import require_env
    prefix = conn_config.get("env_prefix", "").upper()
    # REQUIRED: no localhost default — a missing {prefix}_URI must fail loud
    # rather than silently connecting this source to a local Mongo.
    uri    = require_env(f"{prefix}_URI")
    cached = _MONGO_CLIENT_CACHE.get(uri)
    if cached is not None:
        return cached
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=10_000,
        socketTimeoutMS=30_000,
    )
    _MONGO_CLIENT_CACHE[uri] = client
    return client


def _get_collection(conn_config: Dict[str, Any]):
    """Return a pymongo Collection object."""
    prefix     = conn_config.get("env_prefix", "").upper()
    db_name    = os.getenv(f"{prefix}_DB", "")
    coll_name  = conn_config.get("collection", "")
    if not db_name or not coll_name:
        raise ValueError(
            f"MongoDB connector requires DB name ({prefix}_DB env var) "
            f"and 'collection' in connection config."
        )
    client = _get_mongo_client(conn_config)
    return client[db_name][coll_name]


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

def _infer_field_type(value: Any) -> str:
    """Map a Python/BSON value to a simple type label."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    import datetime
    if isinstance(value, (datetime.datetime, datetime.date)):
        return "datetime"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _sample_distinct(collection, field: str) -> Optional[List[Any]]:
    """
    Return at most CARDINALITY_THRESHOLD distinct values for a field.
    Returns None when the cardinality exceeds the threshold.

    RULE #1 fail-loud: the legitimate None ("too many distinct — don't
    enumerate") is kept, but a genuine query FAILURE used to also return None,
    silently masking a broken describe. On failure we now log and re-raise.
    """
    try:
        vals = collection.distinct(field)
    except Exception as exc:
        logger.error(f"❌ [MONGO] distinct({field}) failed: {exc}")
        raise
    if len(vals) <= CARDINALITY_THRESHOLD:
        return [str(v) for v in vals if v is not None]
    return None   # too many — don't enumerate


def extract_schema(conn_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sample documents and infer field names + types for the configured scalar fields.

    Returns:
      {
        "collection":  "farmer_surveys",
        "columns": [
          {"name": "district",      "type": "string",  "distinct_values": ["Patna", ...]},
          {"name": "yield_tonnes",  "type": "float",   "range": {"min": 0.5, "max": 18.2}},
          ...
        ]
      }
    """
    coll           = _get_collection(conn_config)
    scalar_fields  = conn_config.get("scalar_fields", [])
    cat_fields     = set(conn_config.get("categorical_fields", []))
    coll_name      = conn_config.get("collection", "")

    # Sample to infer types
    samples = list(coll.find({}, limit=SCHEMA_SAMPLE_DOCS))

    # Build a field → type map from samples
    field_types: Dict[str, str] = {}
    numeric_ranges: Dict[str, Dict[str, float]] = {}

    for doc in samples:
        for field in scalar_fields:
            val = doc.get(field)
            if val is None:
                continue
            ft = _infer_field_type(val)
            if field not in field_types:
                field_types[field] = ft

            if ft in ("integer", "float"):
                v = float(val)
                if field not in numeric_ranges:
                    numeric_ranges[field] = {"min": v, "max": v}
                else:
                    numeric_ranges[field]["min"] = min(numeric_ranges[field]["min"], v)
                    numeric_ranges[field]["max"] = max(numeric_ranges[field]["max"], v)

    columns = []
    for field in scalar_fields:
        col: Dict[str, Any] = {
            "name": field,
            "type": field_types.get(field, "string"),
        }
        if field in cat_fields:
            distinct = _sample_distinct(coll, field)
            if distinct is not None:
                col["distinct_values"] = distinct
        elif field in numeric_ranges:
            r = numeric_ranges[field]
            col["range"] = {"min": r["min"], "max": r["max"]}
        columns.append(col)

    return {"collection": coll_name, "columns": columns}


# ---------------------------------------------------------------------------
# Row iteration (scalar fields only)
# ---------------------------------------------------------------------------

def iter_rows(
    conn_config: Dict[str, Any],
    after_id: Optional[Any] = None,
) -> Generator[Dict[str, Any], None, None]:
    """
    Yield documents as flat row dicts (scalar_fields + id_field only).
    Uses range-cursor pagination (no skip) for efficiency on large collections.

    Args:
        after_id: If given, only yield documents with _id > after_id (for incremental scan).
    """
    coll          = _get_collection(conn_config)
    scalar_fields = conn_config.get("scalar_fields", [])
    id_field      = conn_config.get("id_field", "_id")
    page_size     = conn_config.get("page_size", 500)

    # Build projection
    proj: Dict[str, int] = {f: 1 for f in scalar_fields}
    proj["_id"] = 1    # always include _id for stable IDs

    query: Dict[str, Any] = {}
    if after_id is not None:
        query["_id"] = {"$gt": after_id}

    cursor = coll.find(query, proj).sort("_id", 1).batch_size(page_size)
    for doc in cursor:
        row: Dict[str, Any] = {id_field: str(doc.get("_id", ""))}
        for f in scalar_fields:
            row[f] = doc.get(f)
        yield row


# ---------------------------------------------------------------------------
# Text-chunk iteration (text fields only)
# ---------------------------------------------------------------------------

#: Text chunking for long Mongo fields. Inlined from the old
#: ``connectors.file_connector``, which was DELETED: it advertised PDF/DOCX/XLSX
#: ingestion this MCP deliberately does not do — its libraries are not even
#: installed (see requirements.txt: "Ingestion deps removed ... owned by the
#: Citra Workflow Engine"). This chunker was the one live thing in it.
CHUNK_CHAR_SIZE = 2048
CHUNK_OVERLAP_CHARS = 256


def _chunk_text(text: str, source: str, chunk_size: int = CHUNK_CHAR_SIZE,
                overlap: int = CHUNK_OVERLAP_CHARS):
    """Yield overlapping character windows of ``text``."""
    text = text.strip()
    if not text:
        return

    start = 0
    idx = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            yield {
                "text": chunk,
                "source": source,
                "chunk_index": idx,
                "char_start": start,
            }
            idx += 1
        start += chunk_size - overlap


def iter_text_chunks(
    conn_config: Dict[str, Any],
) -> Generator[Dict[str, Any], None, None]:
    """
    Yield text chunks from text_fields of each document.
    Each yielded chunk dict:
      {text, source (coll/doc_id/field), file_path, chunk_index, doc_id}
    """
    coll        = _get_collection(conn_config)
    text_fields = conn_config.get("text_fields", [])
    id_field    = conn_config.get("id_field", "_id")
    coll_name   = conn_config.get("collection", "")
    page_size   = conn_config.get("page_size", 500)

    if not text_fields:
        return

    proj: Dict[str, int] = {f: 1 for f in text_fields}
    proj["_id"] = 1

    cursor = coll.find({}, proj).sort("_id", 1).batch_size(page_size)
    for doc in cursor:
        doc_id = str(doc.get("_id", ""))
        for field in text_fields:
            raw = doc.get(field, "")
            if not raw or not str(raw).strip():
                continue
            text = str(raw)
            source_path = f"{coll_name}/{doc_id}/{field}"
            for chunk in _chunk_text(text, source=source_path):
                chunk["doc_id"]    = doc_id
                chunk["file_path"] = source_path   # used for mtime-key in writer
                chunk["field"]     = field
                yield chunk


# ---------------------------------------------------------------------------
# Fetch all current document IDs (for orphan detection)
# ---------------------------------------------------------------------------

def fetch_all_doc_ids(conn_config: Dict[str, Any]) -> Set[str]:
    """Return the set of all _id values (as strings) in the collection."""
    coll = _get_collection(conn_config)
    ids: Set[str] = set()
    for doc in coll.find({}, {"_id": 1}).sort("_id", 1):
        ids.add(str(doc["_id"]))
    return ids


# ---------------------------------------------------------------------------
# Sampled rows (for structured-path ingestion: schema + ≤N representative docs)
# ---------------------------------------------------------------------------

def sample_rows(conn_config: Dict[str, Any], n: int = 150) -> List[Dict[str, Any]]:
    """
    Return up to `n` randomly-sampled documents projected to scalar_fields + id_field.
    Mirrors the semantics of `connectors.sql_connector.extract_sample_rows`.
    """
    import datetime

    coll          = _get_collection(conn_config)
    scalar_fields = conn_config.get("scalar_fields", [])
    id_field      = conn_config.get("id_field", "_id")

    proj: Dict[str, int] = {f: 1 for f in scalar_fields}
    proj["_id"] = 1

    pipeline = [{"$sample": {"size": int(max(1, n))}}, {"$project": proj}]
    rows: List[Dict[str, Any]] = []
    # RULE #1 fail-loud: a failure here used to return [] — indistinguishable
    # from an empty collection. Log and re-raise instead.
    try:
        for doc in coll.aggregate(pipeline):
            row: Dict[str, Any] = {id_field: str(doc.get("_id", ""))}
            for f in scalar_fields:
                v = doc.get(f)
                if isinstance(v, (datetime.datetime, datetime.date)):
                    v = v.isoformat()
                row[f] = v
            rows.append(row)
    except Exception as exc:
        logger.error(f"❌ [MONGO] sample_rows failed: {exc}")
        raise
    return rows


# ---------------------------------------------------------------------------
# Execute aggregation pipeline (used by mongo_engine at query time)
# ---------------------------------------------------------------------------

_DEFAULT_AGG_ROW_LIMIT = 1000
# Wall-clock cap (ms) so a runaway aggregation can't pin a pooled socket open.
_AGG_MAX_TIME_MS = int(os.getenv("MONGO_AGG_MAX_TIME_MS", "30000"))


def run_aggregation(
    conn_config: Dict[str, Any],
    pipeline: List[Dict[str, Any]],
    row_limit: int = _DEFAULT_AGG_ROW_LIMIT,
) -> List[Dict[str, Any]]:
    """
    Execute a MongoDB aggregation pipeline synchronously and return the results.
    ObjectId values are converted to strings for safe JSON serialisation.

    The pipeline is BOUNDED: a trailing `$limit` of `row_limit` is appended
    (unless the pipeline already ends in `$limit`), and `maxTimeMS` caps the
    server-side execution time. `row_limit` is optional/defaulted so existing
    callers that don't pass it stay bounded too.
    """
    import bson

    coll = _get_collection(conn_config)

    bounded_pipeline = list(pipeline)
    last_stage = bounded_pipeline[-1] if bounded_pipeline else None
    already_limited = isinstance(last_stage, dict) and "$limit" in last_stage
    if not already_limited:
        bounded_pipeline.append({"$limit": int(max(1, row_limit))})

    results = list(coll.aggregate(bounded_pipeline, maxTimeMS=_AGG_MAX_TIME_MS))

    # Convert non-serialisable BSON types to plain Python types
    def _coerce(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _coerce(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_coerce(i) for i in obj]
        if hasattr(obj, "__class__") and obj.__class__.__name__ == "ObjectId":
            return str(obj)
        import datetime
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        return obj

    return [_coerce(r) for r in results]
