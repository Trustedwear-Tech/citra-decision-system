# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Department SOP Library — dept-native document folders (slice 1: ownership).

The SOP Library is the enterprise variant of the personal vault: a folder of
reference documents (SOPs, policies, manuals) that belongs to a DEPARTMENT, not
a user. It reuses the same ``folders`` collection and the same cascade-delete
machinery as personal vaults (folder_management.py), but with a different
ownership + access model:

  * owner_type = "dept" (OwnerType.DEPT), keyed by (org_id, dept_id) — NOT a
    Personal SA. The uploader is recorded for audit only; a user leaving never
    orphans or removes a dept SOP.
  * WRITE (create / delete / manage) — dept_admin (own dept) / org_admin (own
    org) / super_admin ONLY. Regular users never write to the library.
  * READ — every member of that department (public *within* the dept). The
    dept filter is the visibility boundary; there are no per-document ACLs.

Slices 2-3 add ingestion routing to a per-dept Milvus collection + registration
as a ``kind=semantic`` dept_source, and the UI panel. This module is slice 1:
the dept-owned folder lifecycle + the authorization gate.

The authorization logic lives in two PURE functions (``can_manage_dept_library``
/ ``can_read_dept_library``) so it is unit-testable without the web/Mongo stack;
the routes are thin wrappers that read ``request.state`` and call them.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile, status,
)
from pydantic import BaseModel, Field

from citra_auth import get_secure_user_id
from citra_auth.constants import OwnerType, Roles

from CRUD_utils import get_mongo_client, MONGODB_DATABASE

logger = logging.getLogger(__name__)

router = APIRouter()

FOLDER_KIND = "sop_library"
_MANAGER_ROLES = frozenset({Roles.DEPT_ADMIN, Roles.ORG_ADMIN, Roles.SUPER_ADMIN})

# The MCP keys its source registry by source_id ALONE (router.py:116
# `{d["source_id"]: d ...}`), so a dept library's source_id must be GLOBALLY
# unique across every dept an MCP serves — a fixed "sop_library" would make two
# departments' libraries overwrite each other. Hence a dept-scoped source_id.
# The MCP derives the Milvus collection as "<prefix>_<dept>_<source_id>"
# (source-mcp-template/config.py collection_name_semantic); ingestion (slice 2)
# must write to the SAME derived name, so both are computed here identically.
MCP_COLLECTION_PREFIX = "mcp"
SOP_SOURCE_PREFIX = "sop_library"


def _safe_segment(name: str) -> str:
    """Sanitize an id into a Milvus collection-name segment — byte-identical to
    source-mcp-template/config.py ``_safe`` so a dept library's ingest
    collection matches the name the MCP derives at query time (the acme-power
    hyphen gotcha lives here)."""
    return re.sub(r"[^a-zA-Z0-9]", "_", name or "")[:40].lower()


def dept_library_source_id(dept_id: Optional[str]) -> str:
    """Globally-unique source_id for a dept's SOP library:
    ``sop_library_<dept>``. Unique per dept so the MCP's source_id-keyed
    registry never collides across departments."""
    return f"{SOP_SOURCE_PREFIX}_{_safe_segment(dept_id) if dept_id else 'default'}"


def dept_library_collection_name(dept_id: Optional[str] = None, *, prefix: str = MCP_COLLECTION_PREFIX) -> str:
    """The Milvus collection a dept's SOP library ingests into + is queried from.

    BREAKING CHANGE: ALL dept libraries now share ONE collection (see
    ``dept_library_store.shared_dept_collection``) instead of one collection per
    dept — so adding departments never multiplies collections. Isolation is by the
    scalar-indexed ``org_id`` + ``dept`` + ``source_id`` fields at query time, not
    by a per-dept collection. ``dept_id`` is ignored (kept for call-site compat)."""
    from dept_library_store import shared_dept_collection
    return shared_dept_collection()


def dept_library_tool_id(org_id: str, dept_id: str) -> str:
    """Stable discovery ``tool_id`` for a dept library — one per (org, dept), so a
    re-save upserts in place and a delete deregisters exactly that library."""
    return f"deptlib_{_safe_segment(org_id)}_{_safe_segment(dept_id)}"


