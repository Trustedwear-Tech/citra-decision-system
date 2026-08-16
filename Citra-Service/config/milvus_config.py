# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# ============================  Milvus Vector Database Configuration  =============================
# Purpose: Environment-specific Milvus collection configuration
# Supports: Self-hosted Milvus (default) and Zilliz Cloud (legacy)
# Collections: dev, test, prod
# Features: Dense vectors with COSINE similarity search
# --------------------------------------------------------------------------------------

import os
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class MilvusConfig:
    """Configuration class for Milvus/Zilliz Cloud connections"""
    
    def __init__(self):
        # Load connection details from .env
        # Priority: MILVUS_URI (self-hosted) > ZILLIZ_CLOUD_URI (legacy cloud)
        self.uri = os.getenv("MILVUS_URI") or os.getenv("ZILLIZ_CLOUD_URI")
        self.api_key = os.getenv("MILVUS_TOKEN") or os.getenv("ZILLIZ_CLOUD_API_KEY", "")
        self.is_self_hosted = bool(os.getenv("MILVUS_URI"))
        
        # Get collection name from .env (defaults to dev)
        self.collection_name = os.getenv("MILVUS_COLLECTION", "citra")
        
        # Get environment for logging purposes
        self.environment = os.getenv("ENVIRONMENT", "dev").lower()
        
        # Vector dimensions — must match the embedding model's output dim.
        # Driven by EMBEDDING_DIMENSION so the whole stack moves together.
        # Keep at 768 (Qwen3-Embedding via MRL truncation, text-embedding-3-small, etc.).
        self.dense_vector_dim = int(os.getenv("EMBEDDING_DIMENSION", "768"))
        self.enable_hybrid_search = False  # BM25 disabled - using pure dense COSINE search
        
        # Index parameters
        self.dense_index_type = "AUTOINDEX"  # Zilliz Cloud auto-optimizes
        self.dense_metric_type = "COSINE"
        
        # ── Search threshold configuration (pure dense COSINE scores) ──
        # Primary similarity thresholds. NOTE: the legacy `saas` Milvus
        # collection has been retired; vault file metadata is now read from
        # MongoDB (`structured_file_metadata` + `unstructured_file_metadata`),
        # so SAAS_SIMILARITY_THRESHOLD is no longer used.
        self.personal_similarity_threshold = float(os.getenv('PERSONAL_SIMILARITY_THRESHOLD', '0.3'))
        self.absolute_min_score = float(os.getenv('MILVUS_ABSOLUTE_MIN_SCORE', '0.10'))
        
        # Multi-signal relevance rejection
        self.top_score_minimum = float(os.getenv('TOP_SCORE_MINIMUM', '0.50'))
        self.top_score_standout = float(os.getenv('TOP_SCORE_STANDOUT', '0.15'))
        
        # Single-result floor
        self.single_result_min_floor = float(os.getenv('SINGLE_RESULT_MIN_FLOOR', '0.40'))
        
        # Score compression detection
        self.score_spread_threshold = float(os.getenv('SCORE_SPREAD_THRESHOLD', '0.03'))
        self.score_low_std_multiplier = float(os.getenv('SCORE_LOW_STD_MULTIPLIER', '1.5'))
        
        # Log configuration on initialization
        self._log_configuration()
    
    def _log_configuration(self):
        """Log current Milvus configuration"""
        logger.info("🗄️ Milvus/Zilliz Cloud Configuration:")
        logger.info(f"   - Environment: {self.environment}")
        logger.info(f"   - Collection: {self.collection_name}")
        logger.info(f"   - Mode: {'Self-hosted Milvus' if self.is_self_hosted else 'Zilliz Cloud'}")
        logger.info(f"   - URI: {self.uri[:50]}..." if self.uri else "   - URI: Not configured")
        logger.info(f"   - Dense vector dimension: {self.dense_vector_dim}")
        logger.info(f"   - Hybrid search: {'Enabled' if self.enable_hybrid_search else 'Disabled'}")
        logger.info(f"   - Token/API Key: {'Configured' if self.api_key else 'Not set (OK for self-hosted)'}")
        logger.info(f"   - Personal threshold: {self.personal_similarity_threshold}")
        logger.info(f"   - Absolute min score: {self.absolute_min_score}")
        logger.info(f"   - Single result floor: {self.single_result_min_floor}")
        logger.info(f"   - Top score minimum: {self.top_score_minimum}")
    
    def get_collection_name(self) -> str:
        """Get the collection name for current environment"""
        return self.collection_name
    
    def get_uri(self) -> str:
        """Get Zilliz Cloud URI"""
        return self.uri
    
    def get_api_key(self) -> str:
        """Get Zilliz Cloud API key"""
        return self.api_key
    
    def get_vector_dim(self) -> int:
        """Get dense vector dimension (OpenAI text-embedding-3-small)"""
        return self.dense_vector_dim
    
    def get_dense_vector_dim(self) -> int:
        """Get dense vector dimension (OpenAI text-embedding-3-small)"""
        return self.dense_vector_dim
    
    def get_environment(self) -> str:
        """Get current environment"""
        return self.environment
    
    def is_hybrid_search_enabled(self) -> bool:
        """Check if hybrid search is enabled"""
        return self.enable_hybrid_search
    
    def get_collection_schema(self) -> Dict[str, Any]:
        """
        Get collection schema definition for hybrid search.
        
        Returns schema with:
        - id: Primary key (string)
        - dense_vector: Dense embeddings from OpenAI text-embedding-3-small (768 dim, COSINE)
        - sparse_vector: Sparse embeddings from BM25 (IP metric) - if hybrid enabled
        - Dynamic fields for metadata
        """
        schema = {
            "collection_name": self.collection_name,
            "fields": [
                {
                    "field_name": "id",
                    "data_type": "VarChar",
                    "is_primary": True,
                    "max_length": 255
                },
                {
                    "field_name": "dense_vector",
                    "data_type": "FloatVector",
                    "dim": self.dense_vector_dim
                }
            ],
            "enable_dynamic_field": True  # Allow arbitrary metadata fields
        }
        
        # Add sparse vector field if hybrid search is enabled
        if self.enable_hybrid_search:
            schema["fields"].append({
                "field_name": "sparse_vector",
                "data_type": "SparseFloatVector"
            })
        
        return schema
    
    def get_index_params(self) -> list:
        """
        Get index parameters for collection.
        
        Returns list of index configurations:
        - Dense vector: AUTOINDEX with COSINE metric
        - Sparse vector: SPARSE_INVERTED_INDEX with IP metric (if hybrid enabled)
        """
        indexes = [
            {
                "field_name": "dense_vector",
                "index_type": self.dense_index_type,
                "metric_type": self.dense_metric_type
            }
        ]
        
        # Add sparse index if hybrid search is enabled
        if self.enable_hybrid_search:
            indexes.append({
                "field_name": "sparse_vector",
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": self.sparse_metric_type
            })
        
        return indexes
    
    def get_search_thresholds(self) -> Dict[str, Any]:
        """Get all search threshold values as a dictionary"""
        return {
            "personal_similarity_threshold": self.personal_similarity_threshold,
            "absolute_min_score": self.absolute_min_score,
            "top_score_minimum": self.top_score_minimum,
            "top_score_standout": self.top_score_standout,
            "single_result_min_floor": self.single_result_min_floor,
            "score_spread_threshold": self.score_spread_threshold,
            "score_low_std_multiplier": self.score_low_std_multiplier,
        }

    def validate_configuration(self) -> bool:
        """Validate that configuration is complete and valid"""
        if not self.uri:
            logger.error("❌ Milvus URI is not configured. Set MILVUS_URI (self-hosted) or ZILLIZ_CLOUD_URI (cloud).")
            return False
        
        # API key/token is required for Zilliz Cloud but optional for self-hosted Milvus
        if not self.is_self_hosted and not self.api_key:
            logger.error("❌ Zilliz Cloud API key not configured. Set MILVUS_TOKEN or ZILLIZ_CLOUD_API_KEY.")
            return False
        
        if not self.collection_name:
            logger.error("❌ MILVUS_COLLECTION is not configured")
            return False
        
        logger.info("✅ Milvus configuration is valid")
        return True
    
    def get_connection_params(self) -> Dict[str, Any]:
        """Get connection parameters for MilvusClient"""
        params = {"uri": self.uri}
        # Only include token if set (self-hosted Milvus may not need auth)
        if self.api_key:
            params["token"] = self.api_key
        return params

