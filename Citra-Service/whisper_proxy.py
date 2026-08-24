# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
whisper_proxy.py

Audio-to-text transcription proxy for Citra AI.
Supports two providers:
  - simplismart  : SimpliSmart Whisper API (base64 JSON, default)
  - openai       : Any OpenAI-compatible Whisper endpoint (multipart file upload)

Env vars (new generic names, with STT_* fallback for backward compat):
  AUDIO_TO_TEXT_PROVIDER  — "simplismart" (default) or "openai"
  AUDIO_TO_TEXT_BASE_URL  — endpoint URL                            (required)
  AUDIO_TO_TEXT_API_KEY   — API key / bearer token                  (required for simplismart)
  AUDIO_TO_TEXT_MODEL     — model name                              (default: openai/whisper-large-v3-turbo)

Used by:
- query.py: transcribe_audio() → transcribe_audio()
- api/quick_chat.py: _extract_audio() → transcribe_audio()
"""

import os
import io
import logging
import time
import base64
import mimetypes
from typing import Optional

import requests as _requests

# Billing
try:
    from middleware.credit_check_middleware import track_query_usage, check_user_credits
    BILLING_AVAILABLE = True
except ImportError:
    BILLING_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Config (resolved at import time, with STT_* fallback for backward compat) ─
STT_PROVIDER = (os.getenv("AUDIO_TO_TEXT_PROVIDER") or os.getenv("STT_PROVIDER", "simplismart")).strip().lower()
STT_BASE_URL = (os.getenv("AUDIO_TO_TEXT_BASE_URL") or os.getenv("STT_BASE_URL", "")).strip()
STT_API_KEY  = (os.getenv("AUDIO_TO_TEXT_API_KEY")  or os.getenv("STT_API_KEY",  "")).strip()
STT_MODEL    = (os.getenv("AUDIO_TO_TEXT_MODEL")    or os.getenv("STT_MODEL",    "openai/whisper-large-v3-turbo")).strip()


_MIME_TO_EXT = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/wav": ".wav",  "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",  "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",  "audio/webm": ".webm",
    "audio/flac": ".flac",
}


def _resolve_filename(filename: str, mime_type: str) -> str:
    """Ensure the upload filename carries a proper audio extension."""
    if filename and os.path.splitext(filename)[1]:
        return filename
    ext = _MIME_TO_EXT.get(mime_type or "", ".mp3")
    return (filename or "audio") + ext


def transcribe_audio(
    audio_bytes: bytes,
    mime_type: str = None,
    filename: str = None,
    user_id: str = None,
    user_email: str = None,
    language: str = None,
    prompt: str = None,
) -> str:
    """
    Transcribe audio via the configured STT provider.

    Providers:
      simplismart — SimpliSmart Whisper API  (base64 JSON payload)
      openai      — OpenAI-compatible endpoint (multipart file upload)

    Args:
        audio_bytes: Raw audio file bytes.
        mime_type:   MIME type (auto-detected from filename if omitted).
        filename:    Original filename (used for logging and MIME detection).
        user_id:     User ID for billing.
        user_email:  User email for billing.
        language:    ISO-639-1 language code (optional; endpoint auto-detects if omitted).
        prompt:      Optional context hint passed to the Whisper endpoint.

    Returns:
        Transcribed text string.
    """

    if not audio_bytes:
        raise ValueError("Audio content is empty; cannot transcribe")

    if not STT_BASE_URL:
        raise ValueError(
            "STT_BASE_URL must be set to use the Whisper transcription endpoint."
        )

    # Credit check
    if BILLING_AVAILABLE and user_id:
        try:
            credit_check = check_user_credits(user_id, 0)
            if not credit_check["success"]:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "insufficient_credits",
                        "message": credit_check.get("message", "Insufficient credits"),
                        "balance": credit_check.get("balance", 0),
                    },
                )
        except ImportError:
            pass
        except Exception as e:
            if hasattr(e, "status_code"):
                raise
            logger.error(f"⚠️ [WHISPER] Credit check error: {e}")

    # Resolve MIME + filename
    if not mime_type and filename:
        mime_type, _ = mimetypes.guess_type(filename)
    mime_type = mime_type or "audio/mpeg"
    upload_filename = _resolve_filename(filename, mime_type)

    # Dispatch to provider
    if STT_PROVIDER == "simplismart":
        result = _transcribe_simplismart(audio_bytes, upload_filename, user_id, language, prompt)
    else:
        result = _transcribe_openai(audio_bytes, upload_filename, user_id, language, prompt)

    # Track billing
    if BILLING_AVAILABLE and user_id and result:
        try:
            estimated_tokens = max(int(len(result.split()) * 1.3), 100)
            track_query_usage(
                user_id=user_id,
                email=user_email or user_id,
                model=STT_MODEL,
                input_tokens=estimated_tokens,
                output_tokens=estimated_tokens,
                cached_tokens=0,
            )
        except Exception as e:
            logger.error(f"❌ [WHISPER] Failed to track usage: {e}")

    return result


# ── SimpliSmart provider ─────────────────────────────────────────────────────

def _transcribe_simplismart(
    audio_bytes: bytes,
    filename: str,
    user_id: str,
    language: str = None,
    prompt: str = None,
) -> str:
    """Transcribe via SimpliSmart Whisper API (base64 JSON payload)."""

    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    payload = {
        "model": STT_MODEL,
        "audio_data": audio_b64,
        "language": language or "en",
        "task": "transcribe",
        "without_timestamps": True,
        "vad_filter": True,
        "vad_model": "frame",
        "vad_onset": 0.5,
        "min_silence_duration_ms": 2000,
        "speech_pad_ms": 400,
        "beam_size": 4,
        "temperature": 0.0,
        "max_tokens": 400,
        "repetition_penalty": 1.01,
    }
    if prompt:
        payload["initial_prompt"] = prompt

    headers = {
        "Authorization": f"Bearer {STT_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(3):
        try:
            logger.info(
                f"🎧 [WHISPER-SIMPLISMART] Transcribing: file={filename}, "
                f"size={len(audio_bytes)} bytes, model={STT_MODEL}, user={user_id} "
                f"(attempt {attempt + 1})"
            )

            resp = _requests.post(
                STT_BASE_URL, headers=headers, json=payload, timeout=120
            )
            resp.raise_for_status()
            data = resp.json()

            # SimpliSmart returns {"transcription": ["text"], ...}
            transcription_list = data.get("transcription", [])
            result = " ".join(transcription_list).strip() if transcription_list else ""

            logger.info(f"✅ [WHISPER-SIMPLISMART] Transcription complete: {len(result)} chars")
            return result

        except _requests.exceptions.RequestException as e:
            status = getattr(e.response, "status_code", None) if hasattr(e, "response") else None
            if attempt < 2 and status in (429, 500, 502, 503):
                wait = 2 ** (attempt + 1)
                logger.warning(f"⚠️ [WHISPER-SIMPLISMART] HTTP {status}, retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"❌ [WHISPER-SIMPLISMART] API error: {e}")
                raise

        except Exception as e:
            logger.error(f"❌ [WHISPER-SIMPLISMART] Error: {e}")
            raise

    return ""


# ── OpenAI-compatible provider ───────────────────────────────────────────────

def _transcribe_openai(
    audio_bytes: bytes,
    filename: str,
    user_id: str,
    language: str = None,
    prompt: str = None,
) -> str:
    """Transcribe via an OpenAI-compatible Whisper endpoint (multipart upload)."""
    from openai import OpenAI as _OpenAI, APIError as _APIError

    client = _OpenAI(api_key=STT_API_KEY or "no-key", base_url=STT_BASE_URL)

    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename

    call_kwargs: dict = {"model": STT_MODEL, "file": audio_file}
    if language:
        call_kwargs["language"] = language
    if prompt:
        call_kwargs["prompt"] = prompt

    for attempt in range(3):
        try:
            logger.info(
                f"🎧 [WHISPER-OPENAI] Transcribing: file={filename}, "
                f"size={len(audio_bytes)} bytes, model={STT_MODEL}, user={user_id} "
                f"(attempt {attempt + 1})"
            )

            response = client.audio.transcriptions.create(**call_kwargs)
            result = (getattr(response, "text", "") or "").strip()

            logger.info(f"✅ [WHISPER-OPENAI] Transcription complete: {len(result)} chars")
            return result

        except _APIError as e:
            status = getattr(e, "status_code", None)
            if attempt < 2 and status in (429, 500, 502, 503):
                wait = 2 ** (attempt + 1)
                logger.warning(f"⚠️ [WHISPER-OPENAI] HTTP {status}, retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"❌ [WHISPER-OPENAI] API error: {e}")
                raise

        except Exception as e:
            logger.error(f"❌ [WHISPER-OPENAI] Error: {e}")
            raise

    return ""