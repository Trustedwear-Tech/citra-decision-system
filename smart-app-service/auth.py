"""JWT authentication for Smart-App-Service.

Validates the same JWTs Citra-Service mints (same ``JWT_SECRET`` /
``JWT_ISSUER``). Modelled directly on action-chat-service/auth.py so the
behaviour is consistent across the platform.

Public endpoints are intentionally narrow:
  - ``/``, ``/health``, ``/metrics``, ``/favicon.ico``
  - ``/docs``, ``/redoc``, ``/openapi.json`` (non-prod only)
  - ``/publish``           — authenticated by the builder pod via a
                             scoped sandbox token (handled in route)

Everything else (``/apps``, ``/build``, ``/apps/{slug}/run``, …)
requires a valid user JWT.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import jwt
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# mint_system_workflow_token — re-exported from the shared citra-auth package
# (the canonical signer) rather than duplicated here. smart-app-service now
# depends on citra-auth (-e ../citra-common/citra-auth), so there is a single source of truth
# for the token payload; `from auth import mint_system_workflow_token` keeps
# working for existing callers.
from citra_auth import mint_system_workflow_token  # noqa: E402,F401


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validate the user JWT on every public smart-app-service route."""

    def __init__(self, app, dispatch=None) -> None:
        super().__init__(app, dispatch)
        self.jwt_secret = os.getenv("JWT_SECRET")
        self.jwt_issuer = os.getenv("JWT_ISSUER", "Citra-AI")
        if not self.jwt_secret:
            raise RuntimeError(
                "JWT_SECRET must be configured for smart-app-service"
            )
        if self.jwt_secret == "change-me":
            raise RuntimeError(
                "JWT_SECRET is set to the placeholder value 'change-me'. "
                "Set a real secret (≥ 32 bytes recommended for HS256)."
            )
        # HS256 with < 32 bytes is below RFC 7518 §3.2 recommendation.
        # Allow it in dev, refuse in prod.
        env = os.getenv("ENVIRONMENT", "dev").lower()
        if len(self.jwt_secret) < 32 and env in ("prod", "production"):
            raise RuntimeError(
                f"JWT_SECRET is {len(self.jwt_secret)} bytes; HS256 requires "
                "≥ 32 bytes in production environments."
            )

        self.public_endpoints: Set[str] = {
            "/", "/health", "/metrics", "/favicon.ico",
        }

        self.public_patterns: List[str] = [
            "/static/",
            # /publish is called by the builder pod with a scoped sandbox
            # token; the route handler validates the scoped claim itself.
            "/publish",
            # /smart-app/internal/* uses HMAC-bearer auth (see
            # internal_routes._require_internal_claims). The user-JWT
            # middleware is bypassed here so the internal router can do
            # its own validation.
            "/smart-app/internal/",
        ]
        # Suffix patterns: any path matching these is public. Used for
        # webhook entry points that authenticate via HMAC instead of JWT.
        self.public_suffix_patterns: List[str] = [
            "/trigger/",  # POST /apps/{slug}/trigger/{trigger_id}
        ]

        # Docs only outside production
        env = os.getenv("ENVIRONMENT", "dev").lower()
        if env not in ("prod", "production"):
            self.public_endpoints.update(
                {"/docs", "/redoc", "/openapi.json"}
            )

    def _is_public(self, path: str) -> bool:
        if path in self.public_endpoints:
            return True
        for pattern in self.public_patterns:
            if path.startswith(pattern):
                return True
        for pattern in self.public_suffix_patterns:
            if pattern in path:
                return True
        return False

    @staticmethod
    def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
        if not authorization:
            return None
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1]

    def _verify(self, token: str) -> dict:
        try:
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                # Citra-Service does not strictly enforce issuer; mirror that.
                options={"verify_aud": False},
            )
        except jwt.ExpiredSignatureError as e:
            logger.warning("token expired: %s", e)
            raise HTTPException(status_code=401, detail="Token has expired") from e
        except jwt.InvalidTokenError as e:
            logger.warning("invalid token: %s", e)
            raise HTTPException(status_code=401, detail="Invalid token") from e
        return payload

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        auth_header = (
            request.headers.get("Authorization")
            or request.headers.get("authorization")
        )
        token = self._extract_bearer(auth_header)
        is_public = self._is_public(path)

        # On public routes, opportunistically decode if a token was provided
        # so per-route handlers can read scoped claims (e.g. /publish).
        if is_public:
            if token:
                try:
                    payload = self._verify(token)
                    self._stash(request, payload)
                except HTTPException:
                    pass
            return await call_next(request)

        if not token:
            return JSONResponse(
                {"detail": "Authentication required"}, status_code=401
            )
        try:
            payload = self._verify(token)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
        self._stash(request, payload)
        return await call_next(request)

    @staticmethod
    def _stash(request: Request, payload: dict) -> None:
        """Populate request.state with the same field names other Citra
        services expose."""
        user_id = (
            payload.get("user_id")
            or payload.get("email")
            or payload.get("sub")
            or ""
        )
        request.state.authenticated_user_id = user_id
        request.state.user_id = user_id
        request.state.user_email = payload.get("email") or user_id
        request.state.user = payload
        request.state.org_id = payload.get("org_id")
        request.state.tenant_id = (
            payload.get("tenant_id")
            or payload.get("org_id")
            or payload.get("dept_id")
        )
        dept_ids = payload.get("dept_ids") or payload.get("department_ids") or []
        if isinstance(dept_ids, str):
            dept_ids = [dept_ids]
        request.state.dept_ids = dept_ids
        request.state.roles = payload.get("roles") or []
        request.state.scope = payload.get("scope")