# Global configuration instance
milvus_config = MilvusConfig()

# Convenience functions for easy access
def get_collection_name() -> str:
    """Get the collection name for current environment"""
    return milvus_config.get_collection_name()

def get_milvus_uri() -> str:
    """Get Zilliz Cloud URI"""
    return milvus_config.get_uri()

def get_milvus_api_key() -> str:
    """Get Zilliz Cloud API key"""
    return milvus_config.get_api_key()

def get_vector_dim() -> int:
    """Get dense vector dimension"""
    return milvus_config.get_dense_vector_dim()

def get_dense_vector_dim() -> int:
    """Get dense vector dimension"""
    return milvus_config.get_dense_vector_dim()

def get_environment() -> str:
    """Get current environment"""
    return milvus_config.get_environment()

def is_hybrid_search_enabled() -> bool:
    """Check if hybrid search is enabled"""
    return milvus_config.is_hybrid_search_enabled()

def get_collection_schema() -> Dict[str, Any]:
    """Get collection schema for hybrid search"""
    return milvus_config.get_collection_schema()

def get_index_params() -> list:
    """Get index parameters for collection"""
    return milvus_config.get_index_params()

def get_connection_params() -> Dict[str, Any]:
    """Get connection parameters for MilvusClient"""
    return milvus_config.get_connection_params()


