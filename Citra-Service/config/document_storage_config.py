# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# ============================  Document Storage Configuration  =============================
# Purpose: Configuration for document file storage
# Uses S3-compatible storage (MinIO for self-hosted, AWS S3 for cloud).
# --------------------------------------------------------------------------------------

import os
import logging
from typing import Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class DocumentStorageConfig:
    """Configuration class for document storage behavior"""
    
    def __init__(self):
        # MongoDB content storage is always enabled
        self.store_content_in_mongodb = True
        
        # File storage is always S3-compatible (MinIO or AWS S3)
        self.store_in_s3 = True
        
        # Bucket configuration
        self.endpoint_url = os.getenv("BUCKET_ENDPOINT_URL", "")
        self.bucket_name = os.getenv("BUCKET_NAME", "citra-documents")
        self.access_key = os.getenv("BUCKET_ACCESS_KEY", "")
        self.secret_key = os.getenv("BUCKET_SECRET_KEY", "")
        self.region = os.getenv("BUCKET_REGION", "us-east-1")
        
        # Log configuration on initialization
        self._log_configuration()
    
    def _log_configuration(self):
        """Log current storage configuration"""
        mode = "MinIO" if self.endpoint_url else "AWS S3"
        logger.info("📄 Document Storage Configuration:")
        logger.info(f"   - Backend: {mode}")
        logger.info(f"   - Bucket: {self.bucket_name}")
        logger.info(f"   - Store content in MongoDB: {self.store_content_in_mongodb}")
        if self.endpoint_url:
            logger.info(f"   - Endpoint: {self.endpoint_url}")
    
    def is_minio(self) -> bool:
        """Check if using MinIO (self-hosted S3-compatible storage)"""
        return bool(self.endpoint_url)
    
    def should_store_content_in_mongodb(self) -> bool:
        """Check if document content should be stored in MongoDB"""
        return self.store_content_in_mongodb
    
    def should_store_in_s3(self) -> bool:
        """Check if document should be stored in S3/MinIO"""
        return self.store_in_s3
    
    def get_storage_metadata(self) -> Dict[str, Any]:
        """Get metadata about storage configuration for document records"""
        return {
            "storage_mode": "minio" if self.is_minio() else "s3",
            "content_in_mongodb": True,
            "file_in_s3": True,
            "s3_endpoint": self.endpoint_url if self.endpoint_url else None,
        }
    
    def validate_configuration(self) -> bool:
        """Validate that configuration is reasonable"""
        return True
    
    def get_ui_capabilities(self) -> Dict[str, bool]:
        """Get capabilities that should be available in UI based on storage configuration"""
        return {
            "can_view_content": True,
            "can_download_file": True,
            "can_search_content": True,
            "has_content_storage": True,
            "has_file_storage": True,
            "metadata_only": False
        }

# Global configuration instance
document_storage_config = DocumentStorageConfig()

# Convenience functions for easy access
def should_store_content_in_mongodb() -> bool:
    """Quick check if content should be stored in MongoDB"""
    return document_storage_config.should_store_content_in_mongodb()

def should_store_in_s3() -> bool:
    """Quick check if files should be stored in S3/MinIO"""
    return document_storage_config.should_store_in_s3()

def get_storage_metadata() -> Dict[str, Any]:
    """Get storage metadata for document records"""
    return document_storage_config.get_storage_metadata()

def get_ui_capabilities() -> Dict[str, bool]:
    """Get UI capabilities based on storage configuration"""
    return document_storage_config.get_ui_capabilities()

# Configuration validation
if __name__ == "__main__":
    # Test configuration
    config = DocumentStorageConfig()
    print("Storage Configuration Test:")
    print(f"MongoDB content storage: {config.should_store_content_in_mongodb()}")
    print(f"S3/MinIO storage: {config.should_store_in_s3()}")
    print(f"Is MinIO: {config.is_minio()}")
    print(f"UI capabilities: {config.get_ui_capabilities()}")
    print(f"Storage metadata: {config.get_storage_metadata()}")
