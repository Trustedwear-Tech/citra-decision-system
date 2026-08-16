# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

import asyncio
import logging
import math
import os
import re
import hashlib
from typing import List, Optional

import openai
from openai import AsyncOpenAI
from fastapi import HTTPException
from citra_mongo import get_sync_database

# LLM SDK removed � all LLM calls routed through llm_oss
genai = None
genai_types = None
_llm_AVAILABLE = False

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ----------- Embedding Configuration (unified EMBEDDING_* with legacy fallback) -----------
# New unified vars: EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSION
# Legacy fallback:  USE_OPENAI_EMBEDDING, OPENAI_EMBEDDING_MODEL, OPENAI_EMBEDDING_DIMENSION, OPENAI_API_KEY
_EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "").strip()
_EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "").strip()
USE_OPENAI_EMBEDDING = os.getenv("USE_OPENAI_EMBEDDING", "true").lower() == "true"
OPENAI_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION") or os.getenv("OPENAI_EMBEDDING_DIMENSION", "768"))
OPENAI_API_KEY = _EMBEDDING_API_KEY or os.getenv("OPENAI_API_KEY")

# embedding configuration
_llm_MODEL = os.getenv("llm_EMBED_MODEL", "text-multilingual-embedding-002")
_DEFAULT_QUERY_TASK = os.getenv("llm_EMBED_QUERY_TASK", "RETRIEVAL_QUERY")
_DEFAULT_DOCUMENT_TASK = os.getenv("llm_EMBED_DOCUMENT_TASK", "RETRIEVAL_DOCUMENT")
_RAW_DIMENSION = os.getenv("llm_EMBED_DIM") or os.getenv("EMBED_DIM")
_llm_DIMENSION = int(_RAW_DIMENSION) if _RAW_DIMENSION else None

# ----------- Query-instruction prefix (Qwen3-Embedding et al.) -----------
# Instruction-aware embedders (Qwen3-Embedding) score ~1-5% higher on retrieval
# when the QUERY carries a task instruction; DOCUMENTS are always embedded as-is.
# This is a no-op for OpenAI / text-embedding models (they'd treat it as noise).
#   EMBEDDING_QUERY_INSTRUCTION unset  -> auto: default instruction iff model looks Qwen-family
#   EMBEDDING_QUERY_INSTRUCTION="off"  -> disabled (bare queries even on Qwen)
#   EMBEDDING_QUERY_INSTRUCTION=<text> -> use <text> as the instruction for every query embed
_DEFAULT_QUERY_INSTRUCTION = "Given a search query, retrieve relevant passages that answer the query"
_EMBEDDING_QUERY_INSTRUCTION_RAW = os.getenv("EMBEDDING_QUERY_INSTRUCTION", "").strip()

_llm_client = None  # Deprecated � llm removed
_llm_lock: Optional[asyncio.Lock] = None
_openai_client: Optional[AsyncOpenAI] = None
_openai_lock: Optional[asyncio.Lock] = None


def _ensure_llm_available() -> None:
    """llm removed - always raises."""
    raise HTTPException(status_code=500, detail="llm removed - use llm_oss or OpenAI embeddings")


async def _get_openai_embedding_client() -> AsyncOpenAI:
    """Get or create OpenAI embedding client."""
    global _openai_client
    global _openai_lock
    
    if _openai_client is not None:
        return _openai_client
    
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY environment variable is not set")
    
    if _openai_lock is None:
        _openai_lock = asyncio.Lock()
    
    async with _openai_lock:
        if _openai_client is None:
            # base_url lets us point at any OpenAI-compatible embedding endpoint
            # (OpenRouter, a local vLLM/Infinity server, etc.). Empty => OpenAI default.
            _openai_client = AsyncOpenAI(
                api_key=OPENAI_API_KEY,
                base_url=_EMBEDDING_BASE_URL or None,
                max_retries=5,
            )
            logging.info(
                f"Embedding client initialized ({OPENAI_EMBEDDING_MODEL}, {OPENAI_EMBEDDING_DIMENSION}D) "
                f"via {_EMBEDDING_BASE_URL or 'api.openai.com (default)'}"
            )
    
    return _openai_client


