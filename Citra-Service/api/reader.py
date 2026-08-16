# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Document Reader API
Provides endpoints for browsing and reading documents in folders with LLM-extracted metadata
"""

from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, status, Query
from fastapi.responses import JSONResponse
from citra_auth import get_secure_user_id
import asyncio
import logging
from datetime import datetime, timedelta
import json
import os
import re
from io import BytesIO

from bson import ObjectId
from citra_mongo import get_mongo_client

# Import LLM function from query.py
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from query import reply
from token_utils import MAX_OUTPUT_TOKENS_DEFAULT


# Import internet search services
from services.serper_service import serper_service
from services.web_content_fetcher import web_content_fetcher

# PDF text extraction
import PyPDF2

# Configure logging
logger = logging.getLogger(__name__)

# ── Large content handling via reranker ──────────────────────────────────────
LARGE_CONTENT_THRESHOLD = 30_000  # Characters — below this, send full content; above this, chunk + rerank
READER_RERANK_TOP_K = 30  # Reranked chunks sent to LLM for Q&A focused reader
READER_CHUNK_SIZE = 512
READER_CHUNK_OVERLAP = 50

# ── Broad query patterns (summarize, key points, etc.) ──────────────────────
# These queries need representative document coverage, not semantic reranking.
_BROAD_QUERY_PATTERNS = [
    # Exact UI quick-action strings
    "please provide a concise summary of this content",
    "what are the key points or main takeaways",
    "can you explain this in simpler terms",
    "please explain this content using a diagram",
    "extract the key points and main ideas",
    "extract any action items or tasks",
    # Common variations
    "summarize", "summary", "summarise",
    "key points", "key takeaways", "main points", "main takeaways",
    "overview", "what is this about", "what is this document about",
    "explain this", "simplify", "break this down",
    "tldr", "tl;dr", "give me the gist",
]


def _is_broad_query(query: str) -> bool:
    """
    Detect broad/generic queries (summarize, key points, explain) that need
    representative document coverage rather than semantic reranking.
    """
    if not query:
        return True
    q = query.lower().strip().rstrip("?.!")
    return any(pattern in q for pattern in _BROAD_QUERY_PATTERNS)


def _positional_sample(chunks: list, top_k: int) -> list:
    """
    Uniformly sample top_k chunks across the document for representative coverage.
    Divides chunks into top_k equal segments and picks the middle chunk of each.
    """
    n = len(chunks)
    if n <= top_k:
        return chunks

    # Evenly spaced indices across the full document
    step = n / top_k
    indices = [int(step * i + step / 2) for i in range(top_k)]
    # Clamp to valid range
    indices = [min(idx, n - 1) for idx in indices]

    return [chunks[i] for i in indices]


def _chunk_and_rerank(content: str, query: str, top_k: int = READER_RERANK_TOP_K) -> str:
    """
    For large content (>30K chars): chunk the text and either rerank (targeted queries)
    or positionally sample (broad queries like summarize/key points) to select top-K chunks.
    For small content: return as-is.

    NOTE: This is synchronous (reranker uses sync httpx). Async callers must use
    ``await asyncio.to_thread(_chunk_and_rerank, content, query)`` to avoid blocking.
    """
    if not content or len(content) <= LARGE_CONTENT_THRESHOLD:
        return content

    from llama_index.core.node_parser import SentenceSplitter
    from reranker import rerank, ENABLE_RERANKER

    # Chunk the content
    splitter = SentenceSplitter(chunk_size=READER_CHUNK_SIZE, chunk_overlap=READER_CHUNK_OVERLAP)
    chunk_texts = splitter.split_text(content)

    if not chunk_texts:
        return content[:LARGE_CONTENT_THRESHOLD]

    logger.info(f"🧩 [READER] Chunked {len(content)} chars → {len(chunk_texts)} chunks")

    # Build chunk dicts with positional IDs
    chunks = [
        {"text": t, "chunk_id": str(i), "score": 0.0}
        for i, t in enumerate(chunk_texts)
    ]

    # Strategy: broad queries → positional sampling; targeted queries → reranking
    broad = _is_broad_query(query)
    if broad:
        logger.info(f"📖 [READER] Broad query detected — using positional sampling ({top_k} chunks across {len(chunks)} total)")
        selected = _positional_sample(chunks, top_k)
    elif ENABLE_RERANKER and len(chunks) > top_k:
        logger.info(f"🎯 [READER] Targeted query — reranking {len(chunks)} chunks to top {top_k}")
        selected = rerank(query, chunks, top_k)
    else:
        selected = chunks[:top_k]

    # Sort by original position for readability
    selected_sorted = sorted(selected, key=lambda c: int(c.get("chunk_id", "0")))

    parts = []
    for chunk in selected_sorted:
        text = chunk.get("text", "").strip()
        if text:
            parts.append(text)

    result = "\n\n".join(parts)
    strategy = "positional_sample" if broad else "rerank"
    logger.info(f"✅ [READER] {strategy}: {len(chunk_texts)} → {len(selected)} chunks ({len(result)} chars)")
    return result

# MongoDB Configuration - read from .env only
from citra_mongo import MONGODB_DATABASE

# Create router
router = APIRouter()

# Helper function to get MongoDB collection (avoid module-level client creation)
def get_chunks_collection():
    """Get chunks collection using the global MongoDB manager"""
    client = get_mongo_client()
    db = client[MONGODB_DATABASE]
    return db['document_chunked']

# Configuration
MAX_DOCUMENTS_PER_BATCH = int(os.getenv("MAX_READER_DOCUMENTS_PER_BATCH", "30"))
MAX_EXTRACTION_PROMPT_LENGTH = int(os.getenv("MAX_EXTRACTION_PROMPT_LENGTH", "200000"))
EXTRACTION_VERSION = "v1"


def serialize_mongo_doc(doc):
    """Convert MongoDB document to JSON serializable format"""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [serialize_mongo_doc(item) for item in doc]
    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if key == '_id' or isinstance(value, ObjectId):
                result[key] = str(value)
            elif isinstance(value, datetime):
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = serialize_mongo_doc(value)
            elif isinstance(value, list):
                result[key] = serialize_mongo_doc(value)
            else:
                result[key] = value
        return result
    return doc


def get_note_content(note_id: str, user_id: str, file_record: dict):
    """
    Retrieve note content from notes collection.
    
    Args:
        note_id: Note document ID (base_note_id)
        user_id: User ID for security
        file_record: File metadata from files collection
        
    Returns:
        Document data with note content
    """
    try:
        mongo_client = get_mongo_client()
        db = mongo_client[MONGODB_DATABASE]
        notes_collection = db["Notes"]
        
        # Get note from notes collection
        note = notes_collection.find_one({
            "note_id": note_id,
            "user_id": user_id
        })
        
        if not note:
            logger.warning(f"⚠️ Note not found: {note_id}")
            raise HTTPException(
                status_code=404,
                detail="Note not found or access denied"
            )
        
        # Extract note content
        note_text = note.get("text", "")
        title = note.get("title", "Untitled Note")
        created_at = note.get("created_at", "")
        
        logger.info(f"✅ Retrieved note content: {title} ({len(note_text)} characters)")
        
        # Format as document response (similar to non-PDF documents)
        document_data = {
            "document_id": note_id,
            "filename": title,
            "file_type": "note",
            "file_type_category": "note",
            "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
            "is_pdf": False,
            "is_note": True,  # Flag for frontend
            "text_content": note_text,  # Full note text
            "total_chunks": 1,  # Single chunk for notes
            "chunks": [{  # Format as single chunk for consistency
                "chunk_index": 0,
                "text": note_text,
                "page_start": 1,
                "page_end": 1
            }]
        }
        
        # Wrap in success response format expected by UI
        response_data = {
            "success": True,
            "document": serialize_mongo_doc(document_data)
        }
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error retrieving note content: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving note: {str(e)}"
        )


def get_audio_content(audio_id: str, user_id: str, file_record: dict):
    """
    Retrieve audio transcription from audio_transcripts collection.
    
    Args:
        audio_id: Audio document ID (transcript_id)
        user_id: User ID for security
        file_record: File metadata from files collection
        
    Returns:
        Document data with audio transcription
    """
    try:
        mongo_client = get_mongo_client()
        db = mongo_client[MONGODB_DATABASE]
        # Audio transcripts are stored in "audio_transcripts" collection
        audio_transcripts = db["audio_transcripts"]
        
        # Get transcript_id from file_record
        transcript_id = file_record.get("mongodb_collections", {}).get("transcripts_id", audio_id)
        
        logger.debug(f"[AUDIO_DEBUG] audio_id={audio_id}, transcript_id={transcript_id}")
        
        # Get audio transcript
        audio = audio_transcripts.find_one({
            "_id": transcript_id,
            "user_id": user_id
        })
        
        if not audio:
            logger.warning(f"⚠️ Audio transcript not found - Requested ID: {audio_id}, Transcript ID: {transcript_id}")
            raise HTTPException(
                status_code=404,
                detail="Audio transcript not found or access denied"
            )
        
        # Extract content - field is "transcript", not "text"
        transcript_text = audio.get("transcript", "")
        topic = audio.get("topic_or_filename", "Untitled Audio")
        created_at = audio.get("created_at", "")
        duration = audio.get("duration", file_record.get("duration_seconds", 0))
        
        logger.info(f"✅ Retrieved audio transcription: {topic} ({len(transcript_text)} characters, {duration}s)")
        
        # Format response
        document_data = {
            "document_id": audio_id,
            "filename": topic,
            "file_type": "audio",
            "file_type_category": "audio",
            "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
            "is_pdf": False,
            "is_audio": True,
            "duration_seconds": duration,
            "text_content": transcript_text,
            "total_chunks": 1,
            "chunks": [{
                "chunk_index": 0,
                "text": transcript_text,
                "page_start": 1,
                "page_end": 1
            }]
        }
        
        # Wrap in success response format expected by UI
        response_data = {
            "success": True,
            "document": serialize_mongo_doc(document_data)
        }
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error retrieving audio content: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving audio: {str(e)}"
        )


def get_video_content(video_id: str, user_id: str, file_record: dict):
    """
    Retrieve video transcription from video_transcripts collection.
    
    Args:
        video_id: Video document ID
        user_id: User ID for security
        file_record: File metadata from files collection
        
    Returns:
        Document data with video transcription
    """
    try:
        mongo_client = get_mongo_client()
        db = mongo_client[MONGODB_DATABASE]
        video_transcripts = db["video_transcripts"]
        
        # Get video_transcripts_id from file_record
        transcript_id = file_record.get("mongodb_collections", {}).get("video_transcripts_id", video_id)
        
        # Get video transcript
        video = video_transcripts.find_one({
            "_id": transcript_id,
            "user_id": user_id
        })
        
        if not video:
            logger.warning(f"⚠️ Video transcript not found: {transcript_id}")
            raise HTTPException(
                status_code=404,
                detail="Video transcript not found or access denied"
            )
        
        # Extract content - video uses "full_transcription" field
        transcript_text = video.get("full_transcription", "")
        topic = video.get("topic", "Untitled Video")
        created_at = video.get("created_at", "")
        duration = file_record.get("duration_seconds", 0)
        
        logger.info(f"✅ Retrieved video transcription: {topic} ({len(transcript_text)} characters, {duration}s)")
        
        # Format response
        document_data = {
            "document_id": video_id,
            "filename": topic,
            "file_type": "video",
            "file_type_category": "video",
            "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
            "is_pdf": False,
            "is_video": True,
            "duration_seconds": duration,
            "text_content": transcript_text,
            "total_chunks": 1,
            "chunks": [{
                "chunk_index": 0,
                "text": transcript_text,
                "page_start": 1,
                "page_end": 1
            }]
        }
        
        # Wrap in success response format expected by UI
        response_data = {
            "success": True,
            "document": serialize_mongo_doc(document_data)
        }
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error retrieving video content: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving video: {str(e)}"
        )


@router.post("/extract-metadata")
async def extract_document_metadata(
    request: Request,
    folder_id: Optional[str] = Query(None, description="Folder ID to extract metadata from. If None, returns all user documents."),
    force_refresh: bool = Query(False, description="Force refresh even if cached metadata exists"),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    per_page: int = Query(10, ge=1, le=50, description="Documents per page (max 50)")
):
    """
    Extract document metadata using LLM with caching support
    """
    try:
        user_id = get_secure_user_id(request)
        if folder_id:
            logger.info(f"📚 Reader metadata extraction requested for folder: {folder_id}, user: {user_id}, force_refresh: {force_refresh}, page: {page}, per_page: {per_page}")
        else:
            logger.info(f"📚 Reader metadata extraction requested for ALL documents, user: {user_id}, force_refresh: {force_refresh}, page: {page}, per_page: {per_page}")
        
        # Get MongoDB collections
        chunks_collection = get_chunks_collection()
        from citra_mongo import get_mongo_client
        mongo_client = get_mongo_client()
        db = mongo_client[MONGODB_DATABASE]
        files_collection = db["files"]
        
        # 1. Get all metadata chunks (chunk_index: 0) - filter by folder if provided
        query_filter = {
            "user_id": user_id,
            "chunk_index": 0
        }
        
        # Only add folder_id filter if it's provided
        if folder_id:
            query_filter["folder_id"] = folder_id
        
        # Get total count for pagination
        total_count = chunks_collection.count_documents(query_filter)
        
        # Calculate skip for pagination
        skip = (page - 1) * per_page
        
        # Fetch paginated documents, sorted by created_at descending (newest first)
        documents = list(chunks_collection.find(query_filter)
                        .sort("created_at", -1)
                        .skip(skip)
                        .limit(per_page))
        
        if not documents:
            folder_msg = f"folder {folder_id}" if folder_id else "your account"
            logger.info(f"📭 No documents found for {folder_msg}")
            return JSONResponse(
                status_code=200,
                content={
                    "success": True if page > 1 else False,  # Success if just no more pages
                    "error": None if page > 1 else f"No documents found in {folder_msg}",
                    "documents": [],
                    "total": total_count,
                    "page": page,
                    "per_page": per_page,
                    "has_more": False
                }
            )
        
        folder_desc = f"folder {folder_id}" if folder_id else "all user documents"
        logger.info(f"📄 Found {len(documents)} documents in {folder_desc} (page {page}, total: {total_count})")
        
        # 2. Check which documents need extraction (unless force_refresh)
        if force_refresh:
            logger.info(f"🔄 Force refresh requested - re-extracting all documents")
            docs_needing_extraction = documents
        else:
            docs_needing_extraction = [
                doc for doc in documents
                if not doc.get("reader_metadata") or 
                   doc.get("reader_metadata", {}).get("extraction_version") != EXTRACTION_VERSION
            ]
            
            cached_count = len(documents) - len(docs_needing_extraction)
            if cached_count > 0:
                logger.info(f"✅ Found {cached_count} documents with cached metadata")
        
        if not docs_needing_extraction:
            logger.info(f"✅ All documents have cached metadata")
            # Return cached data
            result_documents = []
            for doc in documents:
                result_documents.append({
                    "document_id": doc.get("document_id"),
                    "original_filename": doc.get("topic_or_filename", "Unknown"),
                    "file_type": doc.get("file_type", ""),
                    "created_at": doc.get("created_at", ""),
                    "reader_metadata": doc.get("reader_metadata", {})
                })
            
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "documents": serialize_mongo_doc(result_documents),
                    "total": total_count,
                    "page": page,
                    "per_page": per_page,
                    "has_more": (skip + len(documents)) < total_count,
                    "cached": len(result_documents)
                }
            )
        
        logger.info(f"📦 Need to extract metadata for {len(docs_needing_extraction)} documents")
        
        # 3. Fetch first chunks (chunk_index: 0) for LLM extraction
        # Note: chunk_index 0 contains both metadata AND first page text
        first_chunks = []
        doc_id_to_metadata = {}
        
        for doc in docs_needing_extraction:
            doc_id = doc.get("document_id")
            
            # 🔍 ENHANCED: Fetch accurate file_type from files collection
            file_type_from_chunked = doc.get("file_type", "")
            file_record = files_collection.find_one({"_id": doc_id, "user_id": user_id})
            
            if file_record:
                # Use file_extension from files collection (more reliable)
                file_type = file_record.get("file_extension", file_type_from_chunked)
                logger.info(f"📄 Document {doc_id}: file_type from files collection: {file_type}")
            else:
                # Fallback to document_chunked file_type
                file_type = file_type_from_chunked
                if file_type_from_chunked:
                    logger.debug(f"📄 Document {doc_id}: using file_type from document_chunked: {file_type}")
            
            # Get first text chunk (chunk 0 has metadata + first page text)
            first_chunk = chunks_collection.find_one({
                "document_id": doc_id,
                "chunk_index": 0
            })
            
            if first_chunk:
                chunk_text = first_chunk.get("metadata", {}).get("text", "")
                if chunk_text:
                    # Store document metadata for later
                    doc_id_to_metadata[doc_id] = {
                        "original_filename": doc.get("topic_or_filename", "Unknown"),
                        "file_type": file_type,
                        "created_at": doc.get("created_at", "")
                    }
                    
                    first_chunks.append({
                        "document_id": doc_id,
                        "text": chunk_text[:5000],  # Limit to first 5000 chars
                        "filename": doc.get("topic_or_filename", "")
                    })
        
        if not first_chunks:
            logger.warning(f"⚠️ No first chunks found for extraction")
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "documents": [],
                    "total": 0,
                    "warning": "No content available for extraction"
                }
            )
        
        logger.info(f"📦 Prepared {len(first_chunks)} documents for LLM extraction")
        
        # 4. Build extraction prompt
        extraction_prompt = build_extraction_prompt(first_chunks)
        
        logger.info(f"🤖 Sending extraction request to LLM")
        logger.info(f"📏 Prompt length: {len(extraction_prompt)} characters")
        
        # Get user email for credit tracking
        from citra_auth import get_user_email
        user_email = get_user_email(request)
        
        # 6. Call LLM for batch extraction
        try:
            from llm_oss import llm_call
            llm_response = llm_call(
                user_prompt=extraction_prompt,
                system_prompt="You are a document metadata extractor. Return ONLY valid JSON array, no markdown, no explanations.",
                max_tokens=50000,
                user_id=user_id,
                user_email=user_email,
                tier="large",
            )
            
            logger.info(f"✅ Received extraction response from LLM")
            logger.info(f"📏 Response length: {len(llm_response)} characters")
            
            # 7. Parse JSON response
            extracted_metadata = parse_extraction_response(llm_response)
            
            if not extracted_metadata:
                logger.error(f"❌ Failed to parse LLM response as JSON")
                raise ValueError("Invalid JSON response from LLM")
            
            logger.info(f"✅ Parsed {len(extracted_metadata)} document metadata entries")
            
            # 8. Update MongoDB with extracted metadata
            newly_extracted_docs = []
            
            for item in extracted_metadata:
                doc_id = item.get("document_id")
                
                if not doc_id or doc_id not in doc_id_to_metadata:
                    logger.warning(f"⚠️ Skipping invalid document_id: {doc_id}")
                    continue
                
                metadata = {
                    "extracted_title": item.get("title", "Unknown Document"),
                    "document_type": item.get("document_type", "Document"),
                    "case_number": item.get("case_number", ""),
                    "preview": item.get("preview", ""),
                    "parties": item.get("parties", []),
                    "extracted_at": datetime.utcnow(),
                    "extraction_version": EXTRACTION_VERSION
                }
                
                # Update MongoDB
                update_result = chunks_collection.update_one(
                    {
                        "document_id": doc_id,
                        "chunk_index": 0
                    },
                    {
                        "$set": {
                            "reader_metadata": metadata
                        }
                    }
                )
                
                if update_result.modified_count > 0:
                    logger.info(f"✅ Updated metadata for document: {doc_id}")
                    
                    # Add to results
                    doc_info = doc_id_to_metadata[doc_id]
                    newly_extracted_docs.append({
                        "document_id": doc_id,
                        "original_filename": doc_info["original_filename"],
                        "file_type": doc_info["file_type"],
                        "created_at": doc_info["created_at"],
                        "reader_metadata": metadata
                    })
                else:
                    logger.warning(f"⚠️ Failed to update metadata for: {doc_id}")
            
            # 9. Prepare final response with ALL documents (cached + newly extracted)
            all_documents = []
            
            # Add all documents (both cached and newly extracted)
            for doc in documents:
                doc_id = doc.get("document_id")
                reader_meta = doc.get("reader_metadata", {})
                
                # Check if this was newly extracted
                newly_extracted = any(d["document_id"] == doc_id for d in newly_extracted_docs)
                if newly_extracted:
                    # Use the newly extracted metadata
                    matching_doc = next((d for d in newly_extracted_docs if d["document_id"] == doc_id), None)
                    if matching_doc:
                        all_documents.append(matching_doc)
                else:
                    # Use cached metadata - fetch file_type from files collection
                    file_type_from_chunked = doc.get("file_type", "")
                    file_record = files_collection.find_one({"_id": doc_id, "user_id": user_id})
                    file_type = file_record.get("file_extension", file_type_from_chunked) if file_record else file_type_from_chunked
                    
                    all_documents.append({
                        "document_id": doc_id,
                        "original_filename": doc.get("topic_or_filename", "Unknown"),
                        "file_type": file_type,
                        "created_at": doc.get("created_at", ""),
                        "reader_metadata": reader_meta
                    })
            
            # 10. Return all documents
            logger.info(f"📚 Reader metadata extraction complete")
            logger.info(f"📊 Total documents: {len(all_documents)} (Newly extracted: {len(newly_extracted_docs)}, Cached: {len(all_documents) - len(newly_extracted_docs)})")
            
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "documents": serialize_mongo_doc(all_documents),
                    "total": total_count,
                    "page": page,
                    "per_page": per_page,
                    "has_more": (skip + len(documents)) < total_count,
                    "newly_extracted": len(newly_extracted_docs),
                    "cached": len(all_documents) - len(newly_extracted_docs)
                }
            )
            
        except json.JSONDecodeError as json_error:
            logger.error(f"❌ JSON parsing error: {str(json_error)}")
            logger.error(f"❌ LLM Response: {llm_response[:500]}...")
            
            # Fallback: return documents with original filenames
            fallback_docs = []
            for doc in docs_needing_extraction:
                fallback_docs.append({
                    "document_id": doc.get("document_id"),
                    "original_filename": doc.get("topic_or_filename", "Unknown"),
                    "file_type": doc.get("file_type", ""),
                    "created_at": doc.get("created_at", ""),
                    "reader_metadata": {
                        "extracted_title": doc.get("topic_or_filename", "Unknown Document"),
                        "document_type": "Document",
                        "case_number": "",
                        "preview": "Preview extraction failed",
                        "parties": [],
                        "extracted_at": datetime.utcnow(),
                        "extraction_version": EXTRACTION_VERSION
                    }
                })
            
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "documents": serialize_mongo_doc(fallback_docs),
                    "total": total_count,
                    "page": page,
                    "per_page": per_page,
                    "has_more": (skip + len(documents)) < total_count,
                    "warning": "Extraction partially failed, using fallback"
                }
            )
            
        except HTTPException:
            raise
        except Exception as llm_error:
            error_str = str(llm_error)
            logger.error(f"❌ Error during LLM extraction: {error_str}")
            
            # Check for credit errors and raise HTTP 402
            
            raise HTTPException(
                status_code=500,
                detail=f"Metadata extraction failed: {error_str}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        error_str = str(e)
        logger.error(f"❌ Reader metadata extraction error: {error_str}", exc_info=True)
        
        # Check for credit errors and raise HTTP 402
        
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract document metadata: {error_str}"
        )


@router.delete("/clear-cache")
async def clear_reader_cache(
    request: Request,
    folder_id: str = Query(None, description="Optional: Clear cache for specific folder only")
):
    """
    Clear cached reader metadata
    """
    try:
        user_id = get_secure_user_id(request)
        logger.info(f"🗑️ Clear reader cache requested for user: {user_id}, folder: {folder_id or 'ALL'}")
        
        # Get MongoDB collection
        chunks_collection = get_chunks_collection()
        
        # Build query filter
        query_filter = {
            "user_id": user_id,
            "chunk_index": 0
        }
        
        if folder_id:
            query_filter["folder_id"] = folder_id
        
        # Clear reader_metadata field
        result = chunks_collection.update_many(
            query_filter,
            {
                "$unset": {"reader_metadata": ""}
            }
        )
        
        logger.info(f"✅ Cleared cache for {result.modified_count} documents")
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Cache cleared for {result.modified_count} documents",
                "documents_updated": result.modified_count
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Error clearing cache: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear cache: {str(e)}"
        )


@router.get("/document/{document_id}")
async def get_document_content(
    request: Request,
    document_id: str
):
    """
    Fetch document for reading
    """
    try:
        user_id = get_secure_user_id(request)
        logger.info(f"📖 Fetching document: {document_id} for user: {user_id}")
        
        # Get MongoDB collections
        from citra_mongo import get_mongo_client
        from bucket import generate_download_url
        
        mongo_client = get_mongo_client()
        db = mongo_client[MONGODB_DATABASE]
        files_collection = db["files"]
        chunks_collection = get_chunks_collection()
        
        # 1. Try direct document_id lookup first (UUID format)
        file_record = files_collection.find_one({
            "_id": document_id,
            "user_id": user_id
        })
        
        if file_record:
            logger.debug(f"[FILES_LOOKUP] Found document: _id={file_record.get('_id')}, type={file_record.get('file_type_category')}")
        else:
            logger.debug(f"[FILES_LOOKUP] Not found by owner, checking shared access: {document_id}")
            
            # Check if document exists under a different owner (shared access)
            shared_file_record = files_collection.find_one({"_id": document_id})
            if shared_file_record:
                # Document exists but belongs to another user - check shared access
                doc_folder_id = shared_file_record.get("folder_id")
                if doc_folder_id:
                    try:
                        from services.authorization_service import get_authorization_service
                        auth_service = get_authorization_service()
                        access_result = await auth_service.check_access(
                            user_id=user_id,
                            resource_id=doc_folder_id,
                            resource_type="vault",
                            required_permission="read"
                        )
                        if access_result.get("allowed"):
                            logger.debug(f"[FILES_LOOKUP] Shared access granted for {document_id}")
                            file_record = shared_file_record
                        else:
                            logger.warning(f"Shared access denied for document {document_id}")
                    except Exception as auth_err:
                        logger.warning(f"Error checking shared access: {auth_err}")
            
            if not file_record:
                logger.debug(f"[FILES_LOOKUP] Checking notes/audio/video collections for: {document_id}")
        
        # 🔧 FALLBACK: If not found by document_id, try by original_filename
        # This handles cases where frontend sends filename instead of document UUID
        if not file_record:
            logger.info(f"🔍 Document not found by ID, trying filename lookup: {document_id}")
            
            # Try exact match first
            file_record = files_collection.find_one({
                "original_filename": document_id,
                "user_id": user_id
            })
            
            # If still not found, try partial match (handles truncated filenames)
            if not file_record:
                logger.info(f"🔍 Trying partial filename match for: {document_id}")
                # Escape regex special characters in document_id (e.g., parentheses, brackets)
                escaped_doc_id = re.escape(document_id)
                file_record = files_collection.find_one({
                    "original_filename": {"$regex": f"^{escaped_doc_id}", "$options": "i"},
                    "user_id": user_id
                })
            
            if file_record:
                # Update document_id to the actual UUID for subsequent queries
                actual_document_id = str(file_record.get("_id"))
                logger.info(f"✅ Found document by filename '{document_id}' → actual ID: {actual_document_id}")
                document_id = actual_document_id
        
        if not file_record:
            # 🌐 INTERNET RESEARCH FALLBACK: Check document_chunked collection
            # Internet research docs (from presentation generator) are stored only
            # in document_chunked, not in the files collection
            logger.info(f"🔍 Checking document_chunked collection for: {document_id}")
            chunk_docs = list(chunks_collection.find({
                "document_id": document_id,
                "user_id": user_id
            }).sort("chunk_index", 1))
            
            if chunk_docs:
                logger.info(f"✅ Found {len(chunk_docs)} chunks in document_chunked for: {document_id}")
                # Reconstruct text from chunks
                full_text = "\n\n".join(
                    chunk.get("text", "") or chunk.get("metadata", {}).get("text", "")
                    for chunk in chunk_docs
                )
                topic = chunk_docs[0].get("topic_or_filename", "") or chunk_docs[0].get("metadata", {}).get("topic_or_filename", "") or document_id
                created_at = chunk_docs[0].get("created_at", "")
                
                document_data = {
                    "document_id": document_id,
                    "filename": topic,
                    "file_type": "txt",
                    "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
                    "is_pdf": False,
                    "text_content": full_text,
                    "total_chunks": len(chunk_docs),
                    "chunks": [{
                        "chunk_index": i,
                        "text": chunk.get("text", "") or chunk.get("metadata", {}).get("text", ""),
                        "page_start": 1,
                        "page_end": 1
                    } for i, chunk in enumerate(chunk_docs)]
                }
                
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "document": serialize_mongo_doc(document_data)
                    }
                )
            
            logger.warning(f"⚠️ Document not found by ID or filename: {document_id}")
            raise HTTPException(
                status_code=404,
                detail="Document not found or access denied"
            )
        
        # Use the file record's owner user_id for sub-collection lookups
        # (shared access may mean the requesting user differs from the resource owner)
        content_user_id = file_record.get("user_id", user_id)
        
        # 📝 NOTES HANDLING: Check if this is a note and route to notes collection
        file_type_category = file_record.get("file_type_category", "")
        if file_type_category == "note":
            logger.info(f"📝 Note detected - retrieving from notes collection: {document_id}")
            return get_note_content(document_id, content_user_id, file_record)
        
        # 🎵 AUDIO HANDLING: Check if this is audio and route to audio_transcripts collection
        if file_type_category == "audio":
            logger.info(f"🎵 Audio detected - retrieving transcription from audio_transcripts collection: {document_id}")
            return get_audio_content(document_id, content_user_id, file_record)
        
        # 🎥 VIDEO HANDLING: Check if this is video and route to video_transcripts collection
        if file_type_category == "video":
            logger.info(f"🎥 Video detected - retrieving transcription from video_transcripts collection: {document_id}")
            return get_video_content(document_id, content_user_id, file_record)
        
        # 2. Get file metadata (for PDF and document handling)
        filename = file_record.get("original_filename") or file_record.get("filename") or f"document_{document_id}"
        file_type = file_record.get("file_extension", "").lower()
        created_at = file_record.get("uploaded_at", "")
        
        # 3. Determine rendering strategy based on file type
        is_pdf = file_type in ['pdf', '.pdf']
        
        if is_pdf:
            # 📄 PDF FILES: Return presigned S3 URL for iframe rendering
            logger.info(f"📄 PDF document detected - generating S3 URL for: {filename}")
            
            s3_url = file_record.get("s3_url")
            s3_key = file_record.get("s3_key")
            
            if not s3_url and not s3_key:
                logger.warning(f"❌ No S3 URL or key in document: {document_id}")
                raise HTTPException(status_code=404, detail="PDF file not found in storage")
            
            # Extract S3 key from URL if not directly available
            if not s3_key and s3_url:
                if ".amazonaws.com/" in s3_url:
                    s3_key = s3_url.split(".amazonaws.com/")[-1]
                elif "s3://" in s3_url:
                    bucket_name = os.getenv("BUCKET_NAME", "citra-ai")
                    s3_key = s3_url.replace(f"s3://{bucket_name}/", "")
                else:
                    s3_key = s3_url
            
            # Generate presigned URL for document access (30 minutes expiry)
            presigned_url = generate_download_url(s3_key, expiry_seconds=1800)
            
            document_data = {
                "document_id": document_id,
                "filename": filename,
                "file_type": file_type,
                "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
                "is_pdf": True,
                "download_url": presigned_url,
                "s3_key": s3_key,
                "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat() + "Z"
            }

            # 🧠 Provide extracted text chunks alongside PDF URL so AI chat has content
            try:
                text_chunks = list(chunks_collection.find({
                    "document_id": document_id,
                    "user_id": content_user_id,
                    "chunk_index": {"$gte": 0}
                }).sort("chunk_index", 1))

                formatted_chunks = []
                for chunk in text_chunks:
                    chunk_text = chunk.get("metadata", {}).get("text", "")
                    if chunk_text:
                        formatted_chunks.append({
                            "chunk_index": chunk.get("chunk_index", 0),
                            "text": chunk_text,
                            "page_number": chunk.get("metadata", {}).get("page_number", 0)
                        })

                document_data["total_chunks"] = len(formatted_chunks)
                document_data["chunks"] = formatted_chunks

                logger.info(
                    f"📦 Added {len(formatted_chunks)} text chunks for PDF document {document_id} to support chat"
                )
            except Exception as chunk_err:
                logger.warning(
                    f"⚠️ Unable to attach text chunks for PDF {document_id}: {chunk_err}"
                )
            
            logger.info(f"✅ PDF S3 URL generated successfully for: {filename}")
            
        else:
            # 📝 NON-PDF FILES: Extract text chunks from MongoDB
            logger.info(f"📝 Non-PDF document detected - extracting chunks from MongoDB for: {filename}")
            
            # Get all text chunks (chunk_index >= 0), sorted
            text_chunks = list(chunks_collection.find({
                "document_id": document_id,
                "user_id": content_user_id,
                "chunk_index": {"$gte": 0}
            }).sort("chunk_index", 1))
            
            if not text_chunks:
                logger.warning(f"⚠️ No chunks found in MongoDB for document: {document_id}")
                raise HTTPException(status_code=404, detail="Document content not found")
            
            logger.info(f"📦 Found {len(text_chunks)} text chunks for document {document_id}")
            
            # Format chunks
            formatted_chunks = []
            for chunk in text_chunks:
                chunk_text = chunk.get("metadata", {}).get("text", "")
                if chunk_text:
                    formatted_chunks.append({
                        "chunk_index": chunk.get("chunk_index", 0),
                        "text": chunk_text,
                        "page_number": chunk.get("metadata", {}).get("page_number", 0)
                    })
            
            document_data = {
                "document_id": document_id,
                "filename": filename,
                "file_type": file_type,
                "created_at": created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
                "is_pdf": False,
                "total_chunks": len(formatted_chunks),
                "chunks": formatted_chunks
            }
            
            logger.info(f"✅ Non-PDF chunks extracted successfully for: {filename}")
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "document": serialize_mongo_doc(document_data)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error fetching document content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch document: {str(e)}"
        )


def build_extraction_prompt(first_chunks: List[Dict[str, Any]]) -> str:
    """
    Build prompt for LLM to extract document metadata
    """
    # Build documents section
    documents_text = []
    
    for idx, chunk_info in enumerate(first_chunks, 1):
        doc_entry = f"""
