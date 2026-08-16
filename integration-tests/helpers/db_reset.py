# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Reset the test database between sessions.

The golden rule: NEVER touch a Mongo database that doesn't end with
``_integration_test``. Same rule for Milvus collections (must start
with ``itest_``) and Redis DB (must equal 15).

Called from ``conftest.py`` session-start and session-end hooks so each
run starts clean. The watchdog in this module hard-fails if the env
points at a non-test target — defense against accidentally torching a
prod DB.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# ── Watchdog ─────────────────────────────────────────────────────────────


_TEST_DB_SUFFIX = "_integration_test"
_TEST_MILVUS_PREFIX = "itest_"
_TEST_REDIS_DB = 15


def assert_test_targets() -> None:
    """Raise loudly if any of the env targets a non-test instance.

    Safety net so CI-misconfig doesn't drop a prod database. Called from
    conftest before any reset.
    """
    db = os.getenv("MONGODB_DATABASE", "")
    if not db.endswith(_TEST_DB_SUFFIX):
        raise RuntimeError(
            f"REFUSING TO RESET: MONGODB_DATABASE='{db}' does not end with "
            f"'{_TEST_DB_SUFFIX}'. Set MONGODB_DATABASE=citra_integration_test."
        )

    milvus_prefix = os.getenv("MILVUS_COLLECTION_PREFIX", "")
    if milvus_prefix and not milvus_prefix.startswith(_TEST_MILVUS_PREFIX):
        raise RuntimeError(
            f"REFUSING TO RESET: MILVUS_COLLECTION_PREFIX='{milvus_prefix}' "
            f"does not start with '{_TEST_MILVUS_PREFIX}'."
        )

    redis_db = int(os.getenv("REDIS_DB", "0"))
    if redis_db != _TEST_REDIS_DB:
        raise RuntimeError(
            f"REFUSING TO RESET: REDIS_DB={redis_db} is not {_TEST_REDIS_DB}."
        )


# ── Mongo ────────────────────────────────────────────────────────────────


async def drop_mongo_db() -> None:
    """Drop the entire test database."""
    assert_test_targets()
    from motor.motor_asyncio import AsyncIOMotorClient

    conn = (
        os.getenv("MONGODB_CONN_STRING")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = os.getenv("MONGODB_DATABASE", "citra_integration_test")
    client = AsyncIOMotorClient(conn)
    await client.drop_database(db_name)
    logger.info("[db_reset] dropped Mongo database %s", db_name)


# ── Milvus ───────────────────────────────────────────────────────────────


def drop_milvus_collections() -> None:
    """Drop every Milvus collection whose name starts with the test prefix."""
    prefix = os.getenv("MILVUS_COLLECTION_PREFIX", _TEST_MILVUS_PREFIX)
    if not prefix.startswith(_TEST_MILVUS_PREFIX):
        raise RuntimeError(
            f"refusing to drop Milvus collections with prefix '{prefix}'"
        )

    try:
        from pymilvus import MilvusClient
    except ImportError:
        logger.warning("pymilvus not installed — skipping Milvus reset")
        return

    uri = os.getenv("MILVUS_URI", "")
    token = os.getenv("MILVUS_TOKEN", "")
    if not uri:
        logger.info("[db_reset] MILVUS_URI not set; skipping Milvus reset")
        return

    kwargs = {"uri": uri, "timeout": 10}
    if token:
        kwargs["token"] = token
    client = MilvusClient(**kwargs)
    dropped = 0
    for name in client.list_collections() or []:
        if name.startswith(prefix):
            try:
                client.drop_collection(name)
                dropped += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("[db_reset] failed to drop %s: %s", name, exc)
    logger.info("[db_reset] dropped %d Milvus collection(s) with prefix %s", dropped, prefix)


# ── Redis ────────────────────────────────────────────────────────────────


def flush_redis_db() -> None:
    """FLUSHDB on the test Redis db."""
    assert_test_targets()
    try:
        import redis
    except ImportError:
        logger.warning("redis not installed — skipping Redis reset")
        return

    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "15"))
    r = redis.Redis(host=host, port=port, db=db)
    try:
        r.flushdb()
        logger.info("[db_reset] flushed Redis DB %d on %s:%d", db, host, port)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[db_reset] Redis flush failed: %s", exc)


# ── One-shot reset ───────────────────────────────────────────────────────


async def reset_all() -> None:
    """Reset Mongo + Milvus + Redis. Idempotent."""
    assert_test_targets()
    await drop_mongo_db()
    drop_milvus_collections()
    flush_redis_db()


def reset_all_sync() -> None:
    """Sync wrapper for pytest hooks (which can't be async)."""
    asyncio.run(reset_all())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    reset_all_sync()
