"""
Dept MCP Server — FastAPI Application
=======================================
Query plane only. Two endpoints:

  POST /query   — Called by Citra enterprise_search.py per selected tool
  GET  /health  — Used by discovery service health-proxy

Source definitions are loaded from Citra-Service's central ``dept_sources``
Mongo collection. Ingestion is owned exclusively by the Citra Workflow Engine
("Dept Data Flow" pipelines) — this service no longer schedules, embeds,
or writes anything.
"""

import asyncio
import logging
import os
import re
import time as _time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

# Secret bootstrap MUST run BEFORE any import that pulls `config` — Settings
# reads env at class-definition time (SOURCES_FILE is required by the validator).
# Env-first precedence resolved by bootstrap_secrets (see vault_bootstrap):
#   • CUSTOMER CLOUD — config injected as env vars (SECRETS_PROVIDER=env or just
#     no Vault vars set) → used directly; Vault never contacted.
#   • DEMO-PROD (ours) — VAULT_* set → the whole bag loads from Vault, but any
#     already-injected env var wins (overwrite=False).
#   • LOCAL DEV — no Vault vars → .env keeps working (loaded lazily in config).
# Configure logging FIRST or the bootstrap's confirmation lines are swallowed
# (no root handler exists this early).
logging.basicConfig(level=logging.INFO)
from vault_bootstrap import bootstrap_secrets
bootstrap_secrets()

import boto3
import httpx
from botocore.client import Config as _BotoConfig
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from auth import (
    AuthzError,
    check_visibility,
    log_audit,
    verify_user_jwt,
)
from config import get_settings
from metrics import mount_metrics
from models import (
    ExecuteActionRequest,
    ExecuteActionResponse,
    HealthResponse,
    ListDatasetsResponse,
    QueryRequest,
    QueryResponse,
    RunQueryRequest,
    RunQueryResponse,
    ResolveMediaRequest,
    ResolveMediaResponse,
    SampleResponse,
    DatasetSchema,
)
from registration import deregister_all, register_all
from router import (
    all_sources,
    get_source,
    list_source_ids,
    load_sources,
    start_refresh_task,
    stop_refresh_task,
)
from query_planner import plan_and_execute

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# §1 — drop per-query INFO chatter to WARNING in prod (explicit LOG_LEVEL wins).
# basicConfig above ran at import with no settings; realign the root level now
# that config is importable. Invalid level names fall back to INFO, loudly.
try:
    _lvl = get_settings().resolved_log_level
    logging.getLogger().setLevel(getattr(logging, _lvl, logging.INFO))
    if not hasattr(logging, _lvl):
        logger.warning("[MCP] unknown LOG_LEVEL %r — using INFO", _lvl)
except Exception as _exc:  # pragma: no cover — settings load is boot-fatal elsewhere
    logger.warning("[MCP] log-level resolution skipped: %s", _exc)

_discovery_registered = False

# Concurrency cap for the /query plan_and_execute chain. Built lazily on first
# use so the Semaphore binds to the running event loop (not a loop captured at
# import time). Sized from settings.query_max_concurrency.
_query_semaphore: Optional[asyncio.Semaphore] = None


def _get_query_semaphore() -> asyncio.Semaphore:
    global _query_semaphore
    if _query_semaphore is None:
        _query_semaphore = asyncio.Semaphore(max(1, get_settings().query_max_concurrency))
    return _query_semaphore


# Lazily-built, process-wide S3 client. boto3.client() is relatively expensive
# (parses botocore models, builds the signer), so build it once and reuse it for
# the media endpoints (/resolve_media, /media) that presign customer-storage
# references. generate_presigned_url is purely local (no network) but still
# blocking, so it's run via asyncio.to_thread.
_s3_client = None


