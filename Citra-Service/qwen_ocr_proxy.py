# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
qwen_ocr_proxy.py

OCR and vision proxy for Citra AI.
Uses the OpenAI-compatible vision API (chat completions with image_url content).

Env vars:
  VISION_BASE_URL  — base URL of your vision endpoint  (required)
  VISION_API_KEY   — API key for the endpoint           (required)
  VISION_MODEL     — model name override (default: qwen/qwen3-vl-32b-instruct)

Used by:
- api/quick_chat.py: _extract_pdf() fallback, _extract_image()
"""

import os
import io
import base64
import logging
import time
from typing import Optional, List, Tuple

from openai import OpenAI, APIError, RateLimitError

from citra_llm import get_vision_client, get_vision_model

# Billing
try:
    from middleware.credit_check_middleware import track_query_usage, check_user_credits
    BILLING_AVAILABLE = True
except ImportError:
    BILLING_AVAILABLE = False

logger = logging.getLogger(__name__)

# Vision model — resolved from env at startup
VISION_MODEL = get_vision_model()


def _get_client() -> OpenAI:
    """Get the configured vision/OCR client."""
    return get_vision_client()


def _check_credits(user_id: str) -> None:
    """Check user credits before making an API call."""
    if BILLING_AVAILABLE and user_id:
        try:
            credit_check = check_user_credits(user_id, 0)
            if not credit_check['success']:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "insufficient_credits",
                        "message": credit_check.get('message', "Insufficient credits"),
                        "balance": credit_check.get('balance', 0)
                    }
                )
        except ImportError:
            pass
        except Exception as e:
            if hasattr(e, 'status_code'):
                raise
            logger.error(f"⚠️ [VISION_OCR] Credit check error: {e}")


def _track_usage(user_id: str, user_email: str, input_tokens: int, output_tokens: int) -> None:
    """Track token usage for billing."""
    if not BILLING_AVAILABLE or not user_id:
        return
    try:
        track_query_usage(
            user_id=user_id,
            email=user_email or user_id,
            model=VISION_MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=0,
        )
        logger.info(f"✅ [VISION_OCR] Usage tracked for {user_id} ({input_tokens}in/{output_tokens}out)")
    except Exception as e:
        logger.error(f"❌ [VISION_OCR] Failed to track usage: {e}")


def extract_text_from_image(
    image_bytes: bytes,
    filename: str = "image.png",
    mime_type: str = None,
    user_id: str = None,
    user_email: str = None,
    prompt: str = None,
) -> str:
    """
    Extract text and describe an image using Qwen 70B vision model.

    Drop-in replacement for _extract_image() that used llm.

    Args:
        image_bytes: Raw image file bytes.
        filename: Original filename for MIME detection.
        mime_type: MIME type of the image.
        user_id: User ID for billing.
        user_email: User email for billing.
        prompt: Custom prompt (default: describe + extract text).

    Returns:
        Description and extracted text from the image.
    """
    _check_credits(user_id)

    if not image_bytes:
        raise ValueError("Image content is empty")

    # Resolve MIME type
    if not mime_type:
        ext = os.path.splitext(filename)[1].lower()
        mime_map = {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
        }
        mime_type = mime_map.get(ext, 'image/png')

    # Encode image as base64 data URI
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_url = f"data:{mime_type};base64,{image_b64}"

    default_prompt = (
        "Describe this image in detail, including any text, data, charts, or tables visible. "
        "Extract ALL text content accurately. Be thorough."
    )

    client = _get_client()

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt or default_prompt
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }
            ]
        }
    ]

    for attempt in range(3):
        try:
            logger.info(
                f"🖼️ [VISION_OCR] Analyzing image: {filename}, "
                f"mime={mime_type}, size={len(image_bytes)} bytes, user={user_id} "
                f"(attempt {attempt + 1})"
            )

            response = client.chat.completions.create(
                model=VISION_MODEL,
                messages=messages,
                max_completion_tokens=4000,
                temperature=0.2,
            )

            if not response.choices:
                raise RuntimeError(
                    f"Vision model '{VISION_MODEL}' returned no choices "
                    f"(likely invalid model slug or non-chat-completion response). "
                    f"Raw response: {response!r}"
                )
            result = response.choices[0].message.content or ""

            # Track billing
            if response.usage:
                _track_usage(
                    user_id=user_id,
                    user_email=user_email,
                    input_tokens=response.usage.prompt_tokens or 0,
                    output_tokens=response.usage.completion_tokens or 0,
                )

            logger.info(f"✅ [VISION_OCR] Image analysis complete: {len(result)} chars")
            return result

        except RateLimitError as e:
            if attempt < 2:
                wait_time = 2 ** (attempt + 1)
                logger.warning(f"⚠️ [VISION_OCR] Rate limited, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                raise

        except (APIError,) as e:
            if attempt < 2:
                wait_time = 2 ** (attempt + 1)
                logger.warning(f"⚠️ [VISION_OCR] API error, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                raise

        except Exception as e:
            logger.error(f"❌ [VISION_OCR] Error: {e}")
            raise

    return ""


def extract_text_from_pdf_pages(
    page_images: List[Tuple[int, bytes]],
    filename: str = "document.pdf",
    user_id: str = None,
    user_email: str = None,
) -> str:
    """
    Extract text from scanned PDF pages using Qwen 70B vision model.

    Drop-in replacement for the vision API fallback in _extract_pdf().

    Args:
        page_images: List of (page_number, png_bytes) tuples.
        filename: Original PDF filename for logging.
        user_id: User ID for billing.
        user_email: User email for billing.

    Returns:
        Extracted text from all pages combined.
    """
    _check_credits(user_id)

    if not page_images:
        return f"[PDF: {filename} — no pages to process]"

    client = _get_client()

    # Build multimodal content: text prompt + all page images
    content = [
        {
            "type": "text",
            "text": (
                f"This is a scanned PDF document ({filename}). "
                "Describe and extract ALL text content from each page accurately. "
                "Include all data, tables, values, labels, and headers you can see."
            )
        }
    ]

    for page_num, img_bytes in page_images:
        image_b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"}
        })

    messages = [{"role": "user", "content": content}]

    total_input = 0
    total_output = 0

    for attempt in range(3):
        try:
            logger.info(
                f"📄 [VISION_OCR] Processing {len(page_images)} page(s) of {filename} "
                f"for user {user_id} (attempt {attempt + 1})"
            )

            response = client.chat.completions.create(
                model=VISION_MODEL,
                messages=messages,
                max_completion_tokens=8000,
                temperature=0.2,
            )

            if not response.choices:
                raise RuntimeError(
                    f"Vision model '{VISION_MODEL}' returned no choices "
                    f"(likely invalid model slug or non-chat-completion response). "
                    f"Raw response: {response!r}"
                )
            result = response.choices[0].message.content or ""

            if response.usage:
                total_input = response.usage.prompt_tokens or 0
                total_output = response.usage.completion_tokens or 0
                _track_usage(user_id, user_email, total_input, total_output)

            logger.info(f"✅ [VISION_OCR] PDF OCR complete: {len(result)} chars from {len(page_images)} pages")
            return f"[PDF: {filename} — scanned document, OCR]\n{result}"

        except RateLimitError as e:
            if attempt < 2:
                wait_time = 2 ** (attempt + 1)
                logger.warning(f"⚠️ [VISION_OCR] Rate limited, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                raise

        except (APIError,) as e:
            if attempt < 2:
                wait_time = 2 ** (attempt + 1)
                logger.warning(f"⚠️ [VISION_OCR] API error, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                raise

        except Exception as e:
            logger.error(f"❌ [VISION_OCR] Error: {e}")
            raise

    return f"[PDF: {filename} — OCR failed after retries]"
