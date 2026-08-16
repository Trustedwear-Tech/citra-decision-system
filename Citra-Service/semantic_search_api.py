# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Platform semantic-search endpoint (RAG short-circuit #6).

The ONE platform-side RAG surface — the MCP does no RAG anymore. Consumers
(chat, smart-app runtime, builder) POST here for any `kind == semantic` source;
structured kinds still go to the MCP. Thin wrapper over semantic_reader (which
generalizes Citra-Service's existing vault RAG to a per-source dept collection).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel, Field

from citra_auth import get_secure_user_id
from semantic_reader import can_query_semantic, fetch_document, semantic_search

logger = logging.getLogger(__name__)
router = APIRouter()


class SemanticSearchRequest(BaseModel):
    source_id: str
    # A CONSUMER HINT only — the server resolves the authoritative dept from the
    # dept_sources registry (a consumer must not be able to widen its own access
    # by lying about a source's dept). Kept so a caller that already knows it can
    # skip a registry miss, but the resolved value wins when present.
    dept_id: Optional[str] = None
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    filters: Optional[Dict[str, Any]] = None
    # When set, return EVERY chunk of this ONE document (by doc_path), ordered —
    # a metadata fetch, not a vector search. For 'read/summarize the whole
    # document' intents that top-k search can't reassemble. ``query`` is ignored.
    doc_path: Optional[str] = None
    # The authoritative Milvus collection the source's MCP published to discovery
    # (`rag_collection`). When the caller knows it, we read it DIRECTLY instead of
    # deriving from a prefix convention — closes the "wrong prefix → silent miss"
    # gap. None ⇒ the reader derives <prefix>_<dept>_<source>.
    rag_collection: Optional[str] = None


def _roles(request: Request) -> List[str]:
    return list(getattr(request.state, "roles", []) or [])


def _dept_ids(request: Request) -> List[str]:
    return list(getattr(request.state, "dept_ids", []) or [])


def _bearer(request: Request) -> Optional[str]:
    """The caller's raw JWT (for forwarding to discovery)."""
    auth = request.headers.get("authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


async def resolve_source_scoping(
    source_id: str, jwt_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """AUTHORITATIVE dept scoping for a semantic source, read from the DISCOVERY
    registry — the MCP publishes every source there at boot (``source_type`` +
    ``org_ids`` + ``dept_ids`` + ``visibility.public_within_org`` +
    ``rag_collection``), and platform-native dept SOP libraries register there
    directly. The retired central ``dept_sources`` collection is NO LONGER read.

    Returns ``{dept_id, org_id, public_within_org, rag_collection}`` or ``None``
    when the source isn't actively registered. The endpoint enforces authz on
    THIS, not on a consumer-supplied ``dept_id`` — a consumer must not be able to
    escalate by lying about scope. Fail-CLOSED: a discovery outage returns
    ``None`` (⇒ 403), never a blind grant (see ``get_source_scope``)."""
    from services.enterprise_mcp_client import get_source_scope
    scope = await get_source_scope(source_id, jwt_token=jwt_token)
    if not scope:
        return None
    return {"dept_id": scope.get("dept_id"),
            "org_id": scope.get("org_id"),
            "rag_collection": scope.get("rag_collection"),
            "public_within_org": bool(scope.get("public_within_org")),
            # The source's declared read allow-list — server truth, like the rest
            # of this dict. Without it the dept gate below was the ONLY check, so
            # any member of the owning dept could read an admin-restricted corpus.
            "roles_allowed": list(scope.get("roles_allowed") or [])}


@router.post("/semantic/search")
async def semantic_search_endpoint(
    request: Request, body: SemanticSearchRequest = Body(...),
):
    """Search a semantic source's dept collection → ranked chunks. Dept‑scoped:
    the caller must belong to the source's dept (or be org/super admin), unless
    the source is org‑wide (``public_within_org``). Scope is resolved SERVER‑side
    from ``dept_sources`` — the request's ``dept_id`` is only a fallback hint."""
    get_secure_user_id(request)  # 401 if unauthenticated
    scoping = await resolve_source_scoping(body.source_id, jwt_token=_bearer(request))
    # A source that isn't ACTIVELY registered — never registered OR retired
    # (is_active=false) — has no authority to serve, so deny for EVERYONE. This
    # closes the retirement gap (a retired source's Milvus collection may still
    # hold chunks) and refuses an unknown source_id rather than falling through
    # to a consumer-supplied dept hint (which a `None` hint would have waved past).
    if not scoping:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="unknown or retired semantic source")
    dept_id = scoping["dept_id"]                       # server truth, authoritative
    org_id = scoping.get("org_id")                     # bounds the Milvus read
    # Prefer the caller-supplied collection; else the source's registered one
    # (shared dept-library collection). None ⇒ the reader derives it.
    rag_collection = body.rag_collection or scoping.get("rag_collection")
    public = bool(scoping["public_within_org"])

    # A trusted platform-minted SERVICE identity (agent/trigger run with no
    # end-user — carries `semantic_service: True`, signature-verified by the auth
    # middleware, so it can't be forged without JWT_SECRET) is authorized to read
    # a registered source; the DATA is bounded to the source's OWN dept (above).
    # A regular end-user must pass the dept gate.
    user_claims = getattr(request.state, "user", None) or {}
    is_service = bool(user_claims.get("semantic_service"))
    if not is_service and not can_query_semantic(
        roles=_roles(request), user_dept_ids=_dept_ids(request),
        dept_id=dept_id, public_within_org=public,
        roles_allowed=scoping.get("roles_allowed"),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="not permitted to read that source")
    if body.doc_path:
        # Whole-document read: every chunk of ONE doc, ordered (query + top_k
        # ignored — a whole-doc read must not be clipped to a display top_k).
        chunks = await fetch_document(
            source_id=body.source_id, dept_id=dept_id, org_id=org_id,
            doc_path=body.doc_path, collection=rag_collection or None,
        )
    else:
        chunks = await semantic_search(
            source_id=body.source_id, dept_id=dept_id, org_id=org_id,
            query=body.query, top_k=body.top_k, filters=body.filters,
            collection=rag_collection or None,
        )
    return {"source_id": body.source_id, "dept_id": dept_id,
            "count": len(chunks), "chunks": chunks}
