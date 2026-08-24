# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
citra-service-utils
===================
Shared infrastructure utilities for every Citra Python service.

Modules
-------
- ``circuit_breaker`` — async-aware breaker for outbound HTTP calls.
  Use to wrap calls to upstream services (action-sandbox-host, MCP
  servers, third-party APIs) so a hung dependency fails fast instead
  of starving the caller's connection pool.

- ``tracing`` — OpenTelemetry setup + helpers. Single ``setup_tracing(app)``
  call instruments FastAPI inbound + httpx outbound. Adds
  ``X-Request-ID`` propagation as a fallback for non-OTel-aware peers.

- ``milvus_client`` — singleton MilvusClient + lifecycle helpers. Same
  PID-aware / channel-liveness-aware pattern Citra-Service has used in
  production. ``pymilvus`` is a soft dep (imported lazily inside the
  functions) so consumers that never touch Milvus aren't forced to
  install it.

Why this package exists
-----------------------
Every Citra service needs the same handful of cross-cutting helpers
(timeout shells, breakers, trace context, vector store client). Without
a shared package they get re-implemented (or worse, omitted) per service.
One package keeps the patterns consistent and the surface area small.
"""
from .circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from .data_classifier import classify_column, classify_value
from .milvus_client import (
    MilvusSettings,
    close_milvus_client,
    create_throwaway_milvus_client,
    get_milvus_client,
    recreate_milvus_client,
)
from .tracing import (
    setup_tracing,
    get_request_id,
    outbound_headers,
    request_id_middleware,
)
from .vault_bootstrap import load_from_vault, VaultBootstrapError
from .require_env import (
    MissingConfigError,
    require_env,
    require_env_int,
    require_env_url,
)

__all__ = [
    "classify_column",
    "classify_value",
    "MissingConfigError",
    "require_env",
    "require_env_int",
    "require_env_url",
    "CircuitBreaker",
    "CircuitBreakerOpen",
    "MilvusSettings",
    "close_milvus_client",
    "create_throwaway_milvus_client",
    "get_milvus_client",
    "recreate_milvus_client",
    "setup_tracing",
    "get_request_id",
    "outbound_headers",
    "request_id_middleware",
    "load_from_vault",
    "VaultBootstrapError",
]

__version__ = "1.3.0"
