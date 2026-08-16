# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Shared test helpers for the smart-app-service e2e suites.

Provides:
  * ``_MemCol`` — minimal async motor-compatible in-memory collection
  * ``client`` — pytest fixture wiring main with stubbed collections
  * ``_mint`` / ``_mint_builder`` — JWT minters for user / builder-pod tokens

The leading underscore tells pytest to skip this as a test module; suites
that need the helpers import them via ``from tests._test_helpers import ...``.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "smart-app-service-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")


_MISSING = object()


def _set_path(doc: dict, key: str, value) -> None:
    parts = key.split(".") if "." in key else [key]
    cur = doc
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _unset_path(doc: dict, key: str) -> None:
    parts = key.split(".") if "." in key else [key]
    cur = doc
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            return
        cur = nxt
    cur.pop(parts[-1], None)


def _apply_update(doc: dict, update: dict) -> None:
    """Apply a subset of Mongo update operators in-place."""
    for op, body in update.items():
        if not isinstance(body, dict):
            continue
        if op == "$set":
            for k, v in body.items():
                _set_path(doc, k, v)
        elif op == "$unset":
            for k in body.keys():
                _unset_path(doc, k)
        elif op == "$push":
            for k, v in body.items():
                cur = _get_path(doc, k)
                if cur is _MISSING or not isinstance(cur, list):
                    _set_path(doc, k, [v])
                else:
                    cur.append(v)
        elif op == "$addToSet":
            for k, v in body.items():
                cur = _get_path(doc, k)
                if cur is _MISSING or not isinstance(cur, list):
                    _set_path(doc, k, [v])
                elif v not in cur:
                    cur.append(v)


def _get_path(doc: dict, key: str):
    """Traverse a dotted Mongo-style path. Returns _MISSING if any segment
    along the way is absent. Doesn't try to handle array-indexing or
    array-projection — sufficient for AppSpec-shaped docs."""
    parts = key.split(".") if "." in key else [key]
    cur = doc
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return cur


def _value_matches(actual, expected) -> bool:
    """Match a doc-side ``actual`` value against a Mongo-style operator
    document (e.g. ``{"$in": [...]}``) or a literal."""
    # Operator document: every key starts with $
    if isinstance(expected, dict) and expected and all(k.startswith("$") for k in expected):
        for op, opval in expected.items():
            if op == "$in":
                if isinstance(actual, list):
                    if not any(a in opval for a in actual):
                        return False
                elif actual not in opval:
                    return False
            elif op == "$nin":
                if isinstance(actual, list):
                    if any(a in opval for a in actual):
                        return False
                elif actual in opval:
                    return False
            elif op == "$ne":
                if actual == opval:
                    return False
            elif op == "$eq":
                if actual != opval:
                    return False
            elif op == "$exists":
                if bool(opval) != (actual is not _MISSING):
                    return False
            elif op == "$gt":
                if not (actual is not _MISSING and actual > opval):
                    return False
            elif op == "$gte":
                if not (actual is not _MISSING and actual >= opval):
                    return False
            elif op == "$lt":
                if not (actual is not _MISSING and actual < opval):
                    return False
            else:
                # Unknown operator → fail-closed so tests catch additions.
                return False
        return True
    # Literal match. AppSpec.dept_ids[] queried with a scalar should match
    # when the scalar is IN the array (Mongo semantics).
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


def _matches(doc: dict, query: dict) -> bool:
    """Mongo-style filter matcher: supports $and / $or / $nor, dotted paths,
    and per-field operator docs ($in / $ne / $exists / …). Sufficient for
    the audience-model queries in smart-app-service."""
    if not query:
        return True
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(doc, sub) for sub in expected):
                return False
        elif key == "$or":
            if not any(_matches(doc, sub) for sub in expected):
                return False
        elif key == "$nor":
            if any(_matches(doc, sub) for sub in expected):
                return False
        else:
            actual = _get_path(doc, key)
            if isinstance(expected, dict) and expected and "$exists" in expected:
                if not _value_matches(actual, expected):
                    return False
                continue
            if actual is _MISSING:
                # Treat as not-equal unless caller asked for $exists:false
                if isinstance(expected, dict) and expected.get("$exists") is False:
                    continue
                return False
            if not _value_matches(actual, expected):
                return False
    return True


