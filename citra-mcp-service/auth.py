# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""JWT validation for tool calls.

Both action-chat-service and smart-app-service mint scoped tokens with
the same JWT_SECRET. The MCP service accepts either as long as the
scope claim is in the allow-list configured via
``CITRA_MCP_ACCEPTED_SCOPES``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import HTTPException, Request

from config import get_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallerContext:
    """What we know about whoever called us — extracted from the JWT."""
    user_id: str
    org_id: str | None
    session_id: str | None
    scope: str
    raw_claims: dict[str, Any]
    # Original "Bearer <token>" header value. Tools that forward to a
    # JWT-aware downstream (e.g. discovery-service) reuse this so the
    # caller's claims propagate without us re-minting a token.
    auth_header: str = ""


def require_caller(request: Request) -> CallerContext:
    """Validate the Bearer token and return caller identity. Raises 401/403
    on missing / malformed / wrong-scope tokens."""
    cfg = get_config()
    if not cfg.jwt_secret:
        # Mis-deployed; fail closed so we don't silently accept anything.
        raise HTTPException(
            status_code=500,
            detail="citra-mcp-service: JWT_SECRET is not configured",
        )

    header = (request.headers.get("Authorization") or "").strip()
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization: Bearer <token> required",
        )
    token = header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="empty bearer token")

    try:
        # Audience is sandbox-specific (action-chat-service uses
        # ``citra-action-sandbox``; smart-app may differ) so we don't
        # verify audience here — the scope claim does the equivalent
        # job and is enforced below.
        claims = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
            issuer=cfg.jwt_issuer,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError as e:
        logger.info("rejected invalid JWT: %s", e)
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")

    scope = str(claims.get("scope") or "")
    if cfg.accepted_scopes and scope not in cfg.accepted_scopes:
        raise HTTPException(
            status_code=403,
            detail=(
                f"token scope {scope!r} not in allow-list "
                f"{list(cfg.accepted_scopes)}"
            ),
        )

    user_id = str(claims.get("user_id") or claims.get("sub") or "")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="token missing user_id / sub claim",
        )

    return CallerContext(
        user_id=user_id,
        org_id=str(claims.get("org_id") or "") or None,
        session_id=str(claims.get("session_id") or claims.get("sid") or "") or None,
        scope=scope,
        raw_claims=claims,
        auth_header=header,
    )
