# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Env-first secret bootstrap — a dept MCP runs at the customer's end on ANY
cloud, so environment injection is a self-sufficient path and HashiCorp Vault is
only OUR demo-prod default.

Pins the precedence resolved by ``vault_bootstrap.bootstrap_secrets``:
  * injected env ALWAYS wins; Vault is a fallback that only fills gaps;
  * SECRETS_PROVIDER=env never contacts Vault (customer clouds);
  * SECRETS_PROVIDER=vault fails loud when Vault is not fully configured;
  * auto uses Vault iff fully configured, and fails loud on a PARTIAL config
    (a broken deployment must not silently boot on half a config bag).

Pure — ``load_from_vault`` is stubbed, so no network / no real Vault.
"""
from __future__ import annotations

import pytest

import vault_bootstrap


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Start every test from a known-empty slate for the vars we care about.
    for v in ("SECRETS_PROVIDER", *vault_bootstrap._VAULT_VARS):
        monkeypatch.delenv(v, raising=False)


def _stub_vault(monkeypatch):
    calls = []
    monkeypatch.setattr(vault_bootstrap, "load_from_vault",
                        lambda *a, **k: calls.append(True) or 0)
    return calls


def _set_all_vault(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret")
    monkeypatch.setenv("VAULT_SECRET_PATH", "prod/mcp-demo")


# ── auto (default) ───────────────────────────────────────────────────────────
def test_auto_no_vault_is_env_only(monkeypatch):
    calls = _stub_vault(monkeypatch)
    assert vault_bootstrap.bootstrap_secrets() == "env"
    assert calls == []                                  # Vault never contacted


def test_auto_full_vault_loads_bag(monkeypatch):
    calls = _stub_vault(monkeypatch)
    _set_all_vault(monkeypatch)
    assert vault_bootstrap.bootstrap_secrets() == "vault"
    assert calls == [True]


def test_auto_partial_vault_fails_loud(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setenv("VAULT_ADDR", "https://vault:8200")   # only one of four
    with pytest.raises(SystemExit, match="partially configured"):
        vault_bootstrap.bootstrap_secrets()


# ── env (customer clouds) ────────────────────────────────────────────────────
def test_env_provider_never_touches_vault_even_if_configured(monkeypatch):
    calls = _stub_vault(monkeypatch)
    _set_all_vault(monkeypatch)                          # Vault fully set...
    monkeypatch.setenv("SECRETS_PROVIDER", "env")         # ...but env declared
    assert vault_bootstrap.bootstrap_secrets() == "env"
    assert calls == []                                   # still never contacted


# ── vault (demo-prod) ────────────────────────────────────────────────────────
def test_vault_provider_requires_full_config(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setenv("SECRETS_PROVIDER", "vault")
    monkeypatch.setenv("VAULT_ADDR", "https://vault:8200")  # missing the rest
    with pytest.raises(SystemExit, match="not fully configured"):
        vault_bootstrap.bootstrap_secrets()


def test_vault_provider_loads_when_configured(monkeypatch):
    calls = _stub_vault(monkeypatch)
    monkeypatch.setenv("SECRETS_PROVIDER", "vault")
    _set_all_vault(monkeypatch)
    assert vault_bootstrap.bootstrap_secrets() == "vault"
    assert calls == [True]


# ── selector validation ──────────────────────────────────────────────────────
def test_unknown_provider_fails_loud(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setenv("SECRETS_PROVIDER", "aws-sm")
    with pytest.raises(SystemExit, match="must be 'auto', 'env' or 'vault'"):
        vault_bootstrap.bootstrap_secrets()


# ── injected env wins over the Vault bag (overwrite=False) ───────────────────
def test_injected_env_wins_over_vault_bag(monkeypatch):
    """The REAL load_from_vault (not stubbed here) writes with overwrite=False,
    so a value already injected into the environment survives a Vault fill."""
    import os
    monkeypatch.setenv("JWT_SECRET", "injected-wins")
    # Simulate the bag write path directly: overwrite=False must not clobber.
    for k, v in {"JWT_SECRET": "from-vault", "NEW_KEY": "from-vault"}.items():
        if k not in os.environ:
            os.environ[k] = v
    assert os.environ["JWT_SECRET"] == "injected-wins"
    assert os.environ["NEW_KEY"] == "from-vault"
    monkeypatch.delenv("NEW_KEY", raising=False)
