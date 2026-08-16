# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""The crawl leader-lease.

It exists for exactly one reason: two replicas booting together must not crawl
the same tenant twice (racing the upserts, re-embedding everything N times).

It is NOT a schedule. Sizing it like one is what broke prod: the TTL was derived
from `crawl_interval_seconds` (86400) left over from the nightly loop removed on
2026-07-01, so the lease lived ~24h. A redeploy recreates the container, which
changes HOSTNAME and therefore the lock holder — so the new process saw a valid
lease held by a name that no longer existed and skipped its crawl:

    INFO:main:Startup crawl skipped — another replica holds the crawl lease

while main.py's own docstring told operators that restarting the service is how
you re-crawl after a schema change. It was a no-op for a day at a time.

So: the holder releases when its pass ends, and the TTL is only the backstop for
a replica killed mid-crawl.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-secret")

import main as main_mod  # noqa: E402


class _Locks:
    """The bits of a Mongo collection the lease uses."""

    def __init__(self) -> None:
        self.doc: Optional[Dict[str, Any]] = None

    async def find_one_and_update(self, filt, update, upsert=False, return_document=None):
        now = filt["$or"][0]["expires_at"]["$lte"]
        holder = filt["$or"][1]["holder"]
        if self.doc is not None:
            free = self.doc["expires_at"] <= now or self.doc["holder"] == holder
            if not free:
                # Real Mongo raises DuplicateKeyError here: the filter matched
                # nothing, so upsert tries to insert a second doc with the same _id.
                from pymongo.errors import DuplicateKeyError
                raise DuplicateKeyError("catalogue-crawler")
        if self.doc is None and not upsert:
            return None
        self.doc = {"_id": "catalogue-crawler", **update["$set"]}
        return dict(self.doc)

    async def delete_one(self, filt):
        if self.doc and all(self.doc.get(k) == v for k, v in filt.items()):
            self.doc = None


class _DB:
    def __init__(self) -> None:
        self.locks = _Locks()

    def __getitem__(self, name):
        assert name == "crawler_locks"
        return self.locks


def _as(monkeypatch, instance_id: str) -> None:
    """Run the next call as a given replica/container."""
    monkeypatch.setattr(main_mod, "_INSTANCE_ID", instance_id)


@pytest.mark.asyncio
async def test_a_second_replica_cannot_crawl_while_a_pass_is_running(monkeypatch):
    """The lease's actual job."""
    db = _DB()
    _as(monkeypatch, "replica-a")
    assert await main_mod._acquire_crawl_leadership(db, 900) is True

    _as(monkeypatch, "replica-b")
    assert await main_mod._acquire_crawl_leadership(db, 900) is False


@pytest.mark.asyncio
async def test_a_redeploy_crawls_instead_of_waiting_out_the_old_lease(monkeypatch):
    """THE prod bug. A new container gets a new HOSTNAME, so it can never match
    the old holder — if the finished pass didn't release, it would skip its crawl
    until the TTL lapsed."""
    db = _DB()
    _as(monkeypatch, "container-old")
    assert await main_mod._acquire_crawl_leadership(db, 900) is True
    await main_mod._release_crawl_leadership(db)          # pass ended

    _as(monkeypatch, "container-new")                      # redeploy
    assert await main_mod._acquire_crawl_leadership(db, 900) is True


@pytest.mark.asyncio
async def test_release_only_drops_our_own_lease(monkeypatch):
    """A slow replica must never release the lease of whoever took over."""
    db = _DB()
    _as(monkeypatch, "replica-a")
    await main_mod._acquire_crawl_leadership(db, 900)

    _as(monkeypatch, "replica-b")
    await main_mod._release_crawl_leadership(db)           # not ours

    assert db.locks.doc is not None
    assert db.locks.doc["holder"] == "replica-a"


@pytest.mark.asyncio
async def test_a_lease_from_a_killed_replica_lapses(monkeypatch):
    """The TTL backstop: a replica killed mid-crawl never releases, so the lease
    must expire on its own."""
    db = _DB()
    _as(monkeypatch, "replica-dead")
    await main_mod._acquire_crawl_leadership(db, 900)
    db.locks.doc["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    _as(monkeypatch, "replica-live")
    assert await main_mod._acquire_crawl_leadership(db, 900) is True


@pytest.mark.asyncio
async def test_the_same_container_restarting_in_place_re_acquires(monkeypatch):
    """`docker restart` keeps HOSTNAME, so the holder matches and the lease is
    simply retaken — this path always worked, and must keep working."""
    db = _DB()
    _as(monkeypatch, "container-x")
    assert await main_mod._acquire_crawl_leadership(db, 900) is True
    assert await main_mod._acquire_crawl_leadership(db, 900) is True


def test_the_lease_is_sized_like_a_crawl_not_like_a_day():
    """Guards the regression directly: any TTL near 24h means a redeploy that
    fails to release silently skips its crawl for a day."""
    from config import Settings
    ttl = Settings().crawl_lock_ttl_seconds
    assert 60 <= ttl <= 3600, f"crawl lease TTL {ttl}s is not on the scale of one pass"
    assert not hasattr(Settings(), "crawl_interval_seconds"), (
        "crawl_interval_seconds is vestigial (the nightly loop went on 2026-07-01) "
        "— it must not come back to size the lease"
    )
