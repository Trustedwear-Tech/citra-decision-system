# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# ============================  Milvus Chunk Models  =============================
# Purpose: MongoDB models for storing Milvus vector ID mappings
# Features: Document-to-vector mapping for reliable deletion and tracking
# ----------------------------------------------------------------------------------------

from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, model_validator


class MilvusChunkModel(BaseModel):
    """MongoDB model for storing Milvus vector ID mappings.
    
    Links document_id to vector IDs in Milvus for batch deletion and tracking.
    Stores metadata for UI attribution and citation.
    """

    id: Optional[str] = Field(alias="_id", default=None)
    document_id: str
    user_id: str
    vector_ids: List[str] = Field(default_factory=list)
    total_vectors: int

    # Metadata mirrors what is stored on each vector
    topic_or_filename: str = ""
    file_type: str = ""
    folder_id: Optional[str] = None

    # Optional per-chunk details for improved UI attribution
    page_number: Optional[int] = None
    paragraph_number: Optional[int] = None
    chunk_index: Optional[int] = None
    total_chunks: Optional[int] = None

    # Backward compatibility for individual vector documents
    vector_id: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        extra = "allow"
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            ObjectId: lambda v: str(v)
        }

    @model_validator(mode="before")
    @classmethod
    def _normalize_topic(cls, values):
        if values is None:
            return {}

        topic_or_filename = values.get("topic_or_filename")
        if topic_or_filename is None:
            topic_or_filename = ""
        values["topic_or_filename"] = topic_or_filename

        values.pop("topic", None)

        return values
