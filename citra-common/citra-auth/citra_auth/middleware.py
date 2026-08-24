# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
JWT Authentication middleware + claim helpers.

Centralises JWT validation for every Citra FastAPI service. Mounts as
Starlette `BaseHTTPMiddleware`, stamps `request.state` with the caller's
identity + enterprise claims, and exposes `get_secure_user_id()` for
routes to read.

Originally lived as `Citra-Service/auth_middleware.py` and was imported
across services via PYTHONPATH; that drifted and broke once citra-workflow
needed its own process. Extracted here as the one source of truth.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import jwt
from fastapi import HTTPException, Request, status
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .constants import Roles
from .revocation import is_token_revoked

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# DISABLE_AUTH bypass (test/dev only)
# ────────────────────────────────────────────────────────────────────────
# Set DISABLE_AUTH=true in test envs to skip JWT validation and inject the
# fake user defined below. Never honoured when ENVIRONMENT is production —
# defence-in-depth so a leaked env var can't open prod auth.

_TEST_USER_INFO: Dict[str, Any] = {
    "user_id": "test_user_id",
    "email": "test@citra.ai",
    "name": "Test User",
    "googleId": "test_google_id",
    "iss": "Citra-AI-Test",
}


def _is_auth_disabled() -> bool:
    """True only when DISABLE_AUTH=true AND environment is non-prod."""
    if (os.getenv("DISABLE_AUTH") or "").lower() not in ("1", "true", "yes", "on"):
        return False
    env = (os.getenv("ENVIRONMENT") or "dev").lower()
    if env in ("prod", "production"):
        logger.error(
            "🚨 [AUTH_BYPASS] DISABLE_AUTH=true ignored: environment is %s. "
            "DISABLE_AUTH is only honoured in dev/test environments.",
            env,
        )
        return False
    return True


def mint_workflow_org_token(
    *,
    workflow_id: str,
    org_id: str,
    dept_ids: Optional[List[str]] = None,
    author_email: Optional[str] = None,
    ttl_seconds: int = 900,
) -> Optional[str]:
    """Mint a short-lived HS256 JWT for an org-owned workflow's runtime
    identity.

    Used by the cron scheduler and webhook trigger when triggering a
    workflow with no live caller JWT. The token carries the workflow's
    ``org_id`` / ``dept_ids`` directly, so dept-MCP per-source filtering
    works without looking up a human user record — important because the
    workflow's original author may have left the org but the workflow
    is org-owned and keeps running.

    The token identifies the runtime as a synthetic ``workflow-system:<id>``
    user with the ``workflow_system`` role marker. ``IT-workflow`` is
    also granted so the system identity can call workflow APIs during
    execution (artifact upload, status writes, etc.) without a per-call
    auth dance. The ``workflow_system: True`` claim lets audit logs
    distinguish minted-by-scheduler from minted-by-real-user.

    Returns None when JWT_SECRET is not configured (caller should fall
    back to running without per-source filtering).
    """
    if not workflow_id or not org_id:
        return None
    secret = os.getenv("JWT_SECRET")
    if not secret:
        logger.warning(
            "⚠️ [SYSTEM_TOKEN] JWT_SECRET not set — cannot mint workflow "
            "org token. Run will degrade dept-MCP visibility."
        )
        return None
    issuer = os.getenv("JWT_ISSUER", "Citra-AI")
    now_dt = datetime.now(timezone.utc)
    now_ts = int(now_dt.timestamp())
    payload: Dict[str, Any] = {
        # Synthetic stable user_id so downstream consumers don't try to
        # look up a human record. The workflow_id makes it traceable.
        "user_id": f"workflow-system:{workflow_id}",
        "email": f"workflow-{workflow_id}@system.citra",
        "name": "Workflow System",
        # Enterprise identity inherited directly from the workflow doc.
        "org_id": org_id,
        "dept_ids": list(dept_ids or []),
        # Roles list:
        #   - workflow_system : audit marker; dept-MCPs may bypass role-list
        #                       intersection when this is present.
        #   - IT-workflow     : grants self-callbacks into the workflow API
        #                       (status writes, artifact upload) during run.
        #   - org_admin       : org-scoped admin — NOT super_admin (single-
        #                       tenant; we never cross orgs). Grants the
        #                       workflow's MCP/agent node org-wide dept-MCP
        #                       visibility (the source-mcp ``org_admin_within_org``
        #                       rule) so it can query EVERY source in the
        #                       workflow's own org, not only its dept_ids.
        #   - user            : back-compat. The legacy
        #                       mint_workflow_system_token had no `roles`
        #                       claim at all, which the source-mcp-template
        #                       auth.py defaulted to ["user"]. Including
        #                       "user" here keeps already-deployed dept-MCPs
        #                       admitting scheduled runs even before they
        #                       grow an explicit workflow_system bypass.
        "roles": ["workflow_system", Roles.IT_WORKFLOW, Roles.ORG_ADMIN, Roles.USER],
        # Provenance — for audit, not auth.
        "workflow_id": workflow_id,
        "author_email": author_email or "",
        "workflow_system": True,
        # jti for the revocation blocklist (revoked_tokens).
        "jti": str(uuid.uuid4()),
        "iss": issuer,
        "iat": now_ts,
        "exp": now_ts + max(60, int(ttl_seconds)),
    }
    try:
        token = jwt.encode(payload, secret, algorithm="HS256")
        return token.decode("utf-8") if isinstance(token, bytes) else token
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ [SYSTEM_TOKEN] Failed to mint workflow org token: {exc}")
        return None