def build_dept_library_registration(
    *,
    org_id: str,
    dept_id: str,
    name: str,
    description: Optional[str] = None,
    api_key: str = "",
    public_within_org: bool = False,
    source_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The DISCOVERY-registry payload advertising a dept SOP library as a
    ``source_type="semantic"`` source. Platform-native libraries publish here
    DIRECTLY (no MCP), so chat + ``/semantic/search`` discover + scope them the
    SAME way as MCP-advertised sources — the retired central ``dept_sources``
    collection is no longer used. Pure — the caller POSTs it via
    ``enterprise_mcp_client.register_source``.

    ``query_endpoint`` is EMPTY: a semantic source is answered by the platform
    RAG reader, not an MCP ``/query`` (the RAG short-circuit). ``rag_collection``
    is the SHARED dept-library collection; ``org_ids`` + ``dept_ids`` scope the
    read. Readable by every dept role (reads = dept-public); the dept scope is
    the boundary. ``api_key`` is the stable secret used to later deregister."""
    return {
        "tool_id": dept_library_tool_id(org_id, dept_id),
        "name": name,
        "description": description or f"Department SOP library for {dept_id}.",
        "query_endpoint": "",                 # semantic ⇒ answered by the reader
        "source_id": source_id or dept_library_source_id(dept_id),
        "org_ids": [org_id],
        "dept_ids": [dept_id],
        "visibility": {
            "roles_allowed": [
                Roles.USER, Roles.DEPT_ADMIN, Roles.ORG_ADMIN, Roles.SUPER_ADMIN,
            ],
            "cross_org_ids": [],
            # Org-wide when public; otherwise dept-scoped. Public makes every user
            # in the org able to query the library (the reader's can_query_semantic).
            "public_within_org": bool(public_within_org),
        },
        "api_key": api_key,                   # stable secret for later deregister
        "tags": ["sop", "rag", FOLDER_KIND],
        "data_types": ["semantic"],
        "source_type": "semantic",
        "rag_collection": dept_library_collection_name(dept_id),
    }


# ---------------------------------------------------------------------------
# Authorization — PURE, unit-testable (no request/Mongo). See module docstring.
# ---------------------------------------------------------------------------


def can_manage_dept_library(
    *,
    roles: List[str],
    user_org_id: Optional[str],
    user_dept_ids: List[str],
    target_org: Optional[str],
    target_dept: Optional[str],
) -> bool:
    """May this caller CREATE / DELETE / manage a dept library folder?

    super_admin      → always;
    org_admin        → within their own org;
    dept_admin       → within their own org AND for a dept they administer.
    Everyone else    → no. Cross-org is never permitted below super_admin
    (dept_ids are non-unique human strings reused across tenants, so a dept
    match alone would leak cross-tenant — same-org is always required)."""
    roles = set(roles or [])
    if Roles.SUPER_ADMIN in roles:
        return True
    if not target_org or not target_dept:
        return False
    same_org = bool(user_org_id) and user_org_id == target_org
    if Roles.ORG_ADMIN in roles and same_org:
        return True
    if (
        Roles.DEPT_ADMIN in roles
        and same_org
        and target_dept in set(user_dept_ids or [])
    ):
        return True
    return False


def can_read_dept_library(
    *,
    roles: List[str],
    user_org_id: Optional[str],
    user_dept_ids: List[str],
    target_org: Optional[str],
    target_dept: Optional[str],
    target_public: bool = False,
) -> bool:
    """May this caller READ a dept library? Managers can (edit ⇒ read); plus
    ANY member of that department (public-within-dept); plus — when the library
    is ``public_within_org`` — ANY user in the same org. Same-org required in all
    cases (dept ids are non-unique across tenants)."""
    if can_manage_dept_library(
        roles=roles, user_org_id=user_org_id, user_dept_ids=user_dept_ids,
        target_org=target_org, target_dept=target_dept,
    ):
        return True
    if not target_org:
        return False
    same_org = bool(user_org_id) and user_org_id == target_org
    if not same_org:
        return False
    if target_public:
        return True
    return bool(target_dept) and target_dept in set(user_dept_ids or [])


def readable_dept_ids(
    *, roles: List[str], user_org_id: Optional[str], user_dept_ids: List[str],
    org_dept_ids: List[str],
) -> List[str]:
    """Which depts' libraries this caller may LIST. org_admin/super_admin see
    every dept in the org (``org_dept_ids``); everyone else sees only the depts
    they belong to. Pure — the caller supplies the org's dept universe."""
    roles = set(roles or [])
    if Roles.SUPER_ADMIN in roles or Roles.ORG_ADMIN in roles:
        return list(org_dept_ids or [])
    return list(user_dept_ids or [])


# ---------------------------------------------------------------------------
# request.state readers (thin)
# ---------------------------------------------------------------------------


def _roles(request: Request) -> List[str]:
    return list(getattr(request.state, "roles", []) or [])


def _org_id(request: Request) -> Optional[str]:
    return getattr(request.state, "org_id", None) or None


def _dept_ids(request: Request) -> List[str]:
    return list(getattr(request.state, "dept_ids", []) or [])


def _folders():
    return get_mongo_client()[MONGODB_DATABASE].folders


def ensure_dept_collection(collection_name: str) -> None:
    """Create the dept library's Milvus collection (if absent) with the SAME
    hybrid dense+BM25 schema as the deployment's default collection, by reusing
    the authoritative builder in ``scripts/setup_milvus_schema``. Idempotent
    (``create_collection`` no-ops when the collection exists).

    Milvus‑side: cannot be unit‑verified without a live Milvus — needs a
    live‑stack smoke test. Fails LOUD (raises) so an upload never silently
    lands nowhere."""
    from config.milvus_config import (
        get_dense_vector_dim,
        get_milvus_api_key,
        get_milvus_uri,
        is_hybrid_search_enabled,
    )
    from scripts.setup_milvus_schema import connect_milvus, create_collection

    config = {
        "collection_name": collection_name,
        "uri": get_milvus_uri(),
        "token": get_milvus_api_key(),
        "dense_dim": get_dense_vector_dim(),
        "hybrid_enabled": is_hybrid_search_enabled(),
    }
    connect_milvus(config)                       # idempotent connect (alias "default")
    create_collection(config, force_recreate=False)  # idempotent create-if-absent


def _esc_id(v: str) -> str:
    """Escape an id for a Milvus string-equality filter (ids are uuids/filenames
    — defensive against a quote sneaking in)."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def delete_dept_vectors(collection_name: str, folder_id: Optional[str] = None,
                        *, document_id: Optional[str] = None) -> int:
    """Delete a dept library's vectors from ITS OWN collection — by whole
    ``folder_id`` (library delete) or a single ``document_id`` (one-doc delete).
    This is where the docs actually live — the personal‑vault cascade deletes
    from the DEFAULT collection and scopes by a single uploader's user_id, so it
    would leave dept vectors orphaned and the dept‑MCP would keep serving
    'deleted' SOPs. Best‑effort + loud; returns the delete count (0 if the
    collection is absent / nothing to scope). Milvus I/O — live‑stack smoke."""
    if document_id:
        expr = f'document_id == "{_esc_id(document_id)}"'
        scope = f"document {document_id}"
    elif folder_id:
        expr = f'folder_id == "{_esc_id(folder_id)}"'
        scope = f"folder {folder_id}"
    else:
        return 0
    try:
        from config.milvus_config import get_milvus_client
        client = get_milvus_client()
        if not client.has_collection(collection_name):
            return 0
        res = client.delete(collection_name=collection_name, filter=expr)
        n = int((res or {}).get("delete_count", 0) or 0)
        logger.info("[dept-library] deleted %d vector(s) from %s for %s",
                    n, collection_name, scope)
        return n
    except Exception:  # noqa: BLE001 — loud; the record is still removed
        logger.exception("[dept-library] failed deleting vectors from %s for %s "
                         "— vectors may be orphaned", collection_name, scope)
        return 0


# ---------------------------------------------------------------------------
# Original-file storage (folder-panel parity): dept docs are first-class `files`
# records → View / Download / open-in-reader resolve them by document_id exactly
# like a personal doc, and delete/replace clean up S3 + the record too.
# ---------------------------------------------------------------------------


def _derive_s3_key(s3_url: Optional[str]) -> Optional[str]:
    """The S3 object key from a stored ``s3_url`` — same derivation the personal
    download/proxy endpoints use."""
    if not s3_url:
        return None
    if ".amazonaws.com/" in s3_url:
        return s3_url.split(".amazonaws.com/", 1)[-1]
    if "s3://" in s3_url:
        return s3_url.split("s3://", 1)[-1].split("/", 1)[-1]
    return s3_url


def _store_dept_original(*, content: bytes, content_type: Optional[str],
                         org_id: Optional[str], dept_id: Optional[str], folder_id: str,
                         document_id: str, filename: str, file_ext: str,
                         user_id: str, doc_type: str = "") -> str:
    """Store the ORIGINAL file: S3 object + a dept-scoped ``files`` record keyed by
    ``document_id`` (so the existing files-based reader/download resolve it), marked
    ``owner_type=dept`` + ``department`` so dept members — not only the uploader —
    are authorized. Returns the s3_url."""
    from datetime import datetime as _dt2
    from bucket import upload_file
    key = (f"dept/{_safe_segment(org_id)}/{_safe_segment(dept_id)}/sop-library/"
           f"{folder_id}/documents/{document_id}_{filename}")
    s3_url = upload_file(content, key, content_type or "application/octet-stream")
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    _now = _dt2.utcnow()
    get_mongo_client()[MONGODB_DATABASE]["files"].insert_one({
        "_id": document_id, "user_id": user_id, "folder_id": folder_id,
        "owner_type": "dept", "org_id": org_id, "department": dept_id,
        "s3_url": s3_url, "filename": filename, "filename_stem": stem,
        "topic_or_filename": stem, "file_type_category": "document",
        "file_extension": file_ext, "content_type": content_type,
        "file_size_bytes": len(content), "storage_location": "s3",
        "upload_source": "dept_sop_library",
        # Mirror the taxonomy category onto the file record (the vectors carry it
        # too) so the library UI can show/relabel it without re-reading Milvus.
        "doc_type": doc_type or "",
        "created_at": _now, "updated_at": _now,
    })
    return s3_url


def _delete_dept_originals(document_ids: List[str]) -> int:
    """Hard-delete the S3 object(s) + ``files`` record(s) for the given dept doc
    ids (delete-not-archive). Best-effort on S3; always removes the records."""
    ids = [d for d in (document_ids or []) if d]
    if not ids:
        return 0
    db = get_mongo_client()[MONGODB_DATABASE]
    from bucket import delete_file as _s3_delete
    for r in db["files"].find({"_id": {"$in": ids}}, {"s3_url": 1}):
        key = _derive_s3_key(r.get("s3_url"))
        if key:
            try:
                _s3_delete(key)
            except Exception:  # noqa: BLE001 — loud; the record is still removed
                logger.exception("[dept-library] S3 delete failed for doc %s", r.get("_id"))
    return db["files"].delete_many({"_id": {"$in": ids}}).deleted_count


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DeptLibraryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    color: Optional[str] = Field("#6b7280")
    # Public-within-org: any user in the org may READ/query this library (not
    # just members of the owning dept). Management stays curator-only.
    public: Optional[bool] = Field(False)
    # Advanced: pin this library to an EXISTING source_id (e.g. one an MCP already
    # advertises to discovery) instead of the derived ``sop_library_<dept>``. Used
    # to make a directly-seeded RAG corpus manageable in the panel without changing
    # its source_id. When omitted, the derived id is used.
    source_id: Optional[str] = Field(None, max_length=128)
    # When False, DON'T register this library as a discovery source — the source
    # is already advertised elsewhere (e.g. by an MCP's sources.json), so a second
    # registration would duplicate it. Default True (normal libraries self-register).
    register_discovery: Optional[bool] = Field(True)


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": doc.get("_id"),
        "name": doc.get("name"),
        "description": doc.get("description") or "",
        "color": doc.get("color") or "#6b7280",
        "org_id": doc.get("org_id"),
        "dept_id": doc.get("dept_id"),
        "milvus_collection": doc.get("milvus_collection"),
        "source_id": doc.get("source_id"),
        "public_within_org": bool(doc.get("public_within_org")),
        "created_by": doc.get("created_by"),
        "created_at": (doc.get("created_at").isoformat()
                       if isinstance(doc.get("created_at"), datetime) else None),
        "updated_at": (doc.get("updated_at").isoformat()
                       if isinstance(doc.get("updated_at"), datetime) else None),
    }


def shape_document_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure: shape a `document_chunked` group aggregate (one row per document_id
    with filename/chunks/created_at) into the doc-list response the panel shows."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        created = r.get("created_at")
        out.append({
            "document_id": r.get("_id"),
            "filename": r.get("filename") or r.get("_id"),
            "chunks": int(r.get("chunks") or 0),
            "created_at": created.isoformat() if isinstance(created, datetime) else created,
            "uploaded_by": r.get("uploaded_by"),
        })
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/api/dept-library/folders")
async def create_dept_library(
    request: Request,
    dept_id: str = Query(..., description="Department that owns this library"),
    body: DeptLibraryCreate = Body(...),
):
    """Create a dept-owned SOP library folder. Manager-gated (dept_admin of
    this dept / org_admin / super_admin)."""
    user_id = get_secure_user_id(request)
    org_id = _org_id(request)
    if not org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="caller has no org_id; cannot own a dept library")
    if not can_manage_dept_library(
        roles=_roles(request), user_org_id=org_id, user_dept_ids=_dept_ids(request),
        target_org=org_id, target_dept=dept_id,
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="dept_admin (of this dept) / org_admin / super_admin required "
                   "to manage a department SOP library")

    folders = _folders()
    if folders.find_one({
        "owner_type": OwnerType.DEPT, "folder_kind": FOLDER_KIND,
        "org_id": org_id, "dept_id": dept_id,
        "name": {"$regex": f"^{body.name}$", "$options": "i"},
        "deleted": {"$ne": True},
    }):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"library '{body.name}' already exists in this department")

    folder_id = str(uuid.uuid4())
    now = datetime.now()
    # Effective source_id: an explicit override (to align with an already-advertised
    # source, e.g. an MCP-seeded corpus) or the derived per-dept id.
    effective_source_id = (body.source_id or "").strip() or dept_library_source_id(dept_id)
    is_public = bool(body.public)
    # Skip registration when the source is already advertised elsewhere (a second
    # registration would duplicate it in discovery).
    do_register = bool(body.register_discovery)
    folders.insert_one({
        "_id": folder_id,
        "owner_type": OwnerType.DEPT,
        "folder_kind": FOLDER_KIND,
        "org_id": org_id,
        "dept_id": dept_id,
        # The Milvus collection this library's docs ingest into — derived to
        # MATCH what the MCP queries (slice 2 reads this to route ingestion).
        # Stamped on the record so the target is explicit, not re-derived ad hoc.
        "milvus_collection": dept_library_collection_name(dept_id),
        # The source_id every chunk of this library is stamped with (upload reads
        # this so an override flows through to the vectors + discovery scope).
        "source_id": effective_source_id,
        # Org-wide readability (panel + RAG). Management stays curator-only.
        "public_within_org": is_public,
        "created_by": user_id,          # audit only — never an ownership key
        "name": body.name,
        "description": body.description or "",
        "color": body.color or "#6b7280",
        "created_at": now,
        "updated_at": now,
        "deleted": False,
    })
    # Advertise the library as a semantic source in the DISCOVERY registry so
    # chat + /semantic/search find + scope it (no MCP, no dept_sources).
    # Idempotent per (org, dept): one tool_id per dept library, upserted in place.
    # Fail loud but don't fail the create — the folder exists; a missing
    # registration is recoverable by re-save and is logged (discovery just won't
    # see the library until it lands). Skipped entirely when register_discovery is
    # False (the source is already advertised, e.g. by an MCP's sources.json).
    registered = True
    reg_error: Optional[str] = None
    if do_register:
        try:
            from services.enterprise_mcp_client import register_source, service_api_key
            reg = build_dept_library_registration(
                org_id=org_id, dept_id=dept_id, name=body.name,
                description=body.description, api_key=service_api_key(),
                public_within_org=is_public, source_id=effective_source_id)
            await register_source(reg, service_api_key())
        except Exception as e:  # noqa: BLE001 — recoverable, but the caller MUST be told
            registered = False
            reg_error = str(e)
            logger.exception("[dept-library] discovery registration FAILED for "
                             "org=%s dept=%s — library created but not discoverable "
                             "until re-registered", org_id, dept_id)
    # Record whether THIS library owns a discovery registration, so delete only
    # deregisters sources it created (never an externally-advertised one).
    folders.update_one({"_id": folder_id},
                       {"$set": {"discovery_registered": do_register and registered}})

    logger.info("[dept-library] created '%s' org=%s dept=%s by %s (registered=%s)",
                body.name, org_id, dept_id, user_id, registered)
    out = _serialize(folders.find_one({"_id": folder_id}))
    # Surface the half-created state to the caller — a registration failure is
    # NOT silent (RULE #1): the library exists but is not yet searchable by the
    # dept-MCP, so the admin must know to retry rather than assume success.
    out["registered"] = registered
    if not registered:
        out["warning"] = (
            "Library created but NOT yet searchable — registering it with the "
            "discovery registry failed. Re-save the library to retry. "
            f"({reg_error})")
    return out


@router.get("/api/dept-library/folders")
async def list_dept_libraries(
    request: Request,
    dept_id: Optional[str] = Query(None, description="Filter to one department"),
):
    """List dept SOP libraries the caller may read. A regular dept member sees
    their own dept's libraries; org/super admins see every dept in the org."""
    get_secure_user_id(request)  # 401 if unauthenticated
    org_id = _org_id(request)
    if not org_id:
        return {"folders": []}

    folders = _folders()
    base = {
        "owner_type": OwnerType.DEPT, "folder_kind": FOLDER_KIND,
        "org_id": org_id, "deleted": {"$ne": True},
    }
    if dept_id:
        # A public library of the requested dept is readable by anyone in the org;
        # a private one only by that dept's members/admins.
        if not can_read_dept_library(
            roles=_roles(request), user_org_id=org_id,
            user_dept_ids=_dept_ids(request), target_org=org_id, target_dept=dept_id,
            target_public=bool(folders.find_one(
                {**base, "dept_id": dept_id, "public_within_org": True})),
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail="not a member of that department")
        query = {**base, "dept_id": dept_id}
    else:
        # The org's dept universe = every dept that actually owns a library
        # here (managers see all of them; members see only theirs).
        org_dept_ids = folders.distinct("dept_id", base)
        dept_filter = readable_dept_ids(
            roles=_roles(request), user_org_id=org_id,
            user_dept_ids=_dept_ids(request), org_dept_ids=org_dept_ids,
        )
        # Readable = libraries of your depts (or all, for admins) PLUS any
        # public-within-org library, regardless of dept.
        query = {**base, "$or": [
            {"dept_id": {"$in": dept_filter}},
            {"public_within_org": True},
        ]}

    rows = list(folders.find(query).sort("name", 1))
    return {"folders": [_serialize(r) for r in rows]}


@router.delete("/api/dept-library/folders/{folder_id}")
async def delete_dept_library(folder_id: str, request: Request):
    """Delete a dept SOP library folder (record + cascade). Manager-gated.

    Cascade of the folder's documents (S3 + Milvus + Mongo) reuses the personal-
    vault cascade helpers but scoped by folder_id ALONE (a dept library's docs
    come from many uploaders, so the personal-vault user_id scope must not
    apply). Slice 2 wires dept ingestion; until then a library holds no docs and
    the cascade is a no-op — the folder record is removed either way."""
    user_id = get_secure_user_id(request)
    org_id = _org_id(request)
    folders = _folders()
    doc = folders.find_one({
        "_id": folder_id, "owner_type": OwnerType.DEPT,
        "folder_kind": FOLDER_KIND, "deleted": {"$ne": True},
    })
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="library not found")
    if not can_manage_dept_library(
        roles=_roles(request), user_org_id=org_id, user_dept_ids=_dept_ids(request),
        target_org=doc.get("org_id"), target_dept=doc.get("dept_id"),
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="dept_admin (of this dept) / org_admin / super_admin required")

    from dept_library_store import delete_dept_docs
    db = get_mongo_client()[MONGODB_DATABASE]
    # Hard-delete (delete-not-archive) the WHOLE library: all its vectors from the
    # shared collection (scoped by folder_id) + every document's S3 object + files
    # record. folder_id is globally unique, so this never touches another dept.
    try:
        n_vecs = delete_dept_docs(folder_id=folder_id)
    except RuntimeError as e:
        # Vectors could NOT be removed — keep the folder + file records so the
        # library isn't half-deleted (records gone, chunks still served). Retryable.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"could not remove library vectors: {e}") from e
    _lib_doc_ids = [d for d in db["files"].distinct("_id", {"folder_id": folder_id}) if d]
    n_files = _delete_dept_originals(_lib_doc_ids)
    related_docs = _lib_doc_ids                                          # for the response count

    folders.delete_one({"_id": folder_id})

    # Deregister the semantic source from the DISCOVERY registry (soft-delete →
    # active=False) so chat + /semantic/search stop offering it. Loud, non-fatal —
    # the folder/vectors/files are already gone regardless. ONLY deregister a source
    # THIS library created: a library pinned to an externally-advertised source
    # (register_discovery=False) must never deregister that shared source.
    if doc.get("discovery_registered"):
        try:
            from services.enterprise_mcp_client import deregister_source, service_api_key
            await deregister_source(
                dept_library_tool_id(doc.get("org_id"), doc.get("dept_id")),
                service_api_key())
        except Exception:  # noqa: BLE001 — cleanup best-effort; loud, never fatal
            logger.exception("[dept-library] failed to deregister semantic source for "
                             "deleted library org=%s dept=%s", doc.get("org_id"),
                             doc.get("dept_id"))

    logger.info("[dept-library] deleted '%s' (%d doc(s)) org=%s dept=%s by %s",
                doc.get("name"), len(related_docs), doc.get("org_id"),
                doc.get("dept_id"), user_id)
    return {"message": f"Department library '{doc.get('name')}' deleted",
            "documents_removed": len(related_docs)}


