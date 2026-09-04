# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
data-discovery-service — FastAPI app
======================================
Endpoints
---------
GET  /health
GET  /catalogue                        — list catalogue entries (tenant-scoped)
GET  /catalogue/{dataset_id}           — single catalogue entry
POST /crawl/run                        — trigger a one-shot crawl (manual)
POST /crawl/dataset                    — re-crawl a single dataset on demand

Catalogue refresh cadence
-------------------------
When CRAWL_ENABLED=true the catalogue is rebuilt ONCE, on service startup —
NOT on a nightly timer. Source schemas change rarely and only through IT change
management, so the deliberate act after a schema change lands is to restart
this service (or call POST /crawl/run for an on-demand rebuild). There is no
recurring background loop.

Auth
----
All non-health endpoints require Bearer JWT (Citra user-service). The crawler
forwards the user's JWT to dept-mcp shims so visibility rules apply per call;
the catalogue we build is therefore tenant-scoped by construction.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

# Vault bootstrap MUST run BEFORE `from config import ...` because Settings
# reads env at class-def time. Uses inline urllib loader (own-folder context).
if os.getenv("VAULT_ADDR"):
    from vault_bootstrap import load_from_vault
    load_from_vault()

import socket
import uuid
from datetime import timedelta, timezone

import jwt as pyjwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from config import Settings, get_settings
from crawler import TenantMismatchError, crawl_all, crawl_mcp

# Stable-per-process identity for the crawl leader-lock.
_INSTANCE_ID = os.getenv("HOSTNAME") or socket.gethostname() or uuid.uuid4().hex