def _get_s3_client():
    """Build the S3 client once (module-level singleton) and reuse it.

    Raises HTTPException(503) when object storage isn't configured — same
    contract the handler had inline. Credentials/region come from env, fixed
    for the process lifetime.
    """
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    region = os.getenv("BUCKET_REGION", "us-east-1")
    access = os.getenv("BUCKET_ACCESS_KEY") or ""
    secret = os.getenv("BUCKET_SECRET_KEY") or ""
    bucket_name = os.getenv("BUCKET_NAME") or ""
    endpoint_url = (os.getenv("BUCKET_ENDPOINT_URL") or "").strip() or None

    if not (bucket_name and access and secret):
        raise HTTPException(
            status_code=503,
            detail="object storage is not configured on this MCP (BUCKET_NAME / "
                   "BUCKET_ACCESS_KEY / BUCKET_SECRET_KEY missing)",
        )

    # Virtual-hosted style ("<bucket>.<host>/<key>") is what AWS S3 wants, but it
    # breaks against every S3-compatible service: with BUCKET_ENDPOINT_URL set to
    # a MinIO instance boto3 dials `demo-source-citra.host.docker.internal:9002`,
    # which does not resolve, and /media 502s with "Could not connect to the
    # endpoint URL". MinIO serves path style ("<host>/<bucket>/<key>"), so a
    # custom endpoint implies path style.
    addressing_style = "path" if endpoint_url else "virtual"

    client_kwargs: Dict[str, Any] = dict(
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name=region,
        config=_BotoConfig(signature_version="s3v4",
                           s3={"addressing_style": addressing_style}),
    )
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url
    elif region and region != "us-east-1":
        # Mirrors Citra-Service/bucket.py — pin the regional endpoint so
        # the SigV4 signed URL matches the signed region on first request.
        client_kwargs["endpoint_url"] = f"https://s3.{region}.amazonaws.com"

    _s3_client = boto3.client("s3", **client_kwargs)
    return _s3_client


class EmbeddingDimMismatch(RuntimeError):
    """Query embedding dim != the dim the Milvus collections were ingested at."""


async def _check_embedding_dim_parity() -> None:
    """Assert the query embedding dim == milvus_vector_dim, FAIL LOUD on mismatch.

    A mismatch is a hard configuration bug: semantic search returns nothing and
    precomputed dataset signature vectors are silently discarded by the ranker's
    dim-guard. Per RULE #1 we refuse to boot in that state rather than serve
    silently-degraded results — the operator must align EMBEDDING_* with the dim
    the Workflow Engine ingested at. An embedding-service outage at boot is the
    one transient we tolerate (warn + continue): it is not a config error and
    the per-query path surfaces it.
    """
    cfg = get_settings()
    expected = int(getattr(cfg, "milvus_vector_dim", 0) or 0)
    if expected <= 0:
        return
    try:
        from rag.embed import embed_query
        vec = await embed_query("dimension probe")
    except Exception as exc:  # transient — embedding service down at boot
        logger.warning(
            "[MCP] embedding-dim parity check skipped — embedding service "
            "unreachable at startup (%s). Per-query dim-guards still apply.", exc,
        )
        return
    actual = len(vec or [])
    if actual != expected:
        raise EmbeddingDimMismatch(
            f"query embeddings are {actual}-dim but milvus_vector_dim={expected}. "
            f"Semantic search would return NOTHING and precomputed signature "
            f"vectors would be discarded. Fix EMBEDDING_MODEL/EMBEDDING_DIMENSION "
            f"so the query side matches the dim the Workflow Engine ingested at "
            f"(e.g. baai/bge-m3 with EMBEDDING_DIMENSION={expected})."
        )
    logger.info("✅ [MCP] embedding-dim parity OK (%d == milvus_vector_dim)", actual)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

