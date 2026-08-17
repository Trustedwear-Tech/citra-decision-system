# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Embed keys — the credential a customer pastes into their own codebase.

The properties that matter, and why:

  * STABILITY. `emb_live_` is minted once and preserved by every promote after
    it. A key that changed per release would force every customer to re-paste
    their snippet — worse than the problem environment-tagged keys solve.
  * ENVIRONMENT IN THE PREFIX. A customer's UAT page and production page must
    address different environments AT THE SAME TIME, which slug resolution
    cannot express (promote copies test→prod, so the slug then always resolves
    to prod). Putting the environment in the key also means it cannot be chosen
    by the caller — an `env: "test"` parameter would let a production page read
    test data by editing one string in the browser.
  * ONLY FOR EXTERNAL SURFACES. An ordinary app or dashboard is opened from
    Citra and needs no key; minting one would be a credential to revoke later
    for no reason.
"""

from __future__ import annotations

import pytest

from embed_keys import (
    LIVE_PREFIX,
    TEST_PREFIX,
    ensure_embed_key,
    env_for_key,
    is_external_surface,
    mint_embed_key,
)
from models import AppSpec


def _spec(**over):
    base = {
        "spec_version": "v0",
        "slug": "k-demo",
        "title": "Key Demo",
        "agent_id": "a",
        "data_sources": [{"id": "ds", "type": "mcp", "ref": "s.t"}],
    }
    base.update(over)
    return AppSpec.model_validate(base)


def _embed_page():
    return [{
        "id": "card", "kind": "embed", "title": "Card",
        # Publish requires a trigger on an embed page — a card that cannot
        # run the agent is a viewer.
        "panels": [{"id": "d", "type": "detail", "data_source": "ds",
                    "actions": [{"label": "Review",
                                 "agent_action": "review_application"}],
                    "sections": [{"type": "fields"}]}],
    }]


def _standard_page():
    return [{
        "id": "main", "kind": "standard", "title": "Main",
        "panels": [{"id": "q", "type": "queue", "data_source": "ds",
                    "columns": ["id"]}],
    }]


# ── which apps get a key ────────────────────────────────────────────────────

def test_embed_page_is_an_external_surface():
    assert is_external_surface(_spec(pages=_embed_page()))


def test_headless_is_an_external_surface():
    assert is_external_surface(_spec(headless=True))


def test_ordinary_app_is_not():
    assert not is_external_surface(_spec(pages=_standard_page()))


def test_ordinary_app_gets_no_key():
    assert ensure_embed_key(
        app_spec=_spec(pages=_standard_page()), env="test") is None


def test_raw_stored_dicts_work_without_model_validation():
    """The promote path and the spec-edit guard hold a STORED spec dict.

    Requiring `AppSpec.model_validate()` there would add a new way for those
    LIVE paths to fail: a document written before a later model tightening would
    500 a promote that used to work. Deciding whether an app is externally
    consumed needs two fields, so it reads them directly.
    """
    assert is_external_surface({"pages": [{"kind": "embed"}]}) is True
    assert is_external_surface({"headless": True}) is True
    assert is_external_surface({"pages": [{"kind": "standard"}]}) is False
    assert is_external_surface({}) is False
    assert is_external_surface(None) is False
    # A spec that would FAIL AppSpec validation must still be classified, not
    # raise — that is the whole point.
    assert is_external_surface(
        {"pages": [{"kind": "embed", "panels": [{"type": "nonsense"}]}]}
    ) is True


# ── prefix carries the environment ──────────────────────────────────────────

def test_prefix_determines_environment():
    assert env_for_key(mint_embed_key("test")) == "test"
    assert env_for_key(mint_embed_key("prod")) == "prod"


def test_non_embed_key_resolves_to_nothing():
    # A slug, an app_id, or junk must not be mistaken for an embed key.
    for junk in ["loan-triage", "app_9c21b4", "", None, 42]:
        assert env_for_key(junk) is None  # type: ignore[arg-type]


def test_minting_an_unknown_environment_fails_loud():
    with pytest.raises(ValueError):
        mint_embed_key("staging")


def test_keys_are_unique():
    keys = {mint_embed_key("prod") for _ in range(50)}
    assert len(keys) == 50


# ── stability across republish / re-promote ─────────────────────────────────

def test_existing_key_is_preserved():
    """THE contract. A changing key means every customer re-pastes on release."""
    spec = _spec(pages=_embed_page())
    first = ensure_embed_key(app_spec=spec, env="prod", existing_doc=None)
    again = ensure_embed_key(
        app_spec=spec, env="prod", existing_doc={"embed_key": first})
    assert again == first


def test_promote_does_not_inherit_the_test_key():
    """Promote copies the whole TEST document, test key included. Inheriting it
    would give a customer's production page a key whose prefix says test —
    silently addressing the wrong environment."""
    spec = _spec(pages=_embed_page())
    test_key = ensure_embed_key(app_spec=spec, env="test", existing_doc=None)
    assert test_key.startswith(TEST_PREFIX)

    # The prod row does not exist yet, but the SOURCE doc carries the test key.
    prod_key = ensure_embed_key(
        app_spec=spec, env="prod", existing_doc={"embed_key": test_key})
    assert prod_key.startswith(LIVE_PREFIX)
    assert prod_key != test_key


def test_both_environments_coexist():
    """The whole point: UAT and production addressable at the same time."""
    spec = _spec(pages=_embed_page())
    test_key = ensure_embed_key(app_spec=spec, env="test")
    live_key = ensure_embed_key(app_spec=spec, env="prod")
    assert env_for_key(test_key) == "test"
    assert env_for_key(live_key) == "prod"
    assert test_key != live_key
