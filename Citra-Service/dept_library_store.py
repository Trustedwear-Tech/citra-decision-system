# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Shared dept SOP-Library vector store.

ONE Milvus collection for ALL dept libraries across every dept/org — so adding
departments never multiplies collections (it stays well under provider caps).
Isolation is enforced at QUERY time by the platform reader's scope filter
(``org_id`` + ``dept`` + ``source_id``), and those isolation keys are FIRST-CLASS
**scalar-indexed** fields (not slow dynamic-JSON fields), so dept-level filtered
RAG is fast at scale.

Field names match the VectorSink schema ``semantic_reader`` already understands
(``dense_vector`` / ``text`` / ``dept`` / ``org_id`` / ``source_id`` / ``doc_path``),
so there is NO schema fork — ``semantic_search`` + ``fetch_document`` query this
collection unchanged. Text lives IN Milvus (like VectorSink), so the reader needs
no side lookup and there is no ``document_chunked`` dependency.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

_VECTOR_DIM = int(os.getenv("MILVUS_VECTOR_DIM", "768"))

# Isolation / lookup fields that get a scalar index for fast filtering. org_id +
# dept + source_id are the dept-level RAG scope; document_id + folder_id key
# delete/replace; doc_path keys the whole-document fetch; doc_type is a filterable
# taxonomy dimension (a strict/non-dynamic collection would ERROR a doc_type filter
# if the field were absent — same class as the `department` landmine).
_SCALAR_INDEXED = ("org_id", "dept", "source_id", "folder_id", "document_id",
                   "doc_path", "doc_type")


def _prefix() -> str:
    p = re.sub(r"[^a-zA-Z0-9]", "_", os.getenv("SEMANTIC_COLLECTION_PREFIX", "mcp"))[:40].lower()
    return p or "mcp"


def shared_dept_collection() -> str:
    """The single shared collection every dept library ingests into + is queried from."""
    return f"{_prefix()}_dept_libraries"