@router.post("/api/dept-library/folders/{folder_id}/documents")
async def upload_dept_library_document(
    folder_id: str,
    request: Request,
    file: UploadFile = File(...),
    doc_type: str = Form(""),
):
    """Upload one SOP/policy/manual into a dept library. Manager-gated.

    Extracts text (reusing the platform's ``extract_text_by_file_type``),
    ensures the dept's Milvus collection exists (same hybrid schema as the
    default), and ingests the chunks into THAT collection — the one the dept-MCP
    derives and queries (``mcp_<dept>_sop_library_<dept>``). Chunks carry the
    ``folder_id`` + ``department`` metadata like any document.

    ``doc_type`` is the document's taxonomy category (``sop`` / ``charter`` /
    ``circular`` / ``tariff_order`` / …, as declared by the source's taxonomy).
    It is stamped on every chunk and is what a ``document_view`` panel's
    ``doc_types`` filter — and a rag tool's ``doc_types`` — match on. Uploading
    WITHOUT it stores ``doc_type=""``, and any consumer that filters by category
    then silently matches NOTHING: the corpus is retrievable but every filtered
    panel renders "No documents found" (observed in prod 2026-07-16 — the acme
    Policy Library held 12 retrievable docs, all untagged, behind a panel
    filtering `charter`). Untagged upload stays ALLOWED (an unfiltered panel /
    chat still finds the doc) but is logged, because the failure it causes is
    silent and far from here.

    Milvus/ingest paths reuse existing, proven code but cannot be unit-verified
    here (no live Milvus) — a live-stack smoke test is required."""
    import time
    from datetime import datetime as _dt

    user_id = get_secure_user_id(request)
    org_id = _org_id(request)
    folders = _folders()
    lib = folders.find_one({
        "_id": folder_id, "owner_type": OwnerType.DEPT,
        "folder_kind": FOLDER_KIND, "deleted": {"$ne": True},
    })
    if not lib:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="library not found")
    if not can_manage_dept_library(
        roles=_roles(request), user_org_id=org_id, user_dept_ids=_dept_ids(request),
        target_org=lib.get("org_id"), target_dept=lib.get("dept_id"),
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="dept_admin (of this dept) / org_admin / super_admin required")

    filename = file.filename or "document"
    file_ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty file")

    # Extract text with the SAME helper the personal upload path uses.
    from text_extractors import extract_text_by_file_type
    try:
        extracted_text, _meta = extract_text_by_file_type(content, filename, file_ext)
    except Exception as e:  # noqa: BLE001 — surface as a clean 422, don't 500
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"could not extract text from {filename!r}: {e}") from e
    if not (extracted_text or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"no text extracted from {filename!r}")

    from dept_library_store import (
        ensure_shared_dept_collection, ingest_dept_document, delete_dept_docs)
    _org = lib.get("org_id")
    _dept = lib.get("dept_id")
    # Prefer the source_id pinned on the folder (an explicit override flows to the
    # vectors); fall back to the derived per-dept id for older folders.
    _source_id = lib.get("source_id") or dept_library_source_id(_dept)
    # Ensure the ONE shared dept-library collection exists (fail loud — never
    # ingest into nowhere). All depts share it; isolation is by indexed scalars.
    try:
        collection = ensure_shared_dept_collection()
    except Exception as e:  # noqa: BLE001
        logger.exception("[dept-library] could not ensure shared collection")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"vector store unavailable: {e}") from e

    # NON-LOSSY REPLACE (delete-not-archive): a re-upload of the same filename
    # supersedes the prior version — but we fully stage the NEW version (S3 +
    # files record + vectors) BEFORE retiring any old one. If ingest fails we roll
    # back only the new doc and the old version stays intact + queryable, so the
    # SOP is never gone mid-replace. doc_path (folder + filename) is the doc key.
    document_id = str(uuid.uuid4())
    doc_path = f"{folder_id}/{filename}"
    _doc_type = (doc_type or "").strip().lower()
    if not _doc_type:
        # Not fatal — an unfiltered panel / chat still retrieves this doc. But a
        # doc_types-filtered consumer will silently skip it forever, so say so
        # HERE (the only place that knows) rather than let it surface as an empty
        # panel nobody can explain.
        logging.warning(
            "[dept-library] %s uploaded to folder=%s source=%s WITHOUT doc_type — "
            "it will NOT match any doc_types-filtered panel or rag tool",
            filename, folder_id, _source_id)
    _store_dept_original(
        content=content, content_type=file.content_type,
        org_id=_org, dept_id=_dept, folder_id=folder_id,
        document_id=document_id, filename=filename, file_ext=file_ext,
        user_id=user_id, doc_type=_doc_type)
    try:
        n_chunks = await ingest_dept_document(
            org_id=_org, dept_id=_dept, source_id=_source_id, folder_id=folder_id,
            document_id=document_id, doc_path=doc_path, filename=filename,
            text=extracted_text, doc_type=_doc_type)
    except Exception:
        _delete_dept_originals([document_id])     # roll back the just-stored file
        raise

    # New version is fully in place — NOW retire prior version(s) of this filename
    # (excluding the new doc by its distinct id).
    replace_warning = None
    _db = get_mongo_client()[MONGODB_DATABASE]
    _old_ids = [d for d in _db["files"].distinct(
        "_id", {"folder_id": folder_id, "filename": filename, "owner_type": "dept",
                "_id": {"$ne": document_id}}) if d]
    if _old_ids:
        try:
            delete_dept_docs(document_ids=_old_ids)   # old vectors (raises on failure)
            _delete_dept_originals(_old_ids)          # old S3 object(s) + files record(s)
            logger.info("[dept-library] replace: retired %d prior version(s) of '%s' "
                        "in lib %s", len(_old_ids), filename, folder_id)
        except RuntimeError as e:
            # The new version is live; only the OLD cleanup failed. Don't fail the
            # upload — but surface loudly and leave the old files record in place so
            # a later re-upload retries the deletion (never a silent orphan).
            logger.exception("[dept-library] replace: could not retire prior "
                             "version(s) of '%s' in lib %s", filename, folder_id)
            replace_warning = (f"New version stored, but {len(_old_ids)} old "
                               f"version(s) could not be fully removed: {e}")

    logger.info("[dept-library] ingested '%s' (%d chunk(s)) into %s (lib=%s dept=%s) by %s",
                filename, n_chunks, collection, folder_id, _dept, user_id)
    return {
        "document_id": document_id,
        "library_id": folder_id,
        "collection": collection,
        "filename": filename,
        "doc_path": doc_path,
        "chunks": n_chunks,
        **({"warning": replace_warning} if replace_warning else {}),
    }