async def _acquire_crawl_leadership(db, ttl_seconds: int) -> bool:
    """Mongo leader-lock so only ONE replica runs the background crawl per pass.

    Without it, N replicas all crawl the same tenant concurrently — racing the
    `data_catalogue` upserts and re-embedding every dataset N times (a Milvus
    upsert/embedding herd). The lock doc carries an ``expires_at``; the current
    holder renews its own lease each pass, and a dead leader's lock lapses so a
    survivor takes over. Canonical upsert pattern: filter matches an expired lock
    OR our own lock; a DuplicateKey means another replica holds a still-valid
    lease (→ not leader this pass). data-discovery has no Redis, so this rides
    the Mongo it already uses — no new dependency.
    """
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    try:
        doc = await db["crawler_locks"].find_one_and_update(
            {"_id": "catalogue-crawler",
             "$or": [{"expires_at": {"$lte": now}}, {"holder": _INSTANCE_ID}]},
            {"$set": {"holder": _INSTANCE_ID, "expires_at": expires, "acquired_at": now}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        return bool(doc and doc.get("holder") == _INSTANCE_ID)
    except DuplicateKeyError:
        return False  # another replica holds a valid (unexpired) lease


async def _release_crawl_leadership(db) -> None:
    """Drop our lease the moment the pass ends.

    The lease guards ONE pass against a concurrent replica; holding it any longer
    only blocks the next legitimate crawl. Keeping it for the full TTL is what
    made a redeploy silently skip its startup crawl: recreating the container
    changes HOSTNAME, so the new process no longer matches ``holder`` and had to
    wait out the old lease.

    Scoped to ``_INSTANCE_ID`` so we can never release a lease that a surviving
    replica has since taken over.
    """
    try:
        await db["crawler_locks"].delete_one(
            {"_id": "catalogue-crawler", "holder": _INSTANCE_ID}
        )
    except Exception as exc:  # noqa: BLE001
        # The TTL is the backstop, so the worst case is a delayed next crawl —
        # never worth taking the service down over.
        logger.warning("Could not release the crawl lease (TTL will expire it): %s", exc)
from models import (
    ApplyDescriptionsRequest,
    ApplyDescriptionsResponse,
    CatalogueEntry,
    CatalogueListResponse,
    CrawlSummary,
    DraftDescriptionsResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mongo
# ---------------------------------------------------------------------------

_mongo_client: Optional[AsyncIOMotorClient] = None
_catalogue_col: Optional[AsyncIOMotorCollection] = None
_crawl_task: Optional[asyncio.Task] = None


def get_catalogue_col() -> AsyncIOMotorCollection:
    if _catalogue_col is None:
        raise RuntimeError("Catalogue collection not initialised")
    return _catalogue_col


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _verify_jwt(authorization: Optional[str], settings: Settings) -> dict:
    """Verify Bearer JWT and return claims. Tenant is taken from claims."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = pyjwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256", "RS256"],
            options={"verify_aud": False},
            issuer=settings.jwt_issuer,
            leeway=30,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    # The tenant claim, under any of the names the platform issues it.
    #
    # No token this system mints has ever carried "tenant_id". Citra-User-Service
    # issues "org_id", and smart-app-service has always resolved the scope as
    # tenant_id -> org_id -> dept_id (auth.py) and then STORED the result under
    # the name tenant_id -- which is why decision_records and smartapp_apps hold
    # tenant_id values that are org ids like "acme-bank".
    #
    # This service required "tenant_id" outright and never implemented that
    # fallback, so every human user's token was rejected: /catalogue answered
    # 401 "Token missing tenant_id", the builder could not read the catalogue,
    # and a fresh install could not build an app at all. The convention was
    # already settled; this file was the one that did not follow it.
    #
    # Normalised once, here, rather than at the seven call sites below that
    # index claims["tenant_id"] directly -- those keep working unchanged, and a
    # future one cannot forget the fallback.
    tenant = (
        claims.get("tenant_id")
        or claims.get("org_id")
        or claims.get("dept_id")
    )
    if not tenant:
        raise HTTPException(
            status_code=401,
            detail="Token carries no tenant scope (tenant_id, org_id or dept_id)",
        )
    claims["tenant_id"] = tenant
    return claims


def auth_dep(
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    return _verify_jwt(authorization, settings), authorization


def _is_super_admin(claims: dict) -> bool:
    """True when the caller holds the platform-wide ``super_admin`` role.

    The catalogue is tenant-scoped by construction, but ``super_admin`` is
    a Citra platform role that spans every org — so a super_admin reads
    every tenant's catalogue, not just its own.
    """
    return "super_admin" in {str(r).lower() for r in (claims.get("roles") or [])}


def _can_see_all_depts(claims: dict) -> bool:
    """org_admin / super_admin see every dept in their org → no dept filter."""
    roles = {str(r).lower() for r in (claims.get("roles") or [])}
    return bool(roles & {"super_admin", "org_admin"})


def _require_catalogue_curator(claims: dict) -> None:
    """Curating catalogue descriptions (draft + apply) is an admin task —
    dept_admin / org_admin / super_admin. Regular callers 403. Dept scoping of
    WHICH datasets they may touch is still enforced by the visibility filter on
    the read (a dept_admin can't apply to a dataset they can't see)."""
    roles = {str(r).lower() for r in (claims.get("roles") or [])}
    if not (roles & {"super_admin", "org_admin", "dept_admin"}):
        raise HTTPException(
            status_code=403,
            detail="dept_admin / org_admin / super_admin required to edit catalogue descriptions")


def _dept_visibility_filter(claims: dict) -> dict:
    """Mongo sub-filter for dept RBAC on the catalogue. ``{}`` when the caller
    sees all depts (org_admin / super_admin). Otherwise: dataset's ``dept_id``
    in the caller's depts, OR ``public_within_org``, OR unstamped (fail-open —
    we can't enforce a dept we never recorded)."""
    if _can_see_all_depts(claims):
        return {}
    depts = claims.get("dept_ids") or []
    return {
        "$or": [
            {"dept_id": {"$in": depts}},
            {"public_within_org": True},
            {"dept_id": None},
            {"dept_id": {"$exists": False}},
        ]
    }


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _catalogue_col, _crawl_task
    settings = get_settings()

    _mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    db = _mongo_client[settings.mongo_db]
    _catalogue_col = db[settings.catalogue_collection]
    await _catalogue_col.create_index(
        [("tenant_id", 1), ("source_id", 1), ("dataset_id", 1)], unique=True
    )
    await _catalogue_col.create_index("last_refreshed_at")
    await _catalogue_col.create_index("has_pii")

    # Single-customer boot guard: ORG_ID must resolve to a real `orgs` row.
    # A typo'd ORG_ID would file the background catalogue under a phantom
    # tenant — invisible to real users and a silent duplicate. Fail loud at
    # startup rather than crawl into the void. (RULE: fail loud, fail early.)
    if settings.org_id:
        org_doc = await db["orgs"].find_one({"id": settings.org_id})
        if org_doc is None:
            msg = (f"ORG_ID={settings.org_id!r} does not match any row in "
                   f"{settings.mongo_db}.orgs.")
            if settings.crawl_enabled:
                # The BACKGROUND crawler files the catalogue under settings.org_id;
                # a phantom tenant would be invisible + a silent duplicate. Fail loud.
                raise RuntimeError(
                    msg + " The background crawler would file the catalogue under a "
                    "phantom tenant. Fix ORG_ID or seed the org before starting."
                )
            # Crawl disabled (the default): the on-demand /crawl/run uses the
            # CALLER's tenant, not settings.org_id — so a not-yet-seeded org must
            # NOT crash the service (a fresh bring-up seeds the org AFTER the stack
            # starts). Warn instead of exiting.
            logger.warning("%s On-demand crawls use the caller's tenant; not failing boot.", msg)
        else:
            logger.info(
                "ORG_ID=%s resolves to org %r", settings.org_id, org_doc.get("name")
            )
    elif settings.crawl_enabled:
        # A background crawl with no org has no tenant to mint a token for —
        # mint_crawler_token would raise per-pass; surface it at boot instead.
        raise RuntimeError(
            "CRAWL_ENABLED=true but ORG_ID is unset — the background crawler "
            "has no tenant to crawl as. Set ORG_ID."
        )

    if settings.crawl_enabled:
        # ONE-SHOT ON STARTUP — NOT a nightly cron.
        # ---------------------------------------------------------------
        # Source schemas change rarely, and only through IT change management
        # (the schema in dept_sources / source-mcp-template is hand-authored on
        # a controlled change, not discovered ad-hoc). A periodic re-crawl would
        # therefore just redo identical work every night — and worse, could race
        # a controlled schema change mid-flight. So the catalogue is rebuilt
        # exactly once, when this service (re)starts. The deliberate act after a
        # schema change lands is to restart data-discovery-service (or hit
        # POST /crawl/run for an on-demand rebuild); we no longer loop on a timer.
        #
        # (Was: a `while True` loop that slept crawl_interval_seconds — default
        # 86400s / nightly — between passes. Removed 2026-07-01.)
        _lock_ttl = max(60, settings.crawl_lock_ttl_seconds)

        async def _startup_crawl():
            from crawler import mint_crawler_token
            # Leader-gate: only ONE replica runs the startup crawl. Acquired
            # OUTSIDE the try below so the release in `finally` can only ever
            # drop a lease we actually took.
            if not await _acquire_crawl_leadership(db, _lock_ttl):
                logger.info(
                    "Startup crawl skipped — another replica is crawling this "
                    "tenant right now (lease held; it is released when that pass "
                    "ends). Re-run on demand with POST /crawl/run."
                )
                return
            try:
                # Single-tenant deployment: mint an org_admin system token
                # for the configured ORG_ID and crawl as that tenant. dept-
                # MCPs run AUTHZ_ENFORCE=true, so a tokenless crawl is
                # rejected (401). mint_crawler_token raises loudly if ORG_ID
                # is unset — fail fast rather than crawl nothing silently.
                token = mint_crawler_token(settings, settings.org_id)
                _reports, _total, _pruned = await crawl_all(
                    settings=settings,
                    catalogue_col=_catalogue_col,
                    auth_header=f"Bearer {token}",
                    tenant_id=settings.org_id,
                )
                logger.info(
                    "Startup catalogue crawl complete — %d dataset(s) written, "
                    "%d orphaned row(s) pruned", _total, _pruned,
                )
            except TenantMismatchError as exc:
                # ORG_ID is misconfigured against the registered sources —
                # a correctness error, not a transient blip. Log loudly; a
                # fix + re-register + restart self-heals.
                logger.error(
                    "Startup crawl aborted on tenant mismatch (ORG_ID=%s): %s",
                    settings.org_id, exc,
                )
            except Exception as exc:
                logger.warning("Startup crawl failed: %s", exc)
            finally:
                # Release however the pass ends, success or failure — a lease
                # that outlives its pass buys nothing and blocks the next
                # restart's crawl. Best-effort on cancellation at shutdown (the
                # await may not survive the cancel); the TTL is the backstop.
                await _release_crawl_leadership(db)
        # Run as a background task so a slow crawl doesn't block the app from
        # accepting requests; it runs once and exits (no re-schedule).
        _crawl_task = asyncio.create_task(_startup_crawl(), name="catalogue-crawler")
        logger.info("Startup catalogue crawl scheduled (one-shot; re-run via POST /crawl/run)")

    yield

    if _crawl_task and not _crawl_task.done():
        _crawl_task.cancel()
        try:
            await _crawl_task
        except (asyncio.CancelledError, Exception):
            pass
    if _mongo_client is not None:
        _mongo_client.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set (loaded
# from Vault above in prod). Errors flow to GlitchTip → Monitoring-Service email.
try:
    from observability import init_sentry, install_trace_id_middleware
    init_sentry("data-discovery-service")
except Exception as _exc:
    logger.warning(f"[sentry] init skipped: {_exc}")
    install_trace_id_middleware = None  # type: ignore[assignment]

app = FastAPI(
    title="data-discovery-service",
    description=(
        "Crawls every registered Citra dept-mcp shim and builds a tenant-scoped "
        "catalogue of datasets with PII flags, semantic types, read_via and "
        "write_actions. Powers dataset selection in the smart-app builder."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

if install_trace_id_middleware is not None:
    try:
        install_trace_id_middleware(app)
    except Exception as _exc:
        logger.warning(f"[sentry] trace_id middleware skipped: {_exc}")


@app.get("/health")
async def health():
    # Expose ORG_ID so a fleet check can diff it against user-service /
    # dept-MCPs and catch single-customer org drift.
    return {
        "status": "ok",
        "service": "data-discovery-service",
        "org_id": get_settings().org_id or None,
    }


@app.get("/catalogue", response_model=CatalogueListResponse)
async def catalogue_list(
    auth=Depends(auth_dep),
    has_pii: Optional[bool] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    source_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
):
    claims, _ = auth
    # Tenant-scoped by default; a platform super_admin sees every tenant's
    # catalogue cross-org.
    q: dict = {} if _is_super_admin(claims) else {"tenant_id": claims["tenant_id"]}
    if has_pii is not None:
        q["has_pii"] = has_pii
    if kind:
        q["kind"] = kind
    if source_id:
        q["source_id"] = source_id
    q.update(_dept_visibility_filter(claims))  # RBAC: dept scope (no-op for org_admin/super)

    col = get_catalogue_col()
    # True match count so truncation is never silent (RULE: fail loud). The
    # response ``total`` carries the real number even when ``entries`` is
    # capped, and we log a warning so a large org doesn't quietly lose tables.
    total_matched = await col.count_documents(q)
    cursor = col.find(q).limit(limit)
    entries = []
    async for doc in cursor:
        doc.pop("_id", None)
        try:
            entries.append(CatalogueEntry(**doc))
        except Exception as exc:
            logger.debug("Skipping malformed catalogue row: %s", exc)
    if total_matched > len(entries):
        logger.warning(
            "[CATALOGUE] TRUNCATED: returned %d of %d datasets for tenant=%s "
            "(limit=%d). Caller should narrow scope (source_id / dept) or use "
            "query-driven search — see docs/catalogue-retrieval-plan.md.",
            len(entries), total_matched, q.get("tenant_id", "*"), limit,
        )
    return CatalogueListResponse(entries=entries, total=total_matched)


# NOTE: /catalogue/search MUST be declared before the /catalogue/{dataset_id:path}
# catch-all below — FastAPI matches in definition order, so a path-param route
# placed first would swallow "search" as a dataset_id and 404. Keep search first.
@app.get("/catalogue/search", response_model=CatalogueListResponse)
async def catalogue_search(
    q: str = Query(..., min_length=1),
    source_id: Optional[str] = Query(default=None),
    top_k: int = Query(default=50, ge=1, le=200),
    auth=Depends(auth_dep),
    settings: Settings = Depends(get_settings),
):
    """Query-driven dataset recall — ANN over the catalogue Milvus collection,
    scoped to the caller's tenant (+ optional source). Returns the relevant
    top-K datasets so the builder never fetches/ranks the whole catalogue.

    Falls back to a plain tenant-scoped Mongo list (capped at ``top_k``) when
    the vector index is disabled or unavailable — so callers get a sensible,
    bounded result either way.
    """
    import catalogue_vectors

    claims, _ = auth
    tenant_id = claims["tenant_id"]
    dept_ids = claims.get("dept_ids") or []
    all_depts = _can_see_all_depts(claims)

    # Fail loud: an ENABLED-but-broken index must surface, not be masked as a
    # silent fallback. catalogue_vectors.search returns None ONLY when vectoring
    # is *disabled* (a configured mode); any genuine failure raises.
    try:
        ranked = await catalogue_vectors.search(
            settings,
            query=q,
            tenant_id=tenant_id,
            dept_ids=dept_ids,
            source_id=source_id,
            top_k=top_k,
            all_depts=all_depts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[CATALOGUE] vector search failed for tenant=%s: %s", tenant_id, exc)
        raise HTTPException(
            status_code=503, detail=f"catalogue vector search unavailable: {exc}"
        )

    col = get_catalogue_col()

    # Scope filter shared by the disabled-mode list and the empty-index check.
    fq: dict = {} if _is_super_admin(claims) else {"tenant_id": tenant_id}
    if source_id:
        fq["source_id"] = source_id
    fq.update(_dept_visibility_filter(claims))

    if ranked is None:
        # Vectoring DISABLED (configured) → bounded scoped list. This is the
        # intended mode, not a failure fallback.
        entries = []
        async for doc in col.find(fq).limit(top_k):
            doc.pop("_id", None)
            try:
                entries.append(CatalogueEntry(**doc))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping malformed catalogue row: %s", exc)
        return CatalogueListResponse(entries=entries, total=len(entries))

    if not ranked:
        # Enabled + zero hits. Either the tenant genuinely has no datasets in
        # scope, OR the index isn't populated. Distinguish via the catalogue
        # count and FAIL LOUD on an unpopulated index (don't quietly serve the
        # Mongo list — that would mask a missing crawl).
        existing = await col.count_documents(fq)
        if existing > 0:
            logger.error(
                "[CATALOGUE] vector index returned 0 results for tenant=%s but "
                "%d datasets exist in scope — index not populated.",
                tenant_id, existing,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"catalogue vector index returned no results, but {existing} "
                    f"datasets exist in this scope — the index is not populated. "
                    f"Run /crawl/run to (re)index."
                ),
            )
        return CatalogueListResponse(entries=[], total=0)

    # Hydrate the ranked dataset_ids from Mongo, preserving rank order.
    dids = [d for (_s, d) in ranked]
    by_id: dict = {}
    if dids:
        async for doc in col.find({"tenant_id": tenant_id, "dataset_id": {"$in": dids}}):
            doc.pop("_id", None)
            by_id[doc.get("dataset_id")] = doc
    entries = []
    for _sid, did in ranked:
        doc = by_id.get(did)
        if doc:
            try:
                entries.append(CatalogueEntry(**doc))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping malformed catalogue row: %s", exc)
    return CatalogueListResponse(entries=entries, total=len(entries))


@app.get("/catalogue/{dataset_id:path}", response_model=CatalogueEntry)
async def catalogue_get(
    dataset_id: str,
    auth=Depends(auth_dep),
    source_id: Optional[str] = Query(default=None),
):
    claims, _ = auth
    q: dict = {"dataset_id": dataset_id}
    if not _is_super_admin(claims):
        q["tenant_id"] = claims["tenant_id"]
    if source_id:
        q["source_id"] = source_id

    col = get_catalogue_col()
    doc = await col.find_one(q)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id!r} not in catalogue")
    doc.pop("_id", None)
    return CatalogueEntry(**doc)


def _scoped_dataset_query(dataset_id: str, claims: dict, source_id: Optional[str]) -> dict:
    """Dataset lookup filter honoring tenant + dept RBAC (same visibility the
    read endpoints use) so a curator can only touch datasets they can see."""
    q: dict = {"dataset_id": dataset_id}
    if not _is_super_admin(claims):
        q["tenant_id"] = claims["tenant_id"]
    q.update(_dept_visibility_filter(claims))
    if source_id:
        q["source_id"] = source_id
    return q


@app.post("/catalogue/{dataset_id:path}/draft-descriptions",
          response_model=DraftDescriptionsResponse)
async def catalogue_draft_descriptions(
    dataset_id: str,
    auth=Depends(auth_dep),
    source_id: Optional[str] = Query(default=None),
    settings: Settings = Depends(get_settings),
):
    """LLM-draft table + column descriptions for a catalogued dataset, for the
    DBA to review before applying. Reuses the crawl's enricher (schema-based;
    the same model, prompt, and never-rename invariant). Writes NOTHING."""
    claims, _ = auth
    _require_catalogue_curator(claims)
    col = get_catalogue_col()
    doc = await col.find_one(_scoped_dataset_query(dataset_id, claims, source_id))
    if not doc:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id!r} not in catalogue")

    from catalogue_descriptions import draft_to_response
    from enricher import enrich_dataset

    draft = await enrich_dataset(
        settings=settings,
        mcp_description=doc.get("description"),
        table_physical_name=doc.get("physical_name") or doc.get("name") or dataset_id,
        table_kind=doc.get("kind") or "sql",
        columns=doc.get("columns") or [],
        sample_rows=[],   # schema-based draft on demand (no fresh sample fetch)
    )
    if draft is None:
        raise HTTPException(
            status_code=503,
            detail="description drafting unavailable (LLM disabled or unreachable)")
    return DraftDescriptionsResponse(**draft_to_response(dataset_id, draft))


@app.put("/catalogue/{dataset_id:path}/descriptions",
         response_model=ApplyDescriptionsResponse)
async def catalogue_apply_descriptions(
    dataset_id: str,
    body: ApplyDescriptionsRequest,
    auth=Depends(auth_dep),
    source_id: Optional[str] = Query(default=None),
):
    """Apply DBA-approved descriptions to a catalogued dataset. Marks it
    approved + manual (wins over future LLM re-drafts) and never renames a
    column. Reports any approved keys that matched no column (surfaced, not
    silently dropped)."""
    from datetime import datetime, timezone

    claims, _ = auth
    _require_catalogue_curator(claims)
    col = get_catalogue_col()
    doc = await col.find_one(_scoped_dataset_query(dataset_id, claims, source_id))
    if not doc:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id!r} not in catalogue")

    from catalogue_descriptions import merge_descriptions

    update, matched, unmatched = merge_descriptions(
        doc,
        table_description=body.table_description,
        column_descriptions=body.column_descriptions or {},
        actor=str(claims.get("user_id") or claims.get("sub") or "unknown"),
        at=datetime.now(timezone.utc),
    )
    await col.update_one({"_id": doc["_id"]}, {"$set": update})
    return ApplyDescriptionsResponse(
        dataset_id=dataset_id, mapping_status="approved",
        updated_columns=matched, unmatched=unmatched)


@app.post("/crawl/run", response_model=CrawlSummary)
async def crawl_run(auth=Depends(auth_dep), settings: Settings = Depends(get_settings)):
    """Run a one-shot tenant-scoped crawl. Returns a per-MCP summary."""
    claims, auth_header = auth
    started = datetime.utcnow()
    try:
        reports, total, pruned = await crawl_all(
            settings=settings,
            catalogue_col=get_catalogue_col(),
            auth_header=auth_header,
            tenant_id=claims["tenant_id"],
        )
    except TenantMismatchError as exc:
        # Fail loud: the crawl would have filed datasets under the wrong
        # tenant. Surface it as a 409 instead of writing bad catalogue rows.
        logger.error("crawl/run aborted on tenant mismatch (tenant=%s): %s", claims.get("tenant_id"), exc)
        raise HTTPException(status_code=409, detail=f"tenant mismatch: {exc}")
    return CrawlSummary(
        started_at=started,
        finished_at=datetime.utcnow(),
        reports=reports,
        total_datasets=total,
        total_pruned=pruned,
    )


@app.post("/crawl/dataset", response_model=CrawlSummary)
async def crawl_dataset(
    mcp: dict,
    auth=Depends(auth_dep),
    settings: Settings = Depends(get_settings),
):
    """Re-crawl one MCP on demand. Body: {tool_id, base_url}."""
    claims, auth_header = auth
    started = datetime.utcnow()

    # Resolve BOTH the source's org (fail-closed tenant guard — a hand-supplied
    # body has no org_ids) AND per-source RBAC scope (dept_id/public_within_org)
    # from the discovery registry, which replaced the retired dept_sources.
    from crawler import list_registered_mcps, _resolve_base_url, _scoping_from_tools
    registered = await list_registered_mcps(settings, auth_header)
    scoping_by_source = _scoping_from_tools(registered)
    if not (mcp.get("org_ids") or mcp.get("org_id")):
        want_base = _resolve_base_url(mcp)
        want_tool = mcp.get("tool_id") or mcp.get("id")
        match = next(
            (
                m for m in registered
                if (want_base and _resolve_base_url(m) == want_base)
                or (want_tool and (m.get("tool_id") or m.get("id")) == want_tool)
            ),
            None,
        )
        if match is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"source not discoverable for tenant="
                    f"{claims.get('tenant_id')!r}; cannot verify its org before "
                    f"crawling"
                ),
            )
        if match.get("org_ids"):
            mcp["org_ids"] = match["org_ids"]
        elif match.get("org_id"):
            mcp["org_id"] = match["org_id"]

    try:
        report = await crawl_mcp(
            mcp,
            settings=settings,
            catalogue_col=get_catalogue_col(),
            scoping_by_source=scoping_by_source,
            auth_header=auth_header,
            tenant_id=claims["tenant_id"],
        )
    except TenantMismatchError as exc:
        logger.error("crawl/dataset aborted on tenant mismatch (tenant=%s): %s", claims.get("tenant_id"), exc)
        raise HTTPException(status_code=409, detail=f"tenant mismatch: {exc}")
    return CrawlSummary(
        started_at=started,
        finished_at=datetime.utcnow(),
        reports=[report],
        total_datasets=report.datasets_written,
    )


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