class _MemCol:
    """In-memory MongoDB collection stub. Async motor-compatible.

    Supports the subset of Mongo query / update used by smart-app-service:
    dotted paths, $and / $or, $in / $nin / $ne / $exists, list-membership
    literal match. Update operators: $set (with dotted paths), $push,
    $addToSet, $unset."""

    def __init__(self, docs=None) -> None:
        self.docs: list[dict] = list(docs or [])

    def find(self, q=None, *_a, **_kw):
        q = q or {}
        matches = [d for d in self.docs if _matches(d, q)]

        class _Cur:
            def __init__(self, m):
                self._m = m

            def sort(self, *_a, **_kw):
                return self

            def skip(self, n):
                self._m = self._m[n:]
                return self

            def limit(self, n):
                self._m = self._m[:n]
                return self

            async def to_list(self, n=None, *, length=None):
                # Motor's real signature is to_list(length) and callers use BOTH
                # positional and length= . Accepting only positional made
                # production code that says `.to_list(length=None)` blow up with
                # a TypeError inside the fixture — a stub limitation surfacing
                # as a product-looking failure.
                lim = n if n is not None else length
                return self._m if lim is None else self._m[:lim]

            def __aiter__(self):
                async def _g():
                    for x in self._m:
                        yield x
                return _g()

        return _Cur(matches)

    async def count_documents(self, q=None, *_a, **_kw):
        q = q or {}
        return sum(1 for d in self.docs if _matches(d, q))

    async def find_one(self, q=None, *_a, **_kw):
        q = q or {}
        for d in self.docs:
            if _matches(d, q):
                return d
        return None

    async def insert_one(self, doc, *_a, **_kw):
        self.docs.append(dict(doc))

        class _R:
            inserted_id = "stub"
        return _R()

    async def update_one(self, q, update, *_a, **_kw):
        for d in self.docs:
            if _matches(d, q):
                _apply_update(d, update)

                class _R:
                    matched_count = 1
                    modified_count = 1
                    upserted_id = None
                return _R()

        class _R:
            matched_count = 0
            modified_count = 0
            upserted_id = None
        return _R()

    async def find_one_and_update(self, q=None, update=None, *_a, **_kw):
        """Match Motor's DEFAULT: return the document as it was BEFORE the
        update (ReturnDocument.BEFORE), or None when nothing matched.

        Callers use this for read-and-clear-once semantics — the pre-update
        value IS the payload — so returning the post-update doc here would make
        a passing test out of a claim-once bug.
        """
        for d in self.docs:
            if _matches(d, q or {}):
                before = dict(d)
                _apply_update(d, update or {})
                return before
        return None

    async def replace_one(self, q, doc, upsert=False, *_a, **_kw):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in q.items()):
                self.docs[i] = dict(doc)

                class _R:
                    upserted_id = None
                return _R()
        if upsert:
            self.docs.append(dict(doc))

        class _R:
            upserted_id = "stub"
        return _R()

    async def delete_many(self, q=None, *_a, **_kw):
        q = q or {}
        kept = [d for d in self.docs if not _matches(d, q)]
        removed = len(self.docs) - len(kept)
        self.docs = kept

        class _R:
            deleted_count = removed
        return _R()

    async def insert_many(self, docs, *_a, **_kw):
        for d in docs:
            self.docs.append(dict(d))

        class _R:
            inserted_ids = ["stub"] * len(docs)
        return _R()

    async def create_index(self, *_a, **_kw):
        return None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import main

    apps = _MemCol()
    agents = _MemCol()
    spec_versions = _MemCol()
    monkeypatch.setattr(main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(main, "_spec_versions_col", spec_versions, raising=False)
    monkeypatch.setattr(main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_pending_runs_col", _MemCol(), raising=False)
    # Kill-switch (halt/pause) collection. get_control_col() raises
    # "Database not initialised" without it, and the runtime consults the
    # kill switch on every run — so any test that reaches execute_run dies
    # on infrastructure rather than on the behaviour it is asserting.
    # Empty = nothing halted, which is the normal state.
    monkeypatch.setattr(main, "_control_col", _MemCol(), raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(main.app) as c:
        c._cols = {"apps": apps, "agents": agents, "spec_versions": spec_versions}  # type: ignore[attr-defined]
        yield c


def _mint(user_id: str, tenant_id: str = "bajaj") -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "tenant_id": tenant_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": "Citra-AI",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _mint_builder(session_id: str = "bs_share", tenant_id: str = "bajaj", owner: str = "u_owner") -> str:
    return jwt.encode(
        {
            "sub": owner,
            "user_id": owner,
            "scope": "smart-app-builder",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": "Citra-AI",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