@router.get("/api/dept-library/folders/{folder_id}/documents")
async def list_dept_library_documents(folder_id: str, request: Request):
    """List the documents uploaded into a dept library (name, chunk count,
    when, uploader). Read‑scoped: any member of the library's dept (or admin).
    Mirrors the personal folder's document list."""
    get_secure_user_id(request)
    org_id = _org_id(request)
    folders = _folders()
    lib = folders.find_one({
        "_id": folder_id, "owner_type": OwnerType.DEPT,
        "folder_kind": FOLDER_KIND, "deleted": {"$ne": True},
    })
    if not lib:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="library not found")
    if not can_read_dept_library(
        roles=_roles(request), user_org_id=org_id, user_dept_ids=_dept_ids(request),
        target_org=lib.get("org_id"), target_dept=lib.get("dept_id"),
        target_public=bool(lib.get("public_within_org")),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="not a member of that department")
    # List from the `files` records (each doc is a first-class stored file now),
    # exactly like the personal folder panel — filename, size, type, when, uploader.
    db = get_mongo_client()[MONGODB_DATABASE]
    recs = list(db["files"].find(
        {"folder_id": folder_id, "owner_type": "dept"},
        {"_id": 1, "filename": 1, "file_size_bytes": 1, "content_type": 1,
         "file_extension": 1, "created_at": 1, "user_id": 1},
    ).sort("created_at", -1))
    documents = [{
        "document_id": r.get("_id"),
        "filename": r.get("filename"),
        "file_size_bytes": r.get("file_size_bytes"),
        "content_type": r.get("content_type"),
        "file_extension": r.get("file_extension"),
        "created_at": r.get("created_at"),
        "uploaded_by": r.get("user_id"),
    } for r in recs]
    return {"folder_id": folder_id, "documents": documents}