---
DOCUMENT {idx}:
document_id: {chunk_info['document_id']}
filename: {chunk_info['filename']}

CONTENT:
{chunk_info['text']}
---
"""
        documents_text.append(doc_entry)
    
    combined_text = "\n".join(documents_text)
    
    # Truncate if too long
    if len(combined_text) > MAX_EXTRACTION_PROMPT_LENGTH:
        logger.warning(f"⚠️ Prompt too long ({len(combined_text)}), truncating...")
        combined_text = combined_text[:MAX_EXTRACTION_PROMPT_LENGTH] + "\n\n[... content truncated ...]"
    
    prompt = f"""You are a document metadata extractor. Analyze these documents and extract key information.

For EACH document, extract:
1. **Title/Header**: The actual document title from the content (NOT the filename). Look for document names, headings, or main titles.
2. **Document Type**: Classify the document based on its content (e.g., Report, Contract, Specification, Analysis, Proposal, etc.)
3. **Case Number/Reference**: Extract any reference number, case number, project ID, or filing number if present
4. **Preview**: Write a 2-3 sentence summary covering the key points (parties, subject matter, main purpose)
5. **Parties**: Extract relevant party names, stakeholders, or entities mentioned

DOCUMENTS TO ANALYZE:
{combined_text}

Return ONLY a valid JSON array with this EXACT structure (no markdown, no code blocks, no explanations):

