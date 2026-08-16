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
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


def init_sentry(service_name: str, *, default_sample_rate: float = 0.05) -> bool:
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
    try:
        return getattr(request.state, "trace_id", None)
    except Exception:
        return None
