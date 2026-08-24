# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
pytest session config for the integration-tests suite.

Session hooks reset the test DB / Milvus / Redis at start, and tear
down at end. Per-test fixtures provide:

  * ``acme_ba``, ``acme_admin``, ``bravo_ba`` — pre-seeded user records
  * ``acme_ba_headers`` etc. — auth headers for each persona
  * ``mongo_db`` — async Motor handle to the test DB
  * ``smart_app_url``, ``fake_mcp_url`` — stub endpoints (overridable
    via env)

The watchdog in ``helpers.db_reset.assert_test_targets()`` blocks any
session that points at a non-test DB. This is intentional: tests must
NEVER drop a prod database, full stop.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import pytest_asyncio

# Ensure helpers/ is importable from the scenarios.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from helpers import jwt_mint  # noqa: E402
from helpers.db_reset import assert_test_targets  # noqa: E402
from helpers.seed import _get_db, reset_and_seed  # noqa: E402

logger = logging.getLogger(__name__)


# ── Defaults — can be overridden via env in CI ──────────────────────────


def _default_env() -> None:
    """Set safe test defaults so tests don't accidentally hit prod.

    Each variable is only set if not already in the environment, so CI
    can override.
    """
    defaults = {
        # Mongo / Redis / Milvus point at the off-band ports used by
        # docker-compose.test.yml so tests don't collide with a dev stack.
        "MONGODB_DATABASE": "citra_integration_test",
        "MONGODB_CONN_STRING": "mongodb://localhost:27018",
        "MILVUS_COLLECTION_PREFIX": "itest_default_",
        "MILVUS_URI": "http://localhost:19531",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6380",
        "REDIS_DB": "15",
        "JWT_SECRET": "test-only-not-for-prod",
        "SMART_APP_SERVICE_URL": "http://localhost:9100",
        "FAKE_MCP_BASE_URL": "http://localhost:8500",
        "FAKE_LLM_BASE_URL": "http://localhost:8600",
        "FAKE_NOTIFY_URL": "http://localhost:8700",
        "LLM_MODE": "mock",  # 'mock' or 'real' (real = OpenAI)
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)


_default_env()


# ── Session hooks ───────────────────────────────────────────────────────


def pytest_sessionstart(session: pytest.Session) -> None:  # noqa: ARG001
    """Hard-validate test targets, then reset + seed the database.

    If Mongo is unreachable, the watchdog still runs (so we never seed a
    non-test DB) but the seed step is skipped with a warning rather than
    aborting the session — that lets pure-Python unit-style tests
    (test_03, test_04, test_11) still collect and run without a stack.

    Set ``REQUIRE_DB=1`` to make seed failures fatal (use in CI).
    """
    assert_test_targets()
    try:
        counts = asyncio.run(reset_and_seed())
        logger.info("[conftest] session start; seeded %s", counts)
    except Exception as exc:  # noqa: BLE001
        if os.getenv("REQUIRE_DB") == "1":
            raise
        logger.warning(
            "[conftest] DB seed skipped (%s); pure-Python tests will still "
            "run, integration tests will fail their own connection check.",
            exc,
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    """Optional: drop the test DB at session end so the next run is clean.

    Skipped when ``KEEP_TEST_DB=1`` so engineers can introspect a flaky
    run's final Mongo state.
    """
    if os.getenv("KEEP_TEST_DB") == "1":
        logger.info("[conftest] KEEP_TEST_DB=1 — leaving test DB intact")
        return
    from helpers.db_reset import reset_all
    try:
        asyncio.run(reset_all())
        logger.info("[conftest] session end; test DB reset")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[conftest] session-end reset skipped: %s", exc)


# ── User personas ───────────────────────────────────────────────────────


@pytest.fixture
def acme_ba() -> Dict[str, Any]:
    return jwt_mint.ACME_BA.copy()


@pytest.fixture
def acme_admin() -> Dict[str, Any]:
    return jwt_mint.ACME_OPS_ADMIN.copy()


@pytest.fixture
def bravo_ba() -> Dict[str, Any]:
    return jwt_mint.BRAVO_BA.copy()


@pytest.fixture
def builder_service() -> Dict[str, Any]:
    return jwt_mint.BUILDER_SERVICE.copy()


# ── Auth header bundles ─────────────────────────────────────────────────


@pytest.fixture
def acme_ba_headers(acme_ba) -> Dict[str, str]:
    return jwt_mint.auth_headers(acme_ba)


@pytest.fixture
def acme_admin_headers(acme_admin) -> Dict[str, str]:
    return jwt_mint.auth_headers(acme_admin)


@pytest.fixture
def bravo_ba_headers(bravo_ba) -> Dict[str, str]:
    return jwt_mint.auth_headers(bravo_ba)


@pytest.fixture
def builder_headers(builder_service) -> Dict[str, str]:
    return jwt_mint.auth_headers(builder_service)


# ── Mongo handle ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def mongo_db():
    """Yield an async Motor handle to the test database.

    Reseeded between tests via the ``reset_per_test`` autouse fixture
    when ``RESET_PER_TEST=1`` (off by default — session-start reset is
    enough for most scenarios).
    """
    db = _get_db()
    yield db


@pytest_asyncio.fixture(autouse=False)
async def reset_per_test():
    """Opt-in fixture: reset DB before every test.

    Pulls a fresh fixture set every time; expensive but bulletproof for
    tests that mutate state heavily (e.g. concurrency tests).
    """
    await reset_and_seed()
    yield


# ── Service URLs ────────────────────────────────────────────────────────


@pytest.fixture
def smart_app_service_url() -> str:
    return os.environ["SMART_APP_SERVICE_URL"].rstrip("/")


@pytest.fixture
def fake_mcp_url() -> str:
    return os.environ["FAKE_MCP_BASE_URL"].rstrip("/")


@pytest.fixture
def fake_llm_url() -> str:
    return os.environ["FAKE_LLM_BASE_URL"].rstrip("/")


@pytest.fixture
def fake_notify_url() -> str:
    return os.environ["FAKE_NOTIFY_URL"].rstrip("/")