[
  {{
    "document_id": "exact document_id from above",
    "title": "Actual document title extracted from content",
    "document_type": "Type of document based on content",
    "case_number": "Reference/case/project number if found, empty string otherwise",
    "preview": "2-3 sentence summary of document",
    "parties": ["Party 1 name", "Party 2 name"]
  }}
]

CRITICAL RULES:
- Return ONLY the JSON array, nothing else
- Use exact document_id values from above
- Extract actual titles from document content, not filenames
- Keep previews concise (2-3 sentences max)
- If information not found, use empty string ""
- Ensure valid JSON syntax"""

    return prompt


def parse_extraction_response(response: str) -> List[Dict[str, Any]]:
    """
    Parse LLM response and extract JSON array
    """
    try:
        # Remove markdown code blocks if present
        response = response.strip()
        
        # Handle markdown code blocks
        if "```" in response:
            json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
            if json_match:
                response = json_match.group(1).strip()
            else:
                # Try to find JSON array
                json_match = re.search(r'\[.*\]', response, re.DOTALL)
                if json_match:
                    response = json_match.group(0)
        
        # Parse JSON
        metadata = json.loads(response)
        
        if not isinstance(metadata, list):
            logger.warning(f"⚠️ Expected JSON array, got {type(metadata)}")
            return []
        
        logger.info(f"✅ Successfully parsed {len(metadata)} metadata entries")
        return metadata
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON decode error: {e}")
        logger.error(f"❌ Response snippet: {response[:500]}...")
        return []
    except Exception as e:
        logger.error(f"❌ Parse error: {e}")
        return []


# Health check
@router.get("/health")
async def reader_health_check():
    """Health check for reader service"""
    try:
        # Get MongoDB collection and test connection
        chunks_collection = get_chunks_collection()
        chunks_collection.find_one({}, {"_id": 1})
        
        return JSONResponse(
            content={
                "status": "healthy",
                "service": "document_reader",
                "mongodb": "connected",
                "extraction_version": EXTRACTION_VERSION,
                "timestamp": datetime.utcnow().isoformat()
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Reader service health check failed: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")


# ==================== INTERNET SEARCH ENDPOINTS ====================

@router.post("/internet/search")
async def internet_search(request: Request):
    """
    Search the internet using Serper API.
    Returns structured search results including knowledge graph, organic results, PAA, and related searches.
    """
    try:
        body = await request.json()
        query = body.get("query")
        country = body.get("country", "in")
        language = body.get("language", "en")
        page = body.get("page", 1)

        if not query:
            raise HTTPException(status_code=400, detail="Query is required")

        user_id = get_secure_user_id(request)
        logger.info(f"🔍 Internet search request: '{query}' (country: {country}, lang: {language}, page: {page}) user={user_id}")

        results = serper_service.search(
            query=query,
            country=country,
            language=language,
            page=page
        )

        if not results.get("success"):
            raise HTTPException(status_code=500, detail=results.get("error", "Search failed"))

        return JSONResponse(
            content={
                "success": True,
                "query": query,
                "results": results.get("results", {})
            },
            status_code=200
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Internet search error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post("/internet/fetch-page")
async def fetch_web_page(
    request: Request
):
    """
    Fetch and extract clean content from a web page
    Body: { "url": "https://example.com" }
    Returns: Clean page content with metadata
    Note: For anti-bot protection, pass proxy URL instead of direct URL
    """
    try:
        body = await request.json()
        url = body.get("url")
        
        if not url:
            raise HTTPException(
                status_code=400,
                detail="URL is required"
            )
        
        logger.info(f"🌐 Fetch page request: {url}")
        
        # Fetch page content via proxy function (full anti-bot protection + Playwright fallback)
        result = await web_content_fetcher.fetch_page(url)
        
        if not result.get("success"):
            error_msg = result.get("error", "Failed to fetch page")
            status_code = 400
            
            if "too large" in error_msg.lower():
                status_code = 413
            elif "timeout" in error_msg.lower():
                status_code = 408
                
            raise HTTPException(
                status_code=status_code,
                detail=error_msg
            )
        
        return JSONResponse(
            content={
                "success": True,
                "url": result.get("url"),
                "title": result.get("title"),
                "description": result.get("description"),
                "content": result.get("content"),
                "content_length": result.get("content_length")
            },
            status_code=200
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Fetch page error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch page: {str(e)}"
        )


@router.post("/internet/clean-html")
async def clean_html_content(request: Request):
    """
    Clean raw HTML/text content using BeautifulSoup.
    Accepts text extracted from the browser and strips tags, entities, etc.
    Body: { "html": "<html>...</html>", "title": "optional title" }
    Returns: { "success": true, "content": "clean text", "title": "...", "content_length": 123 }
    """
    try:
        body = await request.json()
        raw_html = body.get("html", "")
        title = body.get("title", "Untitled Web Page")

        if not raw_html or not raw_html.strip():
            raise HTTPException(status_code=400, detail="HTML content is required")

        from bs4 import BeautifulSoup
        import html as html_module
        import re

        soup = BeautifulSoup(raw_html, 'lxml')

        # Remove unwanted elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript', 'svg']):
            element.decompose()

        # Try to find main content wrapper
        main_content = soup.select_one('main') or soup.select_one('[role="main"]')

        if not main_content:
            articles = soup.select('article')
            if articles:
                combined = '\n\n'.join(a.get_text(separator='\n', strip=True) for a in articles)
                if len(combined.split()) >= 50:
                    clean_text = combined
                    main_content = None  # signal we already have text
                else:
                    main_content = soup.body or soup
            else:
                main_content = soup.select_one('.content, #content, .article, .post, .entry-content') or soup.body or soup

        if main_content is not None:
            clean_text = main_content.get_text(separator='\n', strip=True)

        # Unescape HTML entities
        clean_text = html_module.unescape(clean_text)

        # Clean whitespace
        clean_text = re.sub(r'\n\s*\n\s*\n+', '\n\n', clean_text)
        clean_text = re.sub(r' +', ' ', clean_text)
        clean_text = clean_text.strip()

        # Truncate if too long
        max_chars = 1250000
        if len(clean_text) > max_chars:
            clean_text = clean_text[:max_chars] + "\n\n[Content truncated...]"

        # Extract title from HTML if not provided
        if title == "Untitled Web Page" and soup.title and soup.title.string:
            title = soup.title.string.strip()

        logger.info(f"✅ HTML cleaned: {len(clean_text)} characters")

        return JSONResponse(
            content={
                "success": True,
                "content": clean_text,
                "title": title,
                "content_length": len(clean_text)
            },
            status_code=200
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Clean HTML error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to clean HTML: {str(e)}")


@router.post("/internet/chat")
async def chat_with_page(
    request: Request
):
    """
    AI chat based on web page content.
    Body: { 
        "url": "https://example.com", 
        "query": "user question", 
        "page_content": "...",
        "page_title": "...",
        "conversation_history": [] // Optional: array of {role: "user"|"assistant", content: "..."}
    }
    Returns: AI response based on page content
    """
    try:
        # Extract user info for billing
        user_id = getattr(request.state, 'user_id', None)
        user_email = getattr(request.state, 'user_email', None)
        
        body = await request.json()
        url = body.get("url")
        user_query = body.get("query")
        page_content = body.get("page_content", "")
        page_title = body.get("page_title", "")
        conversation_history = body.get("conversation_history", [])  # Client-side conversation tracking
        
        if not user_query:
            raise HTTPException(
                status_code=400,
                detail="Query is required"
            )
        
        # Use reranker for large content, otherwise keep full text
        if page_content and len(page_content) > LARGE_CONTENT_THRESHOLD:
            page_content = await asyncio.to_thread(_chunk_and_rerank, page_content, user_query)
        
        # Cap conversation history to last 8 messages to maintain context while preventing unbounded growth
        MAX_HISTORY_MESSAGES = 8
        original_history_length = len(conversation_history)
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            logger.info(f"⚠️ [CONVERSATION_CAP] Limiting history from {len(conversation_history)} to {MAX_HISTORY_MESSAGES} messages")
            conversation_history = conversation_history[-MAX_HISTORY_MESSAGES:]
        
        is_first_question = original_history_length == 0
        logger.info(f"💬 Internet chat request for URL: {url}, query: '{user_query}', user: {user_id}, first_question: {is_first_question}, history_length: {len(conversation_history)}")
        
        # Build static context for the LLM
        content_section = f"\n{page_content}\n" if page_content else "\n(No page content provided - use web search to answer the query)\n"
        static_page_context = f"""You are a knowledgeable assistant with web search capabilities.

