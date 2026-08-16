# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Rate Limit Middleware for Citra AI Service
Implements distributed rate limiting using Redis as the backend storage.

Architecture:
  - CentralRateLimitMiddleware handles ALL rate limiting via route-based config.
  - No per-endpoint @limiter.limit() decorators or response: Response params needed.
  - Routes are matched against RATE_LIMIT_ROUTES config (prefix matching).
  - Unmatched routes get the default limit (100/minute).
  
Benefits:
  - Zero boilerplate in endpoint files (no imports, no decorators, no Response param)
  - Add rate limits by editing ONE config dict, not touching endpoint code
  - No SlowAPI "response: Response" foot-gun that breaks endpoints silently
"""

import os
import re
import time
import logging
from typing import Optional
from collections import defaultdict
from fastapi import Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Configure logging
logger = logging.getLogger(__name__)

# ============================================================================
# RATE LIMIT CONSTANTS
# ============================================================================

LIMIT_AI_ENDPOINTS = os.getenv("RATE_LIMIT_AI", "20/minute")
LIMIT_IMAGE_GEN = os.getenv("RATE_LIMIT_IMAGE_GEN", "60/minute")  # Higher limit for image gen (presentations generate many images in parallel)
LIMIT_Standard_ENDPOINTS = os.getenv("RATE_LIMIT_STANDARD", "100/minute")
LIMIT_Auth_ENDPOINTS = os.getenv("RATE_LIMIT_AUTH", "10/minute")
LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "10/minute")
LIMIT_UPLOAD_VIDEO = os.getenv("RATE_LIMIT_UPLOAD_VIDEO", "3/minute")
LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "30/minute")
LIMIT_ERROR_REPORT = os.getenv("RATE_LIMIT_ERROR_REPORT", "3/15minute")

# ============================================================================
# ROUTE → RATE LIMIT CONFIG  (single source of truth)
# ============================================================================
# Routes are matched by prefix (longest match wins).
# Add new rate-limited routes here — no endpoint code changes needed.
#
# Format: { "METHOD:path_prefix": "rate_limit_string" }
# Use "*" as method to match any HTTP method.

RATE_LIMIT_ROUTES: dict[str, str] = {
    # === UPLOADS (restrictive) ===
    "POST:/v2/documents":                     LIMIT_UPLOAD,           # 10/min - document upload
    "POST:/v2/transcripts":                   LIMIT_UPLOAD,           # 10/min - audio upload
    "POST:/upload-video":                     LIMIT_UPLOAD_VIDEO,     # 3/min  - video upload
    
    # === CHAT (moderate) ===
    "POST:/query":                            LIMIT_CHAT,             # 30/min - main query
    "POST:/query/stream":                     LIMIT_CHAT,             # 30/min - streaming query
    "*:/chat":                                LIMIT_CHAT,             # 30/min - chat CRUD
    "POST:/reader/internet/chat":             LIMIT_CHAT,             # 30/min - reader internet chat
    "POST:/reader/internet/chat/stream":      LIMIT_CHAT,             # 30/min - reader internet chat streaming
    "POST:/reader/document/chat":             LIMIT_CHAT,             # 30/min - reader document chat
    "POST:/reader/document/chat/stream":      LIMIT_CHAT,             # 30/min - reader document chat streaming
    "POST:/api/v2/projects/chat/query":       LIMIT_CHAT,             # 30/min - project AI chat
    
    # === AI ENDPOINTS (standard) ===
    "POST:/presentation/":                    LIMIT_IMAGE_GEN,        # 60/min - all presentation endpoints (burst for parallel slide gen)
    "POST:/printable/":                       LIMIT_IMAGE_GEN,        # 60/min - all printable endpoints (burst for parallel page gen)
    "POST:/composer/":                        LIMIT_IMAGE_GEN,        # 60/min - all composer endpoints (burst for parallel gen)
    "POST:/llm/":                          LIMIT_IMAGE_GEN,        # 60/min - llm image gen (deprecated)
    "POST:/runware/":                         LIMIT_IMAGE_GEN,        # 60/min - runware image gen (legacy route)
    "POST:/image-gen/":                       LIMIT_IMAGE_GEN,        # 60/min - image generation (new route)
    "POST:/api/v2/query/enhanced":            LIMIT_AI_ENDPOINTS,     # 20/min - enhanced query (persona)
    "POST:/api/diagram/generate":             LIMIT_AI_ENDPOINTS,     # 20/min - diagram generation
    "POST:/api/diagram/create-with-ai":       LIMIT_AI_ENDPOINTS,     # 20/min - diagram AI create
    "POST:/api/diagram/edit-with-ai":         LIMIT_AI_ENDPOINTS,     # 20/min - diagram AI edit
    "POST:/page-builder":                     LIMIT_AI_ENDPOINTS,     # 20/min - page builder (prefix)
    
    # === PROXY / READER (moderate) ===
    "GET:/proxy":                             LIMIT_CHAT,             # 30/min - web proxy
    "POST:/reader/internet/search":           LIMIT_CHAT,             # 30/min - internet search
    "POST:/reader/internet/fetch-page":       LIMIT_CHAT,             # 30/min - fetch page content
    
    # === ERROR REPORTING (strict — unauthenticated, email-sending) ===
    "POST:/api/report-error":                 LIMIT_ERROR_REPORT,     # 3/15min - error reports (sends email)
    
    # === QUICK CHAT (moderate) ===
    "POST:/quick-chat/session":                LIMIT_CHAT,             # 30/min - session creation
    "POST:/quick-chat/upload":                 LIMIT_UPLOAD,           # 10/min - file upload
    "POST:/quick-chat/query/stream":           LIMIT_CHAT,             # 30/min - streaming query
    "DELETE:/quick-chat/session/":             LIMIT_CHAT,             # 30/min - session cleanup
    "DELETE:/quick-chat/file/":                LIMIT_CHAT,             # 30/min - file delete
    "GET:/quick-chat/download/":               LIMIT_CHAT,             # 30/min - file download
    "GET:/quick-chat/session/":                LIMIT_CHAT,             # 30/min - session info
}

# ============================================================================
# KEY FUNCTION (user ID or IP)
# ============================================================================

def rate_limit_key_func(request: Request) -> str:
    """
    Determine the unique key for rate limiting.
    
    Priority:
    1. Authenticated User ID (from JWT middleware)
    2. IP Address (for anonymous users)
    """
    if hasattr(request.state, "authenticated_user_id") and request.state.authenticated_user_id:
        return f"user:{request.state.authenticated_user_id}"
    
    ip = get_remote_address(request)
    return f"ip:{ip}"

# ============================================================================
# LIMITER INSTANCE (Redis-backed)
# ============================================================================

def get_limiter() -> Limiter:
    """
    Initialize and return the rate limiter with Redis backend.
    """
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = os.getenv("REDIS_PORT", "6379")
    redis_db = os.getenv("REDIS_DB", "0")
    redis_password = os.getenv("REDIS_PASSWORD")
    redis_username = os.getenv("REDIS_USERNAME", "")
    redis_ssl = os.getenv("REDIS_SSL", "false").lower() == "true"
    
    if redis_password:
        if redis_username:
            auth_part = f"{redis_username}:{redis_password}@"
        else:
            auth_part = f":{redis_password}@"
    else:
        auth_part = ""
        
    protocol = "rediss" if redis_ssl else "redis"
    storage_uri = f"{protocol}://{auth_part}{redis_host}:{redis_port}/{redis_db}"
    
    redis_enabled = os.getenv("REDIS_CACHE_ENABLED", "true").lower() == "true"
    
    if not redis_enabled:
        logger.warning("⚠️ Redis disabled. Rate limiting will use IN-MEMORY storage (not distributed).")
        storage_uri = "memory://"
    
    logger.info(f"🛡️ Initializing Rate Limiter with storage: {storage_uri.split('@')[-1] if '@' in storage_uri else storage_uri}")
    
    default_limits = [os.getenv("RATE_LIMIT_STANDARD", "100/minute")]
    
    limiter = Limiter(
        key_func=rate_limit_key_func,
        storage_uri=storage_uri,
        default_limits=default_limits,
        headers_enabled=True,
        swallow_errors=True    # Fail open if Redis is down
    )
    
    return limiter

# Create the global limiter instance
limiter = get_limiter()

# ============================================================================
# CENTRAL RATE LIMIT MIDDLEWARE
# ============================================================================

def _match_route_limit(method: str, path: str) -> Optional[str]:
    """
    Find the rate limit for a given method + path using prefix matching.
    Longest prefix wins (most specific route takes priority).
    Returns the limit string or None for default handling.
    """
    # Strip root_path prefix if present (FastAPI sets this)
    if path.startswith("/citra-ai"):
        path = path[len("/citra-ai"):]
    
    best_match = None
    best_match_len = 0
    
    for route_key, limit in RATE_LIMIT_ROUTES.items():
        route_method, route_path = route_key.split(":", 1)
        
        # Check method match (* = any method)
        if route_method != "*" and route_method != method.upper():
            continue
        
        # Prefix matching
        if path == route_path or path.startswith(route_path):
            if len(route_path) > best_match_len:
                best_match = limit
                best_match_len = len(route_path)
    
    return best_match


class CentralRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Centralized rate limiting middleware that replaces per-endpoint @limiter.limit() decorators.
    
    How it works:
    1. Intercepts every request before it reaches the endpoint
    2. Matches the route against RATE_LIMIT_ROUTES config (longest prefix wins)
    3. If matched, checks rate limit using SlowAPI's limiter
    4. If exceeded, returns 429 with retry-after headers
    5. If allowed, injects X-RateLimit headers into the response
    
    This eliminates the need for:
    - @limiter.limit() decorators on every endpoint
    - response: Response parameter in endpoint signatures
    - Per-file imports of limiter and limit constants
    """
    
    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = request.url.path
        
        # Skip rate limiting for non-API paths and health checks
        if path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)
        
        # Skip OPTIONS (CORS preflight)
        if method == "OPTIONS":
            return await call_next(request)
        
        # Find matching rate limit
        limit_string = _match_route_limit(method, path)
        
        if limit_string:
            # Get rate limit key (user ID or IP)
            key = rate_limit_key_func(request)
            
            try:
                # Use limiter's internal check
                # Parse limit string (e.g., "20/minute" → 20 per 60 seconds)
                from limits import parse as parse_limit
                
                limit_item = parse_limit(limit_string)
                
                # Check against the storage
                if not limiter._limiter.hit(limit_item, key):
                    # Rate limit exceeded
                    reset = limiter._limiter.get_window_stats(limit_item, key)
                    retry_after = str(int(reset[0] - time.time())) if reset[0] > time.time() else "60"
                    
                    logger.warning(f"🛡️ Rate limit exceeded: {key} on {method} {path} (limit: {limit_string})")
                    
                    return JSONResponse(
                        status_code=429,
                        content={"error": "Rate limit exceeded", "detail": f"Rate limit: {limit_string}"},
                        headers={
                            "Retry-After": retry_after,
                            "X-RateLimit-Limit": str(limit_item.amount),
                        }
                    )
                
                # Rate limit not exceeded — proceed and add headers
                response = await call_next(request)
                
                # Inject rate limit headers into response
                window_stats = limiter._limiter.get_window_stats(limit_item, key)
                response.headers["X-RateLimit-Limit"] = str(limit_item.amount)
                response.headers["X-RateLimit-Remaining"] = str(max(0, window_stats[1]))
                response.headers["X-RateLimit-Reset"] = str(int(window_stats[0]))
                
                return response
                
            except Exception as e:
                # Fail open — if rate limiting breaks, don't block the request, but log prominently
                logger.warning(f"🛡️ Rate limit check failed (fail-open) for {method} {path}: {e}")
                return await call_next(request)
        
        # No specific rate limit configured — pass through (default SlowAPI limits still apply)
        return await call_next(request)
