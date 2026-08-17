# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for internal_bearer (HMAC mint/verify) and the OCR-related
internal routes (`/smart-app/internal/ocr` and `/ocr/pages`).

These tests use httpx-mock to stub out the upstream vision API, and the
FastAPI TestClient so the public middleware stack runs (which is how
we confirm /smart-app/internal/ bypasses JWT auth and only enforces
the HMAC bearer).
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal_bearer import (  # noqa: E402
    InternalBearerError,
    mint_internal_bearer,
    verify_internal_bearer,
)


# ---------------------------------------------------------------------------
# internal_bearer pure-unit tests
# ---------------------------------------------------------------------------


SIGNING_KEY = "unit-test-signing-key"


def _mint(**overrides) -> str:
    kwargs = dict(
        signing_key=SIGNING_KEY,
        kind="builder",
        subject="build:abc",
        tenant_id="t1",
        tools=["vision_ocr"],
        ttl_seconds=60,
    )
    kwargs.update(overrides)
    return mint_internal_bearer(**kwargs)


def test_mint_and_verify_round_trip():
    bearer = _mint()
    claims = verify_internal_bearer(signing_key=SIGNING_KEY, bearer=bearer)
    assert claims.kind == "builder"
    assert claims.subject == "build:abc"
    assert claims.tenant_id == "t1"
    assert claims.tools == ["vision_ocr"]
    assert claims.exp > claims.iat


