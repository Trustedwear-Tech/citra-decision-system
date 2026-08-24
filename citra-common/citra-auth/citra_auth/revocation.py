# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Token revocation seam for the shared JWT middleware.

``JWTAuthMiddleware`` rejects any token whose ``jti`` is revoked. The actual
blocklist lives in the ``revoked_tokens`` Mongo collection (written by
Citra-User-Service on impersonation-revoke / demotion), but citra-auth is
deliberately dependency-light and does NOT own a Mongo client. So a consuming
service REGISTERS a checker at startup and the middleware calls it:

    from citra_auth import register_revocation_checker

    async def _is_revoked(jti: str) -> bool:
        return await db.revoked_tokens.find_one({"jti": jti}) is not None

    register_revocation_checker(_is_revoked)   # call once at app startup

The checker may be sync or async and should be cache-fronted by the service
(the middleware calls it on every authenticated request). If no checker is
registered, revocation is NOT enforced for that service — to avoid a SILENT
gap (RULE #1: fail loud), the middleware logs a one-time warning the first time
it sees a token carrying a ``jti`` with no checker wired.
"""
from __future__ import annotations

import inspect
import logging
from typing import Awaitable, Callable, Optional, Union

logger = logging.getLogger(__name__)

# A checker takes a jti and returns (or awaits) True when that token is revoked.
RevocationChecker = Callable[[str], Union[bool, Awaitable[bool]]]

_checker: Optional[RevocationChecker] = None
_warned_unwired = False


def register_revocation_checker(checker: Optional[RevocationChecker]) -> None:
    """Install (or clear, with None) the revocation checker for this process."""
    global _checker, _warned_unwired
    _checker = checker
    _warned_unwired = False
    if checker is not None:
        logger.info("🔐 [REVOCATION] checker registered — jti blocklist enforced")


def has_revocation_checker() -> bool:
    return _checker is not None


async def is_token_revoked(jti: Optional[str]) -> bool:
    """True if ``jti`` is revoked. False when there's no jti or no checker is
    wired (logged once so the gap is visible, never silent)."""
    global _warned_unwired
    if not jti:
        return False
    if _checker is None:
        if not _warned_unwired:
            logger.warning(
                "⚠️ [REVOCATION] token carries a jti but no revocation checker is "
                "registered — the revoked_tokens blocklist is NOT enforced in this "
                "service. Call citra_auth.register_revocation_checker() at startup."
            )
            _warned_unwired = True
        return False
    try:
        result = _checker(jti)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
    except Exception as exc:  # noqa: BLE001
        # A failing blocklist lookup must NOT silently admit a possibly-revoked
        # token. Log loudly and fail closed (treat as revoked).
        logger.error(
            "❌ [REVOCATION] checker raised for jti=%s: %s — failing closed (deny)",
            jti, exc,
        )
        return True
