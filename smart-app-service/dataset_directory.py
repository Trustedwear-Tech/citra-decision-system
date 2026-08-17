# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Publish-time hydrator for ``AppSpec.dataset_directory``.

Pulls from BOTH upstream registries:

  * **discovery-service** ``/tools/available`` — source-level metadata
    (source name, taxonomy_summary, source_type, query_timeout). One
    call covers every source the AppSpec references.

  * **data-discovery-service** ``/catalogue/{dataset_id}`` — dataset-level
    metadata (dataset name + description, kind, columns + pii flags,
    has_pii, row_count_approx, write_actions, mcp_base_url). One call
    per unique ``(source_id, dataset_id)``, cached for 60s by
    ``catalogue_client``.

Both calls are fail-soft. If discovery-service is unreachable the
directory is empty (runtime falls back to ``resolve_source`` per call).
If data-discovery-service is unreachable, the entry is still created
with source-level fields populated but dataset-level fields blank —
the runtime falls back to per-call ``fetch_catalogue_entry`` exactly
like it did before this work.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from catalogue_client import fetch_catalogue_entry
from config import Settings
from discovery_cache import DiscoveryError
from models import (
    Action,
    AgentSpec,
    AppSpec,
    DataBindingRead,
    DataBindingWrite,
    DatasetDirectoryColumn,
    DatasetDirectoryEntry,
    DatasetDirectoryWriteAction,
)

logger = logging.getLogger(__name__)


def _collect_bindings(
    actions: Iterable[Action],
) -> Tuple[
    Dict[str, List[DataBindingRead]],
    Dict[str, List[DataBindingWrite]],
]:
    """Group an AppSpec's bindings by source_id for batch resolution."""
    reads_by_source: Dict[str, List[DataBindingRead]] = {}
    writes_by_source: Dict[str, List[DataBindingWrite]] = {}
    for action in actions or []:
        if not action.data_bindings:
            continue
        for r in action.data_bindings.reads or []:
            reads_by_source.setdefault(r.source_id, []).append(r)
        for w in action.data_bindings.writes or []:
            writes_by_source.setdefault(w.source_id, []).append(w)
    return reads_by_source, writes_by_source


def _summarise_taxonomy(taxonomy: Optional[Dict[str, Any]]) -> Optional[str]:
    """Compact one-liner: 'invoices, receipts, statements' from doc_types."""
    if not taxonomy or not isinstance(taxonomy, dict):
        return None
    doc_types = taxonomy.get("doc_types") or []
    if not isinstance(doc_types, list):
        return None
    labels: List[str] = []
    for dt in doc_types:
        if not isinstance(dt, dict):
            continue
        label = (dt.get("label") or dt.get("id") or "").strip()
        if label:
            labels.append(label)
    if not labels:
        return None
    summary = ", ".join(labels[:8])
    if len(doc_types) > 8:
        summary += f", … (+{len(doc_types) - 8} more)"
    return summary


async def _fetch_visible_tools(
    *, discovery_url: str, user_jwt: str
) -> List[Dict[str, Any]]:
    """One-shot fetch of every claim-visible tool for the publisher.

    Hits ``/tools/available`` (unranked, full list) rather than
    ``/tools/search``: the publisher needs every ``source_id`` the
    AppSpec references, not a query-ranked subset.
    """
    if not discovery_url or not user_jwt:
        return []
    url = discovery_url.rstrip("/") + "/tools/available"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                url,
                headers={"Authorization": f"Bearer {user_jwt}"},
            )
        r.raise_for_status()
        data = r.json() or []
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(
            "[dataset_directory] discovery unreachable at publish "
            "(falling back to empty directory): %s",
            e,
        )
        return []
    return data if isinstance(data, list) else []


def _project_columns(
    columns: List[Dict[str, Any]],
    *,
    max_cols: int = 50,
) -> List[DatasetDirectoryColumn]:
    """Project catalogue columns down to the shape the runtime needs.

    Caps at ``max_cols`` so very wide tables don't blow the AppSpec
    document size. The runtime can still fall back to
    ``fetch_catalogue_entry`` for the full column list when the agent
    asks about a column not in the projection.
    """
    out: List[DatasetDirectoryColumn] = []
    for col in (columns or [])[:max_cols]:
        if not isinstance(col, dict):
            continue
        name = (col.get("name") or "").strip()
        if not name:
            continue
        out.append(DatasetDirectoryColumn(
            name=name,
            type=str(col.get("type") or "unknown"),
            semantic_type=col.get("semantic_type"),
            pii=bool(col.get("pii")),
            description=(col.get("description") or None),
        ))
    return out


def _project_write_actions(
    actions: List[Dict[str, Any]],
) -> List[DatasetDirectoryWriteAction]:
    out: List[DatasetDirectoryWriteAction] = []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        aid = (a.get("id") or "").strip()
        verb = (a.get("verb") or "").strip()
        if not aid or not verb:
            continue
        out.append(DatasetDirectoryWriteAction(
            id=aid,
            verb=verb,
            description=(a.get("description") or None),
        ))
    return out


