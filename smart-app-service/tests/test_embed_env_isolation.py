"""A test embed must stay in test — even after the app is promoted.

THE BUG THIS GUARDS
-------------------
Environment resolution is by STORE, prod-first (`resolve_app_environment`), and
promote COPIES test→prod leaving the test row in place. So a promoted app exists
in both stores and its slug always resolves to prod.

An embedded card resolves its environment from the key prefix on its FIRST call
(`/embed/{key}/spec`). Every call after that — run, panel data, detail, approve
— is addressed by SLUG. So once a BA promoted, a bank's UAT page holding an
`emb_test_` key would silently read PRODUCTION records, run against PRODUCTION
sources, and file its officers' corrections into PRODUCTION clause memory.

That is not only a learning-isolation failure. It means a customer's UAT screen
operates on live customer data.

THE FIX
-------
The card sends `X-Citra-Embed-Key` on every request; `_bind_app_env` prefers the
key's environment. It is a HINT, not a caller-chosen environment: the key must
exist in that environment's store bound to THAT slug, so a page can only reach
an environment it genuinely holds a key for.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("JWT_SECRET", "embed-isolation-secret-key-32-bytes")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27999/test")
os.environ.setdefault("DISCOVERY_SERVICE_URL", "http://discovery.test")
os.environ.setdefault("TEST_DISCOVERY_SERVICE_URL", "http://discovery-test.test")
os.environ.setdefault("CITRA_SERVICE_URL", "http://citra.test")
os.environ.setdefault("WORKFLOW_SERVICE_URL", "http://wf.test")

import main  # noqa: E402
from env_context import set_current_embed_key, set_current_env  # noqa: E402

SLUG = "loan-triage"
TEST_KEY = "emb_test_1111111111111111"
LIVE_KEY = "emb_live_2222222222222222"


class _Col:
    """Docs for ONE store."""

    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, q=None, *_a, **_kw):
        q = q or {}
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None


class _DB:
    def __init__(self, test_col):
        self._test = test_col

    def __getitem__(self, _name):
        return self._test


class _TestPlaneOn:
    """The root conftest pins the test plane OFF for the unit suite (hermetic
    by default) and tells tests that need the test→prod flow to supply their own
    Settings. This wraps the real one and flips only that flag — a whole fake
    Settings would drift from the real field set."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def test_environment_available(self) -> bool:
        return True


@pytest.fixture
def promoted(monkeypatch):
    """The steady state after a promote: the app is in BOTH stores, each row
    carrying the key for its own environment."""
    real_settings = main.get_settings()
    monkeypatch.setattr(
        main, "get_settings", lambda: _TestPlaneOn(real_settings), raising=False
    )
    prod = _Col([{"slug": SLUG, "embed_key": LIVE_KEY}])
    test = _Col([{"slug": SLUG, "embed_key": TEST_KEY}])
    monkeypatch.setattr(main, "_apps_col", prod, raising=False)
    monkeypatch.setattr(main, "_db", _DB(test), raising=False)
    # get_apps_col() is env-routed in real life; here route it by contextvar.
    monkeypatch.setattr(
        main, "get_apps_col",
        lambda: test if main.current_env() == "test" else prod,
        raising=False,
    )
    yield
    set_current_embed_key(None)
    set_current_env("prod")


@pytest.mark.asyncio
async def test_slug_alone_resolves_a_promoted_app_to_prod(promoted):
    """The underlying behaviour — correct for an app opened from Citra, and the
    reason an embed cannot rely on it."""
    set_current_embed_key(None)
    assert await main.resolve_app_environment(SLUG) == "prod"


@pytest.mark.asyncio
async def test_test_key_keeps_a_promoted_app_in_test(promoted):
    """THE REGRESSION GUARD. Without the key, this binds prod and the customer's
    UAT card reads live records."""
    set_current_embed_key(TEST_KEY)
    assert await main._bind_app_env(SLUG) == "test"


@pytest.mark.asyncio
async def test_live_key_binds_prod(promoted):
    set_current_embed_key(LIVE_KEY)
    assert await main._bind_app_env(SLUG) == "prod"


@pytest.mark.asyncio
async def test_no_key_falls_back_to_store_resolution(promoted):
    """An ordinary Citra user opening the app is unaffected."""
    set_current_embed_key(None)
    assert await main._bind_app_env(SLUG) == "prod"


@pytest.mark.asyncio
async def test_a_key_for_another_app_is_ignored(promoted):
    """The header is a HINT, verified against THIS slug. A key that does not
    name this app cannot drag it into another environment."""
    set_current_embed_key("emb_test_9999999999999999")  # no such row
    assert await main._bind_app_env(SLUG) == "prod"


@pytest.mark.asyncio
async def test_a_forged_non_key_is_ignored(promoted):
    set_current_embed_key("not-a-key")
    assert await main._bind_app_env(SLUG) == "prod"


@pytest.mark.asyncio
async def test_env_binding_drives_the_learning_stores(promoted):
    """What the isolation is FOR: corrections and clauses are env-routed on
    `current_env()`, so binding test is what keeps a UAT officer's reject out of
    production clause memory."""
    import clause_store
    import corrections

    set_current_embed_key(TEST_KEY)
    await main._bind_app_env(SLUG)
    assert main.current_env() == "test"
    assert corrections._COLLECTION == "smartapp_corrections"
    assert clause_store._COLLECTION == "smartapp_clauses"
    # Both resolve their collection name through main.current_env() at call
    # time — the same contextvar _bind_app_env just set.
    assert main._test_collection_name(corrections._COLLECTION) == (
        "test_smartapp_corrections"
    )
    assert main._test_collection_name(clause_store._COLLECTION) == (
        "test_smartapp_clauses"
    )
