# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Distributed tracing setup for Citra services.

Two layers:

1. **OpenTelemetry** — auto-instruments FastAPI inbound + httpx outbound
   so spans propagate through the W3C ``traceparent`` header. Spans go
   to an OTLP exporter when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set,
   else to the console (dev mode).

2. **X-Request-ID** — fallback for non-OTel-aware peers. A middleware
   reads the inbound header (or generates one) and stashes it on
   ``request.state.request_id``. ``outbound_headers(request)`` produces
   the dict to forward when calling another service.

Why both?
---------
OTel handles spans cleanly within instrumented services, but some
peers (action-sandbox-host, the sandbox containers
themselves) don't yet auto-propagate. ``X-Request-ID`` gives every
log line a stable correlation key regardless of OTel adoption.

Typical use::

    from citra_service_utils import setup_tracing, request_id_middleware

    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    setup_tracing(app, service_name="citra-service")

To propagate to an outbound HTTP call::

    from citra_service_utils import outbound_headers

    headers = outbound_headers(request)        # {"X-Request-ID": "...", "traceparent": "..."}
    headers["X-Sandbox-Host-Secret"] = secret
    await client.post(url, json=payload, headers=headers)
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"


# ── OpenTelemetry setup ────────────────────────────────────────────────────


_TRACING_INITIALISED = False


def setup_tracing(app: Any, service_name: str, excluded_urls: Optional[str] = None) -> None:
    """Wire OpenTelemetry into a FastAPI app + httpx clients.

    Idempotent — safe to call multiple times in a process; only the
    first call has effect.

    Exporter selection:
      * If ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set → OTLP-HTTP exporter.
      * Else → console exporter (writes spans to stdout, useful in dev).

    No-ops if OpenTelemetry packages aren't installed (fresh-clone
    tolerance — services should still boot without OTel deps).
    """
    global _TRACING_INITIALISED
    if _TRACING_INITIALISED:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError as exc:
        logger.warning(
            f"⚠️ tracing: OpenTelemetry packages not installed ({exc}); "
            f"X-Request-ID propagation still works but no spans will be emitted"
        )
        _TRACING_INITIALISED = True
        return

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    console_enabled = os.getenv("OTEL_CONSOLE_EXPORTER", "false").lower() == "true"

    if not otlp_endpoint and not console_enabled:
        logger.info("🔭 tracing: disabled (no OTLP endpoint; OTEL_CONSOLE_EXPORTER not set)")
        _TRACING_INITIALISED = True
        return

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            # When ``endpoint`` is passed explicitly the OTLP-HTTP exporter uses
            # it verbatim — it does NOT append the ``/v1/traces`` signal path the
            # way it does when reading OTEL_EXPORTER_OTLP_ENDPOINT from the env
            # itself. A bare base URL (e.g. http://citra-tempo:4318) therefore
            # POSTs to the collector root and gets a 404 on every batch flush.
            # Normalise to the signal path so a base URL just works.
            traces_endpoint = otlp_endpoint.rstrip("/")
            if not traces_endpoint.endswith("/v1/traces"):
                traces_endpoint += "/v1/traces"
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint))
            )
            logger.info(f"🔭 tracing: OTLP exporter → {traces_endpoint}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️ tracing: OTLP exporter setup failed: {exc} — using console")
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif console_enabled:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("🔭 tracing: console exporter enabled")

    trace.set_tracer_provider(provider)

    # Auto-instrument FastAPI inbound + httpx outbound. Both are no-ops
    # if the libraries aren't loaded by the calling service.
    effective_excluded = excluded_urls or os.getenv("OTEL_EXCLUDED_URLS")
    if effective_excluded:
        FastAPIInstrumentor.instrument_app(app, excluded_urls=effective_excluded)
    else:
        FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    _TRACING_INITIALISED = True
    logger.info(f"🔭 tracing: instrumented service '{service_name}'")


# ── X-Request-ID middleware (works without OTel) ───────────────────────────


async def request_id_middleware(request: Any, call_next: Any) -> Any:
    """FastAPI middleware: read or mint X-Request-ID, stash on request.state.

    Wire as::

        app.middleware("http")(request_id_middleware)
    """
    request_id = request.headers.get(REQUEST_ID_HEADER) or _new_request_id()
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def get_request_id(request: Any) -> Optional[str]:
    """Return the request-id stashed by the middleware (or None)."""
    return getattr(getattr(request, "state", None), "request_id", None)


def _new_request_id() -> str:
    """Mint a new request id. UUID4 hex (32 chars), no dashes."""
    return uuid.uuid4().hex


# ── Outbound header helper ─────────────────────────────────────────────────


def outbound_headers(request: Any) -> Dict[str, str]:
    """Return headers to forward when calling another service.

    Includes:
      * ``X-Request-ID`` if the inbound request has one (or mints one)
      * ``traceparent`` / ``tracestate`` (W3C) if propagation context
        is available — auto-populated by OTel httpx instrumentation,
        but we set the headers here too as a fallback for non-OTel
        peers.
    """
    headers: Dict[str, str] = {}

    rid = get_request_id(request)
    if rid:
        headers[REQUEST_ID_HEADER] = rid

    # If OTel is set up, the current span's context can be injected.
    try:
        from opentelemetry import trace
        from opentelemetry.propagate import inject

        # inject() mutates a dict-like with traceparent/tracestate.
        inject(headers)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"tracing: propagate.inject failed: {exc}")

    return headers
