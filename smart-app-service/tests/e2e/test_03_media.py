"""Media streaming (the hardened /apps/{slug}/media path) — live.

Exercises the real MCP-streamed media on the golden-base app:
  * s3:// media column streams real bytes with the right content-type,
  * a non-media / unknown column fails loud (4xx/5xx, never a fake 200),
  * the endpoint is auth-gated.

SSRF / filename-injection / stream-before-status are covered deterministically by
the offline unit suite (scratchpad/test_media_local.py against the real
_ssrf_check + httpx MockTransport); this module proves the happy path in-env.
"""
from __future__ import annotations

import httpx
import pytest

from conftest import CFG, auth, mint_jwt

_MAGIC = {
    "image/jpeg": b"\xff\xd8\xff",
    "application/pdf": b"%PDF",
    "image/png": b"\x89PNG",
}


def _media(sas, token, col, key=None, key_field=None):
    return sas.get(
        f"/apps/{CFG.APP_SLUG}/media/{CFG.DS_ID}",
        params={"key_field": key_field or CFG.KEY_FIELD,
                "key": key or CFG.RECORD_ID, "col": col},
        headers=auth(token) if token else {},
    )


@pytest.fixture()
def tok(base_specs) -> str:
    return mint_jwt(roles=["super_admin"], user_id="media@acme-power.citra.ai")


def test_media_streams_real_bytes(sas: httpx.Client, base_specs, tok: str):
    r = _media(sas, tok, CFG.MEDIA_COL)
    if r.status_code == 404:
        pytest.skip(f"record {CFG.RECORD_ID} / column {CFG.MEDIA_COL} not present in this env")
    assert r.status_code == 200, r.text[:300]
    ct = r.headers.get("content-type", "").split(";")[0]
    assert ct in _MAGIC, f"unexpected content-type {ct!r}"
    assert r.content[:4].startswith(_MAGIC[ct]), "body does not match its content-type magic"
    # Streamed SoR media is served inline, never as a storage URL.
    assert "inline" in r.headers.get("content-disposition", "").lower()


def test_media_unknown_column_fails_loud(sas: httpx.Client, base_specs, tok: str):
    r = _media(sas, tok, "e2e_column_that_does_not_exist")
    # Must NOT be a 200 with a bogus body — a non-media/unknown column errors.
    assert r.status_code >= 400, f"unknown column unexpectedly returned {r.status_code}"


def test_media_requires_auth(sas: httpx.Client, base_specs):
    r = _media(sas, None, CFG.MEDIA_COL)
    assert r.status_code in (401, 403, 404), r.text[:200]


def test_media_wrong_datasource_type_or_missing(sas: httpx.Client, base_specs, tok: str):
    r = sas.get(
        f"/apps/{CFG.APP_SLUG}/media/ds_does_not_exist",
        params={"key_field": CFG.KEY_FIELD, "key": CFG.RECORD_ID, "col": CFG.MEDIA_COL},
        headers=auth(tok),
    )
    assert r.status_code in (400, 404), r.text[:200]
