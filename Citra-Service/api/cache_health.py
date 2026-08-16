# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Cache Health Check API
Provides endpoints to monitor cache system (Redis + local fallback)
"""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any

from citra_cache import get_cache_manager
from redis_progress_manager import get_progress_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cache", tags=["cache-health"])


class CacheHealthResponse(BaseModel):
    """Cache health check response"""
    cache_manager_health: Dict[str, Any]
    progress_manager_health: Dict[str, Any]


@router.get("/health")
async def check_cache_health() -> CacheHealthResponse:
    """
    Check cache system health
    Returns status for both CacheManager and ProgressManager
    """
    try:
        # Check cache manager
        cache = get_cache_manager()
        cache_stats = cache.get_stats()
        
        # Check progress manager
        progress = get_progress_manager()
        progress_health = progress.health_check()
        
        return CacheHealthResponse(
            cache_manager_health=cache_stats,
            progress_manager_health=progress_health
        )
        
    except Exception as e:
        logger.error(f"Error checking cache health: {e}")
        return CacheHealthResponse(
            cache_manager_health={"error": str(e)},
            progress_manager_health={"error": str(e)}
        )


@router.get("/stats")
async def get_cache_stats() -> Dict[str, Any]:
    """Get detailed cache statistics"""
    try:
        cache = get_cache_manager()
        progress = get_progress_manager()
        
        return {
            "cache_manager": cache.get_stats(),
            "progress_manager": progress.get_connection_pool_stats()
        }
        
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        return {"error": str(e)}


@router.post("/test")
async def test_cache_operations() -> Dict[str, Any]:
    """
    Test cache operations (set, get, delete)
    Useful for debugging cache fallback behavior
    """
    try:
        cache = get_cache_manager()
        test_key = "test:cache:health"
        test_value = "test_value_123"
        
        # Test SET
        set_result = cache.setex(test_key, 60, test_value)
        
        # Test GET
        get_result = cache.get(test_key)
        
        # Test DELETE
        delete_result = cache.delete(test_key)
        
        return {
            "set_successful": bool(set_result),
            "get_successful": get_result == test_value,
            "get_value": get_result,
            "delete_successful": bool(delete_result),
            "cache_type": cache.get_stats()["cache_type"],
            "message": "All cache operations completed successfully"
        }
        
    except Exception as e:
        logger.error(f"Error testing cache operations: {e}")
        return {
            "error": str(e),
            "message": "Cache operations test failed"
        }