PAGE CONTENT:{content_section}
---

IMPORTANT INSTRUCTIONS:
- You have access to web search to find current, real-time information
- If the page content above doesn't answer the question, USE WEB SEARCH to find the answer
- Answer questions naturally as if you have direct knowledge
- Do NOT reference "this page", "the website", or any external sources unless specifically relevant
- Provide helpful, direct answers
- When explaining concepts, speak as an expert who knows the subject matter

DIAGRAM CAPABILITY (ASCII diagrams — lightweight, no JS library):
If the user asks for a diagram, flowchart, timeline, or any visual explanation, OR if explaining a concept would benefit from a diagram, generate a plain-text ASCII diagram inside a ```ascii code block.

✅ Use Unicode box-drawing chars: ┌ ┐ └ ┘ │ ─ ├ ┤ ┬ ┴ ┼ (double: ╔ ╗ ╚ ╝ ║ ═; dashed: ╌ ╍ ╎ ╏)
✅ Use arrows: → ← ↑ ↓ ↔ ⇒ ⇐ ▶ ◀
✅ Wrap in ```ascii fences (```diagram also works).
✅ Align every box edge with spaces — NEVER use tabs.
✅ Keep labels short (1–4 words); stay under ~80 chars wide.
❌ NEVER use ```mermaid — this surface no longer renders inline Mermaid. Always use ```ascii.
❌ NEVER use HTML tags, emojis, or proportional-font characters inside the diagram.

Example:
```ascii
┌──────────┐   request    ┌──────────┐   query    ┌──────────┐
│  Client  │ ───────────▶ │   API    │ ─────────▶ │    DB    │
└──────────┘              └──────────┘            └──────────┘
```

FOLLOW-UP ENGAGEMENT:
At the end of every response, suggest 1-2 brief follow-up questions or next steps the user might find useful. Frame them as natural conversation continuations — for example:
- "Would you like me to dive deeper into [specific aspect]?"
- "I can also help you [related action]. Want me to?"
- "A related question you might find useful: [question]"
Keep suggestions short, relevant to the topic just discussed, and genuinely helpful. Do NOT repeat the same suggestion across turns."""
        
        logger.info(f"📄 Static context: {len(static_page_context)} chars, History: {len(conversation_history)} msgs, First question: {is_first_question}")
        
        # Call reply with proper caching parameters and internet search enabled
        ai_response = reply(
            prompt=user_query,
            static_context=static_page_context,
            conversation_history=conversation_history if conversation_history else None,
            enable_internet_search=True,
            user_id=user_id,
            user_email=user_email,
            max_output_tokens=50000,
            tier="large",
        )
        
        return JSONResponse(
            content={
                "success": True,
                "query": user_query,
                "response": ai_response,
                "url": url,
                "cache_hit_expected": not is_first_question  # Inform client that caching should have occurred
            },
            status_code=200
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Internet chat error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Chat failed: {str(e)}"
        )


@router.post("/document/chat")
async def chat_with_document(
    request: Request
):
    """
    AI chat based on personal document content.
    Body: { 
        "document_id": "...", 
        "query": "user question", 
        "document_content": "...",
        "document_title": "...",
        "conversation_history": [] // Optional: array of {role: "user"|"assistant", content: "..."}
    }
    Returns: AI response based on document content
    """
    try:
        body = await request.json()
        document_id = body.get("document_id")
        user_query = body.get("query")
        document_content = body.get("document_content")
        document_title = body.get("document_title", "")
        conversation_history = body.get("conversation_history", [])  # Client-side conversation tracking
        
        # Extract user info for billing
        user_id = getattr(request.state, 'user_id', None)
        user_email = getattr(request.state, 'user_email', None)
        
        # Cap conversation history to last 8 messages to maintain context while preventing unbounded growth
        MAX_HISTORY_MESSAGES = 8
        original_history_length = len(conversation_history)
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            logger.info(f"⚠️ [CONVERSATION_CAP] Limiting history from {len(conversation_history)} to {MAX_HISTORY_MESSAGES} messages")
            conversation_history = conversation_history[-MAX_HISTORY_MESSAGES:]
        
        if not user_query:
            raise HTTPException(
                status_code=400,
                detail="Query is required"
            )
        
        if not document_content:
            raise HTTPException(
                status_code=400,
                detail="Document content is required"
            )
        
        # Use reranker for large content, otherwise keep full text
        if document_content and len(document_content) > LARGE_CONTENT_THRESHOLD:
            document_content = await asyncio.to_thread(_chunk_and_rerank, document_content, user_query)
        
        is_first_question = len(conversation_history) == 0
        logger.info(f"💬 Document chat request for: {document_id}, query: '{user_query}', user: {user_id}, first_question: {is_first_question}, history_length: {len(conversation_history)}")
        
        # Build static context for the LLM
        static_doc_context = f"""You are a knowledgeable assistant with access to the following information:

{document_content}

---

IMPORTANT INSTRUCTIONS:
- Answer questions naturally as if you have direct knowledge of the content
- Do NOT reference "this document", "the document", or any external sources
- Do NOT mention document titles, IDs, file paths, or metadata unless specifically asked
- Provide helpful, direct answers based on the content above
- When explaining concepts, speak as an expert who knows the subject matter

DIAGRAM CAPABILITY (ASCII diagrams — lightweight, no JS library):
If the user asks for a diagram, flowchart, timeline, or any visual explanation, OR if explaining a concept would benefit from a diagram, generate a plain-text ASCII diagram inside a ```ascii code block.

✅ Use Unicode box-drawing chars: ┌ ┐ └ ┘ │ ─ ├ ┤ ┬ ┴ ┼ (double: ╔ ╗ ╚ ╝ ║ ═; dashed: ╌ ╍ ╎ ╏)
✅ Use arrows: → ← ↑ ↓ ↔ ⇒ ⇐ ▶ ◀
✅ Wrap in ```ascii fences (```diagram also works).
✅ Align every box edge with spaces — NEVER use tabs.
✅ Keep labels short (1–4 words); stay under ~80 chars wide.
❌ NEVER use ```mermaid — this surface no longer renders inline Mermaid. Always use ```ascii.
❌ NEVER use HTML tags, emojis, or proportional-font characters inside the diagram.