def mint_system_workflow_token(
    *,
    org_id: str,
    on_behalf_of_user_id: str,
    on_behalf_of_email: Optional[str] = None,
    dept_ids: Optional[List[str]] = None,
    purpose: str = "smart_app_publish",
    ttl_seconds: int = 300,
) -> Optional[str]:
    """Mint a short-lived org-scoped JWT for a platform service that
    needs IT-workflow access in a specific org on behalf of a user who
    lacks it.

    Primary use case: smart-app-service publishes a Smart App that
    contains a ``smart_app_action`` workflow. The publishing BA is the
    semantic author, but BAs don't carry IT-workflow role — IT does.
    So the smart-app-service mints this token to call workflow APIs as
    a system identity scoped to the BA's org, with ``on_behalf_of`` claims
    so audit trail + workflow.author_user_id reflect the actual BA.

    Token shape:
      - ``user_id``: synthetic ``workflow-publisher:<on_behalf_of_user_id>``
        so audit logs trace back to the BA without granting them IT-workflow
      - ``org_id`` / ``dept_ids``: directly from the BA's identity
      - ``roles``: ``["workflow_system", "IT-workflow", "user"]`` — same
        as mint_workflow_org_token; downstream dept-MCPs bypass role checks
        when ``workflow_system: True``
      - ``on_behalf_of_user_id`` / ``on_behalf_of_email``: the workflow
        router reads these to populate ``author_user_id`` / ``author_email``
        on the created workflow, preserving correct provenance
      - ``purpose``: free-form label for audit (e.g. "smart_app_publish")

    Short default TTL (5min) because the token is minted per-call, not
    cached.
    """
    if not org_id or not on_behalf_of_user_id:
        return None
    secret = os.getenv("JWT_SECRET")
    if not secret:
        logger.warning(
            "⚠️ [SYSTEM_TOKEN] JWT_SECRET not set — cannot mint system "
            "workflow token. Smart-app publishing will 401."
        )
        return None
    issuer = os.getenv("JWT_ISSUER", "Citra-AI")
    now_dt = datetime.now(timezone.utc)
    now_ts = int(now_dt.timestamp())
    payload: Dict[str, Any] = {
        "user_id": f"workflow-publisher:{on_behalf_of_user_id}",
        "email": "workflow-publisher@system.citra",
        "name": f"Workflow Publisher ({purpose})",
        "org_id": org_id,
        "dept_ids": list(dept_ids or []),
        "roles": ["workflow_system", Roles.IT_WORKFLOW, Roles.USER],
        "on_behalf_of_user_id": on_behalf_of_user_id,
        "on_behalf_of_email": on_behalf_of_email or on_behalf_of_user_id,
        "purpose": purpose,
        "workflow_system": True,
        # jti for the revocation blocklist (revoked_tokens).
        "jti": str(uuid.uuid4()),
        "iss": issuer,
        "iat": now_ts,
        "exp": now_ts + max(60, int(ttl_seconds)),
    }
    try:
        token = jwt.encode(payload, secret, algorithm="HS256")
        return token.decode("utf-8") if isinstance(token, bytes) else token
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ [SYSTEM_TOKEN] Failed to mint system workflow token: {exc}")
        return None


