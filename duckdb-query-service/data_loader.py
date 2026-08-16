# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Data Loader — MongoDB bulk fetch with Redis caching.

Migrated from Citra-Service saas_embedding_service.bulk_fetch_all_records().
Owns the data path: MongoDB → (Redis cache) → caller.
"""
import asyncio
import base64
import json
import logging
import time
import zlib
from typing import Any, Dict, List

import redis
from motor.motor_asyncio import AsyncIOMotorClient

from config import config

logger = logging.getLogger(__name__)

# ── MongoDB ──
_mongo_client: AsyncIOMotorClient | None = None


def get_mongo_db():
    global _mongo_client
    if not config.MONGODB_URI:
        logger.warning("MONGODB_CONN_STRING is not set — MongoDB will not work. Check Vault/env config.")
        return None
    if not config.MONGODB_DATABASE:
        logger.warning("MONGODB_DATABASE is not set — MongoDB will not work. Check Vault/env config.")
        return None
    if _mongo_client is None:
        _mongo_client = AsyncIOMotorClient(config.MONGODB_URI)
        logger.info("MongoDB client initialized (db=%s)", config.MONGODB_DATABASE)
    return _mongo_client[config.MONGODB_DATABASE]


# ── Redis ──
_redis_pool: redis.ConnectionPool | None = None
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis | None:
    global _redis_pool, _redis_client
    if _redis_client is not None:
        return _redis_client
    if not config.REDIS_HOST:
        logger.warning("REDIS_HOST is not set — Redis caching disabled. Check Vault/env config.")
        return None
    try:
        pool_kwargs = dict(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            username=config.REDIS_USERNAME,
            max_connections=20,
            decode_responses=True,
        )
        pool_cls = redis.ConnectionPool
        if config.REDIS_SSL:
            pool_cls = redis.BlockingConnectionPool
            pool_kwargs["connection_class"] = redis.SSLConnection
        _redis_pool = pool_cls(**pool_kwargs)
        _redis_client = redis.Redis(connection_pool=_redis_pool)
        _redis_client.ping()
        logger.info("Redis connected for data_loader cache")
        return _redis_client
    except Exception as e:
        logger.warning("Redis unavailable for data_loader: %s", e)
        return None


# ── Per-document locks (prevent cache stampede) ──
_bulk_fetch_locks: Dict[str, asyncio.Lock] = {}
_MAX_BULK_LOCKS = 500


async def bulk_fetch_all_records(
    user_id: str,
    document_id: str,
) -> List[Dict[str, Any]]:
    """
    Fetch ALL raw records for a document from MongoDB saas_records.
    Redis-cached with compression for large payloads.
    """
    cache_key = f"saas_records:{user_id}:{document_id}"

    # Per-document lock to prevent cache stampede
    if document_id not in _bulk_fetch_locks:
        if len(_bulk_fetch_locks) >= _MAX_BULK_LOCKS:
            for key in list(_bulk_fetch_locks.keys()):
                if not _bulk_fetch_locks[key].locked():
                    del _bulk_fetch_locks[key]
                    break
        _bulk_fetch_locks[document_id] = asyncio.Lock()

    async with _bulk_fetch_locks[document_id]:
        # ── Try Redis cache first ──
        try:
            r = _get_redis()
            if r is not None:
                cached = r.get(cache_key)
                if cached is not None:
                    t0 = time.time()
                    if cached.startswith("Z:"):
                        raw_bytes = zlib.decompress(base64.b64decode(cached[2:]))
                        records = json.loads(raw_bytes)
                    else:
                        records = json.loads(cached)
                    logger.info(
                        "[DataLoader] CACHE HIT %s: %.3fs (%d records)",
                        document_id, time.time() - t0, len(records),
                    )
                    return records
        except Exception as e:
            logger.debug("Cache lookup skipped for %s: %s", document_id, e)

        # ── Cache miss — fetch from MongoDB ──
        try:
            t0 = time.time()
            db = get_mongo_db()
            if db is None:
                logger.error("[DataLoader] MongoDB not configured — cannot fetch records for %s", document_id)
                return []
            cursor = db.saas_records.find(
                {
                    "user_id": user_id,
                    "nango_connection_id": f"file_{document_id}",
                    "provider": "file_upload",
                },
                {"raw_data": 1, "_id": 0},
            )

            records: list[Dict[str, Any]] = []
            async for doc in cursor:
                raw_data = doc.get("raw_data")
                if raw_data and isinstance(raw_data, dict):
                    records.append(raw_data)

            logger.info(
                "[DataLoader] CACHE MISS %s: %.3fs (%d records)",
                document_id, time.time() - t0, len(records),
            )

            # ── Store in Redis ──
            try:
                r = _get_redis()
                if r is not None:
                    payload = json.dumps(records)
                    if len(payload) > 2_000_000:
                        compressed = base64.b64encode(
                            zlib.compress(payload.encode())
                        ).decode()
                        r.set(cache_key, f"Z:{compressed}", ex=config.BULK_FETCH_CACHE_TTL)
                        logger.info(
                            "[DataLoader] Cached %s: %dKB -> %dKB compressed (TTL=%ds)",
                            document_id, len(payload) // 1024,
                            len(compressed) // 1024, config.BULK_FETCH_CACHE_TTL,
                        )
                    else:
                        r.set(cache_key, payload, ex=config.BULK_FETCH_CACHE_TTL)
                        logger.info(
                            "[DataLoader] Cached %s: %dKB (TTL=%ds)",
                            document_id, len(payload) // 1024, config.BULK_FETCH_CACHE_TTL,
                        )
            except Exception as e:
                logger.debug("Cache store skipped for %s: %s", document_id, e)

            return records

        except Exception as e:
            logger.error("bulk_fetch_all_records failed for %s: %s", document_id, e, exc_info=True)
            raise
