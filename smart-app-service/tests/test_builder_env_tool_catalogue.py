# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for `_builder_env()` — the env passed to ephemeral builder pods.

Verifies:
  * TOOL_CATALOGUE is valid JSON and only includes vision_ocr when
    settings.ocr_enabled is True.
  * OCR_ENABLED env flag toggles with settings.
  * SMART_APP_PROXY_BASE_URL points at /smart-app/internal.
  * SMART_APP_INTERNAL_SECRET is a verifiable HMAC bearer with
    kind="builder", subject="build:<session>", and tools the catalogue
    promises.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SIGNING_KEY = "builder-env-test-signing-key"
os.environ.setdefault("JWT_SECRET", "builder-env-test-jwt")
os.environ.setdefault("JWT_ISSUER", "Citra-AI")


from contextlib import contextmanager


@contextmanager
def _reload_with(monkeypatch, *, ocr_enabled: bool):
    monkeypatch.setenv("SMART_APP_INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")
    monkeypatch.setenv(
        "SMART_APP_SERVICE_CALLBACK_URL", "https://smart.example/"
    )
    # Builder LLM must be configured or _builder_env raises.
    monkeypatch.setenv("LLM_LARGE_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_LARGE_API_KEY", "lk-test")
    monkeypatch.setenv("LLM_LARGE_MODEL", "gpt-4o-mini")

    if ocr_enabled:
        monkeypatch.setenv("VISION_BASE_URL", "https://vision.test/v1")
        monkeypatch.setenv("VISION_API_KEY", "vk-test")
        monkeypatch.setenv("VISION_MODEL", "qwen/qwen3-vl-32b-instruct")
    else:
        monkeypatch.setenv("VISION_BASE_URL", "")
        monkeypatch.setenv("VISION_API_KEY", "")
        monkeypatch.setenv("VISION_MODEL", "")

    import importlib
    import config as _config
    importlib.reload(_config)
    import internal_routes as _internal_routes
    importlib.reload(_internal_routes)
    import main as _main
    importlib.reload(_main)
    try:
        yield _main, _config.get_settings()
    finally:
        monkeypatch.undo()
        importlib.reload(_config)
        importlib.reload(_internal_routes)
        importlib.reload(_main)


def test_tool_catalogue_includes_vision_ocr_when_enabled(monkeypatch):
    with _reload_with(monkeypatch, ocr_enabled=True) as (main, settings):
        env = main._builder_env(
            settings=settings,
            session_id="bs_test",
            goal="motor claim app",
            tenant_id="bajaj",
            owner="u_owner",
        )
        assert env["OCR_ENABLED"] == "true"
        assert env["SMART_APP_PROXY_BASE_URL"].endswith("/smart-app/internal")

        catalogue = json.loads(env["TOOL_CATALOGUE"])
        kinds = {entry["kind"] for entry in catalogue}
        assert "vision_ocr" in kinds
        assert "validate_form" in kinds


def test_tool_catalogue_drops_vision_ocr_when_disabled(monkeypatch):
    with _reload_with(monkeypatch, ocr_enabled=False) as (main, settings):
        env = main._builder_env(
            settings=settings,
            session_id="bs_test",
            goal="form-only app",
            tenant_id="bajaj",
            owner="u_owner",
        )
        assert env["OCR_ENABLED"] == "false"
        catalogue = json.loads(env["TOOL_CATALOGUE"])
        kinds = {entry["kind"] for entry in catalogue}
        assert "vision_ocr" not in kinds
        # validate_form is always in the catalogue (free, deterministic).
        assert "validate_form" in kinds


def test_internal_secret_is_verifiable_builder_bearer(monkeypatch):
    with _reload_with(monkeypatch, ocr_enabled=True) as (main, settings):
        env = main._builder_env(
            settings=settings,
            session_id="bs_xyz",
            goal="g",
            tenant_id="bajaj",
            owner="u_owner",
        )
        secret = env["SMART_APP_INTERNAL_SECRET"]
        assert secret and "." in secret

        from internal_bearer import verify_internal_bearer
        claims = verify_internal_bearer(signing_key=SIGNING_KEY, bearer=secret)
        assert claims.kind == "builder"
        assert claims.subject == "build:bs_xyz"
    assert claims.tenant_id == "bajaj"
    assert "vision_ocr" in claims.tools
    assert "validate_form" in claims.tools




def test_actionchat_user_falls_back_when_owner_missing(monkeypatch):
    with _reload_with(monkeypatch, ocr_enabled=False) as (main, settings):
        env = main._builder_env(
            settings=settings,
            session_id="bs_anon",
            goal="g",
            tenant_id=None,
            owner=None,
        )
        assert env["CITRA_AGENT_USER_ID"] == "builder:bs_anon"
        # Tenant absence => no ORG_ID set.
        assert "CITRA_AGENT_ORG_ID" not in env
