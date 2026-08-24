# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# main.py
from vault_env_loader import load_environment_variables

# Load environment variables first (before importing other modules)
load_environment_variables()

# Suppress warnings from dependencies
import warnings
warnings.filterwarnings('ignore', category=UserWarning, message='.*validate_default.*')

# Suppress asyncio CancelledError during uvicorn shutdown (harmless)
import asyncio
import logging

# Configure logging to suppress asyncio.CancelledError warnings
logging.getLogger('asyncio').setLevel(logging.ERROR)

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
import os
import signal
import sys
from datetime import datetime
from contextlib import asynccontextmanager
import requests
from validation_utils import serialize_validation_error_details
import httpx
import ssl

# Import JWT Authentication Middleware
from citra_auth import JWTAuthMiddleware, get_current_user
from citra_mongo import get_mongo_client, get_database_name

# Import Rate Limiting Middleware
from middleware.rate_limit_middleware import limiter, CentralRateLimitMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# Custom logging filter to suppress /health access logs
class HealthFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if "/health" not in message:
            return True

        # Gunicorn JSON access logs (configured via --access-logformat)
        if '"path":"/health"' in message or '"path":"/health?' in message:
            return False

        # Common access log lines: "GET /health HTTP/1.1"
        if " /health " in message or " /health?" in message or " /health\"" in message:
            return False

        return True

# Set up logging early to capture startup issues
# Note: When running with gunicorn --capture-output, use only stdout to avoid duplicates
# The FileHandler is commented out to prevent duplicate logs in production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        # logging.FileHandler('citra_service.log', mode='a')  # Disabled - handled by gunicorn + docker
    ]
)

# Reduce noise from HTTP libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

SUPPRESS_HEALTH_LOGS = os.getenv("SUPPRESS_HEALTH_LOGS", "true").lower() == "true"

# Apply health filter for all environments (can be disabled via env)
if SUPPRESS_HEALTH_LOGS:
    for logger_name in ("uvicorn.access", "gunicorn.access"):
        logging.getLogger(logger_name).addFilter(HealthFilter())

logger = logging.getLogger(__name__)

# Add RequestValidationError handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_details = exc.errors()
    logger.error(f"❌ Validation Error for {request.url}: {error_details}")
    safe_details = serialize_validation_error_details(error_details)
    return JSONResponse(
        status_code=422,
        content={"detail": safe_details, "message": "Validation error in request payload"},
    )

# Custom middleware for handling large file uploads
# Uses pure ASGI pattern for better streaming compatibility
class LargeFileMiddleware:
    def __init__(self, app, max_upload_size: int = 100 * 1024 * 1024):  # 100MB default
        self.app = app
        self.max_upload_size = max_upload_size
        
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
            
        # Check content length for upload endpoints
        if scope["method"] == "POST":
            path = scope["path"]
            if "documents" in path:
                headers = dict(scope.get("headers", []))
                content_length = headers.get(b"content-length", b"").decode()
                if content_length and content_length.isdigit():
                    content_length = int(content_length)
                    if content_length > self.max_upload_size:
                        response = JSONResponse(
                            status_code=413,
                            content={
                                "detail": f"File too large. Maximum size allowed: {self.max_upload_size // (1024*1024)}MB, received: {content_length // (1024*1024)}MB"
                            }
                        )
                        await response(scope, receive, send)
                        return
        
        await self.app(scope, receive, send)

# V1 endpoints removed - using V2 APIs only
# PERSONAL VAULT removed — document_manager, the files / chunked-documents /
# document-proxy APIs and their Milvus chunk service went with it. Main chat is
# enterprise-only (dept MCP + the SOP library); nothing uploads to a personal
# vault any more. See docs/open-source-release-plan.md.
from chat import router as chat_router
# Milvus router removed - migrated to Milvus/Zilliz
from query import router as query_router
from bucket import router as bucket_router

# COMPOSER / PRESENTATION / PRINTABLE removed 2026-08-08 (Phase 0 OSS split).
# The presentation, visual-report and Word-style-report composers left the
# Decision App product; they are republished from their own repo. See
# docs/open-source-release-plan.md. Restore point: tag `pre-oss-split`.


# CACHE HEALTH: Monitor Redis + Local Cache fallback system
try:
    from api.cache_health import router as cache_health_router
    CACHE_HEALTH_AVAILABLE = True
    logger.info("?? Cache Health API imported successfully")
except ImportError as e:
    logger.warning(f"?? Cache Health API not available: {e}")
    CACHE_HEALTH_AVAILABLE = False

# FOLDER MANAGEMENT removed — /api/folders/* (personal-vault folder CRUD), a
# product-scope removal. dept_library.py below is independent (confirmed: it
# only shares the collection name "folders" as prose in a comment, no code
# import) and personal_data_tool.py/streaming_response.py's document-search
# tool has no import dependency on this file either — folder_id was always
# just an optional metadata filter that widens to "search everything" when
# unset, which main chat has done unconditionally since PERSONAL_VAULT_ENABLED
# went false. See docs/open-source-release-plan.md.

# DEPT SOP LIBRARY: department-owned document folders (SOP Library slice 1)
from dept_library import router as dept_library_router

# SEMANTIC SEARCH: the one platform-side RAG surface (RAG short-circuit #6)
from semantic_search_api import router as semantic_search_router

# PROJECT MANAGEMENT removed 2026-08-08 (Phase 0 OSS split) — republished from
# its own repo. KNOWLEDGE GRAPH removed in the same pass and is NOT republished;
# the 8 files that existed only in the citra-ai-oss draft are preserved on branch
# `archive/oss-knowledge-graph`. See docs/open-source-release-plan.md.

# SUBSCRIPTION: Subscription and usage tracking endpoints removed for enterprise licensing

# DRAFT / TEMPLATES removed — legal/business document drafting, a product-scope
# removal (the feature was live, not dead) done alongside Enterprise Branding,
# Teams, and folder_management. See docs/open-source-release-plan.md.

# PROJECT / RESOURCE MANAGEMENT removed 2026-08-08 (Phase 0 OSS split) —
# republished from their own repo. See docs/open-source-release-plan.md.

# PAGE BUILDER removed 2026-08-08 (Phase 0 OSS split). Not republished — the
# Pages surface was never developed. See docs/open-source-release-plan.md.

# ENTERPRISE BRANDING removed — custom_domain_api.py deleted. Its two
# non-branding routes (/s/{share_token}, /api/admin/check) were relocated
# above rather than removed; see the comment there.

from dotenv import load_dotenv
load_dotenv()

