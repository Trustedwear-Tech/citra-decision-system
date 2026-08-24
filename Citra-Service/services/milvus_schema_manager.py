# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# ============================  Milvus Schema Validator  =============================
# Purpose: Validate Milvus collection exists and has correct schema on startup
# Note: Collection creation is handled by scripts/setup_milvus_schema.py
# This module only validates that setup has been completed correctly
# ----------------------------------------------------------------------------------------

import logging
from typing import Optional, Dict, Any
from config.milvus_config import (
    get_collection_name,
    get_milvus_uri,
    get_milvus_api_key,
    get_dense_vector_dim,
    is_hybrid_search_enabled,
    get_milvus_client
)

logger = logging.getLogger(__name__)

class MilvusSchemaManager:
    """
    Validates Milvus collection schema on service startup.
    
    NOTE: This class does NOT create collections. Use scripts/setup_milvus_schema.py
    to create the collection with proper hybrid search support.
    """
    
    def __init__(self):
        self.collection_name = get_collection_name()
        self.uri = get_milvus_uri()
        self.token = get_milvus_api_key()
        self.dense_dim = get_dense_vector_dim()
        self.hybrid_enabled = is_hybrid_search_enabled()
        
        # Initialize Milvus client (singleton)
        self.client = get_milvus_client()
        
        logger.info(f"🔧 Milvus Schema Validator initialized for collection: {self.collection_name}")
    
    def collection_exists(self) -> bool:
        """Check if collection already exists"""
        try:
            collections = self.client.list_collections(timeout=5)
            exists = self.collection_name in collections
            logger.info(f"📋 Collection '{self.collection_name}' exists: {exists}")
            return exists
        except Exception as e:
            logger.error(f"❌ Failed to check collection existence: {e}")
            return False
    
    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        """Get basic information about existing collection"""
        try:
            if not self.collection_exists():
                return None
            
            # Get collection description
            desc = self.client.describe_collection(self.collection_name, timeout=5)
            
            logger.info(f"📊 Collection info: {len(desc.get('fields', []))} fields")
            return desc
        except Exception as e:
            logger.error(f"❌ Failed to get collection info: {e}")
            return None
    
    def validate_schema(self) -> bool:
        """
        Validate that collection has required fields and correct configuration.
        Returns True if valid, False otherwise.
        """
        try:
            info = self.get_collection_info()
            if not info:
                return False
            
            fields = info.get("fields", [])
            field_names = [f.get("name") for f in fields]
            
            # Check required fields
            required_fields = ["primary_key", "dense_vector"]
            missing_fields = [f for f in required_fields if f not in field_names]
            
            if missing_fields:
                logger.error(f"❌ Missing required fields: {missing_fields}")
                return False
            
            # Check dense vector dimension
            dense_field = next((f for f in fields if f.get("name") == "dense_vector"), None)
            if dense_field:
                dim = dense_field.get("params", {}).get("dim", 0)
                if dim != self.dense_dim:
                    logger.error(f"❌ Dense vector dimension mismatch: {dim} != {self.dense_dim}")
                    return False
            
            # Check for sparse vector if hybrid is enabled
            if self.hybrid_enabled and "sparse_vector" not in field_names:
                logger.error(f"❌ Hybrid search enabled but sparse_vector field missing")
                logger.error(f"   Run: python scripts/setup_milvus_schema.py --force")
                return False
            
            logger.info(f"✅ Collection schema validation passed")
            logger.info(f"   - Fields: {', '.join(field_names)}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Schema validation failed: {e}")
            return False
    
    def validate_collection_ready(self) -> bool:
        """
        Validate that collection exists and is properly configured.
        
        NOTE: Does NOT create collections. Use scripts/setup_milvus_schema.py
        to create the collection with proper hybrid search support.
        
        Returns:
            True if collection exists and valid, False otherwise
        """
        try:
            # Check if collection exists
            if not self.collection_exists():
                logger.error(f"❌ Collection '{self.collection_name}' does not exist")
                logger.error(f"")
                logger.error(f"🔧 CREATE COLLECTION FIRST:")
                logger.error(f"   python scripts/setup_milvus_schema.py")
                logger.error(f"")
                return False
            
            # Validate schema
            logger.info(f"📋 Validating collection schema...")
            is_valid = self.validate_schema()
            
            if not is_valid:
                logger.error(f"❌ Schema validation failed")
                logger.error(f"🔧 RECREATE COLLECTION:")
                logger.error(f"   python scripts/setup_milvus_schema.py --force")
            
            return is_valid
        
        except Exception as e:
            logger.error(f"❌ Collection validation failed: {e}")
            return False

# Global schema manager instance
_schema_manager: Optional[MilvusSchemaManager] = None

def get_schema_manager() -> MilvusSchemaManager:
    """Get or create global schema manager instance"""
    global _schema_manager
    if _schema_manager is None:
        _schema_manager = MilvusSchemaManager()
    return _schema_manager

def initialize_milvus_schema() -> bool:
    """
    Validate Milvus collection on service startup.
    
    NOTE: Does NOT create collections. Collection must be created first using:
          python scripts/setup_milvus_schema.py
    
    Returns:
        True if collection exists and valid, False otherwise
    """
    try:
        manager = get_schema_manager()
        return manager.validate_collection_ready()
    except Exception as e:
        logger.error(f"❌ Milvus schema validation failed: {e}")
        return False

if __name__ == "__main__":
    # Test schema validation
    logging.basicConfig(level=logging.INFO)
    
    print("Testing Milvus Schema Validator...")
    print("=" * 60)
    
    manager = MilvusSchemaManager()
    
    print("\nValidating collection...")
    is_ready = manager.validate_collection_ready()
    
    if is_ready:
        print("\n✅ Collection is ready for use")
    else:
        print("\n❌ Collection not ready - see errors above")
        print("\nTo create collection, run:")
        print("   python scripts/setup_milvus_schema.py")
    
    print("=" * 60)