@router.delete("/api/dept-library/folders/{folder_id}/documents/{document_id}")
async def delete_dept_library_document(folder_id: str, document_id: str, request: Request):
    """Delete ONE document from a dept library — its vectors (from the dept
    collection, scoped by document_id) and its chunks. Manager‑gated. The
    library itself stays. Mirrors per‑document management in the personal panel."""
    user_id = get_secure_user_id(request)
    org_id = _org_id(request)
    folders = _folders()
    lib = folders.find_one({
        "_id": folder_id, "owner_type": OwnerType.DEPT,
        "folder_kind": FOLDER_KIND, "deleted": {"$ne": True},
    })
    if not lib:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="library not found")
    if not can_manage_dept_library(
        roles=_roles(request), user_org_id=org_id, user_dept_ids=_dept_ids(request),
        target_org=lib.get("org_id"), target_dept=lib.get("dept_id"),
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="dept_admin (of this dept) / org_admin / super_admin required")

    from dept_library_store import delete_dept_docs
    try:
        n = delete_dept_docs(document_ids=[document_id])                 # shared-collection vectors
    except RuntimeError as e:
        # Vectors could NOT be removed — do NOT drop the file record, or the doc
        # would vanish from the panel while the MCP keeps serving its chunks as a
        # live SOP. Fail loud, stay consistent + retryable.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"could not remove document vectors: {e}") from e
    _delete_dept_originals([document_id])                                # S3 object + files record
    logger.info("[dept-library] deleted document %s from lib %s (%d chunk(s) + file) by %s",
                document_id, folder_id, n, user_id)
    return {"folder_id": folder_id, "document_id": document_id, "chunks_removed": n}


