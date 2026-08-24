# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Discovery Service — Data Models
================================
Pydantic request/response models and MongoDB document structure.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Visibility / access-control block embedded in each tool registration
# ---------------------------------------------------------------------------

class ToolVisibility(BaseModel):
    """Controls which identities may discover and query a registered tool."""

    # Roles that are allowed to discover this tool within their own org/dept scope.
    # Defaults to all authenticated users within the owning org.
    roles_allowed: List[str] = Field(
        default=["user", "dept_admin", "org_admin", "super_admin"],
        description="JWT roles that may access this tool",
    )

    # Additional org_ids that can discover this tool cross-org (e.g. CMO of a
    # different state body that has been granted read access).
    cross_org_ids: List[str] = Field(
        default=[],
        description="External org_ids explicitly allowed cross-org discovery",
    )

    # If True, any authenticated user within the same org_id can discover this
    # tool regardless of dept_ids (org-wide shared dataset).
    public_within_org: bool = Field(
        default=False,
        description="Visible to all authenticated users within the owning org",
    )


# ---------------------------------------------------------------------------
# Registration request — sent by a dept MCP on startup
# ---------------------------------------------------------------------------

class ToolRegistrationRequest(BaseModel):
    """Body for POST /tools/register."""

    # Stable identifier chosen by the registrant.  Must be unique per deployment.
    # Convention: "{org_id}-{dept_id}-{source_slug}"  e.g. "bihar-agri-mandi"
    tool_id: str = Field(..., description="Unique tool identifier (chosen by registrant)")

    name: str = Field(..., description="Human-readable name shown in LLM tool selector")
    description: str = Field(
        ...,
        description=(
            "Rich description of what data this source contains. "
            "Used by the LLM to decide whether to call this tool for a given query. "
            "Be specific: mention data types, entities, time range, geography."
        ),
    )

    # REST endpoint on the dept MCP server that accepts RAG queries.
    # Shape:  POST {query_endpoint}  body={query, source_id, max_results}
    #         Response: {results: [{text, score, source, metadata}]}
    query_endpoint: str = Field(..., description="URL of the /query endpoint on the dept MCP")

    # Health check endpoint.
    health_endpoint: Optional[str] = Field(
        default=None,
        description="URL of the /health endpoint on the dept MCP (optional)",
    )

    # Opaque namespace key used by the dept MCP to scope Milvus queries.
    source_id: str = Field(
        ...,
        description="Stable namespace key (maps to Milvus collection prefix inside dept MCP)",
    )

    # Tenancy — which org/dept owns this tool.
    org_ids: List[str] = Field(
        ...,
        description="Org IDs that own this data source (usually just one)",
    )
    dept_ids: List[str] = Field(
        default=[],
        description=(
            "Dept/district IDs this source belongs to. "
            "Empty means org-wide (visible to org_admin and super_admin). "
            "For state deployments, districts are also dept_ids."
        ),
    )

    # Access control
    visibility: ToolVisibility = Field(default_factory=ToolVisibility)

    # Auth — plain-text key used only at registration/deregistration time.
    # Stored as SHA-256 hash; never returned in API responses.
    api_key: str = Field(..., description="Secret used to authenticate deregister calls")

    # Optional metadata for filtering / display
    tags: List[str] = Field(
        default=[],
        description="Topic tags e.g. ['agriculture', 'subsidy', 'mandi']",
    )
    data_types: List[str] = Field(
        default=["documents"],
        description="Types of data: 'structured', 'documents', 'realtime'",
    )

    # Routing & invocation hints used by Citra enterprise_search.py.
    source_type: Optional[str] = Field(
        default=None,
        description=(
            "Source kind: semantic | structured | mongodb | rest_api. "
            "Citra-Service uses this to decide whether to inject taxonomy "
            "filters (semantic only) and which timeout tier to apply."
        ),
    )
    taxonomy: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional document-type taxonomy for semantic sources. Shape: "
            "{doc_types: [{id, label?, synonyms?, examples?}], "
            "classification_levels?: [str]}. Surfaced to the Citra routing "
            "LLM so it can populate the doc_types parameter on /query calls."
        ),
    )
    query_timeout_seconds: Optional[int] = Field(
        default=None,
        description="Per-source timeout override applied by enterprise_search.py.",
    )
    rag_collection: Optional[str] = Field(
        default=None,
        description=(
            "For semantic (RAG) sources only: the Milvus collection the platform "
            "reader fetches directly. Semantic queries are answered in-process "
            "(pure disconnect) — consumers read this collection and NEVER call "
            "query_endpoint. Null for structured sources."
        ),
    )
    supports_history: bool = Field(
        default=False,
        description=(
            "Capability flag from the MCP registration: the source can serve "
            "decision-history reads (citra-app-builder reads this to decide "
            "whether to author a 'Refresh from History' workflow). Was silently "
            "dropped before this field existed (pydantic extra='ignore')."
        ),
    )


