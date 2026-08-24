# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
data-discovery-service — Semantic enrichment
==============================================
Turns a cryptic SQL schema into something an LLM can plan against.

Pipeline (called per dataset, inline from the crawler — no separate worker):

    1. is_cryptic_dataset()  — only spend LLM on schemas whose names are
                                actually unreadable. Tables with sensible
                                names skip enrichment entirely.
    2. _redact_sample_rows() — replace PII column values with type tags
                                ('<email>', '<aadhaar>') so real PII never
                                leaves the network even to an on-prem LLM.
    3. compute_schema_fingerprint() — hash the physical schema. The crawler
                                consults the previously-stored fingerprint
                                in Mongo and skips the LLM call when nothing
                                has changed (the dominant case on a nightly
                                re-crawl of stable schemas).
    4. enrich_dataset()      — single batched call to the configured
                                OpenAI-compatible LLM (OpenRouter for the
                                cloud deployment; any vLLM/on-prem endpoint
                                works by env-var alone) returning a JSON
                                object with semantic ``name`` +
                                ``description`` for the table and every
                                cryptic column.

The enricher NEVER mutates ``physical_name``. Dispatch always uses the
physical identifier — the LLM-derived name is presentation-only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI-compatible client — same pattern as Citra-Service/llm_client.py.
# Swap providers (OpenRouter / vLLM) by changing
# LLM_BASE_URL + LLM_API_KEY + LLM_MODEL — no code change.
# ---------------------------------------------------------------------------

_llm_client: Optional[AsyncOpenAI] = None


def _get_llm_client(settings: Settings) -> AsyncOpenAI:
    global _llm_client
    if _llm_client is None:
        _llm_client = AsyncOpenAI(
            api_key=settings.llm_api_key or "no-key",
            base_url=settings.llm_base_url,
            max_retries=2,
            timeout=settings.llm_request_timeout_seconds,
        )
        logger.info("[ENRICHER] LLM client → %s (model=%s)", settings.llm_base_url, settings.llm_model)
    return _llm_client


# ---------------------------------------------------------------------------
# Crypticness heuristic
# ---------------------------------------------------------------------------

_VOWELS = set("aeiouy")
_WORDY = re.compile(r"[A-Za-z]+")
_DIGITS = re.compile(r"\d")


def cryptic_score(name: str) -> float:
    """Return 0..1 — higher means harder to read. >= 0.5 is "enrich me".

    Heuristic blend:
      • very short (<=3 chars)               → +0.6
      • >40 % digits                          → +0.4
      • <25 % vowels among letters            → +0.4
      • all-caps with underscores AND digits  → +0.2
      • no separator + length < 6             → +0.2
      • ends with ``_001`` / ``_42`` style    → +0.2
    Capped at 1.0. Tuned to flag ``TBL_X_001`` / ``c_dt_42`` while sparing
    ``customers``, ``policy_renewals``, ``claim_line_item``.
    """
    if not name:
        return 1.0
    s = name.strip()
    score = 0.0

    if len(s) <= 3:
        score += 0.6

    letters = "".join(_WORDY.findall(s))
    digit_count = len(_DIGITS.findall(s))
    if s and digit_count / max(1, len(s)) > 0.4:
        score += 0.4

    if letters:
        vowel_count = sum(1 for c in letters.lower() if c in _VOWELS)
        if vowel_count / len(letters) < 0.25:
            score += 0.4

    if s.isupper() and "_" in s and digit_count > 0:
        score += 0.2

    if "_" not in s and "-" not in s and len(s) < 6:
        score += 0.2

    if re.search(r"_\d{2,}$", s):
        score += 0.2

    return min(1.0, score)


def is_cryptic(name: str, threshold: float) -> bool:
    return cryptic_score(name) >= threshold


def is_cryptic_dataset(
    *,
    table_physical_name: str,
    column_physical_names: List[str],
    threshold: float,
) -> bool:
    """A dataset is worth enriching if the table name OR ≥30% of its
    columns are cryptic. Pure-data tables with one cryptic FK column don't
    warrant the LLM cost."""
    if is_cryptic(table_physical_name, threshold):
        return True
    if not column_physical_names:
        return False
    cryptic_cols = sum(1 for c in column_physical_names if is_cryptic(c, threshold))
    return (cryptic_cols / len(column_physical_names)) >= 0.3


