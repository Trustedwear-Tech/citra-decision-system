# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Pydantic models for the Reranker Service API.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class ChunkInput(BaseModel):
    """A single chunk to be reranked."""
    id: str
    text: str = Field(..., max_length=100_000)
    score: float = 0.0
    metadata: Optional[dict] = None


class RerankRequest(BaseModel):
    """Request body for the /rerank endpoint.

    Caps bound worst-case CPU time per request (a single oversized payload
    must not pin a worker indefinitely). They are far above every current
    caller: Citra-Service over-fetches 2x top_k, the MCP tool caps at 200
    chunks, smart-app at 100. Oversized requests fail loud with a 422.
    """
    query: str = Field(..., max_length=10_000)
    chunks: List[ChunkInput] = Field(..., max_length=1000)
    top_k: int = Field(10, ge=1, le=1000)


class ChunkOutput(BaseModel):
    """A single reranked chunk in the response."""
    id: str
    text: str
    score: float
    original_score: float
    reranker_raw_score: float
    metadata: Optional[dict] = None


class RerankResponse(BaseModel):
    """Response body for the /rerank endpoint."""
    reranked_chunks: List[ChunkOutput]
    model: str
    device: str
    elapsed_ms: float


class HealthResponse(BaseModel):
    """Response body for the /health endpoint."""
    status: str
    model: str
    device: str
    model_loaded: bool