Example:
```ascii
┌──────────┐   request    ┌──────────┐   query    ┌──────────┐
│  Client  │ ───────────▶ │   API    │ ─────────▶ │    DB    │
└──────────┘              └──────────┘            └──────────┘
```

FOLLOW-UP ENGAGEMENT:
At the end of every response, suggest 1-2 brief follow-up questions or next steps the user might find useful. Frame them as natural conversation continuations — for example:
- "Would you like me to dive deeper into [specific aspect]?"
- "I can also help you [related action]. Want me to?"
- "A related question you might find useful: [question]"
Keep suggestions short, relevant to the topic just discussed, and genuinely helpful. Do NOT repeat the same suggestion across turns."""
        
        logger.info(f"📄 Static context: {len(static_doc_context)} chars, History: {len(conversation_history)} msgs, First question: {is_first_question}")
        
        # Call reply with proper caching parameters
        ai_response = reply(
            prompt=user_query,
            static_context=static_doc_context,
            conversation_history=conversation_history if conversation_history else None,
            user_id=user_id,
            user_email=user_email,
            max_output_tokens=50000,
            tier="large",
        )
        
        return JSONResponse(
            content={
                "success": True,
                "query": user_query,
                "response": ai_response,
                "document_id": document_id,
                "cache_hit_expected": not is_first_question  # Inform client that caching should have occurred
            },
            status_code=200
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Document chat error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Chat failed: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════════════
# STREAMING CHAT ENDPOINTS FOR READER
# ═══════════════════════════════════════════════════════════════════════════════════════

from fastapi.responses import StreamingResponse
from streaming_response import stream_llm_response, StreamEventType, StreamEvent

async def generate_reader_chat_stream(
    prompt: str,
    static_context: str,
    conversation_history: List[Dict[str, str]] = None,
    source_type: str = "reader",
    enable_internet_search: bool = False,
    user_id: str = None,
    user_email: str = None,
):
    """
    Generate SSE stream for reader chat responses.
    Uses the streaming_response module for real-time LLM output.
    """
    
    # Build the full system prompt with context
    system_prompt = static_context
    
    # Build conversation history list for the LLM
    history = []
    
    # Add conversation history if present
    if conversation_history:
        for msg in conversation_history:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            history.append({"role": role, "content": content})
    
    try:
        # Use the streaming response generator
        async for event in stream_llm_response(
            prompt=prompt,
            system=system_prompt,
            conversation_history=history,
            model=None,
            enable_internet_search=enable_internet_search,
            user_id=user_id,
            user_email=user_email,
            max_output_tokens=50000,
            tier="large",
        ):
            yield event.to_sse()
            
    except Exception as e:
        logger.error(f"❌ Streaming error: {e}")
        error_event = StreamEvent(
            event_type=StreamEventType.ERROR,
            data={"message": str(e)}
        )
        yield error_event.to_sse()


@router.post("/internet/chat/stream")
async def chat_with_page_stream(request: Request):
    """
    STREAMING version of internet page chat.
    Returns Server-Sent Events (SSE) stream for real-time response.
    Uses LLM with internet search tool for live web data.
    """
    try:
        user_id = getattr(request.state, 'user_id', None)
        user_email = getattr(request.state, 'user_email', None)

        body = await request.json()
        url = body.get("url")
        user_query = body.get("query")
        page_content = body.get("page_content", "")
        page_title = body.get("page_title", "")
        conversation_history = body.get("conversation_history", [])
        
        if not user_query:
            raise HTTPException(status_code=400, detail="Query is required")
        
        # Cap conversation history
        MAX_HISTORY_MESSAGES = 8
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            conversation_history = conversation_history[-MAX_HISTORY_MESSAGES:]
        
        logger.info(f"🌊 Streaming internet chat (internet search enabled) for: {url}, query: '{user_query}' | user: {user_id}")
        
        # Use reranker for large content, otherwise keep full text
        truncated_content = (await asyncio.to_thread(_chunk_and_rerank, page_content, user_query)) if page_content else '(no page content provided — rely on internet search)'
        static_page_context = f"""You are a helpful AI research assistant browsing the web.