# ---------------------------------------------------------------------------
# Schema fingerprint — drives the enrichment cache
# ---------------------------------------------------------------------------


def compute_schema_fingerprint(
    table_physical_name: str,
    columns: List[Dict[str, Any]],
) -> str:
    """Stable hash of the *physical* schema. If this matches the previously
    stored fingerprint, the schema hasn't changed and we can keep the
    existing semantic mapping without re-calling the LLM."""
    payload = {
        "table": table_physical_name,
        "cols": sorted(
            (str(c.get("physical_name") or c.get("name") or ""), str(c.get("type") or ""))
            for c in columns
        ),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


# ---------------------------------------------------------------------------
# PII-safe sample redaction
# ---------------------------------------------------------------------------


def redact_sample_rows(
    rows: List[Dict[str, Any]],
    columns: List[Dict[str, Any]],
    max_rows: int,
) -> List[Dict[str, Any]]:
    """Replace values in PII-flagged columns with a type tag before sending
    to the LLM. Even an on-prem LLM has logs.

    ``columns`` here is the post-classifier column list (each carries
    ``pii`` and optionally ``semantic_type``).
    """
    pii_lookup = {
        str(c.get("physical_name") or c.get("name")): (
            f"<{c.get('semantic_type') or 'pii'}>"
        )
        for c in columns
        if c.get("pii")
    }
    out: List[Dict[str, Any]] = []
    for row in rows[:max_rows]:
        red: Dict[str, Any] = {}
        for k, v in row.items():
            if k in pii_lookup and v not in (None, ""):
                red[k] = pii_lookup[k]
            else:
                red[k] = v
        out.append(red)
    return out


# ---------------------------------------------------------------------------
# LLM call — single round-trip per dataset
# ---------------------------------------------------------------------------


_PROMPT_SYSTEM = (
    "You DESCRIBE enterprise database schemas — you do NOT rename anything. "
    "You ONLY return a single JSON object. No prose, no markdown. "
    "Keep every table and column identifier EXACTLY as given: the physical "
    "name is the queryable identifier and must never be changed, translated, "
    "or 'cleaned'. Only write a `description` (≤ 100 chars) stating what the "
    "table or column represents in business terms."
)


def _build_prompt(
    *,
    mcp_description: Optional[str],
    table_physical_name: str,
    table_kind: str,
    columns: List[Dict[str, Any]],
    redacted_samples: List[Dict[str, Any]],
) -> str:
    """Assemble a single user-prompt string. Keeps the call cheap by
    sending only what the model actually needs to disambiguate names."""
    cols_for_prompt = [
        {
            "physical_name": c.get("physical_name") or c.get("name"),
            "type": c.get("type"),
            "semantic_type": c.get("semantic_type"),
            "pii": bool(c.get("pii")),
            "distinct_values": (c.get("distinct_values") or [])[:10],
            "range": c.get("range"),
        }
        for c in columns
    ]
    body = {
        "mcp_description": mcp_description or "",
        "kind": table_kind,
        "table_physical_name": table_physical_name,
        "columns": cols_for_prompt,
        "sample_rows_redacted": redacted_samples,
        "instructions": (
            "Return JSON with this exact shape: "
            "{ \"table\": { \"description\": str, \"confidence\": 0..1 }, "
            "\"columns\": [ { \"physical_name\": str, "
            "\"description\": str, \"confidence\": 0..1 } ] }. "
            "Use each column's physical_name VERBATIM as the key — do NOT "
            "rename, translate, abbreviate, or 'clean' any identifier; the "
            "physical name is the real queryable column. Write a `description` "
            "only. Never invent columns or include columns not in the input."
        ),
    }
    return json.dumps(body, ensure_ascii=False)


async def _call_llm(settings: Settings, prompt: str) -> Optional[Dict[str, Any]]:
    """Call the configured OpenAI-compatible chat-completions endpoint.

    Returns the parsed JSON object the model emitted, or None on failure.
    Uses the openai SDK so it works against any OpenAI-protocol provider
    (OpenRouter, vLLM, etc.) by env-var alone.
    """
    client = _get_llm_client(settings)
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": _PROMPT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            # Generous, cost-neutral cap: reasoning models (GLM/DeepSeek) spend
            # hidden reasoning tokens against this budget; 1500 starved them and
            # returned empty content. The model stops when the JSON is done.
            max_tokens=16000,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            return None
        # Some models wrap JSON in code fences even when asked not to.
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)
        return json.loads(content)
    except Exception as exc:
        logger.warning("LLM enrichment call failed: %s", exc)
        return None