def mint_semantic_read_token(
    *,
    org_id: str,
    on_behalf_of_user_id: Optional[str] = None,
    dept_ids: Optional[List[str]] = None,
    ttl_seconds: int = 300,
) -> Optional[str]:
    """Mint a short‑lived, org‑scoped token for a PLATFORM SERVICE to perform a
    semantic (RAG) read on behalf of an AUTOMATED run (agent / trigger) that
    carries no end‑user identity.

    The RAG short‑circuit answers semantic queries at Citra‑Service, which
    normally authorizes on the end‑user's dept membership. An agent/trigger run
    has no user, so this mints a trusted system identity instead:
      - ``semantic_service: True`` — the ONLY capability this token grants.
        Citra‑Service's ``/semantic/search`` authorizes this marker as a trusted
        read and scopes the DATA to the source's OWN dept (resolved server‑side
        from ``dept_sources``). It grants nothing else — not IT‑workflow, not
        admin — so a leak can at worst read semantic sources in this org.
      - ``org_id`` bounds it to the app's org (single‑tenant ⇒ already the data
        boundary); ``on_behalf_of_user_id`` preserves audit provenance.
    HS256 with ``JWT_SECRET`` — only a platform holder of the secret can mint it.
    Short default TTL (5 min); minted per call, never cached. Returns ``None`` if
    ``org_id`` or the secret is missing (caller then fails loud)."""
    if not org_id:
        return None
    secret = os.getenv("JWT_SECRET")
    if not secret:
        logger.warning(
            "⚠️ [SEMANTIC_TOKEN] JWT_SECRET not set — cannot mint semantic-read "
            "token; agent/trigger RAG reads will 401."
        )
        return None
    issuer = os.getenv("JWT_ISSUER", "Citra-AI")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    subject = on_behalf_of_user_id or "system"
    payload: Dict[str, Any] = {
        "user_id": f"semantic-reader:{subject}",
        "email": "semantic-reader@system.citra",
        "name": "Semantic Reader (agent run)",
        "org_id": org_id,
        "dept_ids": list(dept_ids or []),
        "roles": [Roles.USER],
        "semantic_service": True,
        "on_behalf_of_user_id": on_behalf_of_user_id or "",
        "purpose": "agent_semantic_read",
        "jti": str(uuid.uuid4()),
        "iss": issuer,
        "iat": now_ts,
        "exp": now_ts + max(60, int(ttl_seconds)),
    }
    try:
        token = jwt.encode(payload, secret, algorithm="HS256")
        return token.decode("utf-8") if isinstance(token, bytes) else token
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ [SEMANTIC_TOKEN] Failed to mint semantic-read token: {exc}")
        return None


def mint_workflow_system_token(
    user_id: str,
    *,
    user_email: Optional[str] = None,
    ttl_seconds: int = 900,
) -> Optional[str]:
    """DEPRECATED. Mints a session JWT scoped to a single user_id.

    Use ``mint_workflow_org_token(workflow_id, org_id, ...)`` instead —
    org-scoped tokens survive the author leaving the org and carry the
    enterprise claims (org_id, dept_ids) that dept-MCPs need directly.
    This function is kept so existing call sites keep working; remove
    once all callers migrate.
    """
    if not user_id:
        return None
    secret = os.getenv("JWT_SECRET")
    if not secret:
        logger.warning(
            "⚠️ [SYSTEM_TOKEN] JWT_SECRET not set — cannot mint workflow "
            "system token. Scheduled run will degrade dept-MCP visibility."
        )
        return None
    issuer = os.getenv("JWT_ISSUER", "Citra-AI")
    # IMPORTANT: use timezone-aware UTC. datetime.utcnow() returns a NAIVE
    # datetime; calling .timestamp() on it interprets it as local time and
    # offsets by the local TZ — that breaks PyJWT's exp validation on hosts
    # whose local time isn't UTC.
    now_dt = datetime.now(timezone.utc)
    now_ts = int(now_dt.timestamp())
    payload: Dict[str, Any] = {
        "user_id": user_id,
        "email": user_email or f"{user_id}@workflow.system.citra",
        "name": "Workflow System",
        # jti for the revocation blocklist (revoked_tokens).
        "jti": str(uuid.uuid4()),
        "iss": issuer,
        "iat": now_ts,
        "exp": now_ts + max(60, int(ttl_seconds)),
        # Marker so audit logs can distinguish system-minted tokens from
        # real user sessions — does not affect verification.
        "workflow_system": True,
    }
    try:
        token = jwt.encode(payload, secret, algorithm="HS256")
        return token.decode("utf-8") if isinstance(token, bytes) else token
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ [SYSTEM_TOKEN] Failed to mint workflow token: {exc}")
        return None


