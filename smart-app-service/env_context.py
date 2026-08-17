# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Request-scoped environment (test ↔ prod).

A SmartApp request runs in one environment. The builder/build path is always
``test``; a run of a published app resolves by store (see
``resolve_app_environment`` in main.py). The value is held in a contextvar so
the Mongo accessors (main.py) and the discovery helpers (Settings.*_for) pick
the right target WITHOUT threading a parameter through every signature.

Lives in its own module (no imports) so deep modules — proxy_clients,
panel_data, dataset_directory, capabilities, runtime, trigger_runner — can read
``current_env()`` without a circular import on ``main``.

**Default is "prod".** Anything that does not explicitly set the environment
keeps prod behaviour. Because the app *definition* collections are
environment-routed (a test app lives only in the test db), a handler that
forgets to set ``test`` simply fails to load the test app (404) — it can never
proceed to run a test app against prod sources. Fail-closed, never corruption.
"""

from __future__ import annotations

import contextvars

_current_environment: contextvars.ContextVar[str] = contextvars.ContextVar(
    "smartapp_environment", default="prod"
)


#: The embed key presented on this request, if any (``X-Citra-Embed-Key``).
#:
#: Needed because the "resolve by store" rule above is not sufficient for an
#: EMBEDDED card. The embed's first call resolves its environment from the key
#: prefix, but every call after it — run, panel data, detail, approve — is
#: addressed by SLUG, and a PROMOTED app exists in both stores, so slug
#: resolution returns prod. A bank's UAT card would therefore read and write
#: PRODUCTION records after the first promote.
#:
#: The key travels on every embed request so the environment can be re-derived
#: per call. It is NOT a caller-chosen environment: main._bind_app_env verifies
#: the key actually exists in that environment's store bound to the requested
#: slug, so a page can only reach an environment it genuinely holds a key for.
_embed_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "smartapp_embed_key", default=None
)


def current_env() -> str:
    return _current_environment.get()


def current_embed_key() -> str | None:
    return _embed_key.get()


def set_current_embed_key(key: str | None) -> None:
    _embed_key.set(key or None)


def set_current_env(environment: str | None) -> None:
    """Set the environment for the current request/task. Anything other than
    'test' is normalised to 'prod' (safe default)."""
    _current_environment.set("test" if environment == "test" else "prod")