async def _get_llm_client():
    """llm removed � always raises."""
    raise HTTPException(status_code=500, detail="llm removed � use llm_oss or OpenAI embeddings")


def _build_embed_config(task_type: Optional[str]):
    if task_type is None and _llm_DIMENSION is None:
        return None

    config_kwargs = {}
    if task_type:
        config_kwargs["task_type"] = task_type
    if _llm_DIMENSION:
        config_kwargs["output_dimensionality"] = _llm_DIMENSION

    if not config_kwargs:
        return None

    if genai_types is not None:
        return genai_types.EmbedContentConfig(**config_kwargs)
    return config_kwargs


def _normalize_embedding(values: List[float]) -> List[float]:
    if not values:
        return values
    if _llm_DIMENSION and _llm_DIMENSION < 3072:
        norm = math.sqrt(sum(v * v for v in values))
        if norm:
            return [v / norm for v in values]
    return values


def _active_query_instruction() -> Optional[str]:
    """Resolve the instruction to prepend to QUERY embeddings, or None for a no-op.

    Documents are never instructed. OpenAI / text-embedding models resolve to
    None (auto-branch), so this stays a strict no-op unless the model is
    Qwen-family or an explicit instruction is configured via env.
    """
    val = _EMBEDDING_QUERY_INSTRUCTION_RAW
    if val:
        if val.lower() in ("off", "none", "false", "0"):
            return None
        return val
    if "qwen" in (OPENAI_EMBEDDING_MODEL or "").lower():
        return _DEFAULT_QUERY_INSTRUCTION
    return None


def _is_query_task(resolved_task: Optional[str]) -> bool:
    """True when the resolved task_type denotes a query (vs a document)."""
    return bool(resolved_task) and "query" in resolved_task.lower()


def _instruct_query(text: str) -> str:
    """Wrap a query in Qwen's ``Instruct: ...\\nQuery: ...`` format.

    Applied at API-call time on already-truncated text, so the instruction
    never eats into the content's char budget. No-op when no instruction is
    active (see :func:`_active_query_instruction`).
    """
    instruction = _active_query_instruction()
    if not instruction:
        return text
    return f"Instruct: {instruction}\nQuery: {text}"

def get_mongo_collection():
    """
    Get the MongoDB documents collection using centralized manager
    BREAKING CHANGE: Now returns document_chunked collection instead of documents
    """
    db = get_sync_database()
    return db["document_chunked"]

def get_user_id(user_id: str) -> str:
    """
    Return sanitized user_id suitable for Azure Blob Storage container names.
    This function maintains compatibility while ensuring valid container names.
    
    Args:
        user_id (str): The email address used as user_id (already validated by JWT)
        
    Returns:
        str: A sanitized container name suitable for Azure Blob Storage
    """
    # JWT middleware already validates the user exists, so sanitize and return user_id
    sanitized_name = sanitize_container_name(user_id)
    logging.debug(f"Sanitized user_id from {user_id} to {sanitized_name}")
    return sanitized_name