# ── Singleton MilvusClient ──────────────────────────────────────────────
# Reuses a single gRPC channel instead of creating a new connection per request.
# PID tracking ensures a fresh client after Gunicorn fork (gRPC channels are NOT fork-safe).

_milvus_client: Optional["MilvusClient"] = None
_milvus_client_pid: Optional[int] = None
_milvus_client_lock = __import__("threading").Lock()

def _is_milvus_channel_alive(client) -> bool:
    """Best-effort check if the MilvusClient's underlying gRPC channel is usable."""
    try:
        from pymilvus import connections as _conns
        alias = getattr(client, '_using', None)
        if alias is None:
            return False
        # Check if the connection alias still exists in the registry
        if not _conns.has_connection(alias):
            return False
        # Check if the GrpcHandler's channel is still present (set to None after close)
        handler = _conns._fetch_handler(alias)
        if getattr(handler, '_channel', None) is None:
            return False
        # Check the gRPC channel connectivity state if accessible
        # TRANSIENT_FAILURE or SHUTDOWN means it needs recreation
        try:
            import grpc
            channel = getattr(handler, '_channel', None)
            if channel is not None:
                state = channel.get_state(try_to_connect=False)
                if state in (grpc.ChannelConnectivity.SHUTDOWN, grpc.ChannelConnectivity.TRANSIENT_FAILURE):
                    logger.info("🗄️ gRPC channel in %s state, will recreate", state.name)
                    return False
        except Exception:
            pass  # grpc state check is best-effort
        return True
    except Exception:
        return False

