<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-service-utils

Shared infrastructure for Citra Python services: distributed tracing,
circuit breakers, and request-id propagation.

## Why this package exists

Every Citra service needs the same handful of cross-cutting helpers
(timeout shells, breakers, trace context). Without a shared package
they get re-implemented (or worse, omitted) per service. One package
keeps the patterns consistent and the surface area small.

## Install (in-monorepo)

In each service's `requirements.txt`:

```
-e ../citra-service-utils
```

## Modules

### `circuit_breaker`

```python
from citra_service_utils import CircuitBreaker, CircuitBreakerOpen

sandbox_breaker = CircuitBreaker(
    name="action-sandbox-host",
    failure_threshold=3,
    recovery_timeout=30.0,
)

async def spawn_sandbox(payload):
    async def _call():
        async with httpx.AsyncClient(timeout=90.0) as http:
            return await http.post(url, json=payload, headers=hdrs)
    try:
        return await sandbox_breaker.call(_call)
    except CircuitBreakerOpen as exc:
        # Sandbox host has failed 3+ times; fail fast for next 30 s.
        raise SandboxUnavailable(str(exc))
```

States: `CLOSED` → `OPEN` (after `failure_threshold` consecutive
failures) → `HALF_OPEN` (after `recovery_timeout`) → `CLOSED` (on
success) or `OPEN` (on failure).

### `tracing`

One-time setup at app startup:

```python
from fastapi import FastAPI
from citra_service_utils import setup_tracing, request_id_middleware

app = FastAPI()
app.middleware("http")(request_id_middleware)
setup_tracing(app, service_name="citra-service")
```

Forwarding to another service:

```python
from citra_service_utils import outbound_headers

headers = outbound_headers(request)
headers["X-Sandbox-Host-Secret"] = secret
await client.post(url, json=payload, headers=headers)
```

`outbound_headers()` returns:
- `X-Request-ID` (always — minted if inbound didn't have one)
- `traceparent` + `tracestate` (W3C OTel context, when OTel is set up)

### Configuration

| Env var                          | Effect                                          |
|----------------------------------|--------------------------------------------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT`    | Send spans to this OTLP-HTTP collector          |
| (unset)                          | Spans go to console (dev mode)                  |

## Behaviour without OpenTelemetry installed

If a service doesn't install the OTel packages, `setup_tracing()` logs
a warning and no-ops. `request_id_middleware` and `outbound_headers()`
still work, so `X-Request-ID` correlation continues regardless.

This is intentional — services should boot fresh-cloned without the
full OTel stack.