The user is viewing a webpage and asking you questions about it or related topics.
You have internet search capabilities — use them to find current, accurate information.
First check if the provided page content answers the question, then supplement with web search if needed.
Be concise, factual, and cite sources where helpful.

The user is viewing this webpage:
Title: {page_title}
URL: {url}

Page Content (may be partial):
{truncated_content}

DIAGRAM CAPABILITY (ASCII diagrams — lightweight, no JS library):
If the user asks for a diagram or visual explanation, generate a plain-text ASCII diagram using Unicode box-drawing chars (┌─┐ │ └─┘ ├ ┬ ┼) and arrows (→ ← ↑ ↓ ▶).
✅ Wrap in ```ascii fences (```diagram also works).
✅ Align columns with spaces only; keep labels short (1–4 words).
❌ NEVER use ```mermaid — this surface no longer renders inline Mermaid. Always use ```ascii.

FOLLOW-UP ENGAGEMENT:
At the end of every response, suggest 1-2 brief follow-up questions or next steps the user might find useful. Frame them as natural conversation continuations — for example:
- "Would you like me to dive deeper into [specific aspect]?"
- "I can also help you [related action]. Want me to?"
- "A related question you might find useful: [question]"
Keep suggestions short, relevant to the topic just discussed, and genuinely helpful. Do NOT repeat the same suggestion across turns."""

        return StreamingResponse(
            generate_reader_chat_stream(
                prompt=user_query,
                static_context=static_page_context,
                conversation_history=conversation_history,
                source_type="internet",
                enable_internet_search=True,
                user_id=user_id,
                user_email=user_email,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Streaming internet chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Streaming chat failed: {str(e)}")


@router.post("/document/chat/stream")
async def chat_with_document_stream(request: Request):
    """
    STREAMING version of document chat.
    Returns Server-Sent Events (SSE) stream for real-time response.
    """
    try:
        user_id = getattr(request.state, 'user_id', None)
        user_email = getattr(request.state, 'user_email', None)

        body = await request.json()
        document_id = body.get("document_id")
        user_query = body.get("query")
        document_content = body.get("document_content")
        document_title = body.get("document_title", "")
        conversation_history = body.get("conversation_history", [])
        
        if not user_query:
            raise HTTPException(status_code=400, detail="Query is required")
        
        if not document_content:
            raise HTTPException(status_code=400, detail="Document content is required")
        
        # Cap conversation history
        MAX_HISTORY_MESSAGES = 8
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            conversation_history = conversation_history[-MAX_HISTORY_MESSAGES:]
        
        logger.info(f"🌊 Streaming document chat for: {document_id}, query: '{user_query}' | user: {user_id}")
        
        # Use reranker for large content, otherwise keep full text
        truncated_content = (await asyncio.to_thread(_chunk_and_rerank, document_content, user_query)) if document_content else ''
        
        # Build static context (same as non-streaming version)
        static_doc_context = f"""You are a helpful assistant answering questions based on document content.
