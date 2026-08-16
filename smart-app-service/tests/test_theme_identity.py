"""Theme v2 + organization identity (docs/runtime-ui-modernization-plan.md U1).

Covers:
  * Theme v2 token fields are CLOSED enums — unknown values / fields reject.
  * _resolve_org_identity: first mcp source whose catalogue entry carries an
    ``organization`` block wins; fetch failures skip loudly, never raise.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from models import Theme


def _run(coro):
    return asyncio.run(coro)


def test_theme_v2_tokens_are_closed_enums():
    ok = Theme.model_validate({
        "primary": "#0f6b3f", "company_name": "Acme Power",
        "font": "inter", "radius": "round", "density": "compact",
        "surface": "glass", "mode": "auto", "chart_palette": "brand",
    })
    assert ok.company_name == "Acme Power"
    assert ok.mode == "auto"
    for bad in (
        {"font": "comic-sans"},
        {"radius": "extra-round"},
        {"density": "cozy"},
        {"surface": "neon"},
        {"mode": "midnight"},
        {"chart_palette": "rainbow"},
        {"company_name": "x" * 121},
        {"totally_new_field": 1},          # extra=forbid
    ):
        with pytest.raises(ValidationError):
            Theme.model_validate(bad)


def test_theme_v2_unset_is_valid_and_classic():
    t = Theme.model_validate({})
    assert t.company_name is None and t.font is None and t.mode is None


def test_resolve_org_identity_first_declared_wins(monkeypatch):
    import catalogue_client
    import main

    entries = {
        "billing.bills": {"kind": "sql"},                          # no org block
        "field_operations.theft_cases": {
            "kind": "sql",
            "organization": {"name": "Acme Power & Utilities Co.",
                             "short_name": "Acme Power",
                             "brand_color": "#0f6b3f"},
        },
    }

    async def _fake_fetch(*, settings, tenant_id, dataset_id, auth_header):
        if dataset_id == "boom.boom":
            raise RuntimeError("catalogue down")
        return entries.get(dataset_id)
    monkeypatch.setattr(catalogue_client, "fetch_catalogue_entry", _fake_fetch)

    app_spec = SimpleNamespace(data_sources=[
        SimpleNamespace(type="rag", ref="policy_lib"),          # skipped (not mcp)
        SimpleNamespace(type="mcp", ref="boom.boom"),           # fetch fails → skipped
        SimpleNamespace(type="mcp", ref="billing.bills"),       # no org → next
        SimpleNamespace(type="mcp", ref="field_operations.theft_cases"),
    ])
    org = _run(main._resolve_org_identity(
        app_spec=app_spec, settings=None, auth_header=None, tenant_id="acme-power"))
    assert org["short_name"] == "Acme Power"
    assert org["brand_color"] == "#0f6b3f"

    # No source declares identity → None (publish leaves theme untouched).
    app_spec2 = SimpleNamespace(data_sources=[
        SimpleNamespace(type="mcp", ref="billing.bills")])
    assert _run(main._resolve_org_identity(
        app_spec=app_spec2, settings=None, auth_header=None,
        tenant_id="acme-power")) is None
