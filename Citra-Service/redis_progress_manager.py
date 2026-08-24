#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Redis Progress Manager for Citra AI Service
Handles distributed progress tracking with automatic fallback to local cache
Uses centralized CacheManager for Redis + local cache fallback
"""

import json
import logging
import time
import os
import uuid
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from contextlib import contextmanager
from enum import Enum

from citra_cache import get_cache_manager

logger = logging.getLogger(__name__)

class ProgressStatus(Enum):
    """Progress status enumeration"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"

class ProgressType(Enum):
    """Progress type enumeration"""
    DOCUMENT_UPLOAD = "document_upload"
    DEEP_RESEARCH = "deep_research"

class RedisProgressManager:
    """
    Progress manager with automatic Redis/Local cache fallback
    Uses centralized CacheManager for resilient caching
    """
    
    def __init__(self):
        # Use centralized cache manager (handles Redis + local fallback)
        self.cache = get_cache_manager()
        self.enabled = True  # Always enabled (uses fallback if needed)
        
        # Cache settings
        self.progress_ttl = 300  # 5 minutes default TTL for active progress
        self.completed_ttl = 360  # 6 minutes TTL for completed progress
        
        # Key prefixes
        self.document_prefix = "progress:document:"
        self.research_prefix = "progress:research:"
        self.lock_prefix = "lock:"
        
        # Lock settings
        self.lock_timeout = 30  # 30 seconds lock timeout
        
        logger.info(f"🔄 Progress Manager initialized with {self.cache.get_stats()['cache_type']} cache")
    
    def _get_key(self, progress_type: ProgressType, task_id: str) -> str:
        """Generate cache key for progress tracking"""
        if progress_type == ProgressType.DOCUMENT_UPLOAD:
            return f"{self.document_prefix}{task_id}"
        elif progress_type == ProgressType.DEEP_RESEARCH:
            return f"{self.research_prefix}{task_id}"
        else:
            raise ValueError(f"Unknown progress type: {progress_type}")
    
    @contextmanager
    def distributed_lock(self, resource_id: str, timeout: Optional[int] = None):
        """
        Distributed/thread-safe lock using cache manager
        Automatically uses Redis distributed locks or local thread locks
        """
        lock_timeout = timeout or self.lock_timeout
        
        with self.cache.lock(resource_id, timeout=lock_timeout) as acquired:
            if acquired:
                logger.debug(f"🔒 Acquired lock for: {resource_id}")
            else:
                logger.debug(f"🔒 Failed to acquire lock for: {resource_id}")
            yield acquired
    
    def update_progress(
        self, 
        progress_type: ProgressType,
        task_id: str, 
        stage: str, 
        progress: int, 
        status: ProgressStatus = ProgressStatus.PROCESSING,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update progress in cache with automatic fallback
        Uses CacheManager which handles Redis/local cache automatically
        """
        # Use distributed lock to prevent race conditions
        lock_resource_id = f"progress_update:{progress_type.value}:{task_id}"
        
        with self.distributed_lock(lock_resource_id, timeout=5) as acquired:
            if not acquired:
                logger.warning(f"Could not acquire lock for progress update: {task_id}")
                return False
            
            try:
                # Prepare progress data
                progress_data = {
                    "task_id": task_id,
                    "progress_type": progress_type.value,
                    "stage": stage,
                    "progress": min(100, max(0, progress)),  # Clamp to 0-100
                    "status": status.value,
                    "message": message or f"{stage} - {progress}%",
                    "updated_at": datetime.utcnow().isoformat(),
                    "metadata": metadata or {}
                }
                
                # Add completion timestamp if completed
                if status in [ProgressStatus.COMPLETED, ProgressStatus.ERROR, ProgressStatus.CANCELLED]:
                    progress_data["completed_at"] = datetime.utcnow().isoformat()
                
                # Store in cache (Redis or local)
                key = self._get_key(progress_type, task_id)
                self.cache.setex(
                    key,
                    self.completed_ttl if status in [ProgressStatus.COMPLETED, ProgressStatus.ERROR, ProgressStatus.CANCELLED] else self.progress_ttl,
                    json.dumps(progress_data)
                )
                
                # Progress logging is handled by individual upload managers (document_manager, video_upload, etc.)
                # No need to duplicate logging here
                
                return True
                
            except Exception as e:
                logger.error(f"Failed to update progress for {task_id}: {e}")
                return False
    
    def get_progress(self, progress_type: ProgressType, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get current progress from cache with automatic fallback
        Uses CacheManager which handles Redis/local cache automatically
        """
        try:
            key = self._get_key(progress_type, task_id)
            progress_json = self.cache.get(key)
            
            if progress_json:
                progress_data = json.loads(progress_json)
                # Add time since last update
                if "updated_at" in progress_data:
                    updated_at = datetime.fromisoformat(progress_data["updated_at"])
                    progress_data["time_since_update"] = (datetime.utcnow() - updated_at).total_seconds()
                
                return progress_data
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get progress for {task_id}: {e}")
            return None
    
    def clear_progress(self, progress_type: ProgressType, task_id: str) -> bool:
        """
        Clear progress data from cache with automatic fallback
        Uses CacheManager which handles Redis/local cache automatically
        """
        try:
            key = self._get_key(progress_type, task_id)
            deleted = self.cache.delete(key)
            
            if deleted:
                logger.info(f"[{task_id}] 🗑️ Progress data cleared from cache")
            
            return bool(deleted)
            
        except Exception as e:
            logger.error(f"Failed to clear progress for {task_id}: {e}")
            return False
    
    def list_active_progress(self, progress_type: ProgressType) -> List[Dict[str, Any]]:
        """
        List all active progress entries of a specific type
        Uses CacheManager which handles Redis/local cache automatically
        """
        try:
            if progress_type == ProgressType.DOCUMENT_UPLOAD:
                pattern = f"{self.document_prefix}*"
            elif progress_type == ProgressType.DEEP_RESEARCH:
                pattern = f"{self.research_prefix}*"
            else:
                return []
            
            keys = self.cache.keys(pattern)
            active_progress = []
            
            for key in keys:
                try:
                    progress_json = self.cache.get(key)
                    if progress_json:
                        progress_data = json.loads(progress_json)
                        active_progress.append(progress_data)
                except Exception as e:
                    logger.warning(f"Failed to parse progress data for key {key}: {e}")
            
            return active_progress
            
        except Exception as e:
            logger.error(f"Failed to list active progress: {e}")
            return []
    
    def cleanup_expired_progress(self) -> int:
        """
        Cleanup expired progress entries.
        Redis handles TTL automatically; nothing to do here.
        """
        return 0
    
    def get_connection_pool_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring"""
        return self.cache.get_stats()
    
    def health_check(self) -> Dict[str, Any]:
        """
        Comprehensive health check for cache system
        Works with both Redis and local cache
        """
        health_status = {
            "status": "healthy",
            "enabled": True,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        try:
            # Get cache stats
            cache_stats = self.cache.get_stats()
            health_status["cache_stats"] = cache_stats
            
            # Test basic operations
            test_key = f"health_check:{uuid.uuid4()}"
            test_value = "health_check_value"
            
            # Test SET
            start_time = time.time()
            set_result = self.cache.setex(test_key, 10, test_value)
            set_time = (time.time() - start_time) * 1000
            
            # Test GET
            start_time = time.time()
            get_result = self.cache.get(test_key)
            get_time = (time.time() - start_time) * 1000
            
            # Test DELETE
            start_time = time.time()
            del_result = self.cache.delete(test_key)
            del_time = (time.time() - start_time) * 1000
            
            health_status["operations"] = {
                "set": {"successful": bool(set_result), "time_ms": round(set_time, 2)},
                "get": {"successful": get_result == test_value, "time_ms": round(get_time, 2)},
                "delete": {"successful": bool(del_result), "time_ms": round(del_time, 2)}
            }
            
            # Test locking
            lock_test_key = f"health_check_lock:{uuid.uuid4()}"
            start_time = time.time()
            
            with self.distributed_lock(lock_test_key, timeout=5) as acquired:
                lock_time = (time.time() - start_time) * 1000
                health_status["lock_test"] = {
                    "successful": acquired,
                    "time_ms": round(lock_time, 2)
                }
            
            # Check if any operations failed
            all_operations_successful = (
                health_status["operations"]["set"]["successful"] and
                health_status["operations"]["get"]["successful"] and
                health_status["operations"]["delete"]["successful"] and
                health_status["lock_test"]["successful"]
            )
            
            if not all_operations_successful:
                health_status["status"] = "degraded"
                health_status["message"] = "Some cache operations failed"
            
        except Exception as e:
            health_status.update({
                "status": "unhealthy",
                "error": str(e),
                "message": f"Cache health check failed: {e}"
            })
            logger.error(f"Cache health check failed: {e}")
        
        return health_status

# Global instance
_progress_manager = None

def get_progress_manager() -> RedisProgressManager:
    """Get the global progress manager instance"""
    global _progress_manager
    if _progress_manager is None:
        _progress_manager = RedisProgressManager()
    return _progress_manager

# Convenience functions for backward compatibility
def update_document_progress(document_id: str, stage: str, progress: int, status: ProgressStatus = ProgressStatus.PROCESSING, message: str = None, metadata: Dict[str, Any] = None):
    """Update document upload progress"""
    manager = get_progress_manager()
    return manager.update_progress(ProgressType.DOCUMENT_UPLOAD, document_id, stage, progress, status, message, metadata)

def get_document_progress(document_id: str) -> Optional[Dict[str, Any]]:
    """Get document upload progress"""
    manager = get_progress_manager()
    return manager.get_progress(ProgressType.DOCUMENT_UPLOAD, document_id)

def clear_document_progress(document_id: str):
    """Clear document upload progress"""
    manager = get_progress_manager()
    return manager.clear_progress(ProgressType.DOCUMENT_UPLOAD, document_id)

def update_research_progress(research_id: str, stage: str, progress: int, status: ProgressStatus = ProgressStatus.PROCESSING, message: str = None, metadata: Dict[str, Any] = None):
    """Update deep research progress"""
    manager = get_progress_manager()
    return manager.update_progress(ProgressType.DEEP_RESEARCH, research_id, stage, progress, status, message, metadata)

def get_research_progress(research_id: str) -> Optional[Dict[str, Any]]:
    """Get deep research progress"""
    manager = get_progress_manager()
    return manager.get_progress(ProgressType.DEEP_RESEARCH, research_id)

def clear_research_progress(research_id: str):
    """Clear deep research progress"""
    manager = get_progress_manager()
    return manager.clear_progress(ProgressType.DEEP_RESEARCH, research_id)