async def hydrate_dataset_directory(
    app_spec: AppSpec,
    *,
    agent_spec: Optional[AgentSpec],
    discovery_url: str,
    user_jwt: str,
    settings: Settings,
    auth_header: Optional[str],
    tenant_id: str,
) -> List[DatasetDirectoryEntry]:
    """Resolve every (source_id, dataset_id) the app references against
    discovery-service AND data-discovery-service, return hydrated entries.

    Idempotent: callers should overwrite ``app_spec.dataset_directory``
    with the return value on every republish.
    """
    if agent_spec is None or not agent_spec.actions:
        return []

    reads_by_source, writes_by_source = _collect_bindings(agent_spec.actions)
    referenced_sources = set(reads_by_source) | set(writes_by_source)
    if not referenced_sources:
        return []

    # Step 1 — one-shot fetch of source-level metadata.
    tools = await _fetch_visible_tools(
        discovery_url=discovery_url, user_jwt=user_jwt
    )
    tool_by_source: Dict[str, Dict[str, Any]] = {}
    for t in tools:
        sid = t.get("source_id")
        if isinstance(sid, str):
            tool_by_source[sid] = t

    directory: List[DatasetDirectoryEntry] = []
    seen: set[Tuple[str, str]] = set()
    catalogue_resolved = 0
    catalogue_misses = 0

    async def _build_entry(
        source_id: str,
        dataset_id: str,
        access: str,
    ) -> Optional[DatasetDirectoryEntry]:
        if (source_id, dataset_id) in seen:
            return None
        tool = tool_by_source.get(source_id)
        if tool is None:
            logger.info(
                "[dataset_directory] source_id=%s not visible to publisher; "
                "omitting from directory",
                source_id,
            )
            return None
        # We require the source itself to expose SOMETHING reachable — if
        # discovery has no query_endpoint for it the source is mis-registered
        # and the directory entry would point nowhere. The URL itself is
        # not persisted on the directory (we use catalogue.mcp_base_url at
        # runtime); this is just a sanity gate.
        # EXCEPTION: semantic (RAG) sources register an EMPTY query_endpoint BY
        # DESIGN — they are answered by the platform reader, never the dept-MCP
        # (same convention discovery_cache special-cases). Dropping them here
        # silently kept every RAG dataset out of the directory.
        _stype = (tool.get("source_type") or "").strip().lower()
        if not (tool.get("query_endpoint") or "").strip() and _stype != "semantic":
            return None

        # Step 2 — per-dataset fetch from data-discovery-service. Cached
        # for 60s by catalogue_client so publishes touching many bindings
        # of the same dataset only call once.
        nonlocal catalogue_resolved, catalogue_misses
        catalogue_entry: Optional[Dict[str, Any]] = None
        try:
            catalogue_entry = await fetch_catalogue_entry(
                settings=settings,
                auth_header=auth_header,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                source_id=source_id,
            )
        except DiscoveryError as exc:
            # Catalogue unavailable at hydration time — fall back to a
            # source-level-only directory entry, but loudly. The runtime
            # per-call fetch path will itself fail loud later if discovery
            # is still down then. Unexpected (non-DiscoveryError) failures
            # are intentionally NOT swallowed here — let them propagate.
            logger.error(
                "[dataset_directory] catalogue unavailable for "
                "(%s, %s): %s — entry will have source-level fields only",
                source_id, dataset_id, exc,
            )
            catalogue_entry = None

        if catalogue_entry is None:
            catalogue_misses += 1
            dataset_name = None
            dataset_description = None
            kind = None
            columns: List[DatasetDirectoryColumn] = []
            has_pii = False
            row_count = None
            write_actions: List[DatasetDirectoryWriteAction] = []
            mcp_base_url = None
        else:
            catalogue_resolved += 1
            dataset_name = catalogue_entry.get("name")
            dataset_description = catalogue_entry.get("description")
            kind = catalogue_entry.get("kind")
            columns = _project_columns(catalogue_entry.get("columns") or [])
            has_pii = bool(catalogue_entry.get("has_pii"))
            row_count = catalogue_entry.get("row_count_approx")
            write_actions = _project_write_actions(
                catalogue_entry.get("write_actions") or []
            )
            mcp_base_url = catalogue_entry.get("mcp_base_url")

        seen.add((source_id, dataset_id))
        return DatasetDirectoryEntry(
            dataset_id=dataset_id,
            source_id=source_id,
            source_name=tool.get("name") or source_id,
            source_description=tool.get("description"),
            source_type=tool.get("source_type"),
            tags=list(tool.get("tags") or []),
            data_types=list(tool.get("data_types") or []),
            taxonomy_summary=_summarise_taxonomy(tool.get("taxonomy")),
            query_timeout_seconds=tool.get("query_timeout_seconds"),
            dataset_name=dataset_name,
            dataset_description=dataset_description,
            kind=kind,
            columns=columns,
            has_pii=has_pii,
            row_count_approx=row_count,
            write_actions=write_actions,
            mcp_base_url=mcp_base_url,
            access=access,  # type: ignore[arg-type]
        )

    # Reads first, then writes; if a dataset is both, promote to read_write.
    for source_id, reads in reads_by_source.items():
        for r in reads:
            entry = await _build_entry(source_id, r.dataset_id, "read")
            if entry is not None:
                directory.append(entry)

    for source_id, writes in writes_by_source.items():
        for w in writes:
            existing = next(
                (
                    d for d in directory
                    if d.source_id == source_id and d.dataset_id == w.dataset_id
                ),
                None,
            )
            if existing is not None:
                idx = directory.index(existing)
                directory[idx] = existing.model_copy(
                    update={"access": "read_write"}
                )
                continue
            entry = await _build_entry(source_id, w.dataset_id, "write")
            if entry is not None:
                directory.append(entry)

    logger.info(
        "[dataset_directory] hydrated slug=%s entries=%d "
        "referenced_sources=%d resolved_sources=%d "
        "catalogue_resolved=%d catalogue_misses=%d",
        app_spec.slug, len(directory), len(referenced_sources),
        len([s for s in referenced_sources if s in tool_by_source]),
        catalogue_resolved, catalogue_misses,
    )
    return directory
