# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Utility helpers for producing consistent chunk metadata across services."""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator, field_validator


class ChunkMetadata(BaseModel):
    """Typed representation of the metadata stored alongside each chunk."""

    document_id: str
    chunk_index: int
    total_chunks: int
    topic_or_filename: str = ""
    file_type: str = "unknown"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    text: Optional[str] = None
    folder_id: Optional[str] = None
    is_enterprise: bool = False
    entity_id: Optional[str] = None
    department: Optional[str] = None
    user_id: Optional[str] = None
    page_number: Optional[int] = None
    paragraph_number: Optional[int] = None

    class Config:
        populate_by_name = True
        extra = "allow"

    @field_validator('is_enterprise', mode='before')
    @classmethod
    def validate_is_enterprise(cls, v):
        if isinstance(v, str):
            return v.lower() in ('true', '1', 'yes', 'on')
        return bool(v)

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure required aliases exist without legacy fallbacks."""
        if values is None:
            return {}

        topic_or_filename = values.get("topic_or_filename")
        if topic_or_filename is None:
            topic_or_filename = ""
        values["topic_or_filename"] = topic_or_filename

        # Remove any legacy keys if callers still send them
        values.pop("topic", None)

        return values

    def to_metadata_dict(self) -> Dict[str, Any]:
        """Return a Mongo/Milvus friendly dict with optional fields removed."""
        metadata = self.model_dump(exclude_none=True)
        metadata.setdefault("topic_or_filename", self.topic_or_filename or "")
        return metadata

    @classmethod
    def from_components(
        cls,
        document_id: str,
        chunk_index: int,
        total_chunks: int,
        topic_or_filename: str,
        file_type: str,
        created_at: str,
        text: Optional[str],
        folder_id: Optional[str],
        is_enterprise: bool,
        entity_id: Optional[str],
        department: Optional[str],
        user_id: Optional[str],
        page_number: Optional[int],
        paragraph_number: Optional[int]
    ) -> "ChunkMetadata":
        """Factory that keeps topic/title aliases aligned and enforces defaults."""

        canonical_topic = topic_or_filename or ""
        canonical_created_at = created_at or datetime.utcnow().isoformat()

        # Personal documents keep folder attribution, enterprise documents do not
        normalized_folder_id = None if is_enterprise else folder_id
        enterprise_user_id = user_id if is_enterprise else None
        normalized_entity_id = entity_id if is_enterprise else None
        normalized_department = department if is_enterprise else None

        return cls(
            document_id=document_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            topic_or_filename=canonical_topic,
            file_type=file_type or "unknown",
            created_at=canonical_created_at,
            text=text,
            folder_id=normalized_folder_id,
            is_enterprise=is_enterprise,
            entity_id=normalized_entity_id,
            department=normalized_department,
            user_id=enterprise_user_id,
            page_number=page_number,
            paragraph_number=paragraph_number
        )


class UnifiedMetadataSchema:
    """Facade that preserves the original helpers while delegating to ChunkMetadata."""

    @staticmethod
    def create_base_metadata(
        document_id: str,
        namespace_id: str,
        chunk_index: int,
        total_chunks: int,
        text: Optional[str],
        topic_or_filename: str,
        file_type: str,
        created_at: str,
        page_number: int = 1,
        paragraph_number: Optional[int] = None
    ) -> Dict[str, Any]:
        """Backward-compatible wrapper that now produces typed metadata."""

        metadata = ChunkMetadata.from_components(
            document_id=document_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            topic_or_filename=topic_or_filename,
            file_type=file_type,
            created_at=created_at,
            text=text,
            folder_id=None,
            is_enterprise=False,
            entity_id=None,
            department=None,
            user_id=namespace_id,
            page_number=page_number,
            paragraph_number=paragraph_number
        )

        return metadata.to_metadata_dict()

    @staticmethod
    def create_full_metadata(
        document_id: str,
        user_id: str,
        chunk_index: int,
        total_chunks: int,
        text: str,
        topic_or_filename: str,
        file_type: str,
        created_at: str,
        page_number: int = 1,
        paragraph_number: Optional[int] = None,
        folder_id: Optional[str] = None,
        is_enterprise: bool = False,
        entity_id: Optional[str] = None,
        department: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create complete metadata dict for Milvus and MongoDB storage."""

        metadata = ChunkMetadata.from_components(
            document_id=document_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            topic_or_filename=topic_or_filename,
            file_type=file_type,
            created_at=created_at,
            text=text,
            folder_id=folder_id,
            is_enterprise=is_enterprise,
            entity_id=entity_id,
            department=department,
            user_id=user_id,
            page_number=page_number,
            paragraph_number=paragraph_number
        )

        return metadata.to_metadata_dict()


class MetadataConstants:
    """Constants for standardized metadata field names."""

    DOCUMENT_ID = "document_id"
    USER_ID = "user_id"
    CHUNK_INDEX = "chunk_index"
    TOTAL_CHUNKS = "total_chunks"
    TEXT = "text"
    TOPIC_OR_FILENAME = "topic_or_filename"
    FILE_TYPE = "file_type"
    CREATED_AT = "created_at"
    IS_ENTERPRISE = "is_enterprise"
    ENTITY_ID = "entity_id"
    DEPARTMENT = "department"
    FOLDER_ID = "folder_id"
    PAGE_NUMBER = "page_number"
    PARAGRAPH_NUMBER = "paragraph_number"


class MetadataValidator:
    """Validation helpers that leverage the typed model for consistency."""

    @staticmethod
    def is_valid_enterprise_metadata(metadata: Dict[str, Any]) -> bool:
        """Enterprise payloads must include user_id and document id."""

        if not metadata.get(MetadataConstants.IS_ENTERPRISE):
            return True
        return all(
            metadata.get(field)
            for field in (MetadataConstants.USER_ID, MetadataConstants.DOCUMENT_ID)
        )

    @staticmethod
    def is_valid_base_metadata(metadata: Dict[str, Any]) -> bool:
        """Base payloads require identifiers, counts, and types."""

        required_fields = (
            MetadataConstants.DOCUMENT_ID,
            MetadataConstants.CHUNK_INDEX,
            MetadataConstants.TOTAL_CHUNKS,
            MetadataConstants.CREATED_AT,
            MetadataConstants.FILE_TYPE,
        )
        return all(metadata.get(field) is not None for field in required_fields)

    @staticmethod
    def validate_metadata(metadata: Dict[str, Any]) -> tuple[bool, list[str]]:
        """Return validation status and any error messages."""

        try:
            ChunkMetadata.model_validate(metadata)
            return True, []
        except ValidationError as exc:
            errors = [
                "{}: {}".format(".".join(str(segment) for segment in err["loc"]), err["msg"])
                for err in exc.errors()
            ]
            return False, errors