Be accurate, concise, and only use information from the provided content.

Document: {document_title}
Document ID: {document_id}

Document Content:
{truncated_content}

DIAGRAM CAPABILITY (ASCII diagrams — lightweight, no JS library):
If the user asks for a diagram or visual explanation, generate a plain-text ASCII diagram using Unicode box-drawing chars (┌─┐ │ └─┘ ├ ┬ ┼) and arrows (→ ← ↑ ↓ ▶).
✅ Wrap in ```ascii fences (```diagram also works).
✅ Align columns with spaces only; keep labels short (1–4 words).
❌ NEVER use ```mermaid — this surface no longer renders inline Mermaid. Always use ```ascii.

FOLLOW-UP ENGAGEMENT:
At the end of every response, suggest 1-2 brief follow-up questions or next steps the user might find useful. Frame them as natural conversation continuations — for example:
- "Would you like me to dive deeper into [specific aspect]?"
- "I can also help you [related action]. Want me to?"
- "A related question you might find useful: [question]"
Keep suggestions short, relevant to the topic just discussed, and genuinely helpful. Do NOT repeat the same suggestion across turns."""

        return StreamingResponse(
            generate_reader_chat_stream(
                prompt=user_query,
                static_context=static_doc_context,
                conversation_history=conversation_history,
                source_type="document",
                enable_internet_search=False,
                user_id=user_id,
                user_email=user_email,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Streaming document chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Streaming chat failed: {str(e)}")