def _q(value) -> str:
    """Quote-escape a scalar for a Milvus expression (injection-safe)."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def ensure_shared_dept_collection() -> str:
    """Create the shared collection (idempotent) with a COSINE vector index and a
    scalar index on every isolation field. Returns the collection name. Fail loud —
    never ingest into a collection that could not be built."""
    name = shared_dept_collection()
    from pymilvus import DataType
    from config.milvus_config import get_milvus_client
    client = get_milvus_client()
    if client.has_collection(name):
        # Ensure it's loaded (idempotent) — self-hosted Milvus does NOT auto-load,
        # and an unloaded collection returns zero search hits.
        try:
            client.load_collection(name)
        except Exception:  # noqa: BLE001 — already loaded / transient
            pass
        return name

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("pk", DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=_VECTOR_DIM)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    # Document-ordered id (<document_id>_p<page>_c<idx>) so the reader's
    # _doc_order_key sorts a whole-document fetch into section order.
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=256)
    schema.add_field("org_id", DataType.VARCHAR, max_length=128)
    schema.add_field("dept", DataType.VARCHAR, max_length=128)
    schema.add_field("source_id", DataType.VARCHAR, max_length=128)
    schema.add_field("folder_id", DataType.VARCHAR, max_length=100)
    schema.add_field("document_id", DataType.VARCHAR, max_length=100)
    schema.add_field("doc_path", DataType.VARCHAR, max_length=512)
    # Filterable taxonomy dimension (e.g. sop / tariff_order / circular). Empty for
    # folder-panel uploads; set by seed/import paths that carry a doc taxonomy.
    schema.add_field("doc_type", DataType.VARCHAR, max_length=64)
    schema.add_field("filename", DataType.VARCHAR, max_length=512)
    schema.add_field("page", DataType.INT64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("created_at", DataType.INT64)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense_vector",
                           index_type="AUTOINDEX", metric_type="COSINE")
    for f in _SCALAR_INDEXED:
        index_params.add_index(field_name=f, index_type="INVERTED")

    client.create_collection(collection_name=name, schema=schema, index_params=index_params)
    try:
        client.load_collection(name)
    except Exception:  # noqa: BLE001 — load is best-effort; search auto-loads
        logger.warning("[dept-library] load_collection(%s) skipped", name)
    logger.info("[dept-library] created shared collection %s (scalar-indexed: %s)",
                name, ", ".join(_SCALAR_INDEXED))
    return name


def _chunk_uniform(text: str, filename: str) -> List[str]:
    """Chunk with the SAME LlamaIndex SentenceSplitter config the personal folder
    panel uses (2048-token chunks / 300 overlap, structure-aware) so dept +
    personal chunks are identical in size. Pure text work — no mongo, no async
    (instantiating the chunked-doc service with the async mongo client tripped a
    'future' error inside the request loop)."""
    from llama_index.core import Document as LlamaDocument
    from llama_index.core.node_parser import SentenceSplitter
    splitter = SentenceSplitter(
        chunk_size=2048, chunk_overlap=300,
        paragraph_separator="\n\n", secondary_chunking_regex=r"[.!?;]\s+")
    nodes = splitter.get_nodes_from_documents([LlamaDocument(text=text)])
    return [n.get_content() for n in nodes if (n.get_content() or "").strip()]


async def ingest_dept_document(
    *, org_id: str, dept_id: str, source_id: str, folder_id: str,
    document_id: str, doc_path: str, filename: str, text: str,
    doc_type: str = "", page: int = 1,
) -> int:
    """Chunk (uniform) → embed → insert into the shared collection, stamping the
    isolation fields (org_id / dept / source_id) + routing keys on EVERY chunk.
    Returns the number of chunks stored. Fail loud on a partial embed."""
    name = ensure_shared_dept_collection()
    chunks = _chunk_uniform(text, filename)
    if not chunks:
        return 0
    from utils import embed_texts_batch
    vectors = await embed_texts_batch(chunks, task_type="RETRIEVAL_DOCUMENT")
    if not vectors or len(vectors) != len(chunks):
        raise RuntimeError(
            f"embedding produced {len(vectors or [])} vectors for {len(chunks)} chunks")
    now = int(time.time())
    rows = [{
        "dense_vector": vectors[i],
        "text": chunks[i],
        "chunk_id": f"{document_id}_p{page}_c{i}",
        "org_id": org_id or "", "dept": dept_id or "", "source_id": source_id or "",
        "folder_id": folder_id or "", "document_id": document_id or "",
        "doc_path": doc_path or "", "doc_type": doc_type or "", "filename": filename or "",
        "page": page, "chunk_index": i, "created_at": now,
    } for i in range(len(chunks))]
    from config.milvus_config import get_milvus_client
    client = get_milvus_client()
    client.insert(collection_name=name, data=rows)
    # Flush so the new rows are sealed + indexed → immediately searchable on
    # self-hosted Milvus (serverless auto-flushes; standalone does not).
    try:
        client.flush(name)
    except Exception:  # noqa: BLE001 — flush best-effort; growing segment still searchable once loaded
        logger.warning("[dept-library] flush after ingest skipped for %s", name)
    logger.info("[dept-library] ingested %d chunk(s) of '%s' (doc=%s dept=%s org=%s) → %s",
                len(rows), filename, document_id, dept_id, org_id, name)
    return len(rows)


def delete_dept_docs(*, document_ids: Optional[List[str]] = None,
                     folder_id: Optional[str] = None) -> int:
    """Hard-delete a dept library's vectors from the shared collection — by a set
    of ``document_ids`` (one doc / replace) or a whole ``folder_id`` (library
    delete). A document/folder id is globally unique, so this never touches another
    dept's rows. Returns the delete count.

    FAILS LOUD: raises ``RuntimeError`` if the Milvus delete errors. Callers MUST
    NOT drop the S3 object / ``files`` record when this raises — otherwise the doc
    vanishes from the panel while the MCP keeps serving its orphaned chunks as a
    live SOP. (Returns 0 only when there is genuinely nothing to delete.)"""
    name = shared_dept_collection()
    from config.milvus_config import get_milvus_client
    client = get_milvus_client()
    if not client.has_collection(name):
        return 0
    ids = [d for d in (document_ids or []) if d]
    if ids:
        expr = f"document_id in [{', '.join(_q(d) for d in ids)}]"
    elif folder_id:
        expr = f"folder_id == {_q(folder_id)}"
    else:
        return 0
    try:
        res = client.delete(collection_name=name, filter=expr)
        n = int((res or {}).get("delete_count", 0) or 0)
        logger.info("[dept-library] deleted %d vector(s) from %s (%s)", n, name, expr[:70])
        return n
    except Exception as e:  # noqa: BLE001 — fail loud: the caller must NOT drop the record
        logger.exception("[dept-library] shared-collection delete failed (%s)", expr[:70])
        raise RuntimeError(f"vector delete failed for [{expr[:70]}]: {e}") from e