def _install_thread_executor() -> None:
    """Right-size the default asyncio executor (§1).

    Every blocking DB/connector read is offloaded with ``asyncio.to_thread``,
    which all share the loop's ONE default ThreadPoolExecutor (~min(32, cpu+4)
    ⇒ ~6 on a 2-vCPU box). Under load the offload path starves before the DB
    pool is even busy. Install an executor sized from
    ``thread_pool_max_workers`` so concurrent /query offloads aren't bottle-
    necked below the DB pool capacity. 0 keeps the interpreter default.
    """
    n = int(getattr(get_settings(), "thread_pool_max_workers", 0) or 0)
    if n <= 0:
        return
    from concurrent.futures import ThreadPoolExecutor
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=n, thread_name_prefix="mcp-offload")
    )
    logger.info("[MCP] default thread executor sized to %d workers", n)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _discovery_registered

    # 0. Size the to_thread offload pool to the expected concurrency.
    _install_thread_executor()

    # 1. Pull source definitions from the configured registry (local
    #    SOURCES_FILE when set, else central Mongo).
    sources_list = await load_sources()

    # 2. Register each source as a discovery-service tool.
    #    ONE-SHOT ON STARTUP — no keep-alive heartbeat.
    #    The discovery-service registry is Mongo-persisted (upsert) and has NO
    #    heartbeat-TTL reaper: `/tools/available` filters on `active:True` only
    #    and never checks `last_heartbeat`. So a per-minute PUT /heartbeat did
    #    nothing for availability — a registration set here stays live until an
    #    explicit deregister (disabled by default). Registrations change rarely
    #    and only via IT change management, so we register once at (re)start and
    #    let the persisted record stand. (Removed the 60s heartbeat loop
    #    2026-07-01; a restart is the deliberate act after a source change.)
    # Register structured sources AND semantic sources. Semantic sources are NOT
    # in the query registry (dropped from _sources — the MCP serves zero RAG), but
    # they must still be advertised to discovery so chat (whose only source of
    # truth is discovery) can surface them and read Milvus directly via the
    # published rag_collection. register_all flags each by source_type.
    from router import get_semantic_sources
    await register_all(sources_list + get_semantic_sources())
    _discovery_registered = True

    # 3. Optionally hot-reload dept_sources in the background. DEFAULT OFF
    #    (SOURCES_REFRESH_SECONDS=0) — the one-shot load above is authoritative
    #    and schema changes come via change management + a restart. Set > 0 only
    #    to pick up hand-authored dept_sources edits without restarting.
    start_refresh_task()

    # 4. Embedding-dim parity check. The query embedding MUST match the dim
    #    the Workflow Engine wrote the Milvus collections + dataset signature
    #    vectors at (milvus_vector_dim), or semantic search returns nothing and
    #    precomputed signature vectors are silently discarded by the ranker's
    #    dim-guard. A definite mismatch is a CONFIG BUG → raise and refuse to
    #    boot (RULE #1). A transient embedding-service outage at boot is the one
    #    case we tolerate (warn + continue).
    await _check_embedding_dim_parity()

    # Audit-trail indexes are owned by smart-app-service now (it is the sole
    # writer of dept_query_audit via /api/audit/ingest) — the MCP no longer
    # creates them or holds any Citra DB handle.

    _sem_n = len(get_semantic_sources())
    logger.info(
        f"✅ [MCP] Dept MCP ready — {len(sources_list)} structured source(s) served + "
        f"{_sem_n} semantic source(s) advertised to discovery (RAG served platform-side)"
    )
    yield

    stop_refresh_task()
    await deregister_all()
    # Close the shared singleton MilvusClient so the gRPC channel doesn't
    # linger and block shutdown for up to 30 s waiting on RPC timeouts.
    try:
        from rag._milvus import close_client as _close_milvus
        _close_milvus()
    except Exception as _exc:
        logger.warning("Milvus client close skipped: %s", _exc)
    logger.info("🛑 [MCP] Dept MCP shutdown complete")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
try:
    from observability import init_sentry, install_trace_id_middleware
    init_sentry("dept-mcp")
except Exception as _exc:
    logger.warning(f"[sentry] init skipped: {_exc}")
    install_trace_id_middleware = None  # type: ignore[assignment]