def get_milvus_client():
    """Return a module-level singleton MilvusClient, creating it on first call.
    
    Tracks the PID so that after a Gunicorn fork the inherited (stale) gRPC
    channel is discarded and a fresh connection is established in the worker.
    Also detects closed/dead gRPC channels and recreates automatically.
    """
    global _milvus_client, _milvus_client_pid
    current_pid = os.getpid()
    need_recreate = (
        _milvus_client is None
        or _milvus_client_pid != current_pid
        or not _is_milvus_channel_alive(_milvus_client)
    )
    if need_recreate:
        with _milvus_client_lock:
            # Double-check after acquiring lock
            need_recreate = (
                _milvus_client is None
                or _milvus_client_pid != current_pid
                or not _is_milvus_channel_alive(_milvus_client)
            )
            if need_recreate:
                # Close stale client inherited from parent process or dead channel
                if _milvus_client is not None:
                    try:
                        _milvus_client.close()
                    except Exception:
                        pass
                    _milvus_client = None
                from pymilvus import MilvusClient
                params = milvus_config.get_connection_params()
                _milvus_client = MilvusClient(
                    uri=params["uri"],
                    # Empty/absent token ⇒ no-auth (self-hosted Milvus Standalone);
                    # get_connection_params omits the key when MILVUS_TOKEN is unset.
                    token=params.get("token", ""),
                    timeout=30,
                    keepalive_time_ms=10000,
                    keepalive_timeout_ms=5000,
                    keepalive_permit_without_calls=True,
                )
                _milvus_client_pid = current_pid
                logger.info("🗄️ Singleton MilvusClient created (pid=%d)", current_pid)
    return _milvus_client

def close_milvus_client():
    """Close the singleton MilvusClient (call during app shutdown)."""
    global _milvus_client, _milvus_client_pid
    if _milvus_client is not None:
        try:
            _milvus_client.close()
            logger.info("🗄️ Singleton MilvusClient closed")
        except Exception as e:
            logger.warning(f"🗄️ MilvusClient close error: {e}")
        finally:
            _milvus_client = None
            _milvus_client_pid = None

def recreate_milvus_client():
    """Force-recreate the singleton MilvusClient (call after gRPC failures)."""
    global _milvus_client, _milvus_client_pid
    with _milvus_client_lock:
        if _milvus_client is not None:
            try:
                _milvus_client.close()
            except Exception:
                pass
            _milvus_client = None
        from pymilvus import MilvusClient
        params = milvus_config.get_connection_params()
        _milvus_client = MilvusClient(
            uri=params["uri"],
            token=params.get("token", ""),
            timeout=30,
            keepalive_time_ms=10000,
            keepalive_timeout_ms=5000,
            keepalive_permit_without_calls=True,
        )
        _milvus_client_pid = os.getpid()
        logger.info("🗄️ Singleton MilvusClient recreated after failure (pid=%d)", _milvus_client_pid)
    return _milvus_client

def create_new_milvus_client():
    """Create a brand-new, short-lived MilvusClient (NOT the singleton).

    Use this for Milvus **.query()** calls so that each query gets its own
    gRPC channel and cannot be affected by singleton recreation in another
    coroutine.  The caller is responsible for closing the client when done.
    """
    from pymilvus import MilvusClient
    params = milvus_config.get_connection_params()
    client = MilvusClient(
        uri=params["uri"],
        token=params.get("token", ""),
        timeout=30,
        keepalive_time_ms=10000,
        keepalive_timeout_ms=5000,
        keepalive_permit_without_calls=True,
    )
    logger.debug("🗄️ Created new short-lived MilvusClient for query (pid=%d)", os.getpid())
    return client

def validate_milvus_config() -> bool:
    """Validate Milvus configuration"""
    return milvus_config.validate_configuration()

def get_search_thresholds() -> Dict[str, Any]:
    """Get all search threshold values"""
    return milvus_config.get_search_thresholds()

# Configuration validation
if __name__ == "__main__":
    # Test configuration
    print("Milvus Configuration Test:")
    print(f"Environment: {milvus_config.get_environment()}")
    print(f"Collection: {milvus_config.get_collection_name()}")
    print(f"URI: {milvus_config.get_uri()}")
    print(f"API Key: {'Configured' if milvus_config.get_api_key() else 'Missing'}")
    print(f"Dense vector dimension: {milvus_config.get_dense_vector_dim()}")
    print(f"Hybrid search enabled: {milvus_config.is_hybrid_search_enabled()}")
    print(f"Configuration valid: {milvus_config.validate_configuration()}")
    print(f"Connection params: {milvus_config.get_connection_params()}")
    print(f"\nCollection Schema:")
    import json
    print(json.dumps(milvus_config.get_collection_schema(), indent=2))
    print(f"\nIndex Parameters:")
    print(json.dumps(milvus_config.get_index_params(), indent=2))
