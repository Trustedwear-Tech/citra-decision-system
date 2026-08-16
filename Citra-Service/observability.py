# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
observability.py — Sentry-SDK init + per-request trace_id middleware.

Used by every Citra Python service. Activates only when SENTRY_DSN is set,
so it's a no-op in dev / tests.

What it captures:
  • Unhandled exceptions (FastAPI + background tasks + asyncio)
  • Full stack trace, request body/headers (scrubbed), user id from the
    JWT middleware if present
  • `trace_id` tag on every event so you can pivot from a Sentry issue to
    the matching Loki log stream.

Loki correlation:
  The middleware sets `request.state.trace_id` and a `X-Trace-Id` response
  header. All loggers on `logging.getLogger(__name__)` go to stdout in the
  normal format; to surface the id in Loki add `%(trace_id)s` to your
  LogRecord via `extra={"trace_id": ...}` at call sites you care about.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


def init_sentry(service_name: str, *, default_sample_rate: float = 0.05) -> bool:
    """
    Initialise the Sentry SDK if SENTRY_DSN is configured.
    Returns True when Sentry was activated.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info(f"[sentry] SENTRY_DSN not set — skipping init for {service_name}")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("ENVIRONMENT", "prod"),
            release=os.getenv("GIT_SHA") or os.getenv("SERVICE_VERSION") or "unknown",
            server_name=os.getenv("HOSTNAME") or service_name,
            traces_sample_rate=float(
                os.getenv("SENTRY_TRACES_SAMPLE_RATE", str(default_sample_rate))
            ),
            # PII off by default — X-User-JWT claims flow through tags instead.
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
        sentry_sdk.set_tag("service", service_name)
        org_id = os.getenv("ORG_ID")
        dept_id = os.getenv("DEPT_ID") or os.getenv("DEPT_IDS")
        if org_id:
            sentry_sdk.set_tag("org", org_id)
        if dept_id:
            sentry_sdk.set_tag("dept", dept_id)
        logger.info(f"[sentry] initialised for service={service_name}")
        return True
    except Exception as exc:
        logger.warning(f"[sentry] init failed: {exc}")
        return False


def install_trace_id_middleware(app) -> None:
    """
    Attach an ASGI middleware that assigns a `trace_id` to every request.

    Priority:
      1. Incoming `X-Trace-Id` header (propagated from an upstream caller)
      2. Freshly generated uuid4 hex

    The id is:
      • exposed as `request.state.trace_id`
      • echoed back in the `X-Trace-Id` response header
      • set as a Sentry tag for the request scope (if Sentry is active)
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    try:
        import sentry_sdk
    except Exception:
        sentry_sdk = None  # type: ignore[assignment]

    class TraceIdMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            incoming = request.headers.get("x-trace-id")
            trace_id = incoming if incoming else uuid.uuid4().hex
            request.state.trace_id = trace_id
            if sentry_sdk is not None:
                with sentry_sdk.configure_scope() as scope:
                    scope.set_tag("trace_id", trace_id)
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response

    app.add_middleware(TraceIdMiddleware)


def current_trace_id(request) -> Optional[str]:
    """Helper so handlers can grab the active trace id."""
    try:
        return getattr(request.state, "trace_id", None)
    except Exception:
        return None
