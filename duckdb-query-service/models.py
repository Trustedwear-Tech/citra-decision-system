# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Pydantic models for DuckDB Query Service API
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class AnalyticalQueryRequest(BaseModel):
    """Request for analytical SQL query (AGGREGATE / TREND / single-file queries)."""
    query: str = Field(..., description="Natural language query")
    user_id: str
    document_ids: List[str]
    vector_samples: Optional[List[Dict[str, Any]]] = None
    chart_type: Optional[str] = None
    schema_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None
    force_aggregate: bool = False


class PerTableQueryRequest(BaseModel):
    """Request for per-table comparison query (COMPARE queries)."""
    query: str = Field(..., description="Natural language query")
    user_id: str
    document_ids: List[str]
    vector_samples: Optional[List[Dict[str, Any]]] = None
    chart_type: Optional[str] = None
    schema_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None


class EvictRequest(BaseModel):
    """Request to evict tables from the pool."""
    user_id: str
    document_ids: Optional[List[str]] = None  # None = evict all for user


class QueryResponse(BaseModel):
    """Response from analytical/per-table queries."""
    success: bool
    error: Optional[str] = None
    sql: Optional[str] = None
    sql_result: Optional[Dict[str, Any]] = None
    formatted_text: Optional[str] = None
    chart_data: Optional[Dict[str, Any]] = None
    per_table_results: Optional[List[Dict[str, Any]]] = None
    tables_loaded: Optional[int] = None
    cache_hit: bool = False


class TableInfo(BaseModel):
    """Info about a loaded table in the pool."""
    table_name: str
    document_id: str
    filename: str
    row_count: int
    columns: List[str]
    loaded_at: float
    last_accessed: float


class PoolStatusResponse(BaseModel):
    """Response for table pool status."""
    user_id: str
    tables: List[TableInfo]
    total_tables: int
    total_rows: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    pool_tables: int = 0
    pool_users: int = 0
