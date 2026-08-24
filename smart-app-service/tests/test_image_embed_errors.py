# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Free-tier embedding failures must be LOUD: a timeout or rate-limit on the
image-embedding model turns the similar-image fraud tier OFF for that
artifact — that has to surface as log.error + a self-explanatory message in
artifact_flags, never as the bare 'ReadTimeout: ' httpx stringifies to."""
import logging

import httpx
import pytest

import fraud_image_index as fii


class _Settings:
    image_embed_model = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
    image_embed_url = "https://openrouter.ai/api/v1/embeddings"
    image_embed_api_key = "k"
    image_embed_store_dim = 2048
    image_embed_timeout_s = 7.0


class _TimeoutClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        raise httpx.ReadTimeout("")  # httpx timeouts stringify to ""


class _RateLimitedClient(_TimeoutClient):
    async def post(self, *a, **k):
        req = httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings")
        resp = httpx.Response(429, text="rate limited", request=req)
        raise httpx.HTTPStatusError("429", request=req, response=resp)


@pytest.mark.anyio
async def test_timeout_raises_rich_message_and_logs_error(monkeypatch, caplog):
    monkeypatch.setattr(fii.httpx, "AsyncClient", _TimeoutClient)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as e:
            await fii.embed_image(_Settings(), b"img", "image/jpeg")
    msg = str(e.value)
    assert "TIMED OUT after 7s" in msg
    assert "nvidia/llama-nemotron-embed-vl-1b-v2:free" in msg
    assert "similar-image fraud tier is OFF" in msg
    assert "IMAGE_EMBED_TIMEOUT_S" in msg
    assert any(r.levelno == logging.ERROR and "TIMED OUT" in r.getMessage()
               for r in caplog.records)


@pytest.mark.anyio
async def test_rate_limit_raises_rich_message(monkeypatch, caplog):
    monkeypatch.setattr(fii.httpx, "AsyncClient", _RateLimitedClient)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as e:
            await fii.embed_image(_Settings(), b"img", "image/jpeg")
    msg = str(e.value)
    assert "HTTP 429" in msg and "free-tier rate limit" in msg
    assert "similar-image fraud tier is OFF" in msg
