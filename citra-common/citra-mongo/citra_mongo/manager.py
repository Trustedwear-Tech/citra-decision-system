# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Centralised MongoDB connection manager.

Singleton-per-process; opens one async + one sync client with the pool
settings configured via env vars. Detects asyncio-loop changes (Gunicorn
pre-fork) and recreates the async client when needed so we don't leak
clients tied to dead event loops.

Originally lived as `Citra-Service/mongodb_manager.py` and was used by
every Python service via PYTHONPATH. Extracted here so each service can
depend on it explicitly via `-e ../citra-mongo`.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

logger = logging.getLogger(__name__)


class MongoDBManager:
    """Singleton MongoDB connection manager with connection pooling."""

    _instance: Optional["MongoDBManager"] = None
    _async_client: Optional[AsyncIOMotorClient] = None
    _sync_client: Optional[MongoClient] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MongoDBManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self.mongo_conn_string = os.getenv("MONGODB_CONN_STRING")
        if not self.mongo_conn_string:
            raise ValueError("MONGODB_CONN_STRING environment variable is required")

        self.database_name = os.getenv("MONGODB_DATABASE", "citra-ai")
        self.app_name = os.getenv("MONGODB_APP_NAME", "citra")

        self.max_pool_size = int(os.getenv("MONGODB_MAX_POOL_SIZE", "20"))
        self.min_pool_size = int(os.getenv("MONGODB_MIN_POOL_SIZE", "1"))
        self.max_idle_time_ms = int(os.getenv("MONGODB_MAX_IDLE_TIME_MS", "60000"))
        self.server_selection_timeout_ms = int(os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "30000"))
        self.connect_timeout_ms = int(os.getenv("MONGODB_CONNECT_TIMEOUT_MS", "10000"))
        self.socket_timeout_ms = int(os.getenv("MONGODB_SOCKET_TIMEOUT_MS", "60000"))
        self.heartbeat_frequency_ms = int(os.getenv("MONGODB_HEARTBEAT_FREQUENCY_MS", "30000"))

        logger.info(f"🗄️ MongoDB Manager initialized for database: {self.database_name}")
        logger.info(f"🔧 Pool: {self.min_pool_size}-{self.max_pool_size}, "
                    f"idle: {self.max_idle_time_ms}ms, app: {self.app_name}")

    def _common_opts(self):
        return dict(
            maxPoolSize=self.max_pool_size,
            minPoolSize=self.min_pool_size,
            maxIdleTimeMS=self.max_idle_time_ms,
            serverSelectionTimeoutMS=self.server_selection_timeout_ms,
            connectTimeoutMS=self.connect_timeout_ms,
            socketTimeoutMS=self.socket_timeout_ms,
            retryWrites=True,
            retryReads=True,
            heartbeatFrequencyMS=self.heartbeat_frequency_ms,
            appName=self.app_name,
            compressors=["zstd", "snappy"],
        )

    def get_async_client(self) -> AsyncIOMotorClient:
        """Singleton async client. Recreates when the asyncio loop changes
        (Gunicorn workers forking) so we never keep a client tied to a dead loop."""
        try:
            import asyncio
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._async_client is not None:
            client_loop = getattr(self._async_client, "io_loop", None)
            if current_loop and client_loop and current_loop != client_loop:
                logger.info("🔄 Event loop changed (post-fork) — recreating async Mongo client")
                self._async_client = None

        if self._async_client is None:
            self._async_client = AsyncIOMotorClient(
                self.mongo_conn_string, **self._common_opts(),
            )
            logger.info(f"🚀 Async Mongo client ready (pool {self.min_pool_size}-{self.max_pool_size})")
        return self._async_client

    def get_sync_client(self) -> MongoClient:
        """Singleton sync client. PyMongo handles auto-reconnect via retry*."""
        if self._sync_client is not None:
            return self._sync_client
        self._sync_client = MongoClient(self.mongo_conn_string, **self._common_opts())
        logger.info(f"🚀 Sync Mongo client ready (pool {self.min_pool_size}-{self.max_pool_size})")
        return self._sync_client

    def get_async_database(self):
        return self.get_async_client()[self.database_name]

    def get_sync_database(self):
        return self.get_sync_client()[self.database_name]

    def get_async_collection(self, name: str):
        return self.get_async_database()[name]

    def get_sync_collection(self, name: str):
        return self.get_sync_database()[name]

    def close_connections(self):
        if self._async_client:
            self._async_client.close()
            self._async_client = None
            logger.info("🔒 Closed async Mongo connection")
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None
            logger.info("🔒 Closed sync Mongo connection")

    def get_connection_status(self) -> dict:
        return {
            "async_client_active": self._async_client is not None,
            "sync_client_active": self._sync_client is not None,
            "database_name": self.database_name,
            "connection_string_configured": bool(self.mongo_conn_string),
        }


# Lazy global instance — only initialised when first accessed, so callers
# that load vault secrets at startup don't race with import-time env reads.
_mongodb_manager: Optional[MongoDBManager] = None


def _get_manager() -> MongoDBManager:
    global _mongodb_manager
    if _mongodb_manager is None:
        _mongodb_manager = MongoDBManager()
    return _mongodb_manager


def get_async_mongo_client() -> AsyncIOMotorClient:
    return _get_manager().get_async_client()


def get_mongo_client() -> MongoClient:
    return _get_manager().get_sync_client()


def get_async_database():
    return _get_manager().get_async_database()


def get_sync_database():
    return _get_manager().get_sync_database()


def get_mongodb_manager() -> MongoDBManager:
    return _get_manager()


def close_all_connections():
    if _mongodb_manager is not None:
        _mongodb_manager.close_connections()


def get_database_name() -> str:
    return _get_manager().database_name


# Backwards-compat module-level constant. Reads env directly so it works
# even before _get_manager() is called the first time.
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "citra-ai")