def sanitize_container_name(unique_code: str) -> str:
    """
    Sanitize unique_code to be a valid Azure Blob Storage container name with guaranteed uniqueness.
    
    Azure container naming rules:
    - 3-63 characters long
    - Only lowercase letters, numbers, and hyphens
    - Must start and end with a letter or number
    - No consecutive hyphens
    
    Strategy: Always append a hash suffix to ensure uniqueness even if different emails
    result in similar sanitized names (e.g., user@domain.com vs user.domain.com)
    
    Args:
        unique_code (str): The original unique identifier (e.g., email address)
        
    Returns:
        str: A valid Azure container name that is guaranteed to be unique
    """
    
    # Create a hash of the original unique_code for guaranteed uniqueness
    hash_obj = hashlib.md5(unique_code.encode('utf-8'))
    hash_hex = hash_obj.hexdigest()[:8]  # Use first 8 chars of hash
    
    # Convert to lowercase and replace invalid chars with hyphens
    sanitized = re.sub(r'[^a-z0-9-]', '-', unique_code.lower())
    
    # Remove consecutive hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    
    # Ensure we have a base name (at least 3 chars before adding hash)
    if len(sanitized) < 3:
        sanitized = 'user'
    
    # Calculate max length for base name (leave room for hash + hyphen)
    max_base_length = 63 - 9  # 63 total - 8 hash chars - 1 hyphen = 54 chars max
    
    # Truncate base name if too long
    if len(sanitized) > max_base_length:
        sanitized = sanitized[:max_base_length]
    
    # Ensure base name ends with alphanumeric (before adding hash)
    if sanitized and not sanitized[-1].isalnum():
        sanitized = sanitized[:-1] + 'r'
    
    # Always append hash for uniqueness: base-hash
    container_name = f"{sanitized}-{hash_hex}"
    
    # Ensure it starts with alphanumeric
    if not container_name[0].isalnum():
        container_name = 'u' + container_name[1:]
    
    # Final safety check
    if len(container_name) > 63:
        # This should not happen with our calculation, but just in case
        container_name = container_name[:63]
    
    # Ensure minimum length
    if len(container_name) < 3:
        container_name = f"usr-{hash_hex}"
    
    return container_name

# OpenAI text-embedding-3-small hard limit is 8192 tokens (~32K chars).
# We truncate at a safe ceiling so long inputs (e.g., users pasting 50K-char log
# blocks as a query) embed the leading portion instead of crashing the API call.
# 28000 chars ≈ 7000 tokens, well under the 8192-token cap.
_MAX_EMBED_INPUT_CHARS = 28000


def _truncate_for_embedding(text: str, label: str = "embed_text") -> str:
    """Truncate input to a safe length for the embedding model. Logs a warning when truncating."""
    if text is None:
        return ""
    if len(text) <= _MAX_EMBED_INPUT_CHARS:
        return text
    logging.warning(
        f"⚠️ [{label}] Input is {len(text)} chars; truncating to {_MAX_EMBED_INPUT_CHARS} "
        f"chars before embedding (OpenAI 8192-token cap)."
    )
    return text[:_MAX_EMBED_INPUT_CHARS]


async def embed_text(text: str, *, task_type: Optional[str] = None) -> List[float]:
    """Create an embedding for a single text string.
    
    Uses OpenAI or llm based on USE_OPENAI_EMBEDDING env var.
    Returns empty list if text is empty (allows graceful handling for audio-only queries).
    """
    # Allow empty text for audio-only queries - return empty embedding
    # This prevents errors when query text is empty but audio transcription will provide the actual query
    if not text or not text.strip():
        logging.warning("embed_text called with empty text - returning empty embedding (likely audio-only query)")
        return []

    text = _truncate_for_embedding(text, label="embed_text")

    if USE_OPENAI_EMBEDDING:
        # Single-embed defaults to a QUERY; instruction-aware models get the prefix.
        resolved_task = task_type or _DEFAULT_QUERY_TASK
        payload = _instruct_query(text) if _is_query_task(resolved_task) else text
        client = await _get_openai_embedding_client()
        try:
            response = await client.embeddings.create(
                model=OPENAI_EMBEDDING_MODEL,
                input=payload,
                dimensions=OPENAI_EMBEDDING_DIMENSION
            )
            return response.data[0].embedding
        except Exception as exc:
            logging.error(f"OpenAI embed_text failed: {exc}")
            raise HTTPException(status_code=502, detail=f"OpenAI embedding request failed: {exc}")
    else:
        # Use embedding
        client = await _get_llm_client()
        resolved_task = task_type or _DEFAULT_QUERY_TASK
        config = _build_embed_config(resolved_task)

        def _embed_sync() -> List[float]:
            kwargs = {"model": _llm_MODEL, "contents": text}
            if config is not None:
                kwargs["config"] = config
            response = client.models.embed_content(**kwargs)
            if not response.embeddings:
                raise ValueError("embedding response did not include embeddings")
            return list(response.embeddings[0].values)

        try:
            embedding = await asyncio.to_thread(_embed_sync)
            return _normalize_embedding(embedding)
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - runtime errors
            logging.error(f"llm embed_text failed: {exc}")
            raise HTTPException(status_code=502, detail=f"embedding request failed: {exc}")


