# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Dept MCP Server — Source Loader + Query Router
================================================
Source definitions are pulled from Citra-Service's central ``dept_sources``
Mongo collection (filtered to this deployment's ``ORG_ID`` + ``DEPT_IDS``).

The collection is the single source of truth — what the Workflow Engine
ingests is exactly what this MCP serves. Optional polling refreshes the
in-memory registry without a restart so operator edits flow through.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from config import get_settings
from metrics import observe_query
from models import ChunkResult, QueryRequest, QueryResponse, SourceType

logger = logging.getLogger(__name__)

# In-memory registry: source_id → source dict. Multi-dept deployments share
# this map but each entry retains its own ``dept_id`` for namespacing.
_sources: Dict[str, Dict[str, Any]] = {}
_refresh_task: Optional[asyncio.Task] = None

# Semantic (RAG) sources are DROPPED from ``_sources`` (the query registry) so
# they never reach /query, /datasets or the planner — the MCP serves ZERO RAG
# (pure disconnect). But they must still be ADVERTISED to discovery so chat (whose
# only source of truth is discovery) can surface them and read Milvus directly.
# We keep the dropped docs here for register_all to publish (flagged semantic +
# rag_collection, no queryable routing).
_semantic_sources: List[Dict[str, Any]] = []


def get_semantic_sources() -> List[Dict[str, Any]]:
    """The ``type == semantic`` sources excluded from the query registry, kept so
    registration can advertise them to discovery for the platform RAG reader."""
    return list(_semantic_sources)


# ─── Registry normalisation ───────────────────────────────────────────

class SourceRegistryInvalid(RuntimeError):
    """One or more selected sources failed schema validation at load.

    Raised (SOURCES_STRICT=true, the default) instead of serving a registry we
    only half understand. Carries EVERY problem across EVERY source, not the
    first: an author fixing a registry should see the whole list once, not
    discover it one boot at a time.
    """


def _validate_selected(doc: Dict[str, Any]) -> List[str]:
    """Problems in one source, as author-facing strings. Empty = valid.

    This replaces a hand-written allow-list of known keys. That list worked, but
    it was a second definition of the schema and would drift from
    registry_models.py the first time someone added a field — the exact failure
    this whole exercise removes. The model is now the only definition.
    """
    from pydantic import ValidationError
    from registry_models import RegistrySource, format_error_path

    try:
        RegistrySource.model_validate(doc)
        return []
    except ValidationError as exc:
        return [
            f"{format_error_path(err['loc']) or '<source>'}: {err['msg']}"
            for err in exc.errors()
        ]


def _flatten(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Map a SOURCES_FILE source document into the flat dict legacy code expects."""
    src_id = str(doc["source_id"])
    out: Dict[str, Any] = {
        "source_id": src_id,
        "dept_id": str(doc.get("dept_id") or ""),
        "org_id": str(doc.get("org_id") or ""),
        "type": str(doc.get("type") or "semantic"),
        "name": str(doc.get("name") or src_id),
        "description": str(doc.get("description") or ""),
        "connection_ref": doc.get("connection_ref"),
        "connection": dict(doc.get("connection") or {}),
        "options": dict(doc.get("options") or {}),
        "visibility": dict(doc.get("visibility") or {}),
        "is_active": bool(doc.get("is_active", True)),
        "workflow_id": doc.get("workflow_id"),
    }
    # Mirror legacy keys consumed by RAG engines/registration:
    #   - connection sub-keys (for SQL/Mongo connectors)
    #   - top-level structured_detection / tags / query_timeout_seconds / taxonomy
    #   - datasets / catalogue   (explicit dataset list — used by bigquery,
    #                              sap_rfc, gcs/duckdb sources whose read_via
    #                              cannot be derived from a connection.table)
    #   - write_actions / columns (source-level, for non-tabular sources —
    #                              e.g. a mongodb source declaring write
    #                              actions; catalogue._datasets_for reads these
    #                              off the source for the single-dataset fallback)
    for k in (
        "structured_detection",
        "tags",
        "query_timeout_seconds",
        "taxonomy",
        "datasets",
        "catalogue",
        "rag",
        "write_actions",
        "columns",
        # registration.py reads this to tell discovery the source can back a
        # "Refresh from History" workflow. It was absent from this list, so the
        # flattened doc never carried it and `source.get("supports_history",
        # False)` was unconditionally False — the flag was undeclarable from
        # sources.json no matter what the author wrote.
        "supports_history",
        # The deployment-targeting triple (vertical/sub_vertical/country) —
        # catalogue._build_dataset_schema reads it off the source for every
        # dataset that doesn't override it. Omitting it here would repeat the
        # supports_history failure above: declarable, validated, never served.
        "domain",
        # The customer's display identity (company name/logo/brand seed) —
        # catalogue stamps it onto every dataset entry; publish defaults the
        # app theme from it. THIRD occurrence of the same trap — any new
        # top-level source key MUST be added here or it silently vanishes.
        "organization",
    ):
        if k in doc:
            out[k] = doc[k]
    return out


def _select_sources(
    docs: List[Dict[str, Any]], org_id: str, dept_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Pure: filter raw source docs to THIS deployment (active + org + dept),
    flatten each, return the source_id→doc map. Shared shape with the Mongo
    path (which filters server-side); used to filter the file path in Python."""
    strict = bool(getattr(get_settings(), "sources_strict", True))
    out: Dict[str, Dict[str, Any]] = {}
    invalid: Dict[str, List[str]] = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        if not bool(d.get("is_active", True)):
            continue
        if org_id and str(d.get("org_id") or "") != org_id:
            continue
        if dept_ids and str(d.get("dept_id") or "") not in dept_ids:
            continue
        # Validate only what we actually SERVE. Another dept's malformed source
        # in a shared registry is not ours to fail on.
        problems = _validate_selected(d)
        if problems:
            sid = str(d.get("source_id") or "<no source_id>")
            invalid[sid] = problems
            logger.error(
                "🚫 [SOURCES] source %r is INVALID and will not be served:\n%s",
                sid, "\n".join(f"    - {p}" for p in problems),
            )
            continue  # never serve a source we don't understand
        flat = _flatten(d)
        out[flat["source_id"]] = flat

    if invalid and strict:
        detail = "\n".join(
            f"  {sid}:\n" + "\n".join(f"    - {p}" for p in probs)
            for sid, probs in invalid.items()
        )
        raise SourceRegistryInvalid(
            f"{len(invalid)} source(s) failed schema validation:\n{detail}\n"
            f"Fix the registry, or run:  python validate_sources.py <file>\n"
            f"To boot anyway WITHOUT these sources (emergency only), set "
            f"SOURCES_STRICT=false — they will be skipped, not repaired."
        )
    if invalid:
        logger.error(
            "⚠️ [SOURCES] SOURCES_STRICT=false — booting WITHOUT %d invalid "
            "source(s): %s. They are absent, not fixed; consumers routing to "
            "them will 404.", len(invalid), sorted(invalid),
        )
    return out


def _swap_registry(new_map: Dict[str, Dict[str, Any]], where: str) -> List[Dict[str, Any]]:
    # RAG short-circuit (pure disconnect): the dept-MCP is a STRUCTURED-only
    # agent — semantic (RAG) sources are answered by the Citra-Service platform
    # reader, NEVER here. Drop them at the load choke point (both file + Mongo
    # paths pass through _swap_registry) so a semantic source never enters the
    # registry, /datasets, or the query planner. The platform discovers semantic
    # sources directly from dept_sources; the MCP needs no RAG deps in the query
    # path.
    # Trim + case-fold so " semantic " / "SEMANTIC" are all treated as semantic
    # and cannot slip a RAG source into the structured registry.
    dropped_docs = [d for sid, d in list(new_map.items())
                    if str((d or {}).get("type") or "").strip().lower() == "semantic"]
    dropped = [d["source_id"] for d in dropped_docs]
    for sid in dropped:
        new_map.pop(sid, None)
    # Retain the dropped semantic docs so registration can advertise them to
    # discovery (chat reads them from there and fetches Milvus directly). Atomic
    # replace, mirroring the _sources swap below.
    global _semantic_sources
    _semantic_sources = dropped_docs
    if dropped:
        # WARNING not INFO: a source dropped here is INVISIBLE to /datasets +
        # every query (404 "unknown source"). A structured source that merely
        # OMITTED `type` defaults to "semantic" and lands here — so make the
        # exclusion loud enough that a mis-authored source is noticed.
        logger.warning(
            "[ROUTER] RAG short-circuit: excluded %d source(s) as type=semantic "
            "(answered platform-side by Citra-Service; a STRUCTURED source that "
            "omitted `type` would be wrongly dropped here — declare `type` "
            "explicitly): %s", len(dropped), dropped)
    # Atomic swap so a polling reload never leaves the registry partially built.
    _sources.clear()
    _sources.update(new_map)
    logger.info(
        f"✅ [ROUTER] Loaded {len(_sources)} structured source(s) from {where}: "
        f"{list(_sources.keys())}"
    )
    return list(new_map.values())


def load_sources_from_file() -> List[Dict[str, Any]]:
    """Load the source registry — the customer-side MCP path that needs NO Citra
    Mongo. Two input modes, ``SOURCES_FILE`` (a path, read via ``open``) taking
    precedence over ``SOURCES_JSON`` (the same payload inline as an env var, for
    file-free hosts like Cloud Run / Container Apps / Fargate / a k8s ConfigMap
    env). Both accept a JSON list of source docs, or ``{"sources": [...]}``.
    Fails LOUD (raises) if the payload is missing/unreadable/malformed — never
    starts on a silently-empty registry."""
    import json

    cfg = get_settings()
    path = cfg.sources_file
    if path:
        origin = f"file {path!r}"
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            raise RuntimeError(
                f"SOURCES_FILE {path!r} could not be read as JSON: {e}") from e
    else:
        # Inline env-var payload (config validator guarantees one of the two is
        # set, so we only reach here with a non-empty SOURCES_JSON).
        origin = "SOURCES_JSON env"
        try:
            raw = json.loads(cfg.sources_json)
        except ValueError as e:
            raise RuntimeError(
                f"SOURCES_JSON could not be parsed as JSON: {e}") from e
    docs = raw.get("sources") if isinstance(raw, dict) else raw
    if not isinstance(docs, list):
        raise RuntimeError(
            f"{origin} must be a JSON list of sources or "
            f"{{'sources': [...]}} — got {type(docs).__name__}")
    new_map = _select_sources(docs, cfg.org_id, cfg.dept_ids)
    return _swap_registry(new_map, origin)


async def load_sources() -> List[Dict[str, Any]]:
    """Load the source registry from the local ``SOURCES_FILE``. One entry point
    so startup + the refresh loop both use it.

    The legacy central-Mongo ``dept_sources`` load mode was REMOVED 2026-07-10 —
    every MCP is now file-defined (and publishes to the discovery registry on
    boot). ``config`` fails loud at startup if ``SOURCES_FILE`` is unset."""
    return load_sources_from_file()


async def _refresh_loop() -> None:
    cfg = get_settings()
    interval = max(int(cfg.sources_refresh_seconds or 0), 0)
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            sources = await load_sources()
        except Exception as exc:  # noqa: BLE001 — never let polling crash the app
            logger.warning(f"⚠️ [ROUTER] sources refresh failed: {exc}")
            continue
        # Re-publish to discovery, exactly as boot does. The reload used to be
        # LOCAL-ONLY while docs/sources-file.md §13 told authors that setting
        # SOURCES_REFRESH_SECONDS makes the MCP "re-register to discovery" — so
        # retiring a source with is_active:false dropped it here (the MCP 404s
        # it) while discovery kept advertising it active and routable, forever.
        # For a semantic source that is worse: /tools/source-scope still handed
        # back its rag_collection, so /semantic/search kept serving chunks from
        # a retired corpus — defeating the very retirement guard the reader's
        # 403 exists to enforce. Newly ADDED sources were never advertised
        # either. register_all also drives the reconcile sweep, so a rename now
        # deactivates the old tool_id on the same cadence.
        # Lazy import: registration imports config only, so there is no cycle,
        # but keep the dependency at the call site rather than module scope.
        try:
            from registration import register_all
            await register_all(list(sources) + get_semantic_sources())
        except Exception as exc:  # noqa: BLE001 — hygiene, not a serving dependency
            logger.warning(
                "⚠️ [ROUTER] sources refresh re-registration failed (the local "
                "registry IS reloaded; discovery may advertise a stale set until "
                "the next cycle): %s", exc,
            )


def start_refresh_task() -> None:
    global _refresh_task
    cfg = get_settings()
    if (cfg.sources_refresh_seconds or 0) <= 0:
        return
    if _refresh_task and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh_loop(), name="dept-sources-refresh")
    logger.info(
        f"🔁 [ROUTER] sources auto-refresh enabled "
        f"(every {cfg.sources_refresh_seconds}s)"
    )


def stop_refresh_task() -> None:
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
    _refresh_task = None


# ─── Public registry accessors ────────────────────────────────────────


def get_source(source_id: str) -> Optional[Dict[str, Any]]:
    return _sources.get(source_id)


def list_source_ids() -> List[str]:
    return list(_sources.keys())


def all_sources() -> List[Dict[str, Any]]:
    return list(_sources.values())


# ─── Dispatch ─────────────────────────────────────────────────────────


async def dispatch(req: QueryRequest) -> QueryResponse:
    """Deprecated thin shim — forwards to the catalogue-keyed orchestrator.

    Kept so any external module that still imports ``router.dispatch``
    keeps working. New code should import ``query_planner.plan_and_execute``
    directly. The per-source-type ``source.type`` switch (structured /
    semantic / mongodb / rest_api) has been replaced by per-dataset
    ``DatasetKind`` resolution from the catalogue.
    """
    from query_planner import plan_and_execute  # late import — avoids cycle

    source = _sources.get(req.source_id)
    if not source:
        logger.warning(f"⚠️ [ROUTER] Unknown source_id: {req.source_id!r}")
        return QueryResponse(
            results=[], source_id=req.source_id,
            source_type=SourceType.semantic, total=0,
        )
    source_type = SourceType(source.get("type", "semantic"))
    with observe_query(req.source_id, source_type.value):
        return await plan_and_execute(req)


async def dispatch_all(query: str, max_results_per_source: int = 5) -> List[ChunkResult]:
    """Fan-out: query ALL registered sources and merge results."""
    async def _query_one(source_id: str) -> List[ChunkResult]:
        try:
            req = QueryRequest(query=query, source_id=source_id, max_results=max_results_per_source)
            resp = await dispatch(req)
            return resp.results
        except Exception as exc:
            logger.warning(f"⚠️ [ROUTER] Fan-out failed for {source_id}: {exc}")
            return []

    tasks = [_query_one(sid) for sid in _sources]
    results_nested = await asyncio.gather(*tasks)
    merged = [chunk for sublist in results_nested for chunk in sublist]
    merged.sort(key=lambda c: c.score, reverse=True)
    return merged