def test_verify_rejects_tampered_payload():
    bearer = _mint()
    payload_b64, sig = bearer.split(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["tools"] = ["vision_ocr", "mcp.evil"]
    new_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    tampered = f"{new_payload}.{sig}"
    with pytest.raises(InternalBearerError) as exc:
        verify_internal_bearer(signing_key=SIGNING_KEY, bearer=tampered)
    assert exc.value.code == "bad_signature"


def test_verify_rejects_expired_bearer():
    bearer = _mint(ttl_seconds=1)
    # Force exp into the past by re-minting with a manipulated payload.
    payload_b64, _ = bearer.split(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["exp"] = int(time.time()) - 60
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    new_payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    # Resign correctly so the bearer fails ONLY on expiry, not signature.
    import hmac
    from hashlib import sha256

    sig = hmac.new(SIGNING_KEY.encode(), payload_bytes, sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    expired = f"{new_payload_b64}.{sig_b64}"
    with pytest.raises(InternalBearerError) as exc:
        verify_internal_bearer(signing_key=SIGNING_KEY, bearer=expired)
    assert exc.value.code == "expired"


def test_verify_rejects_wrong_signing_key():
    bearer = _mint()
    with pytest.raises(InternalBearerError) as exc:
        verify_internal_bearer(signing_key="wrong-key", bearer=bearer)
    assert exc.value.code == "bad_signature"


def test_verify_rejects_malformed():
    with pytest.raises(InternalBearerError):
        verify_internal_bearer(signing_key=SIGNING_KEY, bearer="not-a-bearer")


# ---------------------------------------------------------------------------
# /smart-app/internal/ocr — auth + happy path with mocked upstream
# ---------------------------------------------------------------------------


class _StubCol:
    """Minimal async stub matching motor collection methods used at startup."""

    async def find_one(self, *_a, **_kw):
        return None

    async def insert_one(self, *_a, **_kw):
        class _R:
            inserted_id = "stub"
        return _R()

    async def update_one(self, *_a, **_kw):
        class _R:
            matched_count = 0
            modified_count = 0
            upserted_id = None
        return _R()

    async def create_index(self, *_a, **_kw):
        return None

    def find(self, *_a, **_kw):
        class _Cur:
            def sort(self, *_a, **_kw):
                return self

            def limit(self, *_a, **_kw):
                return self

            def __aiter__(self):
                async def _g():
                    if False:
                        yield None
                return _g()

        return _Cur()

    async def count_documents(self, *_a, **_kw):
        return 0


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with vision endpoint configured + Mongo stubbed."""
    from contextlib import asynccontextmanager

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.test/v1")
    monkeypatch.setenv("VISION_API_KEY", "vk-test")
    monkeypatch.setenv("VISION_MODEL", "qwen/qwen3-vl-32b-instruct")
    monkeypatch.setenv("SMART_APP_INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")

    # Reload settings + main with the patched env.
    import importlib
    import config as _config
    importlib.reload(_config)
    import internal_routes as _internal_routes
    importlib.reload(_internal_routes)
    import main as _main
    importlib.reload(_main)

    # Replace any module-level collection handles with stubs so any code
    # path that touches them during a request will not hit Mongo.
    for attr in [
        "_apps_col",
        "_agents_col",
        "_build_sessions_col",
        "_prompt_packs_col",
        "_skills_col",
        "_pending_runs_col",
    ]:
        if hasattr(_main, attr):
            monkeypatch.setattr(_main, attr, _StubCol(), raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(_main.app.router, "lifespan_context", _noop_lifespan)

    from fastapi.testclient import TestClient
    try:
        with TestClient(_main.app) as c:
            yield c
    finally:
        # Roll back env first, then reload modules so other tests in
        # the session see the original config+main, not our mutated copy.
        monkeypatch.undo()
        importlib.reload(_config)
        importlib.reload(_internal_routes)
        importlib.reload(_main)


def test_ocr_route_rejects_missing_bearer(client):
    resp = client.post(
        "/smart-app/internal/ocr",
        json={"image_b64": "AAA=", "content_type": "image/png"},
    )
    assert resp.status_code == 401
    assert "bearer" in resp.text.lower()


def test_ocr_route_rejects_bearer_without_tool_scope(client):
    bearer = mint_internal_bearer(
        signing_key=SIGNING_KEY,
        kind="runtime",
        subject="app:demo",
        tenant_id="t1",
        tools=["mcp"],  # no vision_ocr
        ttl_seconds=60,
    )
    resp = client.post(
        "/smart-app/internal/ocr",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"image_b64": "AAA=", "content_type": "image/png"},
    )
    assert resp.status_code == 403
    assert "vision_ocr" in resp.text.lower()


def test_ocr_route_rejects_pdf_content_type(client):
    bearer = mint_internal_bearer(
        signing_key=SIGNING_KEY,
        kind="runtime",
        subject="app:demo",
        tenant_id="t1",
        tools=["vision_ocr"],
        ttl_seconds=60,
    )
    resp = client.post(
        "/smart-app/internal/ocr",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"image_b64": "AAA=", "content_type": "application/pdf"},
    )
    assert resp.status_code == 415
    assert "pdf" in resp.text.lower() or "rasterise" in resp.text.lower() or "rasterize" in resp.text.lower()


def test_ocr_route_happy_path_with_mocked_upstream(client, monkeypatch):
    """Stub httpx.AsyncClient.post so the proxy returns the upstream
    chat-completion shape and we get back a clean OcrResult."""

    bearer = mint_internal_bearer(
        signing_key=SIGNING_KEY,
        kind="runtime",
        subject="app:demo",
        tenant_id="t1",
        tools=["vision_ocr"],
        ttl_seconds=60,
    )

    captured: dict = {}

    async def _fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "extracted text from photo"}}
                ],
                "usage": {"prompt_tokens": 42, "completion_tokens": 7},
                "model": "qwen/qwen3-vl-32b-instruct",
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    # 1×1 transparent PNG, base64-encoded.
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    resp = client.post(
        "/smart-app/internal/ocr",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"image_b64": png_b64, "content_type": "image/png"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == "extracted text from photo"
    assert body["tokens_in"] == 42
    assert body["tokens_out"] == 7
    assert body["model"] == "qwen/qwen3-vl-32b-instruct"

    # The proxy forwarded the call to the configured upstream.
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer vk-test"


# ── Curated text is a first-class input, not an unsupported type ─────────────
#
# The architecture is: Citra Flow reads the source system's scanned and
# proprietary files ONCE at ingestion, converts them, and stores text/markdown.
# The MCP brokers the bytes. The runtime reads them. Until this, the runtime
# 415'd exactly the formats the pipeline curates INTO — the one thing it was
# guaranteed to be handed.


# ── The text path: an explicit gate, evidence over claims, honest decoding ────


def test_the_gate_is_an_explicit_set_not_a_text_prefix():
    """`startswith("text/")` also matches text/html, text/xml and
    text/javascript. An HTML page — an intranet report, or an error page served
    where a PDF was promised — would be decoded WITH its tags and scripts and
    reasoned over as a curated document: bad extraction, and an
    untrusted-content prompt-injection vector next to the app's own
    instructions."""
    from ocr_proxy import TEXT_DOC_MIMES

    for allowed in ("text/plain", "text/markdown", "text/csv", "application/json"):
        assert allowed in TEXT_DOC_MIMES
    for refused in ("text/html", "text/xml", "text/javascript"):
        assert refused not in TEXT_DOC_MIMES, f"{refused} must not be a document"


def test_binary_is_detected_whatever_the_content_type_claims():
    """Buckets serve text/plain whenever object metadata was not set at upload.
    The header is a claim; the first bytes are the evidence."""
    from ocr_proxy import sniff_binary

    assert sniff_binary(b"%PDF-1.7\nstuff") == "application/pdf"
    assert sniff_binary(b"PK\x03\x04...") == "a zip/office archive"
    assert sniff_binary(b"\x89PNG\r\n\x1a\n") == "a PNG image"
    assert sniff_binary(b"\xd0\xcf\x11\xe0") == "a legacy Office (OLE) document"
    # real text is not mistaken for binary
    assert sniff_binary(b"# Dealer Finance Credit Policy\n\n4.2 ...") is None
    assert sniff_binary(b"") is None


def test_decoding_honours_bom_and_falls_back_before_giving_up():
    """A plain decode(errors='replace') absorbs every encoding problem: a UTF-16
    file (routine from Windows tooling) comes back interleaved with NULs and a
    cp1252 file loses every accented character — while the citation still
    reports a plausible char count and the model extracts from the wreckage."""
    from ocr_proxy import decode_document_text

    text, enc, lossy = decode_document_text("café — ok".encode("utf-16"))
    assert (text, lossy) == ("café — ok", False) and enc == "utf-16"

    text, enc, lossy = decode_document_text("café - ok".encode("cp1252"))
    assert (text, lossy) == ("café - ok", False) and enc == "cp1252"

    text, enc, _ = decode_document_text(b"\xef\xbb\xbf" + "café".encode("utf-8"))
    assert text == "café" and enc == "utf-8-sig"      # BOM stripped, not kept


def test_an_undecodable_file_says_so_rather_than_pretending():
    """The one case that must not be silent — the model is about to extract
    fields from characters we could not read."""
    from ocr_proxy import decode_document_text

    # invalid utf-8, ODD length so utf-16 cannot apply, and 0x81 is one of
    # cp1252's five undefined positions — genuinely undecodable.
    text, enc, lossy = decode_document_text(b"\xff\x81\x8d")
    assert lossy is True
    assert enc == "utf-8/replace"
    assert text          # still returns what it can, flagged


def test_utf16_is_never_guessed_without_evidence():
    """utf-16 accepts almost ANY even-length byte string — eight arbitrary bytes
    decode to four valid CJK characters. Trying it as a blind fallback turned
    ordinary cp1252 text into confident gibberish, which is worse than failing:
    the model would extract fields from the gibberish and report confidence.

    Found by the test above, which I had expected to be undecodable and which
    utf-16 happily 'decoded'."""
    from ocr_proxy import decode_document_text

    # even length and valid as utf-16 — but it is really cp1252 bytes
    text, enc, lossy = decode_document_text(b"\xc3\x28\xa0\xa1\xf0\x28\x8c\x28")
    assert enc == "cp1252", f"guessed {enc} on evidence-free bytes"
    assert lossy is False

    # ...whereas NUL-heavy bytes ARE evidence of BOM-less utf-16
    text, enc, _ = decode_document_text("hello world".encode("utf-16-le"))
    assert (text, enc) == ("hello world", "utf-16")