async def embed_texts_batch(texts: List[str], *, task_type: Optional[str] = None) -> List[List[float]]:
    """Create embeddings for multiple texts.
    
    Uses OpenAI or llm based on USE_OPENAI_EMBEDDING env var.
    Automatically splits large batches based on both text count and token limits.
    Filters out empty strings to prevent embedding errors.
    """
    if not texts:
        return []
    
    # Filter out empty strings and track indices
    # This prevents errors when dealing with mixed audio/text queries
    valid_texts = []
    valid_indices = []
    for idx, text in enumerate(texts):
        if text and text.strip():
            valid_texts.append(_truncate_for_embedding(text, label=f"embed_texts_batch[{idx}]"))
            valid_indices.append(idx)
        else:
            logging.warning(f"Skipping empty text at index {idx} in batch embedding")
    
    if not valid_texts:
        logging.warning("All texts in batch are empty - returning empty list")
        return []

    if USE_OPENAI_EMBEDDING:
        # Batch defaults to DOCUMENTS (never instructed); only a caller that
        # explicitly passes a query task_type triggers the query-instruction path.
        resolved_task = task_type or _DEFAULT_DOCUMENT_TASK
        apply_instruction = _is_query_task(resolved_task)
        MAX_TEXTS_PER_BATCH = 2048  # OpenAI allows up to 2048 texts per batch

        async def _embed_openai_batch(batch_texts: List[str]) -> List[List[float]]:
            client = await _get_openai_embedding_client()
            payload = (
                [_instruct_query(t) for t in batch_texts]
                if apply_instruction else batch_texts
            )
            response = await client.embeddings.create(
                model=OPENAI_EMBEDDING_MODEL,
                input=payload,
                dimensions=OPENAI_EMBEDDING_DIMENSION
            )
            return [item.embedding for item in response.data]
        
        # Split into batches if needed (using valid_texts)
        if len(valid_texts) <= MAX_TEXTS_PER_BATCH:
            try:
                return await _embed_openai_batch(valid_texts)
            except Exception as exc:
                logging.error(f"OpenAI embed_texts_batch failed: {exc}")
                raise HTTPException(status_code=502, detail=f"OpenAI batch embedding request failed: {exc}")
        
        # Process in batches
        all_embeddings = []
        for i in range(0, len(valid_texts), MAX_TEXTS_PER_BATCH):
            batch = valid_texts[i:i + MAX_TEXTS_PER_BATCH]
            try:
                batch_embeddings = await _embed_openai_batch(batch)
                all_embeddings.extend(batch_embeddings)
            except Exception as exc:
                logging.error(f"OpenAI batch {i//MAX_TEXTS_PER_BATCH + 1} failed: {exc}")
                raise
        return all_embeddings
    
    # embedding (original implementation, using valid_texts)
    MAX_TEXTS_PER_BATCH = 100
    MAX_TOKENS_PER_BATCH = 16000  # More conservative limit (actual is 20k, but headers add ~20-30% overhead)
    
    def estimate_tokens(text: str) -> int:
        """Conservative estimate accounting for metadata headers: ~3 chars per token"""
        return len(text) // 3 + 10  # Add 10 token buffer for API overhead
    
    def create_token_aware_batches(texts: List[str]) -> List[List[str]]:
        """Split texts into batches respecting both count and token limits"""
        batches = []
        current_batch = []
        current_tokens = 0
        
        for text in texts:
            text_tokens = estimate_tokens(text)
            
            # Check if adding this text would exceed limits
            if (len(current_batch) >= MAX_TEXTS_PER_BATCH or 
                (current_tokens + text_tokens > MAX_TOKENS_PER_BATCH and current_batch)):
                # Save current batch and start new one
                batches.append(current_batch)
                current_batch = [text]
                current_tokens = text_tokens
            else:
                current_batch.append(text)
                current_tokens += text_tokens
        
        # Add final batch if not empty
        if current_batch:
            batches.append(current_batch)
        
        return batches
    
    # Create token-aware batches from valid_texts
    batches = create_token_aware_batches(valid_texts)
    
    # If only one batch, process directly
    if len(batches) == 1:
        client = await _get_llm_client()
        resolved_task = task_type or _DEFAULT_DOCUMENT_TASK
        config = _build_embed_config(resolved_task)

        def _embed_batch_sync() -> List[List[float]]:
            kwargs = {"model": _llm_MODEL, "contents": batches[0]}
            if config is not None:
                kwargs["config"] = config
            response = client.models.embed_content(**kwargs)
            embeddings = []
            for embedding_obj in response.embeddings or []:
                embeddings.append(list(embedding_obj.values))
            if len(embeddings) != len(batches[0]):
                raise ValueError("embedding response count does not match input length")
            return embeddings

        try:
            raw_embeddings = await asyncio.to_thread(_embed_batch_sync)
            return [_normalize_embedding(values) for values in raw_embeddings]
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - runtime errors
            logging.error(f"llm embed_texts_batch failed: {exc}")
            raise HTTPException(status_code=502, detail=f"llm batch embedding request failed: {exc}")
    
    # Process multiple batches
    logging.info(f"?? Token-aware batching: {len(valid_texts)} texts ? {len(batches)} batches (max {MAX_TEXTS_PER_BATCH} texts or {MAX_TOKENS_PER_BATCH} tokens per batch)")
    all_embeddings = []
    
    for batch_idx, batch in enumerate(batches, 1):
        estimated_tokens = sum(estimate_tokens(t) for t in batch)
        logging.info(f"?? Processing batch {batch_idx}/{len(batches)} ({len(batch)} texts, ~{estimated_tokens} tokens)")
        
        try:
            # Recursively call for each batch (will hit single-batch path above)
            batch_embeddings = await embed_texts_batch(batch, task_type=task_type)
            all_embeddings.extend(batch_embeddings)
        except Exception as exc:
            logging.error(f"? Batch {batch_idx}/{len(batches)} failed: {exc}")
            raise
    
    logging.info(f"? Successfully processed all {len(valid_texts)} valid texts in {len(batches)} batches")
    return all_embeddings


def embed_text_sync(text: str, *, task_type: Optional[str] = None) -> List[float]:
    """Synchronous wrapper for embed_text - for use in non-async contexts like LlamaIndex"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're in an async context, create a new thread with a new loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, embed_text(text, task_type=task_type))
                return future.result()
        else:
            # No loop running, safe to use asyncio.run
            return loop.run_until_complete(embed_text(text, task_type=task_type))
    except RuntimeError:
        # No event loop, create one
        return asyncio.run(embed_text(text, task_type=task_type))


def embed_texts_batch_sync(texts: List[str], *, task_type: Optional[str] = None) -> List[List[float]]:
    """Synchronous wrapper for embed_texts_batch - for use in non-async contexts like LlamaIndex"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're in an async context, create a new thread with a new loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, embed_texts_batch(texts, task_type=task_type))
                return future.result()
        else:
            # No loop running, safe to use asyncio.run
            return loop.run_until_complete(embed_texts_batch(texts, task_type=task_type))
    except RuntimeError:
        # No event loop, create one
        return asyncio.run(embed_texts_batch(texts, task_type=task_type))
