# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# ============================  Document Chunk Models  =============================
# Purpose: MongoDB models for chunked document storage and retrieval
# Features: Chunked storage for large documents, pagination support
# ----------------------------------------------------------------------------------------

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from bson import ObjectId

class MongoDbChunkModel(BaseModel):
    """Model for storing document chunks in MongoDB
    
    ✅ TRUE HYBRID ARCHITECTURE: Schema + pagination in MongoDB, text in Milvus
    Cross-reference between MongoDB and Milvus using vector_id field.
    
    Storage Strategy:
    - MongoDB: Schema, pagination, statistics (NO TEXT) - lightweight documents
    - Milvus: Vector embeddings + full text content (single source of truth)
    - Cross-reference: vector_id links MongoDB chunk → Milvus vector
    
    Root-level fields kept for indexing/querying:
    - document_id, chunk_index, user_id, folder_id (query fields)
    - start_page, end_page, total_pages (page range)
    - file_size, content_length, word_count (statistics)
    - vector_id, text_location (TRUE HYBRID fields)
    
    Metadata field (WITHOUT text):
    - metadata['topic_or_filename'] - document title or filename
    - metadata['file_type'] - file extension
    - metadata['created_at'] - timestamp
    - NO text field (stored in Milvus only)
    """
    id: Optional[str] = Field(alias="_id", default=None)
    document_id: str  # Parent document ID
    chunk_index: int  # 0-based chunk index
    
    # ✅ TRUE HYBRID: Cross-reference fields
    vector_id: Optional[str] = None  # Milvus vector ID (e.g., "doc123_0")
    text_location: str = "mongodb"   # "mongodb" | "Milvus" | "both" (migration flag)
    
    start_page: int  # Starting page number (1-based)
    end_page: int    # Ending page number (inclusive)
    total_pages: int # Total pages in this chunk
    
    # Content statistics (not duplicate content)
    content_length: Optional[int] = None  # Length of content
    total_chunks: Optional[int] = None  # Total number of chunks in the document
    
    # Essential fields for document organization
    user_id: str  # User device ID for document ownership
    folder_id: Optional[str] = None  # Folder ID for organization
    
    # Document metadata fields
    file_size: Optional[int] = None  # Original file size
    chunk_size: Optional[int] = None  # Size of this chunk
    word_count: Optional[int] = None  # Word count for this chunk
    
    # Processing metadata
    processing_status: Optional[str] = None  # Processing status
    
    # Storage metadata
    has_vectors: Optional[bool] = None  # Whether vectors exist for this chunk
    updated_at: Optional[datetime] = None  # Last update timestamp
    
    # UI compatibility fields (restored for UI functionality)
    topic_or_filename: Optional[str] = None  # Unified field combining topic and filename
    file_type: Optional[str] = None  # File type for context (e.g., pdf, docx)
    
    # Enterprise fields for organizational document management
    is_enterprise: Optional[bool] = None  # Whether this is an enterprise document
    entity_id: Optional[str] = None       # Enterprise entity identifier
    department: Optional[str] = None      # Department or organizational unit
    
    # ✅ SINGLE SOURCE OF TRUTH: All content and citation data stored here
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            ObjectId: lambda v: str(v)
        }

# Backward compatibility alias
DocumentChunkModel = MongoDbChunkModel

class DocumentMetadataModel(BaseModel):
    """Updated document metadata model with chunk support"""
    id: Optional[str] = Field(alias="_id", default=None)
    document_id: str
    file_type: str
    total_pages: int
    total_chunks: int
    chunk_size: int = 1  # Pages per chunk (1 = page-level granularity)
    file_size: int  # Original file size in bytes
    user_id: str
    folder_id: Optional[str] = None
    topic_or_filename: Optional[str] = None  # Unified field for topic and filename
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    processing_status: str = "pending"  # pending, processing, completed, failed
    extracted_metadata: Dict[str, Any] = Field(default_factory=dict)
    # Search metadata flags
    content_in_mongodb: bool = True  # Content stored in MongoDB for search
    file_in_azure_container: bool = True  # Original file stored in Azure
    fileUrl: Optional[str] = None  # Azure download URL if available
    # Enterprise fields for Azure blob path resolution
    is_enterprise: Optional[bool] = False  # Whether this is an enterprise document
    entity_id: Optional[str] = None  # Enterprise entity identifier
    
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            ObjectId: lambda v: str(v)
        }

class ChunkResponse(BaseModel):
    """Response model for chunk data"""
    chunk_index: int
    start_page: int
    end_page: int
    total_pages: int
    content: str
    
    # Citation fields for source attribution (kept lean for UI consumption)
    topic_or_filename: Optional[str] = None
    file_type: Optional[str] = None
    
    metadata: Dict[str, Any]

class DocumentMetadataResponse(BaseModel):
    """Response model for document metadata"""
    document_id: str
    file_type: str
    total_pages: int
    total_chunks: int
    chunk_size: int
    processing_status: str
    created_at: str
    updated_at: str
    topic_or_filename: Optional[str] = None
    file_size: int
    # UI compatibility - download URL
    fileUrl: Optional[str] = None
    # Enterprise/personal document indicator
    is_enterprise: bool = False
    # Formatted date for UI
    utc_date: Optional[str] = None
    # Entity information
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    # Knowledge Graph processing status
    kg_processed: bool = False
    kg_processed_at: Optional[str] = None

class PaginatedChunksResponse(BaseModel):
    """Response model for paginated chunks"""
    chunks: List[ChunkResponse]
    pagination: Dict[str, Any]

class DocumentListResponse(BaseModel):
    """Response model for document list with pagination"""
    documents: List[DocumentMetadataResponse]
    pagination: Dict[str, Any]