def _dept_library_file_or_403(folder_id: str, document_id: str, request: Request):
    """Shared read gate for the serving endpoints: the caller must be able to READ
    the library (dept member / admin), and the document must have a stored dept
    file record. Returns the files record."""
    get_secure_user_id(request)
    org_id = _org_id(request)
    lib = _folders().find_one({
        "_id": folder_id, "owner_type": OwnerType.DEPT,
        "folder_kind": FOLDER_KIND, "deleted": {"$ne": True},
    })
    if not lib:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="library not found")
    if not can_read_dept_library(
        roles=_roles(request), user_org_id=org_id, user_dept_ids=_dept_ids(request),
        target_org=lib.get("org_id"), target_dept=lib.get("dept_id"),
        target_public=bool(lib.get("public_within_org")),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="not a member of that department")
    rec = get_mongo_client()[MONGODB_DATABASE]["files"].find_one(
        {"_id": document_id, "folder_id": folder_id, "owner_type": "dept"})
    if not rec or not rec.get("s3_url"):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="no stored file for this document")
    return rec


@router.get("/api/dept-library/folders/{folder_id}/documents/{document_id}/download")
async def download_dept_library_document(
    folder_id: str, document_id: str, request: Request, expiry_minutes: int = 30):
    """Presigned S3 URL for a dept library document — powers Download AND View
    (open in reader). Read-scoped: any member of the library's dept (or admin).
    Mirrors the personal folder panel's download-by-id."""
    rec = _dept_library_file_or_403(folder_id, document_id, request)
    from bucket import generate_presigned_url
    key = _derive_s3_key(rec.get("s3_url"))
    url = generate_presigned_url(key, max(1, int(expiry_minutes)) * 60)
    return {"download_url": url, "document_id": document_id,
            "filename": rec.get("filename"),
            "content_type": rec.get("content_type"),
            "expires_in_seconds": max(1, int(expiry_minutes)) * 60}