# Default public endpoints that don't need auth. Each consuming service
# can pass `extra_public_endpoints` / `extra_public_patterns` when adding
# the middleware to extend this set.
_DEFAULT_PUBLIC_ENDPOINTS: Set[str] = {
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/metrics",
    "/favicon.ico",
}

_DEFAULT_PUBLIC_PATTERNS: List[str] = [
    "/static/",
]


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that validates JWT tokens for all protected endpoints.

    Mounts via `app.add_middleware(JWTAuthMiddleware)`. Each service can
    extend the default public-endpoint list:

        app.add_middleware(
            JWTAuthMiddleware,
            extra_public_endpoints={"/citra-ai/api/kg/health"},
            extra_public_patterns=["/api/workflows/webhook/"],
        )
    """

    def __init__(
        self,
        app,
        dispatch=None,
        *,
        extra_public_endpoints: Optional[Set[str]] = None,
        extra_public_patterns: Optional[List[str]] = None,
    ):
        super().__init__(app, dispatch)
        self.jwt_secret = os.getenv("JWT_SECRET")
        self.jwt_issuer = os.getenv("JWT_ISSUER", "Citra-AI")

        if not self.jwt_secret:
            logger.error("❌ JWT_SECRET not configured in environment variables")
            raise RuntimeError("JWT_SECRET must be configured for authentication")

        self.public_endpoints: Set[str] = set(_DEFAULT_PUBLIC_ENDPOINTS)
        if extra_public_endpoints:
            self.public_endpoints.update(extra_public_endpoints)

        # Only expose docs endpoints in non-production environments
        _env = os.getenv("ENVIRONMENT", "dev").lower()
        if _env not in ("prod", "production"):
            self.public_endpoints.update({"/docs", "/openapi.json", "/redoc"})

        self.public_patterns: List[str] = list(_DEFAULT_PUBLIC_PATTERNS)
        if extra_public_patterns:
            self.public_patterns.extend(extra_public_patterns)

    def is_public_endpoint(self, path: str) -> bool:
        if path in self.public_endpoints:
            return True
        for pattern in self.public_patterns:
            if path.startswith(pattern):
                return True
        return False

    def extract_token_from_header(self, authorization: str) -> Optional[str]:
        if not authorization:
            return None
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1]

    def verify_token(self, token: str) -> Dict[str, Any]:
        try:
            payload = jwt.decode(
                jwt=token,
                key=self.jwt_secret,
                algorithms=["HS256"],
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    # Action-sandbox scoped tokens include aud=citra-action-sandbox
                    # while regular user tokens omit aud. Skip audience validation
                    # here — scope-specific checks happen in their own routers.
                    "verify_aud": False,
                },
            )
            for claim in ("user_id", "email", "exp"):
                if claim not in payload:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"Invalid token: missing {claim} claim",
                    )
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Token verification error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token verification failed",
            )

    async def _inject_authenticated_user_id(self, request: Request, authenticated_user_id: str):
        try:
            request.state.authenticated_user_id = authenticated_user_id
        except Exception as e:
            logger.error(f"❌ Failed to inject authenticated user_id: {e}")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # Allow CORS preflight to pass through
        if method == "OPTIONS":
            return await call_next(request)

        if self.is_public_endpoint(path):
            return await call_next(request)

        # Test/dev bypass — only when DISABLE_AUTH=true AND env is non-prod.
        if _is_auth_disabled():
            request.state.user = _TEST_USER_INFO
            request.state.user_id = _TEST_USER_INFO["user_id"]
            request.state.user_email = _TEST_USER_INFO["email"]
            request.state.user_name = _TEST_USER_INFO["name"]
            request.state.google_id = _TEST_USER_INFO["googleId"]
            request.state.authenticated = True
            await self._inject_authenticated_user_id(request, _TEST_USER_INFO["user_id"])
            return await call_next(request)

        authorization = request.headers.get("authorization") or request.headers.get("Authorization")
        token_from_query = request.query_params.get("token")

        if not authorization and token_from_query:
            authorization = f"Bearer {token_from_query}"

        if not authorization:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "detail": "Authorization header required",
                    "error_code": "MISSING_AUTHORIZATION",
                    "endpoint": path,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = self.extract_token_from_header(authorization)
        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "detail": "Invalid authorization format. Use 'Bearer <token>'",
                    "error_code": "INVALID_AUTHORIZATION_FORMAT",
                    "endpoint": path,
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            user_info = self.verify_token(token)

            # Revocation check — reject tokens whose jti is on the blocklist
            # (impersonation revoke, demotion, leaked token). Enforced only when
            # the service has wired a checker via register_revocation_checker();
            # otherwise a one-time warning is logged (never a silent gap).
            if await is_token_revoked(user_info.get("jti")):
                logger.warning(f"🔒 Revoked token rejected for {path}")
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "detail": "Token has been revoked",
                        "error_code": "TOKEN_REVOKED",
                        "endpoint": path,
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )

            request.state.user = user_info
            request.state.user_id = user_info.get("user_id")
            request.state.user_email = user_info.get("email")
            request.state.user_name = user_info.get("name")
            request.state.google_id = user_info.get("googleId")
            request.state.authenticated = True

            # Enterprise identity claims
            request.state.org_id = user_info.get("org_id")
            request.state.dept_id = user_info.get("dept_id")
            _dept_ids_jwt = user_info.get("dept_ids") or []
            if isinstance(_dept_ids_jwt, str):
                _dept_ids_jwt = [_dept_ids_jwt]
            if not _dept_ids_jwt and request.state.dept_id:
                _dept_ids_jwt = [request.state.dept_id]
            request.state.dept_ids = _dept_ids_jwt
            request.state.district_ids = user_info.get("district_ids") or []
            request.state.entity_type = user_info.get("entity_type", "general")
            request.state.roles = user_info.get("roles", ["user"])
            # Service-account claims (auto-provisioned by Citra-User-Service at login).
            # Personal SA owns ephemeral resources (presentation/printable/report/diagram);
            # Work SA owns durable team-portable resources (skills/smart-apps/workflows).
            request.state.personal_sa_id = user_info.get("personal_sa_id") or ""
            request.state.work_sa_id = user_info.get("work_sa_id") or ""
            request.state.service_account_admin_of = user_info.get("service_account_admin_of") or []
            request.state.service_account_member_of = user_info.get("service_account_member_of") or []

            # Team context from headers/query
            team_id = request.headers.get("X-Team-ID") or request.query_params.get("team_id")
            if team_id and team_id.lower() == "personal":
                team_id = None
            request.state.team_id = team_id
            request.state.is_personal_workspace = team_id is None

            await self._inject_authenticated_user_id(request, user_info.get("user_id"))

            return await call_next(request)

        except HTTPException as e:
            logger.warning(f"🔒 Authentication failed for {path}: {e.detail}")
            return JSONResponse(
                status_code=e.status_code,
                content={
                    "detail": e.detail,
                    "error_code": "AUTHENTICATION_FAILED",
                    "endpoint": path,
                },
                headers=e.headers or {},
            )
        except RateLimitExceeded:
            raise
        except Exception as e:
            logger.error(
                f"🔒 Unexpected authentication error for {path}: {type(e).__name__}: {e}",
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "Authentication system error",
                    "error_code": "AUTH_SYSTEM_ERROR",
                    "endpoint": path,
                },
            )


# ────────────────────────────────────────────────────────────────────────
# Legacy helpers (call sites read request.state directly via these)
# ────────────────────────────────────────────────────────────────────────


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    return getattr(request.state, "user", None)


def is_authenticated(request: Request) -> bool:
    return getattr(request.state, "authenticated", False)


def get_user_email(request: Request) -> Optional[str]:
    user = get_current_user(request)
    return user.get("email") if user else None


def get_user_id(request: Request) -> Optional[str]:
    user = get_current_user(request)
    return user.get("user_id") if user else None


def get_authenticated_user_id(request: Request) -> Optional[str]:
    return getattr(request.state, "authenticated_user_id", None)


def extract_user_identifier(request: Request) -> Optional[str]:
    return get_user_email(request)


def get_secure_user_id(request: Request) -> str:
    """Return the authenticated user_id (== email). Raises 401 if missing."""
    if not hasattr(request.state, "authenticated_user_id"):
        logger.warning("🔒 Missing authorization header for: " + request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide valid JWT token.",
        )
    return request.state.authenticated_user_id
