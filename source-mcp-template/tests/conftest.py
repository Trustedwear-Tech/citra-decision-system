# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""pytest bootstrap — add repo root to sys.path so `import auth` works."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_plan_cache_singleton():
    """Reset plan_cache's process-level cache client around every test.

    plan_cache memoizes ONE cache client (Redis or the in-process LRU) for the
    whole process. Now that a Redis-less test env transparently gets the
    in-process backend (instead of the old fail-open 'no cache'), that singleton
    would carry cached plans/examples ACROSS tests — a hot plan cached by one
    test makes a later test skip the planner it meant to exercise. Clearing the
    singleton before each test gives every test a fresh, empty cache."""
    try:
        import plan_cache
    except Exception:  # noqa: BLE001 — a test module that doesn't touch plan_cache
        yield
        return
    plan_cache._redis_client = None
    plan_cache._redis_init_done = False
    plan_cache._redis_failed = False
    yield
    plan_cache._redis_client = None
    plan_cache._redis_init_done = False
    plan_cache._redis_failed = False