# Global flag for graceful shutdown
shutdown_event = asyncio.Event()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Enhanced lifespan context manager with improved error handling and graceful shutdown
    """
    logger.info("?? Citra AI Service starting up...")
    
    # Startup
    try:
        await startup_services()
        # Prometheus: start background samplers for circuit breaker state +
        # discovery staleness. Non-fatal if the metrics module fails to import.
        try:
            from metrics import start_samplers
            start_samplers()
        except Exception as e:
            logger.warning(f"?? Metrics samplers not started: {e}")
        logger.info("? Citra AI Service startup completed successfully")
        yield
    except Exception as e:
        logger.error(f"? Startup failed: {e}", exc_info=True)
        raise
    finally:
        # Shutdown
        logger.info("?? Citra AI Service shutting down...")
        try:
            try:
                from metrics import stop_samplers
                stop_samplers()
            except Exception:
                pass
            await shutdown_services()
            logger.info("? Citra AI Service shutdown completed successfully")
        except Exception as e:
            logger.error(f"? Shutdown error: {e}", exc_info=True)

async def startup_services():
    """
    Initialize all services with improved error handling and parallel execution
    """

    # Token revocation: wire citra-auth's jti seam to the shared revoked_tokens
    # blocklist (written by Citra-User-Service on impersonation-revoke /
    # demotion). Without this the middleware logs a one-time "revocation NOT
    # enforced" warning and revoked tokens keep working in this service.
    # Cache-fronted like Citra-User-Service's revocationService: "revoked"
    # never un-revokes so it caches long; "not revoked" caches briefly so a
    # fresh revoke takes effect within NEG_TTL seconds.
    import time as _time
    from citra_auth import register_revocation_checker
    from citra_mongo import get_async_mongo_client, MONGODB_DATABASE

    NEG_TTL = 30.0
    POS_TTL = 3600.0
    _revocation_cache = {}  # jti -> (revoked, valid_until_monotonic)

    async def _is_token_revoked(jti: str) -> bool:
        now = _time.monotonic()
        hit = _revocation_cache.get(jti)
        if hit and hit[1] > now:
            return hit[0]
        collection = get_async_mongo_client()[MONGODB_DATABASE]["revoked_tokens"]
        revoked = await collection.find_one({"jti": jti}, {"_id": 1}) is not None
        if len(_revocation_cache) > 10_000:
            expired = [k for k, v in _revocation_cache.items() if v[1] <= now]
            for k in expired:
                _revocation_cache.pop(k, None)
        _revocation_cache[jti] = (revoked, now + (POS_TTL if revoked else NEG_TTL))
        return revoked

    register_revocation_checker(_is_token_revoked)

    # Define initialization tasks that can run in parallel
    async def init_deep_research():
        try:
            logger.info("?? Deep Research Service disabled - using multi-hop RAG approach")
            logger.info("?? Research Session Manager disabled - using multi-hop RAG approach")
            
            # Deep research system has been replaced with multi-hop RAG
            logger.info("?? Multi-hop RAG is the active research system")
            return True
                
        except Exception as e:
            logger.warning(f"?? Deep Research system disabled: {e}")
            logger.warning("?? Using multi-hop RAG approach")
            return False
    
    # init_workflows() removed — workflows moved to citra-workflow service
    # (Phase J split). See citra-workflow/ for the engine; Citra-Service
    # owns nothing workflow-related anymore.

    async def init_cache():
        try:
            logger.info("??? Concept cache disabled - module removed")
            logger.info("??? Cache will use MongoDB-only storage")
            return True
            
        except Exception as e:
            logger.warning(f"??? Cache initialization note: {e}")
            logger.warning("??? Cache will fall back to MongoDB-only storage")
            return False
    
    async def init_usage_manager():
        try:
            logger.info("💰 Initializing Usage Tracking Service (Credit-based Billing)...")
            from middleware.credit_check_middleware import get_usage_service, initialize_pricing_cache
            
            # Initialize usage service and pricing cache (both are synchronous)
            service = get_usage_service()  # FIXED: Removed await - this is a sync function
            initialize_pricing_cache()  # FIXED: Call sync function directly
            
            logger.info("✅ Usage Tracking Service initialized successfully")
            return True
            
        except Exception as e:
            logger.warning(f"⚠️ Usage tracking initialization failed: {e}")
            logger.warning(f"⚠️ Credit checking may fail until pricing is loaded")
            return False

    # init_template_service removed — Draft/Templates feature deleted.
    # init_vault_sharing_service removed too while here: it imported
    # api.vault_sharing / services.vault_sharing_service, both already deleted
    # in an earlier pass this session (the backend cleanup commit). It was
    # try/excepted so it never crashed the app — only caught at runtime when
    # actually awaited, which is why a module-level boot test never caught it.

    async def init_authorization_service():
        """Initialize Centralized Authorization Service"""
        try:
            logger.info("🔐 Initializing Authorization Service...")
            from services.authorization_service import initialize_authorization_service
            from citra_mongo import get_async_mongo_client, MONGODB_DATABASE
            
            # Initialize authorization service with async MongoDB client
            async_mongo_client = get_async_mongo_client()
            db = async_mongo_client[MONGODB_DATABASE]
            await initialize_authorization_service(db)
            
            logger.info("✅ Authorization Service initialized successfully")
            return True
            
        except Exception as e:
            logger.warning(f"⚠️ Authorization Service initialization failed: {e}")
            logger.warning(f"⚠️ Resource authorization will fall back to legacy checks")
            return False
    
    # Dgraph schema initialization removed - use scripts/setup_dgraph_schema.py instead
    # Run: python scripts/setup_dgraph_schema.py [--force] [--check-only]
    
    # Run initialization tasks in parallel for faster startup
    # Note: Some services have dependencies and must be initialized in order
    logger.info("🚀 Starting parallel service initialization...")
    start_time = asyncio.get_event_loop().time()
    
    # Phase 1: Initialize services that have no dependencies
    results_phase1 = await asyncio.gather(
        init_deep_research(),
        # init_workflows() removed — workflows live in citra-workflow service.
        init_cache(),
        init_usage_manager(),
        init_authorization_service(),
        return_exceptions=True
    )
    
    results = list(results_phase1)

    end_time = asyncio.get_event_loop().time()
    total_time = end_time - start_time
    
    # Must stay positionally in sync with the gather() call above — zip() truncates silently, it doesn't error on mismatch.
    service_names = ["MongoDB Optimizations", "Vector Chunk Service", "Deep Research", "Cache", "Usage Manager", "Authorization Service"]
    successful = sum(1 for r in results if r is True)
    
    logger.info(f"? Parallel initialization completed in {total_time:.2f}s")
    logger.info(f"? {successful}/{len(service_names)} services initialized successfully")
    
    for i, (name, result) in enumerate(zip(service_names, results)):
        if isinstance(result, Exception):
            logger.error(f"? {name} failed with exception: {result}")
        elif result is False:
            logger.warning(f"?? {name} initialization failed")
        else:
            logger.info(f"? {name} initialized successfully")
    
    # Initialize web_content_fetcher with proxy function reference
    try:
        from services.web_content_fetcher import set_proxy_function
        set_proxy_function(proxy_webpage)
        logger.info("✅ Web content fetcher initialized with proxy function")
    except Exception as e:
        logger.error(f"❌ Could not initialize web content fetcher: {e}")
    
    # Note: Metadata sync scheduler is now a separate service
    # Run it using: python metadata-sync-service/main.py
    # It should NOT be started with the main FastAPI app to avoid conflicts
    logger.info("📊 Metadata sync scheduler runs as a separate service")

    # Workflow scheduler now lives in Citra-Worker. Citra-Service no
    # longer starts it on lifespan; the worker process owns the
    # leader-elected APScheduler so a stuck cron-fire can't stall
    # request workers.

    # Action-Chat resource sweeper now lives in action-chat-service/.

# Deep research initialization function removed - using multi-hop RAG approach

# Advanced workflow initialization function removed - using multi-hop RAG approach

async def shutdown_services():
    """
    Gracefully shutdown all services
    """
    # Set shutdown event
    shutdown_event.set()
    
    # Flush all pending credit buffers before shutdown
    # (Buffer system removed — direct MongoDB writes now, nothing to flush)
    logger.info("💳 No credit buffers to flush (direct MongoDB writes)")
    
    # Shutdown Async Usage Manager
    try:
        logger.info("?? Usage Manager disabled in enterprise mode")
        # Usage manager removed for enterprise licensing model  
        logger.info("?? Usage manager shutdown completed")
    except Exception as e:
        logger.warning(f"?? Usage manager disabled: {e}")
    
    # Note: Metadata sync scheduler is a separate service, no need to stop here
    logger.info("📊 Metadata sync scheduler is managed separately")

    # Workflow scheduler is owned by Citra-Worker now — no shutdown
    # hook needed in Citra-Service.
    
    # Close MongoDB connections
    try:
        logger.info("??? Closing MongoDB connections...")
        from citra_mongo import close_all_connections
        close_all_connections()
        logger.info("??? MongoDB connections closed successfully")
    except Exception as e:
        logger.warning(f"??? MongoDB connection cleanup error: {e}")
    
    # Close Milvus singleton client
    try:
        from config.milvus_config import close_milvus_client
        close_milvus_client()
    except Exception as e:
        logger.warning(f"Milvus client cleanup error: {e}")

    # Add any other cleanup logic here
    logger.info("?? Cleaning up resources...")
    
    # Give services time to finish current operations
    await asyncio.sleep(1.0)

def setup_signal_handlers():
    """
    Set up signal handlers for graceful shutdown
    """
    def signal_handler(signum, frame):
        logger.info(f"?? Received signal {signum}, initiating graceful shutdown...")
        shutdown_event.set()
    
    # Only set up signal handlers on Unix-like systems
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, signal_handler)

# Initialize FastAPI app with lifespan context manager and increased limits
_is_production = os.getenv("ENVIRONMENT", "dev").lower() in ("prod", "production")

# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
try:
    from observability import init_sentry, install_trace_id_middleware
    init_sentry("citra-service")
except Exception as _exc:
    logger.warning(f"[sentry] init skipped: {_exc}")
    install_trace_id_middleware = None  # type: ignore[assignment]

app = FastAPI(
    root_path="/citra-ai",
    lifespan=lifespan,
    title="Citra AI API",
    description="Intelligent personal knowledge management system",
    version="2.0.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# Per-request trace_id tagging (correlates Sentry issues ↔ Loki logs)
if install_trace_id_middleware is not None:
    try:
        install_trace_id_middleware(app)
    except Exception as _exc:
        logger.warning(f"[sentry] trace_id middleware skipped: {_exc}")

# Distributed tracing — auto-instruments FastAPI inbound + httpx outbound,
# and propagates X-Request-ID + W3C traceparent on outbound calls (which
# is how sandbox-host picks up the trace context for cross-service
# correlation).
try:
    from citra_service_utils import (
        setup_tracing as _setup_tracing,
        request_id_middleware as _request_id_middleware,
    )
    app.middleware("http")(_request_id_middleware)
    excluded_urls = r"/health" if SUPPRESS_HEALTH_LOGS else None
    _setup_tracing(app, service_name="citra-service", excluded_urls=excluded_urls)
except ImportError:
    logger.warning("citra-service-utils not installed; distributed tracing disabled")

# Register exception handlers
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# Configure maximum file upload size from environment
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE_MB", "100")) * 1024 * 1024  # Default 100MB
logger.info(f"?? Maximum upload size configured: {MAX_UPLOAD_SIZE // (1024*1024)}MB")

# Add large file middleware
app.add_middleware(LargeFileMiddleware, max_upload_size=MAX_UPLOAD_SIZE)

# Add Rate Limiting Middleware (Central - route-based config)
# All rate limits are defined in middleware/rate_limit_middleware.py RATE_LIMIT_ROUTES
# No per-endpoint decorators or response: Response params needed
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CentralRateLimitMiddleware)

# Add JWT Authentication Middleware (before CORS)
# Public-share routes must bypass JWT auth: a read-only share link is, by
# definition, viewed by recipients who are not logged in. This covers every
# share type — presentation, printable, report, chat, diagram — because the
# /s/{token} viewer (custom_domain_api.unified_public_share) is a single
# unified route. Mirrors the skip list in AuthorizationMiddleware.
try:
    app.add_middleware(
        JWTAuthMiddleware,
        extra_public_patterns=[
            "/s/",                                  # unified public share viewer
            "/citra-ai/s/",                         # same, behind /citra-ai path prefix
            "/api/public-share/public/",            # public share render API
            "/citra-ai/api/public-share/public/",   # same, behind /citra-ai path prefix
        ],
    )
    logger.info("?? JWT Authentication Middleware registered successfully")
except Exception as e:
    logger.error(f"? Failed to register JWT Authentication Middleware: {e}")
    raise RuntimeError("JWT Authentication Middleware is required for security")

logger.info("ENVIRONMENT: " + os.getenv("ENVIRONMENT", "production"))

# CORS configuration — origins from CORS_ALLOWED_ORIGINS env var (comma-separated)
# Default: localhost only. Production deployments set this via env/Vault.
_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else []
if not _is_production:
    _dev_origins = [
        "http://localhost:8081",
        "http://localhost:8085",
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8085",
    ]
    for o in _dev_origins:
        if o not in _cors_origins:
            _cors_origins.append(o)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)

# Include routers with V2 API endpoints only (no prefix changes to maintain compatibility)
app.include_router(chat_router, prefix="", tags=["Chat"])
# Milvus router removed - migrated to Milvus/Zilliz
app.include_router(query_router, prefix="", tags=["Query"])
# Composer / Presentation / Printable routers removed — see the import block.

app.include_router(bucket_router, prefix="", tags=["S3 Storage"])
app.include_router(dept_library_router, prefix="", tags=["Dept SOP Library"])
app.include_router(semantic_search_router, prefix="", tags=["Semantic Search"])

# Prometheus /metrics endpoint — public (allow-listed in JWTAuthMiddleware).
try:
    from metrics import mount_metrics
    mount_metrics(app)
    logger.info("📈 Prometheus /metrics endpoint mounted")
except Exception as _e:
    logger.warning(f"📈 /metrics endpoint not mounted: {_e}")

# SSE Credit Alerts removed — license model, no credit enforcement

# Error Report — UI crash reports emailed to support
try:
    from api.error_report import router as error_report_router
    app.include_router(error_report_router, prefix="", tags=["Error Report"])
    logger.info("🐛 Error Report endpoint registered")
except Exception as e:
    logger.warning(f"🐛 Error Report not available: {e}")
# Usage tracking and subscription endpoints removed for enterprise licensing

# Only include Cache Health router if available
if CACHE_HEALTH_AVAILABLE:
    app.include_router(cache_health_router, prefix="", tags=["Cache Health"])
    logger.info("🔍 Cache Health API registered")


# Internal code-exec route (consumed by smart-app-service runtime for
# tools_v2 kind=code_exec). Reuses the quick-chat sandbox pool. Auth
# uses the standard JWT middleware — smart-app-service forwards the
# end-user's bearer token in the proxy call.
try:
    from code_exec_routes import router as code_exec_router
    app.include_router(code_exec_router, prefix="", tags=["Internal Code Exec"])
    logger.info("🐍 Internal Code Exec API registered")
except Exception as exc:  # noqa: BLE001
    logger.warning(f"⚠️ Code Exec router not registered: {exc}")

# RESOURCE SHARING, GDPR ERASURE and PUBLIC SHARE removed — this service's
# role is MCP-backed chat (/query/stream); none of those surfaces sit on that
# path. Sharing pulled in services/sharing_migration_service; public share was
# the sole consumer of services/image_processor. All removed together.

# ENTITY EXTRACTION (mindmap) and DIAGRAM GENERATION removed 2026-08-08
# (Phase 0 OSS split) along with Knowledge Graph and Reader — the whole hidden
# "Visualize Data" category. Not republished.

# PRESENTATION ANALYTICS removed 2026-08-08 (Phase 0 OSS split).

# READER: the document viewer. KEPT -- Citra-UI routes EVERY uploaded document
# (PDF/Word/text/transcript) through it, so removing it left users unable to
# open anything they had uploaded. It is the viewer, not a "Visualize Data"
# extra; only its home-panel card is gone.
try:
    from api.reader import router as reader_router
    app.include_router(reader_router, prefix="/api/reader", tags=["Document Reader"])
    logger.info("Document reader API registered")
except ImportError as e:
    logger.warning(f"Document reader API not available: {e}")

# TEAM/WORKSPACE MANAGEMENT removed — "no use of this, remove fully." This is
# org/dept member management's sibling collaboration product, not the org/dept
# provisioning itself (that lives elsewhere — see docs/open-source-release-plan.md).

# Clean architecture: folder routing handled at UI level via FolderContentService
# No need for unified folder documents API - each API serves its purpose

# Project / Resource Management routers removed — see the import block.

# OPENAI-COMPATIBLE API removed — nothing calls this service as if it were an
# OpenAI endpoint; chat goes through /query/stream.

# QUICK CHAT and ACTION CHAT (Operations Analytics) removed 2026-08-08
# (Phase 0 OSS split). action-chat-service/ was deleted outright.
#
# NOTE: Dockerfile.quick-chat-sandbox and services/code_executor.py STAY — the
# internal code-exec route below still uses that sandbox pool, and
# smart-app-service drives it for tools_v2 kind=code_exec. The image name is now
# a misnomer; renaming it is deliberate churn we are not taking on here.


# ── Workflow engine — REMOVED FROM Citra-Service (Phase J split) ──
# citra-workflow now runs as its own FastAPI service. The ALB / nginx
# routes /api/workflows/* and /api/dept-sources/* directly to it. The UI
# and smart-app-service talk to it over HTTP via WORKFLOW_SERVICE_URL.
# No code mounted here on purpose — keeping Citra-Service's event loop
# free for chat / files / notes / presentations.

# SANDBOX: pre-warm container pool so the first execute_code call doesn't pay
# the docker create+start latency on the user's request path.
try:
    from services.sandbox_pool import WarmContainerPool

    @app.on_event("startup")
    async def _prewarm_sandbox_pool():
        try:
            count = await WarmContainerPool.get().warmup()
            logger.info(f"🔥 Sandbox warm pool: pre-warmed {count} container(s)")
        except Exception as e:
            logger.warning(f"🔥 Sandbox warm pool prewarm skipped: {e}")

    @app.on_event("shutdown")
    async def _shutdown_sandbox_pool():
        try:
            await WarmContainerPool.get().shutdown()
        except Exception as e:
            logger.warning(f"🔥 Sandbox warm pool shutdown error: {e}")
except Exception as e:
    logger.warning(f"🔥 Sandbox warm pool not available: {e}")

# Pre-compile regex patterns for performance (avoid recompiling on every request)
# This optimization reduces HTML processing time by 40-60%
import re
from urllib.parse import urlparse, urljoin, quote
import time
import httpx

SCRIPT_TAG_PATTERN = re.compile(r'<script[^>]+src=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)
LINK_TAG_PATTERN = re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*/?>', re.IGNORECASE)
CSS_URL_PATTERN = re.compile(r'url\(([^)]+)\)')
SRC_ATTR_PATTERN = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
HREF_ATTR_PATTERN = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)

# Playwright Render Service configuration
PLAYWRIGHT_SERVICE_URL = os.getenv("PLAYWRIGHT_SERVICE_URL", "http://localhost:3001")
PLAYWRIGHT_SERVICE_ENABLED = os.getenv("PLAYWRIGHT_SERVICE_ENABLED", "true").lower() == "true"

# Patterns that indicate bot protection / blocked response
BOT_PROTECTION_PATTERNS = [
    'cloudflare',
    'captcha',
    'challenge-running',
    'cf-browser-verification',
    'just a moment',
    'checking your browser',
    'ddos-guard',
    'access denied',
    'forbidden',
    'blocked',
    'security check',
    'verify you are human',
    'please wait while we verify',
]

def _is_blocked_response(status_code: int, content: str = "") -> bool:
    """Check if response indicates bot protection or blocking"""
    # Status code checks
    if status_code in [403, 503, 429]:
        return True
    
    # Content pattern checks (case insensitive)
    content_lower = content.lower()[:5000]  # Check first 5KB only for performance
    for pattern in BOT_PROTECTION_PATTERNS:
        if pattern in content_lower:
            return True
    
    return False

async def _call_playwright_service(url: str, output_format: str = "html", token: str = None) -> tuple[bool, any]:
    """
    Call the Playwright render service as fallback.
    
    Returns:
        (success: bool, response_or_error: Response or error message)
    """
    try:
        playwright_url = f"{PLAYWRIGHT_SERVICE_URL}/render"
        params = {
            "url": url,
            "output_format": output_format,
            "wait_for": "networkidle",
            "inject_base_tag": "true",
            "inject_interceptor": "true"
        }
        
        logger.info(f"🎭 Calling Playwright service for: {url} (format: {output_format})")
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(playwright_url, params=params)
            
            if resp.status_code == 200:
                render_time = resp.headers.get("X-Render-Time-Ms", "unknown")
                logger.info(f"✅ Playwright rendered successfully in {render_time}ms: {url[:60]}")
                
                content_type = resp.headers.get("Content-Type", "text/html")
                
                return True, Response(
                    content=resp.content,
                    media_type=content_type,
                    headers={
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET',
                        'X-Frame-Options': 'ALLOWALL',
                        'Content-Security-Policy': 'frame-ancestors *',
                        'X-Rendered-By': 'playwright',
                        'X-Render-Time-Ms': render_time
                    }
                )
            else:
                error_detail = resp.text[:200]
                logger.error(f"❌ Playwright service returned {resp.status_code}: {error_detail}")
                return False, f"Playwright service error: {resp.status_code}"
                
    except httpx.TimeoutException:
        logger.error(f"⏱️ Playwright service timeout for: {url}")
        return False, "Playwright service timeout"
    except httpx.ConnectError:
        logger.warning(f"⚠️ Playwright service not available at {PLAYWRIGHT_SERVICE_URL}")
        return False, "Playwright service not available"
    except Exception as e:
        logger.error(f"❌ Playwright service error: {str(e)}")
        return False, str(e)

# Proxy endpoint for web pages (bypass CORS for iframe rendering)
# Proxy endpoint for web pages (bypass CORS for iframe rendering)
def get_ssl_context():
    """Create a standard SSL context for HTTPS requests."""
    ctx = ssl.create_default_context()
    return ctx

@app.get("/proxy")
async def proxy_webpage(
    request: Request, 
    url: str, 
    token: str = None, 
    fast: bool = False,
    output_format: str = "html",  # "html" or "pdf" - passed to Playwright fallback
    use_playwright: bool = False  # Force Playwright rendering (skip simple proxy)
):
    """
    Two-tier proxy endpoint to fetch webpages and bypass CORS restrictions.
    
    Tier 1: Simple HTTP request (fast, ~100ms)
    Tier 2: Playwright headless browser (fallback for blocked sites, ~2-4s)
    
    Performance modes:
    - fast=False (default): Full URL rewriting for scripts/CSS/fonts (~300-800ms for large pages)
    - fast=True: Skip URL rewriting, rely on <base> tag only (~50-150ms - 2-5x faster!)
    
    Args:
        url: Target webpage URL to proxy
        token: JWT token for authentication (optional)
        fast: Enable fast mode - skips URL rewriting, uses base tag (recommended for most sites)
        output_format: Output format for Playwright fallback: "html" or "pdf"
        use_playwright: Force Playwright rendering (skip simple proxy attempt)
    
    Returns:
        Proxied HTML/PDF with CORS headers and link interceptor script
    """
    
    start_time = time.time()
    
    try:
        # SSRF protection — block requests to internal/private networks
        from security.url_validator import is_safe_url
        if not is_safe_url(url):
            logger.warning(f"🛡️ [SSRF] Blocked proxy request to internal URL: {url}")
            return JSONResponse({"error": "URL not allowed"}, status_code=403)

        # Validate token if provided via query parameter
        if token:
            try:
                import jwt
                jwt_secret = os.getenv("JWT_SECRET")
                jwt.decode(jwt=token, key=jwt_secret, algorithms=["HS256"])
            except Exception as e:
                logger.warning(f"🔒 Invalid token in proxy request: {str(e)}")
                return JSONResponse(
                    content={"error": "Invalid or expired token"},
                    status_code=401
                )
        
        # Detect PDF URLs (by extension or query params) — PDFs need special handling
        # Playwright cannot navigate to direct PDF URLs (browser triggers download → net::ERR_ABORTED)
        url_lower = url.lower()
        is_likely_pdf = (
            url_lower.endswith('.pdf') or
            'format=pdf' in url_lower or
            'type=pdf' in url_lower
        )
        
        # Force PDF URLs through Tier 1 simple HTTP (never start with Playwright)
        if is_likely_pdf and use_playwright:
            logger.info(f"📄 PDF URL detected — forcing Tier 1 simple HTTP first: {url}")
            use_playwright = False  # Override: PDFs must use simple HTTP, Playwright is fallback only
        
        # If user explicitly requested Playwright or PDF output, go directly to Playwright
        # With reverse fallback: if Playwright fails, try Tier 1 simple HTTP
        if use_playwright or output_format == "pdf":
            if PLAYWRIGHT_SERVICE_ENABLED:
                logger.info(f"🎭 Direct Playwright request for: {url} (format: {output_format})")
                success, result = await _call_playwright_service(url, output_format, token)
                if success:
                    return result
                else:
                    # Reverse fallback: Playwright failed → try Tier 1 simple HTTP
                    logger.warning(f"⚠️ Playwright failed, trying Tier 1 simple HTTP as fallback: {url}")
                    # Fall through to Tier 1 below instead of returning error
            else:
                logger.warning(f"⚠️ Playwright not enabled, using Tier 1 simple HTTP: {url}")
                # Fall through to Tier 1 below
        
        logger.info(f"🌐 [Tier 1] Simple proxy for: {url}")
        
        # Parse URL for Referer header
        parsed_target = urlparse(url)
        target_origin = f"{parsed_target.scheme}://{parsed_target.netloc}"
        
        # Use more complete browser-like headers to avoid 403 Forbidden
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            # Avoid br (brotli) to ensure requests auto-decompresses; gzip/deflate are handled automatically
            'Accept-Encoding': 'gzip, deflate',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'Referer': target_origin,
            'Origin': target_origin,
        }
        
        # Use httpx for async streaming request logic
        # verify=get_ssl_context() handles older SSL/TLS configurations (like legacy renegotiation)
        client = httpx.AsyncClient(verify=get_ssl_context(), follow_redirects=True, timeout=30.0)
        try:
            req = client.build_request("GET", url, headers=headers)
            response = await client.send(req, stream=True)
            
            # Check if response is blocked (403, bot protection, etc.)
            response_text = ""
            if _is_blocked_response(response.status_code):
                logger.warning(f"⚠️ [Tier 1] Blocked response ({response.status_code}) for: {url}")
                await response.aclose()
                await client.aclose()
                
                # Fallback to Playwright (Tier 2) — but NOT for PDF URLs
                # Playwright cannot navigate to PDFs (triggers download → ERR_ABORTED)
                if is_likely_pdf:
                    logger.warning(f"📄 [Tier 1] PDF blocked ({response.status_code}) — cannot use Playwright for PDFs: {url}")
                    return JSONResponse(
                        content={"error": f"PDF access restricted by source server (HTTP {response.status_code}). The PDF host is blocking direct downloads."},
                        status_code=403
                    )
                
                if PLAYWRIGHT_SERVICE_ENABLED and not is_likely_pdf:
                    logger.info(f"🎭 [Tier 2] Falling back to Playwright for blocked site: {url}")
                    success, result = await _call_playwright_service(url, output_format, token)
                    if success:
                        return result
                    else:
                        logger.error(f"❌ [Tier 2] Playwright also failed: {result}")
                        return JSONResponse(
                            content={"error": f"Site blocked and Playwright failed: {result}"},
                            status_code=403
                        )
                else:
                    return JSONResponse(
                        content={"error": "Site blocked and Playwright service not enabled"},
                        status_code=403
                    )
            
            # Check for HTTP errors and handle fallback safely
            if response.is_error:
                # We must read the response body asynchronously before raising,
                # otherwise str(e) during logging will try to read it synchronously and fail
                await response.aread()
                response.raise_for_status()
            
            # Get content type
            content_type = response.headers.get('Content-Type', 'text/html')
            
            # Track if content was already read by bot detection
            content_bytes = None
            
            # Soft bot detection: check HTML body for Cloudflare/bot protection patterns
            # Some sites return 200 OK with a challenge page instead of actual content
            if 'text/html' in content_type and not is_likely_pdf:
                # Peek at first 5KB to check for bot protection patterns
                peek_bytes = await response.aread()
                try:
                    peek_text = peek_bytes[:5000].decode('utf-8', errors='replace')
                except Exception:
                    peek_text = ""
                
                if _is_blocked_response(200, peek_text):
                    logger.warning(f"⚠️ [Tier 1] Soft-blocked (200 + bot patterns) for: {url}")
                    await response.aclose()
                    await client.aclose()
                    
                    if PLAYWRIGHT_SERVICE_ENABLED and not is_likely_pdf:
                        logger.info(f"🎭 [Tier 2] Falling back to Playwright for soft-blocked site: {url}")
                        success, result = await _call_playwright_service(url, output_format, token)
                        if success:
                            return result
                        else:
                            logger.error(f"❌ [Tier 2] Playwright also failed: {result}")
                    
                    # Return the original content as fallback (better than nothing)
                    return Response(
                        content=peek_bytes,
                        media_type='text/html; charset=utf-8',
                        headers={
                            'Access-Control-Allow-Origin': '*',
                            'X-Bot-Detection': 'soft-blocked',
                        }
                    )
                
                # Content already read — use peek_bytes for further processing
                # (response.aread() consumed the stream, so we use peek_bytes below)
                content_bytes = peek_bytes
            
            # For HTML content, we MUST buffer to rewrite URLs (scripts/CSS)
            if 'text/html' in content_type:
                try:
                    # Read full content for HTML processing
                    # content_bytes may already be populated by soft-bot-detection peek above
                    if content_bytes is None:
                        content_bytes = await response.aread()
                    try:
                        # Attempt to decode content
                        encoding = response.encoding or 'utf-8'
                        response_text = content_bytes.decode(encoding, errors='replace')
                    except Exception:
                        response_text = content_bytes.decode('utf-8', errors='replace')
                finally:
                    await response.aclose()
                    await client.aclose()
                
                parse_start = time.time()
                
                # Parse the URL to get the base
                parsed_url = urlparse(url)
                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                
                html_content = response_text
                
                # Build ABSOLUTE proxy URL prefix
                proxy_host = str(request.base_url).rstrip('/')
                # Upgrade to HTTPS if the request came through a known HTTPS origin
                if proxy_host.startswith("http://") and os.getenv("FORCE_HTTPS", "").lower() == "true":
                    proxy_host = proxy_host.replace("http://", "https://")
                proxy_base = f"{proxy_host}/proxy?token={token}&url=" if token else f"{proxy_host}/proxy?url="
                
                # FAST MODE: Skip URL rewriting, rely on base tag
                if not fast:
                    rewrite_start = time.time()
                    
                    def make_absolute_url(original_url):
                        if original_url.startswith(('data:', 'blob:', 'javascript:', '#')):
                            return None
                        if original_url.startswith('//'):
                            return f"{parsed_url.scheme}:{original_url}"
                        elif original_url.startswith('/'):
                            return f"{base_url}{original_url}"
                        elif not original_url.startswith(('http://', 'https://')):
                            return urljoin(url, original_url)
                        return original_url
                    
                    def rewrite_script(match):
                        original_url = match.group(1)
                        absolute_url = make_absolute_url(original_url)
                        if absolute_url is None:
                            return match.group(0)
                        proxied_url = f"{proxy_base}{quote(absolute_url, safe='')}"
                        return match.group(0).replace(original_url, proxied_url)
                    
                    html_content = SCRIPT_TAG_PATTERN.sub(rewrite_script, html_content)
                    
                    def rewrite_link(match):
                        tag = match.group(0)
                        tag_lower = tag.lower()
                        if ('stylesheet' in tag_lower or 'as="font"' in tag_lower or 
                            "as='font'" in tag_lower or ('preload' in tag_lower and 'font' in tag_lower)):
                            original_url = match.group(1)
                            absolute_url = make_absolute_url(original_url)
                            if absolute_url is None:
                                return match.group(0)
                            proxied_url = f"{proxy_base}{quote(absolute_url, safe='')}"
                            return tag.replace(original_url, proxied_url)
                        return tag
                    
                    html_content = LINK_TAG_PATTERN.sub(rewrite_link, html_content)
                    logger.info(f"⚡ URL rewriting took: {(time.time() - rewrite_start)*1000:.0f}ms")
                
                # Inject interactive scripts and base tag
                link_interceptor_script = '''
<script>
(function() {
    document.addEventListener('click', function(e) {
        var link = e.target.closest('a');
        if (link && link.href) {
            e.preventDefault();
            e.stopPropagation();
            var isDownload = link.hasAttribute('download') || /\\.(pdf|doc|docx|xls|xlsx|zip|rar|mp3|mp4)(\\?|$)/i.test(link.href);
            if (window.parent !== window) {
                window.parent.postMessage({
                    type: isDownload ? 'PROXY_DOWNLOAD_LINK' : 'PROXY_LINK_CLICK',
                    url: link.href
                }, '*');
            } else {
                window.open(link.href, '_blank');
            }
            return false;
        }
    }, true);
    document.addEventListener('submit', function(e) {
        e.preventDefault();
        var form = e.target;
        var formData = {};
        form.querySelectorAll('input, select, textarea').forEach(function(el) {
            if (el.name && (el.type !== 'checkbox' && el.type !== 'radio' || el.checked)) {
                formData[el.name] = el.value || '';
            }
        });
        if (window.parent !== window) {
            window.parent.postMessage({
                type: 'PROXY_FORM_SUBMIT',
                action: form.action || window.location.href,
                method: form.method || 'GET',
                formData: formData
            }, '*');
        }
        return false;
    }, true);
    window.addEventListener('message', function(e) {
        if (e.data && e.data.type === 'EXTRACT_PAGE_CONTENT') {
            try {
                var clone = document.body.cloneNode(true);
                ['script','style','nav','header','footer','aside','iframe','noscript','svg'].forEach(function(tag) {
                    clone.querySelectorAll(tag).forEach(function(el) { el.remove(); });
                });
                var main = clone.querySelector('main, [role="main"]');
                if (!main) {
                    var articles = clone.querySelectorAll('article');
                    if (articles.length > 0) {
                        main = document.createElement('div');
                        articles.forEach(function(a) { main.appendChild(a.cloneNode(true)); });
                    }
                }
                if (!main) {
                    main = clone.querySelector('.content, #content, .article, .post, .entry-content') || clone;
                }
                var text = main.textContent || '';
                text = text.replace(/\n\\s*\n\\s*\n+/g, '\n\n').replace(/ +/g, ' ').trim();
                var maxChars = 1250000;
                if (text.length > maxChars) {
                    text = text.substring(0, maxChars) + '\n\n[Content truncated...]';
                }
                window.parent.postMessage({
                    type: 'PAGE_CONTENT_EXTRACTED',
                    content: text,
                    title: document.title,
                    contentLength: text.length
                }, '*');
            } catch (err) {
                window.parent.postMessage({
                    type: 'PAGE_CONTENT_EXTRACTION_ERROR',
                    error: err.message
                }, '*');
            }
        }
    });
})();
</script>
'''
                charset_meta = '<meta charset="UTF-8">'
                base_tag = f'<base href="{base_url}/">'
                head_injection = f'{charset_meta}\n{base_tag}'
                
                if '<head>' in html_content:
                    html_content = html_content.replace('<head>', f'<head>\n{head_injection}\n', 1)
                elif '<HEAD>' in html_content:
                    html_content = html_content.replace('<HEAD>', f'<HEAD>\n{head_injection}\n', 1)
                else:
                    html_content = f'{head_injection}\n{html_content}'
                
                if '</body>' in html_content:
                    html_content = html_content.replace('</body>', f'{link_interceptor_script}\n</body>', 1)
                elif '</BODY>' in html_content:
                    html_content = html_content.replace('</BODY>', f'{link_interceptor_script}\n</BODY>', 1)
                else:
                    html_content = f'{html_content}\n{link_interceptor_script}'
                
                total_time = (time.time() - start_time) * 1000
                parse_time = (time.time() - parse_start) * 1000
                logger.info(f"✅ Proxy completed in {total_time:.0f}ms (parse: {parse_time:.0f}ms) | Mode: {'FAST' if fast else 'FULL'} | {url[:60]}")
                
                return Response(
                    content=html_content.encode('utf-8'),
                    media_type='text/html; charset=utf-8',
                    headers={
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET',
                        'X-Frame-Options': 'ALLOWALL',
                        'Content-Security-Policy': "frame-ancestors *",
                        'Content-Type': 'text/html; charset=utf-8',
                        'X-Proxy-Time': f'{total_time:.0f}ms'
                    }
                )
            
            # For CSS content (buffer to rewrite)
            elif 'text/css' in content_type:
                try:
                    content_bytes = await response.read()
                    try:
                        encoding = response.encoding or 'utf-8'
                        css_content = content_bytes.decode(encoding, errors='replace')
                    except Exception:
                        css_content = content_bytes.decode('utf-8', errors='replace')
                finally:
                    await response.aclose()
                    await client.aclose()
                
                parsed_url = urlparse(url)
                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                proxy_host = str(request.base_url).rstrip('/')
                if proxy_host.startswith("http://") and os.getenv("FORCE_HTTPS", "").lower() == "true":
                    proxy_host = proxy_host.replace("http://", "https://")
                proxy_base = f"{proxy_host}/proxy?token={token}&url=" if token else f"{proxy_host}/proxy?url="
                
                def rewrite_css_url(match):
                    original_url = match.group(1).strip('\'"')
                    if original_url.startswith(('data:', 'blob:', '#')):
                        return match.group(0)
                    if original_url.startswith('//'):
                        absolute_url = f"{parsed_url.scheme}:{original_url}"
                    elif original_url.startswith('/'):
                        absolute_url = f"{base_url}{original_url}"
                    elif not original_url.startswith(('http://', 'https://')):
                        absolute_url = urljoin(url, original_url)
                    else:
                        absolute_url = original_url
                    return f'url("{proxy_base}{quote(absolute_url, safe="")}")'
                
                css_content = CSS_URL_PATTERN.sub(rewrite_css_url, css_content)
                
                return Response(
                    content=css_content.encode('utf-8'),
                    media_type='text/css; charset=utf-8',
                    headers={
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET'
                    }
                )
            
            # STREAMING: For Fonts, JS, JSON, PDFs, Images, etc.
            # This is the key optimization for PDFs: stream them instead of buffering
            else:
                logger.info(f"🌊 Streaming content: {content_type} | {url[:60]}")
                
                async def iterate_content():
                    try:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                    finally:
                        await response.aclose()
                        await client.aclose()
                
                headers_to_pass = {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET',
                    'X-Frame-Options': 'ALLOWALL',
                }
                
                return StreamingResponse(
                    iterate_content(),
                    media_type=content_type,
                    headers=headers_to_pass
                )

        except httpx.TimeoutException:
            logger.error(f"⏱️ [Tier 1] Proxy timeout for: {url}")
            # Always clean up client
            await client.aclose()
            
            # Playwright fallback for timeout — skip for PDF URLs (Playwright can't handle them)
            if PLAYWRIGHT_SERVICE_ENABLED and not is_likely_pdf:
                logger.info(f"🎭 [Tier 2] Falling back to Playwright after timeout: {url}")
                success, result = await _call_playwright_service(url, output_format, token)
                if success: return result
            elif is_likely_pdf:
                logger.warning(f"📄 PDF request timed out — no Playwright fallback for PDFs: {url}")
            return JSONResponse({"error": "Request timed out"}, status_code=504)
            
        except httpx.HTTPError as e:
            # Generic HTTP error (connection, etc.)
            error_msg = str(e)
            logger.error(f"❌ [Tier 1] Proxy request error: {error_msg}")
            await client.aclose()
            
            # Playwright fallback for HTTP errors — skip for PDF URLs
            if PLAYWRIGHT_SERVICE_ENABLED and not is_likely_pdf:
                logger.info(f"🎭 [Tier 2] Falling back to Playwright after error: {url}")
                success, result = await _call_playwright_service(url, output_format, token)
                if success: return result
            elif is_likely_pdf:
                logger.warning(f"📄 PDF request failed — no Playwright fallback for PDFs: {url}")
            return JSONResponse({"error": f"Failed to fetch URL: {error_msg}"}, status_code=502)
            
        except Exception as e:
            logger.error(f"❌ Inner Proxy error: {str(e)}")
            # Ensure client is closed
            try:
                await client.aclose()
            except Exception:
                # Announce loudly; the real proxy error is already logged above
                # and the 500 is returned below — don't mask it.
                logger.error(
                    "Failed to close proxy HTTP client during error cleanup",
                    exc_info=True,
                )

            return JSONResponse({"error": "Failed to proxy URL"}, status_code=500)

    except Exception as e:
        logger.error(f"❌ Outer Proxy error: {str(e)}")
        return JSONResponse({"error": "Proxy request failed"}, status_code=500)


# READER endpoints (/reader/internet/*, /reader/document/*) removed 2026-08-08
# (Phase 0 OSS split). Their only consumers were the Reader / Mindmap / WebViewer
# UI surfaces, all removed in the same pass.

# Health check endpoints
@app.get("/health", operation_id="main_health_check")
async def health_check():
    """Enhanced health check with system status"""
    try:
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "2.0.0"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "degraded",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }

# Upload limits endpoint - provides configurable limits to UI
@app.get("/api/config/upload-limits", operation_id="get_upload_limits")
async def get_upload_limits():
    """
    Returns upload limits from environment variables.
    Used by UI to display limits in upload modal and validate before upload.
    """
    return {
        "limits": {
            "pdf": {
                "maxSizeMB": int(os.getenv("MAX_PDF_SIZE_MB", 100)),
                "maxPages": int(os.getenv("MAX_PDF_PAGES", 200)),
                "description": "PDF documents"
            },
            "powerpoint": {
                "maxSizeMB": int(os.getenv("MAX_PDF_SIZE_MB", 100)),  # Same as documents
                "maxSlides": int(os.getenv("MAX_PPT_SLIDES", 100)),
                "description": "PowerPoint presentations"
            },
            "excel_json": {
                "maxSizeMB": int(os.getenv("MAX_PDF_SIZE_MB", 100)),  # Same as documents
                "maxRecords": int(os.getenv("MAX_EXCEL_JSON_RECORDS", 1000)),
                "description": "Excel spreadsheets and JSON files"
            },
            "html": {
                "maxSizeMB": int(os.getenv("MAX_PDF_SIZE_MB", 100)),  # Same as documents
                "maxChars": int(os.getenv("MAX_HTML_CHARS", 200000)),
                "description": "HTML web pages"
            },
            "audio": {
                "maxSizeMB": int(os.getenv("MAX_AUDIO_SIZE_MB", 100)),
                "maxDurationMinutes": int(os.getenv("MAX_AUDIO_DURATION_MINUTES", 120)),
                "description": "Audio files (MP3, WAV, M4A, etc.)"
            },
            "video": {
                "maxSizeMB": int(os.getenv("MAX_VIDEO_SIZE_MB", 2048)),
                "maxDurationMinutes": int(os.getenv("MAX_VIDEO_DURATION_MINUTES", 180)),
                "description": "Video files"
            },
            "image": {
                "maxSizeMB": int(os.getenv("MAX_IMG_SIZE_MB", 20)),
                "description": "Image files (JPG, PNG, GIF, etc.)"
            },
            "ocr": {
                "maxPages": int(os.getenv("MAX_OCR_PAGES", 10)),
                "description": "OCR scanned documents"
            },
        }
    }

## Removed legacy sparse test & vocabulary endpoints (unified_sparse_manager deprecated)

# Relocated from custom_domain_api.py (deleted — enterprise-branding feature
# removed) when that file's deletion took two routes down with it that were
# NOT branding-specific: the unified public-share short-link viewer, used by
# every share type (chat/diagram/report/presentation/printable) and named
# explicitly in the JWTAuthMiddleware bypass allowlist above, and the generic
# admin-check the UI uses to gate impersonation/app-builder eligibility. Both
# keep their exact original paths so nothing else needs to change.


@app.get("/api/admin/check")
def check_admin(current_user: dict = Depends(get_current_user)):
    """Check if current user is an admin (admins collection, seeded directly in Mongo)."""
    email = current_user.get("email") or current_user.get("user_id")
    admins_col = get_mongo_client()[get_database_name()]['admins']
    return {"is_admin": admins_col.find_one({"email": email}) is not None}


@app.get("/api/config/deployment")
async def get_deployment_config():
    """Returns deployment config flags for the UI."""
    return {
        "deployment_mode": "onprem",
        "is_onprem": True
    }

@app.get("/")
async def root():
    """Root endpoint with service information"""
    return {
        "message": "Citra AI API is running",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
        "status": "ready"
    }

# Enhanced uvicorn startup with better signal handling
if __name__ == "__main__":
    import uvicorn
    import argparse
    
    # Set up signal handlers early
    setup_signal_handlers()
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Citra AI API Service')
    parser.add_argument('--host', default=os.getenv('HOST', '0.0.0.0'), help='Host to bind to')
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '8085')), help='Port to bind to')
    parser.add_argument('--reload', action='store_true', default=os.getenv('RELOAD', 'true').lower() == 'true', help='Enable auto-reload')
    parser.add_argument('--workers', type=int, default=int(os.getenv('WORKERS', '1')), help='Number of workers')
    
    args = parser.parse_args()
    
    logger.info(f"?? Starting Citra AI API Service on {args.host}:{args.port}")
    
    try:
        if args.workers > 1:
            # Multi-worker mode (production) - disable reload for stability
            logger.info(f"🚀 Starting in production mode with {args.workers} workers")
            uvicorn.run(
                "main:app",
                host=args.host,
                port=args.port,
                workers=args.workers,
                reload=False,
                access_log=True,
                log_level="info",
                timeout_keep_alive=1800  # 30 minutes for large video uploads
            )
        else:
            # Single worker mode (production/testing)
            logger.info("🚀 Starting in production mode with single worker")
            config = uvicorn.Config(
                app=app,
                host=args.host,
                port=args.port,
                reload=args.reload,
                access_log=True,
                log_level="info",
                # Enhanced configuration for signal handling
                loop="asyncio",
                lifespan="on",
                timeout_keep_alive=1800  # 30 minutes for large video uploads
            )
            server = uvicorn.Server(config)
            server.run()
            
    except KeyboardInterrupt:
        logger.info("?? Received keyboard interrupt, shutting down gracefully...")
    except Exception as e:
        logger.error(f"? Server startup failed: {e}", exc_info=True)
    finally:
        logger.info("? Citra AI Service has stopped")

