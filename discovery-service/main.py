# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Discovery Service — Main FastAPI Application
=============================================

Endpoints
---------
POST   /tools/register              Dept MCP registers itself on startup
PUT    /tools/{tool_id}/heartbeat   Keep-alive every 60 s
DELETE /tools/{tool_id}             Deregister on shutdown
GET    /tools/available             Citra enterprise_search.py queries this
GET    /tools/{tool_id}/health      Proxy to tool's /health endpoint
GET    /health                      Discovery service liveness
GET    /admin/tools                 Registry view for the admin UI (any admin;
                                    scoped to what the caller may see)

Auth
----
- POST/PUT/DELETE use X-API-Key header (the api_key set at registration)
- GET /tools/available uses Authorization: Bearer {Citra JWT}
- GET /admin/tools uses Authorization: Bearer {Citra JWT} + an admin role.
  super_admin sees every org; org_admin/dept_admin see only their own tools.
"""

import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Vault bootstrap MUST run BEFORE `from config import ...` because Settings
# reads env at class-def time. Local .env wins (overwrite=False).
if os.getenv("VAULT_ADDR"):
    from citra_service_utils import load_from_vault
    load_from_vault()

import httpx
import jose.jwt as jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from config import Settings, get_settings
from embedder import embed as embed_query, embed_tool
from models import (
    DeregisterRequest,
    HeartbeatRequest,
    HealthResponse,
    RankedToolDefinition,
    ReconcileRequest,
    SuccessResponse,
    ToolDefinition,
    ToolDocument,
    ToolRegistrationRequest,
    ToolVisibility,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    import os as _os
    _sentry_dsn = _os.getenv("SENTRY_DSN", "").strip()
    if _sentry_dsn:
        sentry_sdk.init(
            dsn=_sentry_dsn,
            environment=_os.getenv("ENVIRONMENT", "prod"),
            release=_os.getenv("GIT_SHA", "unknown"),
            traces_sample_rate=float(_os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
            send_default_pii=False,
            attach_stacktrace=True,
            max_breadcrumbs=50,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
                AsyncioIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
        )
        sentry_sdk.set_tag("service", "discovery-service")
except Exception as _sentry_exc:
    logger.warning("Sentry init skipped: %s", _sentry_exc)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_mongo_client: Optional[AsyncIOMotorClient] = None
_tools_col: Optional[AsyncIOMotorCollection] = None


def get_tools_col() -> AsyncIOMotorCollection:
    if _tools_col is None:
        raise RuntimeError("Database not initialised")
    return _tools_col


# ---------------------------------------------------------------------------
# Lifespan — connect to MongoDB and ensure indexes
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _tools_col
    settings = get_settings()

    _mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    db = _mongo_client[settings.mongo_db]
    _tools_col = db[settings.tools_collection]

    # Indexes
    # NOTE: a compound index on TWO array fields is rejected by MongoDB
    # with "cannot index parallel arrays". Split into single-field
    # indexes; visibility filters that need both fields hit one index
    # then filter in-memory (acceptable — the tool catalog is small).
    await _tools_col.create_index("tool_id", unique=True)
    await _tools_col.create_index("org_ids")
    await _tools_col.create_index("dept_ids")
    await _tools_col.create_index("active")
    await _tools_col.create_index("last_heartbeat")

    logger.info("✅ [DISCOVERY] Connected to MongoDB and indexes ensured")
    yield

    if _mongo_client:
        _mongo_client.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Citra Discovery Service",
    description=(
        "Central registry for department MCP servers. "
        "Each dept MCP registers here on startup; Citra queries this service "
        "to discover which tools are available for a given user's org/dept/roles."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Distributed tracing — OTel spans → Tempo
try:
    from citra_service_utils import setup_tracing as _setup_tracing, request_id_middleware as _request_id_middleware
    app.middleware("http")(_request_id_middleware)
    _setup_tracing(app, service_name="discovery-service")
except ImportError:
    logger.warning("citra-service-utils not installed; distributed tracing disabled")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _verify_jwt(token: str, settings: Settings) -> Dict[str, Any]:
    """Decode and return JWT claims. Raises HTTPException on failure.

    ``verify_aud=False``: callers carry a sandbox-specific audience —
    action-chat-service mints scoped tokens with ``aud="citra-action-
    sandbox"`` (smart-app may differ). python-jose rejects any token
    whose ``aud`` it cannot match against an expected ``audience``, so
    a token with *any* ``aud`` claim 401s here unless audience
    verification is disabled. Signature + the per-tool visibility /
    role / scope checks do the real gating. Mirrors
    citra-mcp-service/auth.py, which already does exactly this.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("JWT verification failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization[7:]


async def get_jwt_claims(
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    token = _extract_bearer(authorization)
    return _verify_jwt(token, settings)


# ---------------------------------------------------------------------------
# Tool visibility / filtering logic
# ---------------------------------------------------------------------------

# Service scopes that are trusted to enumerate tools regardless of org —
# server-side fetchers, not end users. ``citra-app-runtime`` resolves a
# published app's bound MCP sources when rendering panel data; it carries
# no org of its own (it renders apps for every tenant). This mirrors the
# trust smart-app-service already places in the same scope for reading
# published apps. Keep this list minimal — only non-interactive,
# server-side service identities belong here.
_TRUSTED_SERVICE_SCOPES = {"citra-app-runtime"}

# Roles admitted to /admin/tools. Membership only opens the door — what each
# role actually SEES is decided per-row by _tool_visible_to. Names must exist in
# the platform role enum (citra-auth/citra_auth/constants.py Roles.ALL); a
# literal that isn't in it can never match a real token and is a dead branch.
_FLEET_ADMIN_ROLES = {"dept_admin", "org_admin", "super_admin"}


def _claim_org_and_depts(claims: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """org_id + dept_ids from JWT claims.

    Supports both ``dept_ids`` (array, current) and ``dept_id`` (string,
    legacy), and tolerates ``dept_ids`` itself arriving as a bare string. Every
    caller that scopes by claims must go through here: the shapes are the same
    rule, and a site that normalises them differently mis-scopes silently
    rather than failing.
    """
    org_id: Optional[str] = claims.get("org_id")
    dept_ids_raw = claims.get("dept_ids") or []
    legacy_dept = claims.get("dept_id")
    if isinstance(dept_ids_raw, str):
        dept_ids_raw = [dept_ids_raw]
    if legacy_dept and legacy_dept not in dept_ids_raw:
        dept_ids_raw = [legacy_dept] + list(dept_ids_raw)
    return org_id, list(dept_ids_raw)


def _tool_visible_to(
    doc: Dict[str, Any],
    org_id: Optional[str],
    dept_ids: List[str],
    roles: List[str],
    scope: Optional[str] = None,
) -> bool:
    """Return True if this tool should be included in the response for this user."""
    vis: Dict[str, Any] = doc.get("visibility", {})
    # The dept_sources registry stores SINGULAR org_id/dept_id; some older tool
    # docs use plural org_ids/dept_ids. Accept both — otherwise org/dept scoping
    # never matches for dept_sources docs and every non-super_admin user gets
    # ZERO sources.
    tool_org_ids: List[str] = doc.get("org_ids") or (
        [doc["org_id"]] if doc.get("org_id") else []
    )
    tool_dept_ids: List[str] = doc.get("dept_ids") or (
        [doc["dept_id"]] if doc.get("dept_id") else []
    )
    roles_allowed: List[str] = vis.get("roles_allowed", ["user"])
    cross_org_ids: List[str] = vis.get("cross_org_ids", [])
    public_within_org: bool = vis.get("public_within_org", False)

    # Rule 0: trusted server-side service scopes enumerate every tool.
    if scope in _TRUSTED_SERVICE_SCOPES:
        return True

    # Rule 1: super_admin sees everything
    if "super_admin" in roles:
        return True

    # Rule 2: org_admin sees all tools within their org
    if "org_admin" in roles and org_id and org_id in tool_org_ids:
        return True

    # Rule 3: user/dept_admin sees own dept tools (role must be allowed)
    if org_id and org_id in tool_org_ids:
        role_ok = any(r in roles_allowed for r in roles)
        if role_ok:
            # public_within_org: anyone in the org can read this, regardless
            # of dept_ids. dept_ids on a public source records ownership /
            # governance ("Central PMU owns the policy library"), it does
            # NOT restrict access. Shared knowledge corpora (policy
            # libraries, BERC tariff orders, SOPs) typically live here.
            if public_within_org:
                return True
            # Dept-scoped: user's dept_ids must overlap with tool's dept_ids
            if tool_dept_ids and any(d in tool_dept_ids for d in dept_ids):
                return True

    # Rule 4: cross-org access explicitly granted
    if org_id and org_id in cross_org_ids:
        return True

    return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health():
    col = get_tools_col()
    count = await col.count_documents({"active": True})
    return HealthResponse(tool_count=count)


# ── Registration ─────────────────────────────────────────────────────────────

@app.post("/tools/register", response_model=SuccessResponse, tags=["registry"])
async def register_tool(
    body: ToolRegistrationRequest,
    x_api_key: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    """
    Called by a dept MCP server on startup.
    If tool_id already exists (re-registration after restart), update in place.
    """
    # Admin API key OR the tool's own api_key is accepted for registration.
    # For first-time registration either is fine.  For updates the tool must
    # supply the same api_key it registered with (so we check hash match if
    # a previous registration exists).
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")

    col = get_tools_col()
    existing = await col.find_one({"tool_id": body.tool_id})

    if existing:
        # Re-registration: verify api_key matches stored hash
        if existing["api_key_hash"] != _hash_key(x_api_key) and x_api_key != settings.admin_api_key:
            raise HTTPException(status_code=403, detail="api_key does not match stored registration")

    now = datetime.now(timezone.utc)

    # Embed the tool's catalogue text once at register time. If the
    # embedder is unreachable we proceed without an embedding — the tool
    # is still registered, just not semantically rankable until a re-embed
    # admin sweep. Re-embed automatically when the catalogue text changes.
    new_text_fingerprint = (
        body.name + "\0" + body.description + "\0" +
        ",".join(sorted(body.tags or [])) + "\0" +
        ",".join(sorted(body.data_types or [])) + "\0" +
        str(body.taxonomy or "")
    )
    embedding: Optional[List[float]] = None
    if existing and existing.get("text_fingerprint") == new_text_fingerprint:
        # Catalogue text unchanged — reuse the prior embedding (if any).
        embedding = existing.get("embedding")
    else:
        embedding = await embed_tool(
            name=body.name,
            description=body.description,
            tags=body.tags,
            data_types=body.data_types,
            taxonomy=body.taxonomy,
        )

    doc = {
        "tool_id": body.tool_id,
        "name": body.name,
        "description": body.description,
        "query_endpoint": body.query_endpoint,
        "health_endpoint": body.health_endpoint,
        "source_id": body.source_id,
        "org_ids": body.org_ids,
        "dept_ids": body.dept_ids,
        "visibility": body.visibility.model_dump(),
        "api_key_hash": _hash_key(body.api_key),
        "tags": body.tags,
        "data_types": body.data_types,
        "source_type": body.source_type,
        "taxonomy": body.taxonomy,
        "query_timeout_seconds": body.query_timeout_seconds,
        "rag_collection": body.rag_collection,
        "supports_history": bool(body.supports_history),
        "registered_at": now if not existing else existing["registered_at"],
        "last_heartbeat": now,
        "active": True,
        # Embedding fields — None when the embedder is unreachable.
        "embedding": embedding,
        "text_fingerprint": new_text_fingerprint,
    }

    await col.update_one(
        {"tool_id": body.tool_id},
        {"$set": doc},
        upsert=True,
    )

    action = "updated" if existing else "registered"
    logger.info(f"✅ [DISCOVERY] Tool {action}: {body.tool_id} ({body.name})")
    return SuccessResponse(message=f"Tool {action} successfully", tool_id=body.tool_id)


# ── Heartbeat ─────────────────────────────────────────────────────────────────

@app.put("/tools/{tool_id}/heartbeat", response_model=SuccessResponse, tags=["registry"])
async def heartbeat(
    tool_id: str,
    body: HeartbeatRequest,
):
    col = get_tools_col()
    existing = await col.find_one({"tool_id": tool_id})
    if not existing:
        raise HTTPException(status_code=404, detail=f"Tool {tool_id!r} not registered")
    if existing["api_key_hash"] != _hash_key(body.api_key):
        raise HTTPException(status_code=403, detail="Invalid api_key")

    await col.update_one(
        {"tool_id": tool_id},
        {"$set": {"last_heartbeat": datetime.now(timezone.utc), "active": True}},
    )
    return SuccessResponse(message="Heartbeat recorded", tool_id=tool_id)


# ── Deregister ───────────────────────────────────────────────────────────────

@app.delete("/tools/{tool_id}", response_model=SuccessResponse, tags=["registry"])
async def deregister_tool(
    tool_id: str,
    body: DeregisterRequest,
    settings: Settings = Depends(get_settings),
):
    col = get_tools_col()
    existing = await col.find_one({"tool_id": tool_id})
    if not existing:
        raise HTTPException(status_code=404, detail=f"Tool {tool_id!r} not found")

    if existing["api_key_hash"] != _hash_key(body.api_key) and body.api_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid api_key")

    # Soft-delete: mark inactive rather than delete so history is preserved
    await col.update_one({"tool_id": tool_id}, {"$set": {"active": False}})
    logger.info(f"🗑️ [DISCOVERY] Tool deregistered: {tool_id}")
    return SuccessResponse(message="Tool deregistered", tool_id=tool_id)


@app.post("/tools/reconcile", tags=["registry"])
async def reconcile_tools(body: ReconcileRequest):
    """Deactivate a registrant's stale tools after a re-registration sweep.

    Ownership = api_key_hash AND the caller's own org+dept scope. Any ACTIVE
    tool in that scope whose tool_id is not in the submitted current set is
    soft-deactivated — so a source renamed/removed from SOURCES_FILE stops
    being advertised on the MCP's next restart instead of lingering forever.
    Soft (active=False), same as deregister; a later re-register reactivates.

    The org+dept half is load-bearing, not defence in depth. Keying ownership on
    api_key_hash ALONE assumed one key per MCP; in reality smart-app-service
    holds a single fleet-wide MCP_SERVICE_API_KEY, so every dept MCP presents
    the same key. Unscoped, each MCP's boot deactivated every OTHER dept's
    tools — main chat lost routing, /tools/source-scope 404'd into a 403
    "unknown or retired semantic source", and the catalogue crawler dropped
    them — until that dept's MCP restarted and killed the first's in turn. No
    heartbeat and no reaper, so it never recovered on its own."""
    col = get_tools_col()
    key_hash = _hash_key(body.api_key)
    current = set(body.active_tool_ids or [])
    # Fail loud rather than fall back to the unscoped sweep: a registrant that
    # can't say what it owns must not be allowed to retire anything.
    if not body.org_id or not body.dept_ids:
        logger.error(
            "🚫 [DISCOVERY] Reconcile REFUSED — caller sent no org_id/dept_ids "
            "(org_id=%r dept_ids=%r). Refusing to deactivate unscoped: an "
            "unscoped sweep would retire other departments' tools.",
            body.org_id, body.dept_ids,
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="reconcile requires org_id + dept_ids (the ownership scope)",
        )
    scope = {
        "api_key_hash": key_hash,
        "active": True,
        "tool_id": {"$nin": list(current)},
        "org_ids": body.org_id,
        "dept_ids": {"$in": body.dept_ids},
    }
    _CAP = 1000
    stale = await col.find(scope, {"tool_id": 1}).to_list(length=_CAP)
    stale_ids = [d["tool_id"] for d in stale]
    # A silent truncation here would leave stale tools advertised forever with
    # a success response — say so instead.
    if len(stale_ids) == _CAP:
        logger.warning(
            "⚠️ [DISCOVERY] Reconcile hit the %d-row cap for org=%s depts=%s — "
            "more stale tools may remain active; re-run to sweep the rest.",
            _CAP, body.org_id, body.dept_ids,
        )
    if stale_ids:
        await col.update_many(
            {"tool_id": {"$in": stale_ids}},
            {"$set": {"active": False, "deactivated_reason": "reconcile: absent from SOURCES_FILE"}},
        )
        logger.info(
            f"🧹 [DISCOVERY] Reconcile deactivated {len(stale_ids)} stale tool(s) "
            f"in org={body.org_id} depts={body.dept_ids}: {stale_ids}"
        )
    return {
        "ok": True, "deactivated": stale_ids, "kept": len(current),
        "capped": len(stale_ids) == _CAP,
    }


# ── Discovery — the endpoint consumed by enterprise_search.py ─────────────────

@app.get("/tools/available", response_model=List[ToolDefinition], tags=["discovery"])
async def get_available_tools(
    claims: Dict[str, Any] = Depends(get_jwt_claims),
    settings: Settings = Depends(get_settings),
):
    """
    Returns the role-filtered list of active tool definitions for the requesting
    user.  Called by Citra-Service's enterprise_search.py on every enterprise
    query (with a 5-minute in-process cache on the caller side).

    Auth: Authorization: Bearer {Citra JWT}
    The JWT claims supply org_id, dept_ids, and roles — no extra query params
    needed.  The discovery service applies visibility rules server-side.
    """
    org_id, dept_ids = _claim_org_and_depts(claims)
    roles: List[str] = claims.get("roles") or ["user"]
    scope: Optional[str] = claims.get("scope")

    # Fetch all active tools (discovery cache is on the caller side)
    col = get_tools_col()
    cursor = col.find({"active": True})

    visible: List[ToolDefinition] = []
    async for doc in cursor:
        if _tool_visible_to(doc, org_id, dept_ids, roles, scope):
            visible.append(
                ToolDefinition(
                    name=doc["name"],
                    description=doc["description"],
                    query_endpoint=doc["query_endpoint"],
                    source_id=doc["source_id"],
                    org_ids=doc.get("org_ids", []),
                    dept_ids=doc.get("dept_ids", []),
                    tags=doc.get("tags", []),
                    data_types=doc.get("data_types", []),
                    source_type=doc.get("source_type"),
                    taxonomy=doc.get("taxonomy"),
                    query_timeout_seconds=doc.get("query_timeout_seconds"),
                    rag_collection=doc.get("rag_collection"),
                    supports_history=bool(doc.get("supports_history", False)),
                    public_within_org=bool((doc.get("visibility") or {}).get("public_within_org", False)),
                )
            )

    logger.info(
        f"🔍 [DISCOVERY] /tools/available → {len(visible)} tools "
        f"(org={org_id}, dept_ids={dept_ids}, roles={roles})"
    )
    return visible


@app.get("/tools/source-scope/{source_id}", tags=["discovery"])
async def get_source_scope(
    source_id: str,
    claims: Dict[str, Any] = Depends(get_jwt_claims),
    settings: Settings = Depends(get_settings),
):
    """Server-authoritative scope for ONE source, by ``source_id`` — the platform
    reader's replacement for the retired central ``dept_sources`` registry.

    Deliberately NOT visibility-filtered: it returns the source's DECLARED scope
    (org / dept / public_within_org / rag_collection) so a caller such as
    Citra-Service's ``/semantic/search`` can apply its OWN dept gate
    (``can_query_semantic``) and, for a trusted service identity, read on behalf
    of an agent/trigger with no end-user. The scope metadata itself is not
    sensitive; the access decision stays with the caller. Requires a valid Citra
    JWT. 404 when the source isn't actively registered (⇒ caller denies)."""
    col = get_tools_col()
    # Prefer the FRESHEST active registration: if a stale duplicate lingers for
    # the same source_id (e.g. an old MCP boot that advertised a since-changed
    # rag_collection), the most-recently-registered/heartbeated one is truth.
    doc = await col.find_one(
        {"source_id": source_id, "active": True},
        sort=[("last_heartbeat", -1), ("registered_at", -1)],
    )
    if not doc:
        raise HTTPException(status_code=404, detail=f"source {source_id!r} not registered")
    org_ids = doc.get("org_ids") or []
    dept_ids = doc.get("dept_ids") or []
    vis = doc.get("visibility") or {}
    return {
        "source_id": source_id,
        "source_type": doc.get("source_type"),
        # SINGULAR forms the platform reader expects (single-tenant deployment).
        "org_id": org_ids[0] if org_ids else None,
        "dept_id": dept_ids[0] if dept_ids else None,
        "public_within_org": bool(vis.get("public_within_org", False)),
        # The source's declared read allow-list. The dept-MCP hard-gates on this
        # for STRUCTURED reads (auth.py:155), but it was omitted here, so the
        # platform's semantic reader had nothing to gate on and any member of the
        # owning dept could read a corpus authored roles_allowed:
        # ["dept_admin","org_admin"] — /tools/available hid it from their
        # routing, but /semantic/search takes source_id straight from the body.
        # Empty ⇒ no role restriction (the sources.json default is ["user"]).
        "roles_allowed": list(vis.get("roles_allowed") or []),
        "rag_collection": doc.get("rag_collection"),
    }


# ── Semantic search — ranked discovery ───────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    """Plain-Python cosine. Inputs assumed to be non-zero same-length
    float lists; returns 0 on any anomaly."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


def _substring_score(query: str, doc: Dict[str, Any]) -> float:
    """Fallback scoring when a tool has no embedding (embedder was
    unreachable at register time). Cheap substring match over name,
    description, and tags. Returns a value in [0, 1] but never beats
    a real cosine hit — semantic results always rank above fallback
    results because the search loop scores embedded tools first."""
    q = (query or "").lower().strip()
    if not q:
        return 0.0
    haystack_parts = [
        (doc.get("name") or "").lower(),
        (doc.get("description") or "").lower(),
        " ".join(str(t).lower() for t in (doc.get("tags") or [])),
    ]
    hay = " ".join(haystack_parts)
    if not hay:
        return 0.0
    # Score is fraction of query tokens present, halved so it sits below
    # genuine semantic hits (which typically score > 0.5).
    tokens = [t for t in q.split() if t]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in hay)
    return 0.5 * (hits / len(tokens))


@app.get("/tools/search", response_model=List[RankedToolDefinition], tags=["discovery"])
async def search_tools(
    q: str = Query(..., description="Natural-language query — the question the agent is trying to answer."),
    top_k: Optional[int] = Query(default=None, ge=1, le=50),
    tag: Optional[str] = Query(default=None, description="Optional case-insensitive tag pre-filter."),
    data_type: Optional[str] = Query(default=None, description="Optional data_type pre-filter."),
    claims: Dict[str, Any] = Depends(get_jwt_claims),
    settings: Settings = Depends(get_settings),
):
    """Ranked discovery: claim-filter, then cosine-rank against the
    caller's query embedding, return top_k matches.

    Falls back to substring scoring for tools registered before
    embedding was wired (``embedding=None``). Falls back to
    list-by-recency when the query embedder itself is unreachable.
    """
    org_id, dept_ids = _claim_org_and_depts(claims)
    roles: List[str] = claims.get("roles") or ["user"]
    k = top_k or settings.search_default_top_k

    # 1) Embed the query (best-effort).
    q_vec = await embed_query(q)

    # 2) Claim-filter all active tools, optionally narrow by tag/data_type.
    col = get_tools_col()
    tag_lc = (tag or "").strip().lower() or None
    dtype_lc = (data_type or "").strip().lower() or None

    scored: list[tuple[float, Dict[str, Any]]] = []
    async for doc in col.find({"active": True}):
        if not _tool_visible_to(doc, org_id, dept_ids, roles):
            continue
        if tag_lc:
            tags_lc = [str(t).lower() for t in (doc.get("tags") or [])]
            if not any(tag_lc in t for t in tags_lc):
                continue
        if dtype_lc:
            dtypes_lc = [str(t).lower() for t in (doc.get("data_types") or [])]
            if dtype_lc not in dtypes_lc:
                continue

        emb = doc.get("embedding")
        if q_vec and isinstance(emb, list) and emb:
            score = max(0.0, _cosine(q_vec, emb))
        else:
            score = _substring_score(q, doc)
        scored.append((score, doc))

    # 3) Sort by score desc; truncate to top_k.
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]

    out: List[RankedToolDefinition] = [
        RankedToolDefinition(
            name=doc["name"],
            description=doc["description"],
            query_endpoint=doc["query_endpoint"],
            source_id=doc["source_id"],
            org_ids=doc.get("org_ids", []),
            dept_ids=doc.get("dept_ids", []),
            tags=doc.get("tags", []),
            data_types=doc.get("data_types", []),
            source_type=doc.get("source_type"),
            taxonomy=doc.get("taxonomy"),
            query_timeout_seconds=doc.get("query_timeout_seconds"),
            rag_collection=doc.get("rag_collection"),
            relevance_score=round(score, 4),
        )
        for score, doc in top
    ]

    logger.info(
        "🔎 [DISCOVERY] /tools/search q=%r matched=%d returned=%d (q_vec=%s)",
        q[:80], len(scored), len(out), "yes" if q_vec else "no",
    )
    return out


# ── Admin re-embed sweep ─────────────────────────────────────────────────────

@app.post("/admin/tools/reembed", tags=["admin"])
async def admin_reembed_tools(
    claims: Dict[str, Any] = Depends(get_jwt_claims),
):
    """Re-embed every active tool. Use after rotating EMBED_MODEL or
    after backfilling tools that were registered while the embedder
    was unreachable. super_admin only."""
    roles: List[str] = claims.get("roles") or []
    if "super_admin" not in roles:
        raise HTTPException(status_code=403, detail="super_admin role required")

    col = get_tools_col()
    updated = 0
    skipped = 0
    async for doc in col.find({"active": True}):
        vec = await embed_tool(
            name=doc.get("name") or "",
            description=doc.get("description") or "",
            tags=doc.get("tags") or [],
            data_types=doc.get("data_types") or [],
            taxonomy=doc.get("taxonomy"),
        )
        if vec is None:
            skipped += 1
            continue
        await col.update_one(
            {"tool_id": doc["tool_id"]},
            {"$set": {"embedding": vec}},
        )
        updated += 1
    return {"updated": updated, "skipped": skipped}


# ── Health proxy ──────────────────────────────────────────────────────────────

@app.get("/tools/{tool_id}/health", tags=["discovery"])
async def proxy_tool_health(
    tool_id: str,
    claims: Dict[str, Any] = Depends(get_jwt_claims),
    settings: Settings = Depends(get_settings),
):
    """Proxy a health check to the tool's health_endpoint."""
    col = get_tools_col()
    doc = await col.find_one({"tool_id": tool_id, "active": True})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Tool {tool_id!r} not found or inactive")

    health_url = doc.get("health_endpoint")
    if not health_url:
        return {"tool_id": tool_id, "status": "unknown", "reason": "no health_endpoint registered"}

    try:
        async with httpx.AsyncClient(timeout=settings.health_proxy_timeout_seconds) as client:
            resp = await client.get(health_url)
            return {"tool_id": tool_id, "status": resp.status_code, "body": resp.json()}
    except Exception as exc:
        return {"tool_id": tool_id, "status": "unreachable", "error": str(exc)}


# ── Admin view ────────────────────────────────────────────────────────────────

@app.get("/admin/tools", tags=["admin"])
async def admin_list_tools(
    active_only: bool = Query(default=True),
    claims: Dict[str, Any] = Depends(get_jwt_claims),
):
    """Registry view backing the admin UI's MCP Fleet panel.

    Every admin role may call this; ``_tool_visible_to`` decides the ROWS, so an
    org_admin sees their own fleet and never another tenant's, and super_admin
    passes its Rule 1 to keep the full cross-org registry.

    Do NOT re-tighten this to super_admin-only: the citra-workflow
    ``/api/dept-sources/_discovery/tools`` proxy that calls it admits
    org_admin/dept_admin, so those callers would 403 and the panel would render
    "0 MCP instances registered" against a full registry.
    """
    roles: List[str] = claims.get("roles") or []
    if not any(r in _FLEET_ADMIN_ROLES for r in roles):
        raise HTTPException(
            status_code=403,
            detail="dept_admin, org_admin or super_admin role required",
        )

    org_id, dept_ids = _claim_org_and_depts(claims)

    col = get_tools_col()
    query: Dict[str, Any] = {}
    if active_only:
        query["active"] = True

    docs = []
    async for doc in col.find(query, {"api_key_hash": 0, "_id": 0}):
        # NB: no `scope` argument — a trusted service scope must not widen a
        # human's fleet view (Rule 0 would return every tool). This path is
        # interactive-only; server-side fetchers use /tools/available.
        if not _tool_visible_to(doc, org_id, dept_ids, roles):
            continue
        # Convert datetime to ISO string for JSON serialisation
        for field in ("registered_at", "last_heartbeat"):
            if isinstance(doc.get(field), datetime):
                doc[field] = doc[field].isoformat()
        docs.append(doc)

    logger.info(
        "🔍 [DISCOVERY] /admin/tools → %d tools (org=%s, dept_ids=%s, roles=%s)",
        len(docs), org_id, dept_ids, roles,
    )
    return {"total": len(docs), "tools": docs}


if __name__ == "__main__":
    import uvicorn
    from config import get_settings
    cfg = get_settings()
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port, reload=True)