# ---------------------------------------------------------------------------
# Heartbeat request
# ---------------------------------------------------------------------------

class HeartbeatRequest(BaseModel):
    """Body for PUT /tools/{tool_id}/heartbeat."""
    api_key: str


# ---------------------------------------------------------------------------
# Deregister request
# ---------------------------------------------------------------------------

class DeregisterRequest(BaseModel):
    """Body for DELETE /tools/{tool_id}."""
    api_key: str


class ReconcileRequest(BaseModel):
    """Body for POST /tools/reconcile — sent by a dept MCP after register_all.

    Deactivates this registrant's STALE tools: any active tool IN THIS MCP's
    OWN SCOPE (org_id + dept_ids) whose api_key_hash matches ``api_key`` and
    whose tool_id is NOT in ``active_tool_ids``. Fixes the lingering-source
    hazard: a source renamed or removed from SOURCES_FILE previously stayed
    active in the registry forever (deregister-on-shutdown is off by design,
    there is no heartbeat reaper), silently misleading the builder.

    ``org_id`` + ``dept_ids`` are REQUIRED and are the ownership boundary. The
    first cut keyed ownership on ``api_key_hash`` alone, on the assumption of
    one key per MCP. That is false: smart-app-service holds a single fleet-wide
    MCP_SERVICE_API_KEY, so every dept MCP presents the SAME key — and each
    boot deactivated every OTHER dept's tools (they all matched the $nin),
    leaving them dead until their own MCP restarted, which then killed the
    first's. No heartbeat, no reaper, so it never self-healed. Scoping to the
    caller's own org+dept makes the boundary what it always meant to be: an MCP
    may retire only the sources it actually serves."""
    api_key: str
    active_tool_ids: List[str]
    org_id: str
    dept_ids: List[str]


# ---------------------------------------------------------------------------
# Tool definition returned to Citra enterprise_search.py
# ---------------------------------------------------------------------------

class ToolDefinition(BaseModel):
    """Shape returned by GET /tools/available — consumed by enterprise_search.py."""

    name: str
    description: str
    query_endpoint: str
    source_id: str
    org_ids: List[str]
    dept_ids: List[str]
    tags: List[str]
    data_types: List[str]
    source_type: Optional[str] = None
    taxonomy: Optional[Dict[str, Any]] = None
    query_timeout_seconds: Optional[int] = None
    rag_collection: Optional[str] = None
    supports_history: bool = False
    # Surfaced so the data-catalogue crawler (which no longer reads the retired
    # dept_sources registry) can stamp RBAC scope on each catalogue entry.
    public_within_org: bool = False


class RankedToolDefinition(ToolDefinition):
    """Shape returned by GET /tools/search — same as ToolDefinition plus
    a ``relevance_score`` in [0, 1] (cosine similarity to query embedding,
    or substring-fallback score when embeddings are unavailable)."""

    relevance_score: float = Field(
        ...,
        description="Cosine similarity to query embedding (0..1); higher is more relevant.",
    )


# ---------------------------------------------------------------------------
# Internal MongoDB document (stored in `tools` collection)
# ---------------------------------------------------------------------------

class ToolDocument(BaseModel):
    """Internal storage model — includes api_key_hash and health state."""

    tool_id: str
    name: str
    description: str
    query_endpoint: str
    health_endpoint: Optional[str]
    source_id: str
    org_ids: List[str]
    dept_ids: List[str]
    visibility: Dict[str, Any]  # stored as plain dict
    api_key_hash: str           # SHA-256 of the registrant's api_key
    tags: List[str]
    data_types: List[str]
    source_type: Optional[str] = None
    taxonomy: Optional[Dict[str, Any]] = None
    query_timeout_seconds: Optional[int] = None
    rag_collection: Optional[str] = None
    registered_at: datetime
    last_heartbeat: datetime
    active: bool = True


# ---------------------------------------------------------------------------
# Generic API responses
# ---------------------------------------------------------------------------

class SuccessResponse(BaseModel):
    success: bool = True
    message: str
    tool_id: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "discovery-service"
    tool_count: int = 0