# ---------------------------------------------------------------------------
# Helpers for route handlers
# ---------------------------------------------------------------------------


def get_secure_user_id(request: Request) -> str:
    """Return the authenticated ``user_id`` or raise 401."""
    uid = getattr(request.state, "authenticated_user_id", None)
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return uid


def get_optional_user_id(request: Request) -> Optional[str]:
    """Return user_id if authenticated, else None (for public-ish routes)."""
    return getattr(request.state, "authenticated_user_id", None) or None


def get_tenant_id(request: Request) -> Optional[str]:
    """Return the tenant scope (org_id / dept_id / tenant_id claim)."""
    return getattr(request.state, "tenant_id", None)


def get_org_id(request: Request) -> Optional[str]:
    """Return the canonical org_id claim."""
    return getattr(request.state, "org_id", None)


def get_user_dept_ids(request: Request) -> List[str]:
    return list(getattr(request.state, "dept_ids", None) or [])


def get_user_roles(request: Request) -> List[str]:
    return list(getattr(request.state, "roles", None) or [])


def get_personal_sa_id(request: Request) -> Optional[str]:
    user = getattr(request.state, "user", None) or {}
    return user.get("personal_sa_id")


def get_sa_admin_of(request: Request) -> List[str]:
    user = getattr(request.state, "user", None) or {}
    return list(user.get("service_account_admin_of") or [])


def get_sa_member_of(request: Request) -> List[str]:
    user = getattr(request.state, "user", None) or {}
    return list(user.get("service_account_member_of") or [])


def require_publish_scope(request: Request) -> dict:
    """Validate that /publish is called by a builder pod.

    Allowed callers:
      * Builder pods with ``scope=smart-app-builder`` claim (the production path).
      * In dev/test environments only, a regular user JWT may publish for
        manual testing. ``ENVIRONMENT=prod`` (or ``production``) disables
        this fallback so a stolen user token cannot mint apps directly.

    The previous implementation 403'd only when the scope claim was both
    present *and* wrong *and* there was no user_id, which let any user
    JWT through. This rewrite inverts the logic to fail-closed.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    scope = user.get("scope")
    if scope == "smart-app-builder":
        return user

    env = os.getenv("ENVIRONMENT", "dev").lower()
    if env not in ("prod", "production") and user.get("user_id"):
        # Dev / test fallback for manual publishes from a regular user JWT.
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="publish requires scope=smart-app-builder",
    )
