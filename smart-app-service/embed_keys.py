"""Embed keys — how an externally-consumed app names itself to a customer.

WHY A KEY AND NOT THE SLUG
--------------------------
An app opened from Citra resolves its environment by STORE: present in the prod
apps collection → prod, else test. Promote COPIES test→prod and leaves the test
row in place, so after a promote the same slug always resolves to prod. For an
app that is fine and invisible.

For a surface pasted into a CUSTOMER's codebase it is not. Their UAT page and
their production page must point at different environments AT THE SAME TIME, or
they have no way to validate a change before officers see it. So an external
surface gets an explicit, environment-tagged key:

    emb_test_7f3a9c…   → test spec,  test MCP
    emb_live_9c21b4…   → prod spec,  prod MCP

The prefix carries the environment, so resolution is deterministic and a
mistake is visible in the customer's own code review — an ``emb_test_`` key on
a production page is wrong on sight. This is the same reason Stripe prefixes
publishable keys, and the reason the environment must live in the CREDENTIAL
rather than in a caller-supplied parameter: an ``env: "test"`` argument would
let a production page read test data by editing one string in the browser.

NOT A SECRET
------------
The key is an identifier, not a credential — the equivalent of a publishable
key. It sits in the host page's source where anyone can read it, and grants
nothing on its own: every call still carries the officer's JWT, and the
dept-MCP still scopes rows by their departments.

STABILITY IS THE CONTRACT
-------------------------
``emb_live_`` is minted ONCE, on first promote, and preserved by every promote
after it. If a re-promote minted a fresh key, every customer would have to
re-paste their snippet on every release — worse than the problem keys solve.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

TEST_PREFIX = "emb_test_"
LIVE_PREFIX = "emb_live_"

_PREFIX_BY_ENV = {"test": TEST_PREFIX, "prod": LIVE_PREFIX}


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from an AppSpec model OR a raw stored dict.

    Accepting both matters: the promote path and the spec-edit guard hold a
    STORED spec, and forcing `AppSpec.model_validate()` there would add a new
    way for those paths to fail. A document written before some later model
    tightening would then 500 a promote that used to work — a regression on a
    live path, in exchange for nothing, since deciding whether an app is
    externally consumed needs exactly two fields.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def is_external_surface(app_spec: Any) -> bool:
    """Is this app consumed OUTSIDE the Citra shell?

    Two shapes qualify, and they are the two that a customer integrates into
    their own systems:

    * an EMBED page — the decision card rendered inside their application;
    * a HEADLESS app — no UI at all, just /run + /approve behind their own UI.

    An ordinary app or dashboard is opened from Citra, so it has no need of a
    key and does not get one.

    Accepts an AppSpec or a raw spec dict — see ``_field``.
    """
    if app_spec is None:
        return False
    if bool(_field(app_spec, "headless", False)):
        return True
    for page in (_field(app_spec, "pages", None) or []):
        if _field(page, "kind", "standard") == "embed":
            return True
    return False


def mint_embed_key(env: str) -> str:
    """A fresh key for ``env``. Callers must only mint when none exists."""
    prefix = _PREFIX_BY_ENV.get(env)
    if prefix is None:
        raise ValueError(
            f"cannot mint an embed key for environment {env!r} — "
            f"expected one of {sorted(_PREFIX_BY_ENV)}"
        )
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def env_for_key(key: str) -> Optional[str]:
    """The environment a key addresses, or None when it is not an embed key.

    Deterministic from the prefix — no store lookup, so an unknown key cannot
    be probed for existence across environments.
    """
    if not isinstance(key, str):
        return None
    if key.startswith(TEST_PREFIX):
        return "test"
    if key.startswith(LIVE_PREFIX):
        return "prod"
    return None


def ensure_embed_key(
    *,
    app_spec: Any,
    env: str,
    existing_doc: Optional[dict] = None,
) -> Optional[str]:
    """The key this app document should carry after a publish or promote.

    Preserves an existing key — that stability IS the contract (see module
    docstring). Returns None for an app that is not externally consumed, so no
    key is stored and none has to be revoked later.
    """
    if not is_external_surface(app_spec):
        return None
    current = (existing_doc or {}).get("embed_key")
    if isinstance(current, str) and env_for_key(current) == env:
        return current
    # An existing key for the WRONG environment means this document was copied
    # across stores (promote copies the whole test doc). Mint for the target.
    return mint_embed_key(env)