async def enrich_dataset(
    *,
    settings: Settings,
    mcp_description: Optional[str],
    table_physical_name: str,
    table_kind: str,
    columns: List[Dict[str, Any]],
    sample_rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Top-level entry. Returns:

        {
          "table":  {"name": str, "description": str, "confidence": float},
          "columns": {physical_name: {"name": str, "description": str, "confidence": float}},
        }

    Or ``None`` if enrichment was disabled, the LLM was unreachable, or the
    response was malformed.
    """
    if not settings.llm_enrichment_enabled:
        return None

    redacted = redact_sample_rows(sample_rows, columns, settings.llm_sample_rows_for_naming)
    prompt = _build_prompt(
        mcp_description=mcp_description,
        table_physical_name=table_physical_name,
        table_kind=table_kind,
        columns=columns,
        redacted_samples=redacted,
    )
    raw = await _call_llm(settings, prompt)
    if not isinstance(raw, dict):
        return None

    table_block = raw.get("table") or {}
    cols_block = raw.get("columns") or []
    cols_index: Dict[str, Dict[str, Any]] = {}
    for c in cols_block:
        if not isinstance(c, dict):
            continue
        pname = str(c.get("physical_name") or "")
        if not pname:
            continue
        cols_index[pname] = {
            # NAME = the physical identifier, VERBATIM. The catalogue `name`
            # is what downstream binds AND queries by, so it must equal the
            # real DB column. Enrichment adds `description` only — it must
            # never rename a column (a renamed name silently breaks the
            # runtime query: "column <enriched> does not exist").
            "name": pname,
            "description": _sanitize_desc(c.get("description")),
            "confidence": _coerce_confidence(c.get("confidence")),
        }

    return {
        "table": {
            # Table name kept verbatim too — dispatch/query uses it as-is.
            "name": table_physical_name,
            "description": _sanitize_desc(table_block.get("description")),
            "confidence": _coerce_confidence(table_block.get("confidence")),
        },
        "columns": cols_index,
    }


# ---------------------------------------------------------------------------
# Sanitisation — never trust the model
# ---------------------------------------------------------------------------


def _sanitize_desc(desc: Any) -> Optional[str]:
    if desc is None:
        return None
    s = str(desc).strip()
    if not s:
        return None
    # Strip line breaks and collapse whitespace; keep it a single sentence.
    s = re.sub(r"\s+", " ", s)
    return s[:200]


def _coerce_confidence(c: Any) -> Optional[float]:
    try:
        f = float(c)
    except Exception:
        return None
    return max(0.0, min(1.0, f))


# ---------------------------------------------------------------------------
# Manual override — dept_sources.name_overrides wins over the LLM
# ---------------------------------------------------------------------------


def apply_manual_overrides(
    *,
    overrides: Optional[Dict[str, Any]],
    table_physical_name: str,
    columns_in: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, str]], Dict[str, Dict[str, str]]]:
    """Read a ``name_overrides`` block on the source document. Shape:

        {
          "tables": {"<physical>": {"name": str, "description": str}},
          "columns": {"<table_physical>": {"<col_physical>": {"name": str, "description": str}}}
        }

    Returns (table_override_or_None, column_overrides_keyed_by_physical_name).
    Empty dicts when no overrides apply.
    """
    if not isinstance(overrides, dict):
        return None, {}

    table_block = (overrides.get("tables") or {}).get(table_physical_name)
    cols_for_table = (overrides.get("columns") or {}).get(table_physical_name) or {}

    col_out: Dict[str, Dict[str, str]] = {}
    for c in columns_in:
        pname = str(c.get("physical_name") or c.get("name"))
        if pname in cols_for_table and isinstance(cols_for_table[pname], dict):
            col_out[pname] = {
                # Keep the physical name as the queryable identifier; an
                # override may refine the description but must not rename the
                # column (a renamed `name` breaks the runtime query).
                "name": pname,
                "description": _sanitize_desc(cols_for_table[pname].get("description")) or "",
            }

    table_out: Optional[Dict[str, str]] = None
    if isinstance(table_block, dict):
        table_out = {
            "name": table_physical_name,
            "description": _sanitize_desc(table_block.get("description")) or "",
        }
    return table_out, col_out
