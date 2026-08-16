"""OCR / vision proxy for smart-app-service.

The runtime LLM and the builder pod do not get VISION_API_KEY. They call
POST /smart-app/internal/ocr with an image (base64 or URL) and an optional
prompt; this module forwards the request to the configured OpenAI-compatible
vision endpoint (GLM-4.6V — ``z-ai/glm-4.6v:nitro`` — swappable by env).

Provider-agnostic — only the OpenAI chat-completion contract is assumed.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OcrError(Exception):
    """Raised by the proxy on any vision-call failure."""

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class OcrResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_PROMPT = (
    "Describe this image in detail, including any text, data, charts, or"
    " tables visible. Extract ALL text content accurately. Be thorough."
)

_PDF_PROMPT = (
    "This is a scanned document. Describe and extract ALL text content from"
    " each page accurately. Include all data, tables, values, labels, and"
    " headers you can see."
)

_MAX_RAW_BYTES = 12 * 1024 * 1024  # 12 MB
_MAX_B64_LEN = 16 * 1024 * 1024
_FETCH_TIMEOUT = 15.0
_VISION_TIMEOUT = 60.0
_RETRIES = 3

#: Document MIMEs read as TEXT — decoded and reasoned over directly, no parser
#: and no vision call. THE canonical gate, imported by tools_v2_dispatch so the
#: fetch guard and the dispatch branch cannot drift apart.
#:
#: An EXPLICIT set, deliberately not `mime.startswith("text/")`. That prefix
#: also matches text/html, text/xml and text/javascript: an HTML page — an
#: intranet report, or an error page returned where a PDF was promised — would
#: be decoded with its tags, scripts and navigation intact and reasoned over as
#: if it were a curated document. Poor extraction, and an untrusted-content
#: prompt-injection vector sitting next to the app's own instructions.
#:
#: These are what Citra Flow curates INTO. Anything else is curated at
#: ingestion, not parsed in the request path.
TEXT_DOC_MIMES = frozenset({
    "text/plain", "text/markdown", "text/x-markdown", "text/csv",
    "text/tab-separated-values",
    "application/json", "application/x-ndjson",
})

#: Leading bytes that mean "this is NOT text", whatever the Content-Type says.
#: Buckets serve text/plain or octet-stream whenever object metadata was not set
#: at upload — routine, and this codebase has been bitten by content-type
#: problems before. Without this a PDF served as text/plain decodes to mojibake,
#: passes an emptiness check, and gets field-extracted by the model with a
#: confidence score attached.
_BINARY_MAGIC: tuple = (
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "a zip/office archive"),
    (b"\x89PNG\r\n\x1a\n", "a PNG image"),
    (b"\xff\xd8\xff", "a JPEG image"),
    (b"GIF8", "a GIF image"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"\xd0\xcf\x11\xe0", "a legacy Office (OLE) document"),
    (b"%!PS", "a PostScript file"),
)


def sniff_binary(raw: bytes) -> Optional[str]:
    """Human-readable description of the binary format, or None if it looks like
    text. Content-Type is a claim; the bytes are the evidence."""
    head = raw[:8]
    for magic, what in _BINARY_MAGIC:
        if head.startswith(magic):
            return what
    return None


def decode_document_text(raw: bytes) -> Tuple[str, str, bool]:
    """Decode document bytes to text. Returns ``(text, encoding, lossy)``.

    A plain ``decode("utf-8", errors="replace")`` absorbs every encoding
    problem silently: a UTF-16 file (routine from Windows curation tooling)
    comes back as the real characters interleaved with NULs, and a cp1252 file
    loses every accented character and curly quote — while the citation still
    reports a plausible character count. The model then extracts from corrupted
    text and returns a confidence score.

    So: honour a BOM, try the encodings that actually occur, and when nothing
    decodes cleanly say SO (``lossy=True``) rather than pretending."""
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return (raw.decode("utf-16", errors="replace"), "utf-16", False)
    if raw.startswith(codecs.BOM_UTF8):
        return (raw.decode("utf-8-sig", errors="replace"), "utf-8-sig", False)

    # BOM-less UTF-16, checked BEFORE utf-8 — because it would otherwise
    # "succeed". UTF-16LE ASCII is a NUL after every character, and a NUL is
    # valid UTF-8, so utf-8 decodes such a file without error into text
    # interleaved with NULs: no exception, no signal, corrupted prompt. The NUL
    # density is the tell, and nothing else produces it.
    #
    # Only on that EVIDENCE, never as a blind fallback: utf-16 accepts almost
    # any even-length byte string (eight arbitrary bytes decode to four valid
    # CJK characters), so speculative use turns ordinary cp1252 text into
    # confident gibberish — worse than failing, because the model would extract
    # from it and report confidence.
    sample = raw[:4096]
    if sample and len(raw) % 2 == 0 and sample.count(0) > len(sample) // 4:
        try:
            return (raw.decode("utf-16"), "utf-16", False)
        except (UnicodeDecodeError, UnicodeError):
            pass

    try:
        return (raw.decode("utf-8"), "utf-8", False)
    except UnicodeDecodeError:
        pass

    # cp1252 is the right guess for legacy Windows output and, being
    # single-byte, decodes anything except its five undefined positions.
    try:
        return (raw.decode("cp1252"), "cp1252", False)
    except UnicodeDecodeError:
        pass

    return (raw.decode("utf-8", errors="replace"), "utf-8/replace", True)


def _mime_for_filename(filename: str, default: str = "image/png") -> str:
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, default)


def _data_uri(image_bytes: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _is_public_host(hostname: str) -> bool:
    """Reject obvious SSRF targets (localhost, RFC1918, link-local).

    Identical policy to action-chat-service's vision endpoint. Real
    hostnames (DNS-resolved private IPs, AWS metadata) are not exhaustively
    blocked — operators wanting hardened SSRF should put this proxy behind
    an egress allow-list.
    """

    h = (hostname or "").strip().lower()
    if not h:
        return False
    blocked_prefixes = ("127.", "10.", "192.168.", "169.254.", "0.")
    if h in {"localhost", "::1"}:
        return False
    if any(h.startswith(p) for p in blocked_prefixes):
        return False
    if h.startswith("172."):
        try:
            second = int(h.split(".", 2)[1])
            if 16 <= second <= 31:
                return False
        except (IndexError, ValueError):
            return False
    return True


async def _fetch_image_url(image_url: str) -> Tuple[bytes, str, str]:
    """Server-side fetch with SSRF guards. Returns (raw_bytes, mime, filename)."""

    parsed = urlparse(image_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise OcrError("bad_image_url", "image_url scheme must be http/https", 400)
    if not _is_public_host(parsed.hostname):
        raise OcrError(
            "ssrf_blocked",
            "image_url host is non-public; SSRF blocked",
            400,
        )
    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT, follow_redirects=True, max_redirects=3
    ) as cl:
        resp = await cl.get(image_url)
        if resp.status_code >= 400:
            raise OcrError(
                "fetch_failed",
                f"fetch returned {resp.status_code}",
                400,
            )
        raw = resp.content[:_MAX_RAW_BYTES]
        mime = (
            (resp.headers.get("content-type") or "image/png")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        # Images and PDFs go to a model; TEXT_DOC_MIMES are read directly by
        # doc_extract. Text was rejected here until recently, which made the one
        # format the ingestion pipeline curates INTO the one format the runtime
        # could not open.
        #
        # An EXPLICIT set rather than a `text/` prefix — see TEXT_DOC_MIMES for
        # why text/html must not qualify.
        if not (
            mime.startswith("image/")
            or mime == "application/pdf"
            or mime in TEXT_DOC_MIMES
        ):
            raise OcrError(
                "unsupported_content_type",
                f"unsupported content-type: {mime}. Images and PDFs are read by "
                "a model; plain text, markdown, csv and json are read directly. "
                "Anything else — including HTML — should be curated to one of "
                "those at ingestion rather than parsed in the request path.",
                415,
            )
    filename = parsed.path.rsplit("/", 1)[-1] or "image.png"
    return raw, mime, filename


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ocr_image(
    *,
    settings: Settings,
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: Optional[str] = None,
    max_output_tokens: int = 4000,
) -> OcrResult:
    """OCR a single image.

    Raises ``OcrError`` on bad config, retries 429 / 5xx up to ``_RETRIES``.
    """

    if not settings.ocr_enabled:
        logger.error(
            "ocr_image: vision proxy NOT configured - "
            "VISION_BASE_URL / VISION_API_KEY / VISION_MODEL must be set "
            "(no LLM_LARGE_* fallback); failing loud with 503"
        )
        raise OcrError(
            "ocr_not_configured",
            "VISION_BASE_URL / VISION_API_KEY / VISION_MODEL must be set",
            503,
        )
    if not image_bytes:
        raise OcrError("empty_image", "image bytes are empty", 400)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt or _DEFAULT_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": _data_uri(image_bytes, mime_type)},
                },
            ],
        }
    ]
    return await _post_chat(
        settings=settings,
        messages=messages,
        max_output_tokens=max_output_tokens,
    )


async def ocr_pdf_pages(
    *,
    settings: Settings,
    pages: List[bytes],
    prompt: Optional[str] = None,
    max_output_tokens: int = 8000,
) -> OcrResult:
    """OCR a list of rasterised PDF pages in a single call."""

    if not settings.ocr_enabled:
        logger.error(
            "ocr_pdf_pages: vision proxy NOT configured - "
            "VISION_BASE_URL / VISION_API_KEY / VISION_MODEL must be set "
            "(no LLM_LARGE_* fallback); failing loud with 503"
        )
        raise OcrError(
            "ocr_not_configured",
            "VISION_BASE_URL / VISION_API_KEY / VISION_MODEL must be set",
            503,
        )
    if not pages:
        raise OcrError("empty_pages", "no pages to process", 400)

    content: list = [{"type": "text", "text": prompt or _PDF_PROMPT}]
    for page_bytes in pages:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": _data_uri(page_bytes, "image/png"),
                },
            }
        )
    return await _post_chat(
        settings=settings,
        messages=[{"role": "user", "content": content}],
        max_output_tokens=max_output_tokens,
    )


# ---------------------------------------------------------------------------
# Internal: HTTP call with retries
# ---------------------------------------------------------------------------


async def _post_chat(
    *,
    settings: Settings,
    messages: list,
    max_output_tokens: int,
) -> OcrResult:
    url = settings.vision_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.vision_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.vision_model,
        "messages": messages,
        "max_tokens": max_output_tokens,
        "temperature": 0.2,
    }

    last_exc: Optional[Exception] = None
    for attempt in range(_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=_VISION_TIMEOUT) as cl:
                resp = await cl.post(url, json=body, headers=headers)
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "[OCR] %s from vision endpoint (attempt %d/%d), retrying in %ds",
                    resp.status_code,
                    attempt + 1,
                    _RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise OcrError(
                    "vision_error",
                    f"vision endpoint returned {resp.status_code}: {resp.text[:500]}",
                    502,
                )
            payload = resp.json()
            choices = payload.get("choices") or []
            if not choices:
                raise OcrError(
                    "empty_response",
                    "vision endpoint returned no choices",
                    502,
                )
            text = (choices[0].get("message") or {}).get("content") or ""
            usage = payload.get("usage") or {}
            return OcrResult(
                text=text,
                tokens_in=int(usage.get("prompt_tokens") or 0),
                tokens_out=int(usage.get("completion_tokens") or 0),
                model=settings.vision_model,
            )
        except httpx.HTTPError as e:
            last_exc = e
            wait = 2 ** (attempt + 1)
            logger.warning(
                "[OCR] httpx error (attempt %d/%d): %s; retrying in %ds",
                attempt + 1,
                _RETRIES,
                e,
                wait,
            )
            await asyncio.sleep(wait)
            continue
        except OcrError:
            raise
        except Exception as e:  # noqa: BLE001
            last_exc = e
            break

    raise OcrError(
        "vision_unavailable",
        f"vision endpoint failed after {_RETRIES} attempts: {last_exc}",
        502,
    )
