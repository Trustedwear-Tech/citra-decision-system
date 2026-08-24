# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
TablePool — Persistent DuckDB table management with TTL.

Instead of creating/destroying DuckDB connections per request (the old
ephemeral pattern), tables stay loaded in memory and are shared across
all incoming requests from any Citra-Service instance.

Key behaviours:
- One DuckDB connection per user (all of that user's tables live together)
- ``ensure_tables_loaded()`` checks what's already loaded, loads only missing docs
- TTL-based eviction: idle tables are cleaned up after TABLE_TTL seconds
- Background cleanup task runs every CLEANUP_INTERVAL seconds
"""
import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from config import config
from data_loader import bulk_fetch_all_records

logger = logging.getLogger(__name__)


def _apply_duckdb_tuning(con: duckdb.DuckDBPyConnection) -> None:
    """Tune a per-user DuckDB connection for warehouse-scale joins.

    duckdb-query-service holds one connection per user with many tables
    loaded simultaneously (cross-document analytical queries). The pool
    is shared across all Citra-Service callers on this
    process — so without a memory cap a single user's runaway join can
    OOM the service.

    Defaults via env:
      DUCKDB_MEMORY_LIMIT             default 4GB
      DUCKDB_THREADS                  default 4
      DUCKDB_TEMP_DIRECTORY           default /tmp/duckdb-query-service
      DUCKDB_MAX_TEMP_DIRECTORY_SIZE  default 10GB
    """
    settings = [
        ("memory_limit", os.getenv("DUCKDB_MEMORY_LIMIT", "4GB")),
        ("threads", os.getenv("DUCKDB_THREADS", "4")),
        ("temp_directory", os.getenv("DUCKDB_TEMP_DIRECTORY", "/tmp/duckdb-query-service")),
        ("max_temp_directory_size", os.getenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "10GB")),
    ]
    for key, value in settings:
        if not value:
            continue
        try:
            if key == "temp_directory":
                os.makedirs(value, exist_ok=True)
            safe = str(value).replace("'", "''")
            con.execute(f"SET {key} = '{safe}'")
        except Exception as exc:  # noqa: BLE001
            logger.info("[TablePool] SET %s failed (%s) — skipping", key, exc)


class _UserPool:
    """One DuckDB in-memory connection holding all tables for a single user."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.conn = duckdb.connect(":memory:")
        _apply_duckdb_tuning(self.conn)
        # {document_id: table_meta}
        self.tables: Dict[str, Dict[str, Any]] = {}
        self.created_at = time.time()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self.tables.clear()


class TablePool:
    """Global pool of per-user DuckDB connections with TTL eviction."""

    def __init__(self):
        self._users: Dict[str, _UserPool] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    # ── lifecycle ──

    def start_cleanup_loop(self):
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for up in self._users.values():
                up.close()
            self._users.clear()
        logger.info("[TablePool] Shutdown complete")

    # ── public API ──

    async def ensure_tables_loaded(
        self,
        user_id: str,
        document_ids: List[str],
        schema_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Guarantee the requested documents are loaded in DuckDB.

        Returns the same dict shape as the old ``load_multi_table``:
            {conn, tables: [table_info...], total_rows}

        Only documents NOT already in the pool are fetched from MongoDB.
        """
        async with self._lock:
            up = self._users.get(user_id)
            if up is None:
                up = _UserPool(user_id)
                self._users[user_id] = up

        t_start = time.time()
        loaded_tables: List[Dict[str, Any]] = []
        total_rows = 0
        already_count = 0

        for doc_id in document_ids:
            info = up.tables.get(doc_id)
            if info is not None:
                # Already loaded — touch timestamp and reuse
                info["last_accessed"] = time.time()
                loaded_tables.append(info)
                total_rows += info["row_count"]
                already_count += 1
                continue

            # Need to load this document
            try:
                records = await bulk_fetch_all_records(user_id, doc_id)
                if not records:
                    logger.warning("[TablePool] No records for doc %s (user=%s, db=%s)", doc_id, user_id, config.MONGODB_DATABASE)
                    continue

                meta = (schema_metadata_map or {}).get(doc_id, {})
                filename = meta.get("filename", f"doc_{doc_id[:8]}")
                table_name = re.sub(r"[^a-zA-Z0-9_]", "_", filename.rsplit(".", 1)[0])[:50]
                table_name = f"t_{table_name}"

                t_load = time.time()
                _create_table_from_records(up.conn, table_name, records)

                row_count = up.conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                columns = [
                    desc[0]
                    for desc in up.conn.execute(f'SELECT * FROM "{table_name}" LIMIT 0').description
                ]
                type_info = up.conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                column_types = {row[1]: row[2] for row in type_info}

                info = {
                    "table_name": table_name,
                    "document_id": doc_id,
                    "filename": filename,
                    "columns": columns,
                    "column_types": column_types,
                    "row_count": row_count,
                    "loaded_at": time.time(),
                    "last_accessed": time.time(),
                }
                up.tables[doc_id] = info
                loaded_tables.append(info)
                total_rows += row_count

                logger.info(
                    "[TablePool] Loaded '%s' (%d rows) in %.3fs",
                    table_name, row_count, time.time() - t_load,
                )
            except Exception as e:
                logger.error("[TablePool] Failed to load doc %s: %s", doc_id, e, exc_info=True)

        if not loaded_tables:
            return None

        logger.info(
            "[TablePool] ensure_tables_loaded: %d tables ready (%d reused, %d new) in %.3fs (%d total rows)",
            len(loaded_tables), already_count, len(loaded_tables) - already_count,
            time.time() - t_start, total_rows,
        )
        return {
            "conn": up.conn,
            "tables": loaded_tables,
            "total_rows": total_rows,
        }

    def get_user_tables(self, user_id: str) -> List[Dict[str, Any]]:
        up = self._users.get(user_id)
        if up is None:
            return []
        return list(up.tables.values())

    async def evict(
        self, user_id: str, document_ids: Optional[List[str]] = None
    ):
        """Evict specific tables (or all) for a user."""
        async with self._lock:
            up = self._users.get(user_id)
            if up is None:
                return
            if document_ids is None:
                up.close()
                del self._users[user_id]
                logger.info("[TablePool] Evicted all tables for user %s", user_id)
            else:
                for doc_id in document_ids:
                    info = up.tables.pop(doc_id, None)
                    if info:
                        try:
                            up.conn.execute(f'DROP TABLE IF EXISTS "{info["table_name"]}"')
                        except Exception:
                            pass
                if not up.tables:
                    up.close()
                    del self._users[user_id]
                logger.info(
                    "[TablePool] Evicted %d tables for user %s", len(document_ids), user_id,
                )

    # ── stats ──

    @property
    def total_tables(self) -> int:
        return sum(len(up.tables) for up in self._users.values())

    @property
    def total_users(self) -> int:
        return len(self._users)

    # ── background cleanup ──

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(config.CLEANUP_INTERVAL)
            try:
                await self._cleanup_expired()
            except Exception as e:
                logger.error("[TablePool] Cleanup error: %s", e, exc_info=True)

    async def _cleanup_expired(self):
        now = time.time()
        evicted_count = 0
        async with self._lock:
            empty_users = []
            for user_id, up in self._users.items():
                expired_docs = [
                    doc_id
                    for doc_id, info in up.tables.items()
                    if (now - info["last_accessed"]) > config.TABLE_TTL
                ]
                for doc_id in expired_docs:
                    info = up.tables.pop(doc_id)
                    try:
                        up.conn.execute(f'DROP TABLE IF EXISTS "{info["table_name"]}"')
                    except Exception:
                        pass
                    evicted_count += 1
                if not up.tables:
                    up.close()
                    empty_users.append(user_id)
            for uid in empty_users:
                del self._users[uid]

            # Hard cap: if total tables exceed MAX_TABLES, evict oldest
            if self.total_tables > config.MAX_TABLES:
                all_entries = []
                for uid, up in self._users.items():
                    for doc_id, info in up.tables.items():
                        all_entries.append((uid, doc_id, info["last_accessed"]))
                all_entries.sort(key=lambda x: x[2])  # oldest first
                while self.total_tables > config.MAX_TABLES and all_entries:
                    uid, doc_id, _ = all_entries.pop(0)
                    up = self._users.get(uid)
                    if up and doc_id in up.tables:
                        info = up.tables.pop(doc_id)
                        try:
                            up.conn.execute(f'DROP TABLE IF EXISTS "{info["table_name"]}"')
                        except Exception:
                            pass
                        evicted_count += 1
                        if not up.tables:
                            up.close()
                            del self._users[uid]

        if evicted_count:
            logger.info("[TablePool] Cleanup: evicted %d tables, %d remain", evicted_count, self.total_tables)


def _create_table_from_records(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    records: List[Dict[str, Any]],
):
    """Create a DuckDB table from record dicts via pandas (auto-type detection)."""
    df = pd.DataFrame(records)
    df = df.replace("", None)
    conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM df')


# ── Module-level singleton ──
_pool: TablePool | None = None


def get_table_pool() -> TablePool:
    global _pool
    if _pool is None:
        _pool = TablePool()
    return _pool