@router.get("/api/dept-library/folders/{folder_id}/documents/{document_id}/proxy")
async def proxy_dept_library_document(folder_id: str, document_id: str, request: Request):
    """Stream a dept library document INLINE (same-origin) for the in-app reader —
    the View action. Read-scoped. Mirrors the personal document proxy."""
    import httpx
    from fastapi.responses import StreamingResponse
    rec = _dept_library_file_or_403(folder_id, document_id, request)
    from bucket import generate_presigned_url
    key = _derive_s3_key(rec.get("s3_url"))
    url = generate_presigned_url(key, 300)
    ctype = rec.get("content_type") or "application/octet-stream"
    fname = rec.get("filename") or f"document_{document_id}"

    # Open the upstream stream and CHECK its status BEFORE committing a 200 — a
    # failed S3 fetch must surface as a 502, never a silent empty 200 that the
    # reader renders as a blank document.
    client = httpx.AsyncClient(timeout=60.0)
    try:
        upstream = await client.send(client.build_request("GET", url), stream=True)
    except Exception as e:  # noqa: BLE001 — connection error to storage
        await client.aclose()
        logger.exception("[dept-library] proxy could not reach storage for doc %s", document_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail=f"could not fetch the document from storage: {e}") from e
    if upstream.status_code >= 400:
        body = (await upstream.aread())[:200]
        await upstream.aclose(); await client.aclose()
        logger.error("[dept-library] proxy upstream %s for doc %s: %r",
                     upstream.status_code, document_id, body)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail="could not fetch the document from storage")

    async def _stream():
        try:
            async for chunk in upstream.aiter_bytes(8192):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _stream(), media_type=ctype,
        headers={"Content-Disposition": f'inline; filename="{fname}"'})