app = FastAPI(
    title="Dept MCP Server",
    description=(
        "Department data source query gateway. Wraps structured and unstructured "
        "data sources as RAG query tools. Registered in the Citra discovery service. "
        "Source definitions are owned by Citra-Service /api/dept-sources; ingestion is "
        "owned by the Citra Workflow Engine."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

if install_trace_id_middleware is not None:
    try:
        install_trace_id_middleware(app)
    except Exception as _exc:
        logger.warning(f"[sentry] trace_id middleware skipped: {_exc}")

# Distributed tracing — OTel spans → Tempo
try:
    from citra_service_utils import setup_tracing as _setup_tracing, request_id_middleware as _request_id_middleware
    app.middleware("http")(_request_id_middleware)
    _setup_tracing(app, service_name="dept-mcp")
except ImportError:
    logger.warning("citra-service-utils not installed; distributed tracing disabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus /metrics — unauthenticated, per platform convention. Restrict
# scrape access at the network/ingress layer, not here.
mount_metrics(app)


# ---------------------------------------------------------------------------
# Auth helper — Citra calls this with the SERVICE_API_KEY in Authorization
# ---------------------------------------------------------------------------

def _verify_caller(authorization: Optional[str] = Header(default=None)) -> None:
    cfg = get_settings()
    if not cfg.mcp_api_key:
        return  # dev mode
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != cfg.mcp_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health():
    return HealthResponse(
        sources=list_source_ids(),
        discovery_registered=_discovery_registered,
        org_id=os.getenv("ORG_ID") or None,
    )


@app.post("/query", response_model=QueryResponse, tags=["query"])
async def query(
    req: QueryRequest,
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    """Per-source RAG query.

    Headers:
      • Authorization: Bearer <MCP_API_KEY>  — service-to-service guard
      • X-User-JWT: <HS256>                  — end-user identity (JWT_SECRET-verified)
    """
    # Verify user identity
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        logger.warning(f"[AUTHZ] /query rejected: {e.reason}")
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    # Per-source visibility PDP
    source = get_source(req.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {req.source_id}")

    try:
        # Always run the PDP. In dev mode (AUTHZ_ENFORCE=false) verify_user_jwt
        # returns empty claims; check_visibility → _deny then logs+allows LOUDLY
        # rather than us silently skipping the gate on a falsy dict.
        check_visibility(source, claims)
    except AuthzError as e:
        await log_audit(
            source_id=req.source_id, query=req.query, claims=claims,
            result_count=0, allowed=False, reason=e.reason, source=source,
        )
        logger.warning(f"[AUTHZ] /query denied for {claims.get('user_id')}: {e.reason}")
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    # Dispatch via the catalogue-keyed orchestrator. This is the single
    # entry point for every backend (sql, duckdb, odata, soql, rest,
    # semantic) — the old per-source-type router.dispatch() shim has
    # been retired in favour of catalogue lookup + per-kind NL→query
    # planners + the shared query_engine.
    #
    # Durability guards: gate behind a module-level Semaphore so a backlog of
    # slow LLM/DB chains can't pin unbounded concurrent requests, and wrap in
    # asyncio.wait_for so a hung chain returns a clean 504 instead of holding
    # the connection open forever. A timeout is audited (allowed=True,
    # reason="timeout") so the failure is LOUD, then surfaced to the caller.
    cfg = get_settings()
    # Per-source read timeout (docs §2 `query_timeout_seconds`) overrides the
    # global. It was documented top-level and every acme source authors 90, but
    # the ONLY reader was the bigquery path via `options.query_timeout_seconds`
    # — so the top-level value was a silent no-op on every other backend while
    # registration still advertised it to discovery. Honour top-level first,
    # then the legacy options nest, then the global.
    _src_timeout = (source or {}).get("query_timeout_seconds")
    if _src_timeout in (None, ""):
        _src_timeout = ((source or {}).get("options") or {}).get("query_timeout_seconds")
    try:
        _timeout = int(_src_timeout) if _src_timeout not in (None, "") else int(cfg.query_timeout_seconds)
        if _timeout <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError) as _e:
        # Don't silently fall back to the global on a bad value — the author
        # asked for a specific budget and got something else.
        logger.warning(
            "[QUERY] source=%s has an invalid query_timeout_seconds=%r (%s) — "
            "using the global %ss.",
            req.source_id, _src_timeout, _e, cfg.query_timeout_seconds,
        )
        _timeout = int(cfg.query_timeout_seconds)
    try:
        async with _get_query_semaphore():
            resp = await asyncio.wait_for(plan_and_execute(req), timeout=_timeout)
    except asyncio.TimeoutError:
        logger.error(
            f"[QUERY] timeout after {_timeout}s for "
            f"source={req.source_id} user={claims.get('user_id')}"
        )
        await log_audit(
            source_id=req.source_id, query=req.query, claims=claims,
            result_count=0, allowed=True,
            reason=f"timeout after {_timeout}s",
            source=source,
        )
        raise HTTPException(
            status_code=504,
            detail=f"query timed out after {_timeout}s",
        )

    # Audit allowed call
    try:
        result_count = len(getattr(resp, "results", []) or [])
    except Exception:
        result_count = 0
    await log_audit(
        source_id=req.source_id, query=req.query, claims=claims,
        result_count=result_count, allowed=True, reason="allowed", source=source,
    )
    return resp




# ---------------------------------------------------------------------------
# Catalogue contract — list / describe / sample / run_query / execute_action
# ---------------------------------------------------------------------------
#
# These five endpoints are what the data-discovery-service crawler and the
# Citra smart-app builder consume. They are intentionally read-mostly and
# carry the same auth contract as /query: service API key + X-User-JWT.

import catalogue  # noqa: E402  (imported here to avoid circular init)


@app.get("/datasets", response_model=ListDatasetsResponse, tags=["catalogue"])
async def datasets_list(
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)
    # catalogue.* are synchronous (introspection / connector calls) — run
    # them in a worker thread so blocking IO never stalls the event loop.
    return await asyncio.to_thread(catalogue.list_datasets, claims=claims or None)


# NOTE: the /sample route MUST be registered BEFORE the bare
# /datasets/{dataset_id:path} describe route. The `:path` converter is
# greedy, so the describe route would otherwise match
# `/datasets/<id>/sample` with dataset_id="<id>/sample" and shadow this
# endpoint (404). Starlette matches routes in registration order.
@app.get(
    "/datasets/{dataset_id:path}/sample",
    response_model=SampleResponse,
    tags=["catalogue"],
)
async def datasets_sample(
    dataset_id: str,
    n: int = 100,
    source_id: Optional[str] = None,
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)
    return await asyncio.to_thread(
        catalogue.sample_dataset, dataset_id, n=n, source_id=source_id, claims=claims or None,
    )


@app.get("/datasets/{dataset_id:path}", response_model=DatasetSchema, tags=["catalogue"])
async def datasets_describe(
    dataset_id: str,
    source_id: Optional[str] = None,
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)
    return await asyncio.to_thread(
        catalogue.describe_dataset, dataset_id, source_id=source_id, claims=claims or None,
    )


@app.post("/run_query", response_model=RunQueryResponse, tags=["catalogue"])
async def datasets_run_query(
    req: RunQueryRequest,
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    source = catalogue.get_source(req.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {req.source_id!r}")

    audit_extra = {"dataset_id": req.dataset_id, "kind": req.kind.value}
    try:
        # Always run the PDP. In dev mode (AUTHZ_ENFORCE=false) verify_user_jwt
        # returns empty claims; check_visibility → _deny then logs+allows LOUDLY
        # rather than us silently skipping the gate on a falsy dict.
        check_visibility(source, claims)
    except AuthzError as e:
        await log_audit(
            source_id=req.source_id, query=str(req.query), claims=claims,
            result_count=0, allowed=False, reason=e.reason,
            op="run_query", source=source, extra=audit_extra,
        )
        logger.warning(f"[AUTHZ] /run_query denied for {claims.get('user_id')}: {e.reason}")
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    resp = await catalogue.run_query(req)
    await log_audit(
        source_id=req.source_id, query=str(req.query), claims=claims,
        result_count=resp.total, allowed=True,
        reason="ok" if not resp.error else "execution_error",
        op="run_query", source=source,
        extra={**audit_extra, "ok": resp.error is None, "error": resp.error},
    )
    return resp


@app.post("/resolve_media", response_model=ResolveMediaResponse, tags=["catalogue"])
async def datasets_resolve_media(
    req: ResolveMediaRequest,
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    """Resolve a record's media column to a short-lived fetchable URL.

    The runtime passes a logical reference — (dataset, key, column) — never a URL
    or bytes. We read the row by key UNDER THE CALLER'S VISIBILITY, take the
    column's NATIVE ref, and presign it on demand (so the SoR stores a durable
    ``s3://`` ref, not a baked/expiring URL). Source creds + network reach live
    here, at the one governed boundary. ``http(s)://`` refs pass through unchanged;
    streaming for non-URL-able sources (BLOB / file share / DMS) is a future phase,
    and any unrecognised ref fails loud rather than mis-resolving."""
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    source = catalogue.get_source(req.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {req.source_id!r}")

    # Same PDP as /run_query — you cannot resolve media on a record you can't read.
    try:
        check_visibility(source, claims)
    except AuthzError as e:
        await log_audit(
            source_id=req.source_id, query=f"resolve_media:{req.dataset_id}.{req.column}",
            claims=claims, result_count=0, allowed=False, reason=e.reason,
            op="resolve_media", source=source,
        )
        logger.warning(f"[AUTHZ] /resolve_media denied for {claims.get('user_id')}: {e.reason}")
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    ref = (await catalogue.read_media_ref(
        source_id=req.source_id, dataset_id=req.dataset_id,
        key_field=req.key_field, key_value=req.key_value, column=req.column,
    )).strip()

    # Best-effort MIME from the ref's extension (the bucket object's Content-Type
    # wins at fetch time; this just helps the consumer pick a decoder). Falls back
    # to the column's declared `mime_hint` when the ref carries no recognised
    # extension — a bare object key, a signed URL, an opaque id. That is the ONE
    # case the hint exists for, and it used to be ignored there: declaring
    # mime_hint: "application/pdf" yielded content_type=None anyway. Extension
    # wins: it describes the object actually stored; the hint is what the author
    # expects it to be.
    _ext = ("." + ref.rsplit("?", 1)[0].rsplit(".", 1)[-1].lower()) if "." in ref else ""
    content_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
    }.get(_ext)
    if not content_type:
        content_type = catalogue.declared_mime_hint(
            req.source_id, req.dataset_id, req.column,
        )

    if ref.startswith("http://") or ref.startswith("https://"):
        # Already a fetchable URL — pass through, no minting.
        url, expires_at = ref, None
    else:
        # s3://bucket/key  OR  a bare bucket key → presign on demand.
        if ref.startswith("s3://"):
            bucket, _, key = ref[len("s3://"):].partition("/")
        else:
            bucket, key = (os.getenv("BUCKET_NAME") or ""), ref.lstrip("/")
        if not bucket or not key:
            raise HTTPException(status_code=400, detail=f"unresolvable media ref: {ref!r}")
        ttl = int(os.getenv("DOCUMENT_URL_TTL_SECONDS", "300"))
        s3 = _get_s3_client()
        try:
            url = await asyncio.to_thread(
                s3.generate_presigned_url, ClientMethod="get_object",
                Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl,
            )
        except Exception as exc:  # noqa: BLE001 — surface as 502, don't 500
            logger.error(f"resolve_media presign failed for s3://{bucket}/{key}: {exc}")
            raise HTTPException(status_code=502, detail=f"presign failed: {exc}")
        expires_at = int(_time.time()) + ttl

    await log_audit(
        source_id=req.source_id, query=f"resolve_media:{req.dataset_id}.{req.column}",
        claims=claims, result_count=1, allowed=True, reason="media url issued",
        op="resolve_media", source=source,
    )
    return ResolveMediaResponse(
        url=url, mode="direct", content_type=content_type, expires_at=expires_at,
    )


def _media_content_type(ref: str) -> Optional[str]:
    """Best-effort MIME from a media ref's extension. The object's own
    Content-Type wins at fetch time; this is the fallback."""
    _ext = ("." + ref.rsplit("?", 1)[0].rsplit(".", 1)[-1].lower()) if "." in ref else ""
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".tif": "image/tiff", ".tiff": "image/tiff", ".pdf": "application/pdf",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg",
    }.get(_ext)


@app.post("/media", tags=["catalogue"])
async def datasets_media_stream(
    req: ResolveMediaRequest,
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    """STREAM a record's media column bytes through the MCP — the SoR artifact
    never leaves this governed boundary as a URL. Same logical reference and
    same PDP as ``/resolve_media`` (read the row by key UNDER THE CALLER'S
    VISIBILITY, take the column's native ref), but instead of handing back a
    presigned/passthrough URL for the browser to fetch, the MCP fetches the
    bytes ITSELF (S3 get-object, or a server-side GET for an ``http(s)`` ref)
    and streams them back. The consumer (runtime) only ever talks to the MCP —
    the browser never touches S3 / the source storage. Source creds + network
    reach stay here, at the one boundary."""
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    source = catalogue.get_source(req.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {req.source_id!r}")

    try:
        check_visibility(source, claims)
    except AuthzError as e:
        await log_audit(
            source_id=req.source_id, query=f"media:{req.dataset_id}.{req.column}",
            claims=claims, result_count=0, allowed=False, reason=e.reason,
            op="media", source=source,
        )
        logger.warning(f"[AUTHZ] /media denied for {claims.get('user_id')}: {e.reason}")
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    ref = (await catalogue.read_media_ref(
        source_id=req.source_id, dataset_id=req.dataset_id,
        key_field=req.key_field, key_value=req.key_value, column=req.column,
    )).strip()

    ct = _media_content_type(ref) or "application/octet-stream"
    # Filename for Content-Disposition — sanitize so a crafted ref (embedded
    # quote / CR / LF) can't break the filename token or split the header set.
    raw_name = (ref.rsplit("?", 1)[0].rsplit("/", 1)[-1] or req.column) if ref else req.column
    filename = re.sub(r'[\r\n"\\]', "_", raw_name)[:200]
    # inline so the browser renders images/PDFs; the runtime adds `download` on the
    # anchor when the officer explicitly downloads.
    headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "Cache-Control": "private, max-age=60",
    }

    async def _audit_ok():
        # Record the disclosure only AFTER the bytes are confirmed fetchable, so
        # the ledger never logs a failed/aborted read as a successful egress.
        await log_audit(
            source_id=req.source_id, query=f"media:{req.dataset_id}.{req.column}",
            claims=claims, result_count=1, allowed=True, reason="media streamed",
            op="media", source=source,
        )

    if ref.startswith("http://") or ref.startswith("https://"):
        # Passthrough URL — the MCP fetches it server-side (the browser never sees
        # the source URL). SSRF guard: a media ref must not resolve to a private/
        # loopback/link-local host (Vault, cloud metadata, internal APIs).
        # Redirects are NOT followed — a redirect to a private host is the classic
        # SSRF bypass and a media ref should be a direct URL. Opt in with
        # MEDIA_ALLOW_PRIVATE_HOSTS=1 only if media legitimately lives internally.
        from rag.api_engine import _ssrf_check
        allow_private = os.getenv("MEDIA_ALLOW_PRIVATE_HOSTS", "").lower() in ("1", "true", "yes")
        ssrf_err = _ssrf_check(ref, allow_private=allow_private)
        if ssrf_err:
            logger.warning(f"[SSRF] /media refused {ref!r}: {ssrf_err}")
            raise HTTPException(status_code=400, detail=f"refused media ref: {ssrf_err}")
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=False)
        try:
            r = await client.send(client.build_request("GET", ref), stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.error(f"media fetch failed for {ref!r}: {exc}")
            raise HTTPException(status_code=502, detail=f"media fetch failed: {exc}")
        # Check status BEFORE handing the body to StreamingResponse — once bytes
        # start, 200 + headers are committed and we can no longer signal an error.
        # (>=300 also rejects the unfollowed redirect.)
        if r.status_code >= 300:
            await r.aclose(); await client.aclose()
            raise HTTPException(status_code=502, detail=f"upstream media {r.status_code}")
        await _audit_ok()

        async def _http_stream():
            try:
                async for chunk in r.aiter_bytes(65536):
                    yield chunk
            finally:
                await r.aclose()
                await client.aclose()
        return StreamingResponse(_http_stream(), media_type=ct, headers=headers)

    # s3://bucket/key OR a bare bucket key
    if ref.startswith("s3://"):
        bucket, _, key = ref[len("s3://"):].partition("/")
    else:
        bucket, key = (os.getenv("BUCKET_NAME") or ""), ref.lstrip("/")
    if not bucket or not key:
        raise HTTPException(status_code=400, detail=f"unresolvable media ref: {ref!r}")
    s3 = _get_s3_client()
    try:
        obj = await asyncio.to_thread(s3.get_object, Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 — surface as 502, don't 500
        logger.error(f"media get_object failed for s3://{bucket}/{key}: {exc}")
        raise HTTPException(status_code=502, detail=f"media fetch failed: {exc}")
    body = obj["Body"]  # botocore StreamingBody (sync)
    obj_ct = obj.get("ContentType") or ct
    await _audit_ok()

    async def _s3_stream():
        try:
            while True:
                chunk = await asyncio.to_thread(body.read, 65536)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    return StreamingResponse(_s3_stream(), media_type=obj_ct, headers=headers)


@app.post("/execute_action", response_model=ExecuteActionResponse, tags=["catalogue"])
async def datasets_execute_action(
    req: ExecuteActionRequest,
    x_user_jwt: Optional[str] = Header(default=None, alias="X-User-JWT"),
    _: None = Depends(_verify_caller),
):
    """Run a registered write action on a dataset.

    Every outcome is audited to `dept_query_audit` (op="execute_action"):
    authorisation denial, execution failure, and success — so the write
    plane has the same forensic trail the read plane (/query) has.
    """
    try:
        claims = verify_user_jwt(x_user_jwt)
    except AuthzError as e:
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    source = catalogue.get_source(req.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source {req.source_id!r}")

    # Audit context shared by every outcome. payload_fields records WHICH
    # fields were written (names only — values are deliberately not logged,
    # to keep business data / PII out of the audit collection).
    audit_extra = {
        "action_id": req.action_id,
        "dataset_id": req.dataset_id,
        "dry_run": bool(req.dry_run),
        "payload_fields": sorted((req.payload or {}).keys()),
        "idempotency_key": req.idempotency_key,
    }
    audit_query = f"execute_action {req.action_id} on {req.dataset_id}"

    # Write authorisation. NOTE: today this is the SAME visibility PDP as
    # reads — there is no write-specific permission gate yet.
    try:
        # Always run the PDP. In dev mode (AUTHZ_ENFORCE=false) verify_user_jwt
        # returns empty claims; check_visibility → _deny then logs+allows LOUDLY
        # rather than us silently skipping the gate on a falsy dict.
        check_visibility(source, claims)
    except AuthzError as e:
        await log_audit(
            source_id=req.source_id, query=audit_query, claims=claims,
            result_count=0, allowed=False, reason=e.reason,
            op="execute_action", source=source, extra=audit_extra,
        )
        logger.warning(f"[AUTHZ] /execute_action denied for {claims.get('user_id')}: {e.reason}")
        raise HTTPException(status_code=e.status_code, detail=e.reason)

    # Read-visible — now execute. catalogue.execute_action applies the
    # WRITE-authz gate (check_write_permission) on top; a denial there
    # raises AuthzError and is audited as allowed=False. Execution failures
    # are audited as ok=False. Success is audited below.
    try:
        resp = await catalogue.execute_action(req, claims)
    except AuthzError as e:
        await log_audit(
            source_id=req.source_id, query=audit_query, claims=claims,
            result_count=0, allowed=False, reason=e.reason,
            op="execute_action", source=source, extra=audit_extra,
        )
        logger.warning(
            f"[AUTHZ] /execute_action write denied for {claims.get('user_id')}: {e.reason}"
        )
        raise HTTPException(status_code=e.status_code, detail=e.reason)
    except HTTPException as e:
        await log_audit(
            source_id=req.source_id, query=audit_query, claims=claims,
            result_count=0, allowed=True, reason="execution_error",
            op="execute_action", source=source,
            extra={**audit_extra, "ok": False, "status": e.status_code,
                   "error": str(e.detail)[:300]},
        )
        raise
    except Exception as e:  # noqa: BLE001
        await log_audit(
            source_id=req.source_id, query=audit_query, claims=claims,
            result_count=0, allowed=True, reason="execution_error",
            op="execute_action", source=source,
            extra={**audit_extra, "ok": False, "error": str(e)[:300]},
        )
        raise

    result = resp.result if isinstance(resp.result, dict) else {}
    rowcount = result.get("rowcount")
    await log_audit(
        source_id=req.source_id, query=audit_query, claims=claims,
        result_count=int(rowcount) if isinstance(rowcount, int) else 0,
        allowed=True, reason="ok",
        op="execute_action", source=source,
        extra={**audit_extra, "ok": bool(resp.ok), "verb": result.get("verb")},
    )
    logger.info(
        f"[ACTION] {req.action_id} on {req.dataset_id} by "
        f"{claims.get('user_id')} (dry_run={req.dry_run}, ok={resp.ok})"
    )
    return resp
