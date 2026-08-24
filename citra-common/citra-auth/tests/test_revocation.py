# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the token-revocation seam (citra_auth.revocation).

Uses asyncio.run rather than pytest-asyncio so it needs no extra plugin.
"""
import asyncio

import pytest

from citra_auth import (
    register_revocation_checker,
    has_revocation_checker,
    is_token_revoked,
)


@pytest.fixture(autouse=True)
def _reset_checker():
    register_revocation_checker(None)
    yield
    register_revocation_checker(None)


def test_no_checker_means_not_revoked():
    assert has_revocation_checker() is False
    assert asyncio.run(is_token_revoked("any-jti")) is False
    assert asyncio.run(is_token_revoked(None)) is False


def test_sync_checker():
    revoked = {"bad-jti"}
    register_revocation_checker(lambda jti: jti in revoked)
    assert has_revocation_checker() is True
    assert asyncio.run(is_token_revoked("bad-jti")) is True
    assert asyncio.run(is_token_revoked("good-jti")) is False


def test_async_checker():
    async def checker(jti):
        return jti == "killed"

    register_revocation_checker(checker)
    assert asyncio.run(is_token_revoked("killed")) is True
    assert asyncio.run(is_token_revoked("alive")) is False


def test_checker_failure_fails_closed():
    def boom(jti):
        raise RuntimeError("mongo down")

    register_revocation_checker(boom)
    # A failing blocklist lookup must deny (treat as revoked), not admit.
    assert asyncio.run(is_token_revoked("whatever")) is True
