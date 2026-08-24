# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Unit tests for citra_service_utils.milvus_client.

We don't spin up a real Milvus instance — instead we stub pymilvus's
MilvusClient with a fake so we can verify:

  • Singleton returns the same instance across calls.
  • PID change forces a recreate (Gunicorn-fork safety).
  • Settings change forces a recreate.
  • Dead channel forces a recreate.
  • close_milvus_client() resets the singleton.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from citra_service_utils import milvus_client as mc


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test starts with a clean singleton state."""
    mc._milvus_client = None
    mc._milvus_client_pid = None
    mc._milvus_client_settings = None
    yield
    mc._milvus_client = None
    mc._milvus_client_pid = None
    mc._milvus_client_settings = None


@pytest.fixture
def fake_pymilvus(monkeypatch):
    """Inject a fake pymilvus module into sys.modules.

    Returns a dict so the test can inspect calls + control return values.
    """
    state = {"clients": [], "alive": True}

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._using = f"alias-{len(state['clients'])}"
            self.closed = False
            state["clients"].append(self)

        def close(self):
            self.closed = True

    class _FakeConnections:
        @staticmethod
        def has_connection(_alias):
            return state["alive"]

        @staticmethod
        def _fetch_handler(_alias):
            class _H:
                _channel = MagicMock() if state["alive"] else None
            return _H()

    fake_module = types.ModuleType("pymilvus")
    fake_module.MilvusClient = _FakeClient
    fake_module.connections = _FakeConnections
    monkeypatch.setitem(sys.modules, "pymilvus", fake_module)

    # No grpc stub — _is_channel_alive's grpc check is wrapped in try/except
    # so it falls back to "alive" if grpc isn't importable. That's the
    # behaviour we want in this test.
    return state


def test_get_milvus_client_singleton(fake_pymilvus):
    """Same settings → same instance returned."""
    s = mc.MilvusSettings(uri="http://localhost:19530", token=None)
    c1 = mc.get_milvus_client(s)
    c2 = mc.get_milvus_client(s)
    assert c1 is c2
    assert len(fake_pymilvus["clients"]) == 1


def test_settings_change_forces_recreate(fake_pymilvus):
    """A different MilvusSettings instance forces a new client."""
    s1 = mc.MilvusSettings(uri="http://a:19530", token="t1")
    s2 = mc.MilvusSettings(uri="http://b:19530", token="t2")
    c1 = mc.get_milvus_client(s1)
    c2 = mc.get_milvus_client(s2)
    assert c1 is not c2
    assert c1.closed is True  # old client was closed during swap
    assert len(fake_pymilvus["clients"]) == 2


def test_pid_change_forces_recreate(fake_pymilvus, monkeypatch):
    """Simulating a Gunicorn fork: the PID we record changes mid-flight."""
    s = mc.MilvusSettings(uri="http://localhost:19530", token=None)

    monkeypatch.setattr(mc.os, "getpid", lambda: 1111)
    c1 = mc.get_milvus_client(s)

    monkeypatch.setattr(mc.os, "getpid", lambda: 2222)
    c2 = mc.get_milvus_client(s)

    assert c1 is not c2
    assert mc._milvus_client_pid == 2222


def test_dead_channel_forces_recreate(fake_pymilvus):
    """When the gRPC channel reports dead, next get_milvus_client recreates."""
    s = mc.MilvusSettings(uri="http://localhost:19530", token=None)
    c1 = mc.get_milvus_client(s)

    # Simulate gRPC channel teardown
    fake_pymilvus["alive"] = False
    c2 = mc.get_milvus_client(s)
    assert c1 is not c2


def test_close_resets_singleton(fake_pymilvus):
    """close_milvus_client clears all state."""
    s = mc.MilvusSettings(uri="http://localhost:19530", token=None)
    c1 = mc.get_milvus_client(s)
    mc.close_milvus_client()
    assert mc._milvus_client is None
    assert mc._milvus_client_pid is None
    assert mc._milvus_client_settings is None
    assert c1.closed is True

    # Subsequent get_milvus_client builds a fresh one
    c2 = mc.get_milvus_client(s)
    assert c2 is not c1


def test_close_when_never_created_is_noop():
    """close_milvus_client called with no prior get is a clean no-op."""
    mc.close_milvus_client()  # must not raise


def test_recreate_milvus_client(fake_pymilvus):
    """Explicit recreate closes old + returns fresh, even with same settings."""
    s = mc.MilvusSettings(uri="http://localhost:19530", token=None)
    c1 = mc.get_milvus_client(s)
    c2 = mc.recreate_milvus_client(s)
    assert c1 is not c2
    assert c1.closed is True


def test_throwaway_does_not_touch_singleton(fake_pymilvus):
    """Throwaway client construction leaves the singleton state alone."""
    s = mc.MilvusSettings(uri="http://localhost:19530", token=None)
    singleton = mc.get_milvus_client(s)
    one_off = mc.create_throwaway_milvus_client(s)
    assert singleton is not one_off
    assert mc._milvus_client is singleton


def test_token_optional(fake_pymilvus):
    """When token is None, MilvusClient should be called without a token kwarg."""
    s = mc.MilvusSettings(uri="http://localhost:19530", token=None)
    c = mc.get_milvus_client(s)
    assert "token" not in c.kwargs
    assert c.kwargs["uri"] == "http://localhost:19530"
    assert c.kwargs["timeout"] == 30


def test_token_passed_when_set(fake_pymilvus):
    """When token is set, it's forwarded to MilvusClient."""
    s = mc.MilvusSettings(uri="https://zilliz.example", token="t0p_s3cr3t")
    c = mc.get_milvus_client(s)
    assert c.kwargs["token"] == "t0p_s3cr3t"
