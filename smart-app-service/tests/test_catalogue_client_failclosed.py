# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Fail-first / fail-loud contract for catalogue_client.

The catalogue being *unavailable* must never be silently degraded to an
empty catalogue. catalogue_client raises ``DiscoveryError`` on:
  * no data-discovery URL configured,
  * discovery unreachable (transport error),
  * a non-200 (other than 404 for a single-entry lookup),
  * malformed JSON.

The only non-error answers that survive:
  * fetch_catalogue_list returns [] on a 200 with no entries.
  * fetch_catalogue_entry returns None on a 404.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalogue_client as cc
from discovery_cache import DiscoveryError


def _settings(url="http://discovery.test"):
    # The catalogue (data-discovery) is read-only + shared across environments,
    # so catalogue_client reads settings.data_discovery_service_url directly.
    return SimpleNamespace(data_discovery_service_url=url)


@pytest.fixture(autouse=True)
def _clear_cache():
    cc.reset_cache()
    yield
    cc.reset_cache()


# --- unavailable => raise (fail loud) --------------------------------------


@pytest.mark.asyncio
async def test_list_raises_when_url_unset():
    with pytest.raises(DiscoveryError) as ei:
        await cc.fetch_catalogue_list(
            settings=_settings(url=None), auth_header=None, tenant_id="t1"
        )
    assert ei.value.code == "no_data_discovery_url"
    assert ei.value.status == 503


@pytest.mark.asyncio
async def test_list_raises_on_non_200(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return httpx.Response(503, text="upstream down", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    with pytest.raises(DiscoveryError) as ei:
        await cc.fetch_catalogue_list(settings=_settings(), auth_header=None, tenant_id="t1")
    assert ei.value.code == "catalogue_error"


@pytest.mark.asyncio
async def test_list_raises_on_transport_error(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    with pytest.raises(DiscoveryError) as ei:
        await cc.fetch_catalogue_list(settings=_settings(), auth_header=None, tenant_id="t1")
    assert ei.value.code == "catalogue_unreachable"


# --- available => the genuine non-error answers ----------------------------


@pytest.mark.asyncio
async def test_list_empty_catalogue_returns_empty(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"entries": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    out = await cc.fetch_catalogue_list(settings=_settings(), auth_header=None, tenant_id="t1")
    assert out == []


@pytest.mark.asyncio
async def test_list_returns_entries(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"entries": [{"dataset_id": "ds1"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    out = await cc.fetch_catalogue_list(settings=_settings(), auth_header=None, tenant_id="t1")
    assert out == [{"dataset_id": "ds1"}]


@pytest.mark.asyncio
async def test_entry_404_returns_none(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return httpx.Response(404, text="nope", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    out = await cc.fetch_catalogue_entry(
        settings=_settings(), auth_header=None, tenant_id="t1", dataset_id="ds_missing"
    )
    assert out is None


@pytest.mark.asyncio
async def test_entry_raises_on_500(monkeypatch):
    async def _fake_get(self, url, **kwargs):
        return httpx.Response(500, text="boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    with pytest.raises(DiscoveryError) as ei:
        await cc.fetch_catalogue_entry(
            settings=_settings(), auth_header=None, tenant_id="t1", dataset_id="ds1"
        )
    assert ei.value.code == "catalogue_error"


@pytest.mark.asyncio
async def test_entry_raises_when_url_unset():
    with pytest.raises(DiscoveryError) as ei:
        await cc.fetch_catalogue_entry(
            settings=_settings(url=None), auth_header=None, tenant_id="t1", dataset_id="ds1"
        )
    assert ei.value.code == "no_data_discovery_url"
