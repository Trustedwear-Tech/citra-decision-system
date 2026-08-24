# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

import asyncio
import logging
import os
import re
import hashlib
from typing import List, Optional

from openai import AsyncOpenAI
from fastapi import HTTPException
from citra_mongo import get_sync_database

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ----------- Embedding Configuration -----------
# One OpenAI-compatible embedding endpoint, configured entirely by EMBEDDING_*.
# The platform default is `baai/bge-m3` served by OpenRouter at 768 dimensions
# (bge-m3's native output is 1024; we send `dimensions` so the provider returns
# a re-normalised 768-d vector matching the Milvus collection dim).
# Point EMBEDDING_BASE_URL at any compatible server (vLLM, Infinity, ...) to
# self-host the same model.
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "").strip()
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "").strip()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "baai/bge-m3")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "768"))

_DEFAULT_QUERY_TASK = "RETRIEVAL_QUERY"
_DEFAULT_DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"

# ----------- Query-instruction prefix (Qwen3-Embedding et al.) -----------
# Instruction-aware embedders (Qwen3-Embedding) score ~1-5% higher on retrieval
# when the QUERY carries a task instruction; DOCUMENTS are always embedded as-is.
# This is a no-op for bge-m3 and other non-instruction models (they'd treat it
# as noise).
#   EMBEDDING_QUERY_INSTRUCTION unset  -> auto: default instruction iff model looks Qwen-family
#   EMBEDDING_QUERY_INSTRUCTION="off"  -> disabled (bare queries even on Qwen)
#   EMBEDDING_QUERY_INSTRUCTION=<text> -> use <text> as the instruction for every query embed
_DEFAULT_QUERY_INSTRUCTION = "Given a search query, retrieve relevant passages that answer the query"
_EMBEDDING_QUERY_INSTRUCTION_RAW = os.getenv("EMBEDDING_QUERY_INSTRUCTION", "").strip()

_embedding_client: Optional[AsyncOpenAI] = None
_embedding_lock: Optional[asyncio.Lock] = None


async def _get_embedding_client() -> AsyncOpenAI:
    """Get or create the embedding client for the configured EMBEDDING_* endpoint."""
    global _embedding_client
    global _embedding_lock

    if _embedding_client is not None:
        return _embedding_client

    if not EMBEDDING_API_KEY:
        raise HTTPException(status_code=500, detail="EMBEDDING_API_KEY environment variable is not set")
    if not EMBEDDING_BASE_URL:
        raise HTTPException(status_code=500, detail="EMBEDDING_BASE_URL environment variable is not set")

    if _embedding_lock is None:
        _embedding_lock = asyncio.Lock()

    async with _embedding_lock:
        if _embedding_client is None:
            # Any OpenAI-compatible embedding endpoint: OpenRouter (default),
            # a local vLLM/Infinity server, etc.
            _embedding_client = AsyncOpenAI(
                api_key=EMBEDDING_API_KEY,
                base_url=EMBEDDING_BASE_URL,
                max_retries=5,
            )
            logging.info(
                f"Embedding client initialized ({EMBEDDING_MODEL}, {EMBEDDING_DIMENSION}D) "
                f"via {EMBEDDING_BASE_URL}"
            )

    return _embedding_client


def _active_query_instruction() -> Optional[str]:
    """Resolve the instruction to prepend to QUERY embeddings, or None for a no-op.

    Documents are never instructed. Non-instruction models (bge-m3 and friends)
    resolve to None (auto-branch), so this stays a strict no-op unless the model
    is Qwen-family or an explicit instruction is configured via env.
    """
    val = _EMBEDDING_QUERY_INSTRUCTION_RAW
    if val:
        if val.lower() in ("off", "none", "false", "0"):
            return None
        return val
    if "qwen" in (EMBEDDING_MODEL or "").lower():
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

# bge-m3's context window is 8192 tokens (same ceiling text-embedding-3-small had).
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
    
    Returns empty list if text is empty (allows graceful handling for audio-only queries).
    """
    # Allow empty text for audio-only queries - return empty embedding
    # This prevents errors when query text is empty but audio transcription will provide the actual query
    if not text or not text.strip():
        logging.warning("embed_text called with empty text - returning empty embedding (likely audio-only query)")
        return []

    text = _truncate_for_embedding(text, label="embed_text")

    # Single-embed defaults to a QUERY; instruction-aware models get the prefix.
    resolved_task = task_type or _DEFAULT_QUERY_TASK
    payload = _instruct_query(text) if _is_query_task(resolved_task) else text
    client = await _get_embedding_client()
    try:
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=payload,
            dimensions=EMBEDDING_DIMENSION
        )
        return response.data[0].embedding
    except Exception as exc:
        logging.error(f"embed_text failed ({EMBEDDING_MODEL}): {exc}")
        raise HTTPException(status_code=502, detail=f"embedding request failed: {exc}")


async def embed_texts_batch(texts: List[str], *, task_type: Optional[str] = None) -> List[List[float]]:
    """Create embeddings for multiple texts.
    
    Automatically splits large batches based on text count.
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

    # Batch defaults to DOCUMENTS (never instructed); only a caller that
    # explicitly passes a query task_type triggers the query-instruction path.
    resolved_task = task_type or _DEFAULT_DOCUMENT_TASK
    apply_instruction = _is_query_task(resolved_task)
    MAX_TEXTS_PER_BATCH = 2048  # provider cap for a single /embeddings call

    async def _embed_batch(batch_texts: List[str]) -> List[List[float]]:
        client = await _get_embedding_client()
        payload = (
            [_instruct_query(t) for t in batch_texts]
            if apply_instruction else batch_texts
        )
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=payload,
            dimensions=EMBEDDING_DIMENSION
        )
        return [item.embedding for item in response.data]

    # Split into batches if needed (using valid_texts)
    if len(valid_texts) <= MAX_TEXTS_PER_BATCH:
        try:
            return await _embed_batch(valid_texts)
        except Exception as exc:
            logging.error(f"embed_texts_batch failed ({EMBEDDING_MODEL}): {exc}")
            raise HTTPException(status_code=502, detail=f"batch embedding request failed: {exc}")

    # Process in batches
    all_embeddings = []
    for i in range(0, len(valid_texts), MAX_TEXTS_PER_BATCH):
        batch = valid_texts[i:i + MAX_TEXTS_PER_BATCH]
        try:
            batch_embeddings = await _embed_batch(batch)
            all_embeddings.extend(batch_embeddings)
        except Exception as exc:
            logging.error(f"embedding batch {i//MAX_TEXTS_PER_BATCH + 1} failed: {exc}")
            raise
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
