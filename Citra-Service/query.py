# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

from typing import List, Dict, Any, Optional, Union
from fastapi import APIRouter, Request, Body, HTTPException, status, Form
from fastapi.responses import JSONResponse, StreamingResponse

import logging
import json
import threading
import time
import os
import hashlib
from dataclasses import dataclass
from collections import defaultdict
from queue import Queue
import uuid
import re

import os
import time
import asyncio

# Import configuration
from citra_ai_config import config

import openai
import pymongo
import pytz
from dateparser import parse as dt_parse
from datetime import datetime, timedelta
from pytz import timezone

# Import LLM functions for all calls
from llm_oss import llm_call, llm_call_streaming, llm_call_with_internet


def _resolve_temp(explicit: Optional[float], profession: Optional[str] = None) -> float:
    """
    Resolve LLM temperature for chat-style calls. Precedence:
        1) explicit caller value
        2) llm.llm_defaults.temperature_for() — enterprise default = factual (0.2)
        3) legacy 0.7 fallback
    `profession` is accepted for backward compatibility but ignored — the
    enterprise posture is factual for everyone.
    """
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    try:
        from llm.llm_defaults import temperature_for
        return temperature_for()
    except Exception:
        return 0.7

from whisper_proxy import transcribe_audio as whisper_transcribe_audio
from qwen_ocr_proxy import extract_text_from_image
# Milvus import removed - migrated to Milvus/Zilliz
# from sentence_transformers import SentenceTransformer # Commented out

# Vision/OCR handled by qwen_ocr_proxy
# Legacy flag removed

from utils import embed_text, embed_texts_batch
from CRUD_utils import store_chat, retrieve_chat, update_message_pair
# chunk_embed_and_upsert removed - deprecated, use EnhancedChunkedDocumentService instead
from chat import get_chat_session_by_id

# Import MongoDB manager for pricing configs
from citra_mongo import get_sync_database

# Import usage service for cached pricing
from middleware.credit_check_middleware import get_usage_service

# Import persona utilities for persona-enhanced queries
from persona import generate_persona_system_prompt

# Import for direct API calls to text extraction services
import requests
import base64
import mimetypes

# Domain classifier removed - not used in multi-hop RAG approach

from citra_auth import get_secure_user_id, get_user_email

# Configure logging
logger = logging.getLogger(__name__)

# Intent system removed - using multi-hop RAG approach

# Import agentic RAG orchestrator system (replaces legacy multi-hop)
# Using lazy import to avoid circular dependency with orchestrator
AGENTIC_RAG_AVAILABLE = None  # Will be checked when needed
QueryOrchestrator = None  # Will be imported when needed

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PRICING HELPER FUNCTIONS - Fetch pricing from cached MongoDB configuration
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•















# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# Legacy multi-hop RAG system removed (2025-09-25)
# âœ… MIGRATED: Multi-hop functionality now handled by MultiHopAgent in agentic_rag.agents
# âœ… MIGRATED: Strategy planning now handled by StrategyPlanner in agentic_rag.planner  
# âœ… MIGRATED: Retrieval coordination now handled by QueryOrchestrator with LangGraph
MULTI_HOP_RAG_AVAILABLE = False  # Deprecated - functionality moved to agentic orchestrator

# Usage tracking removed for enterprise licensing model
from citra_auth import get_secure_user_id, get_user_email

# Credit checking integration for pay-as-you-go billing
from middleware.credit_check_middleware import check_user_credits
from middleware import InsufficientCreditsError

# BACKGROUND OPERATION MANAGEMENT SYSTEM
# Enhanced with monitoring and optimization

import os
import threading
import time
from queue import Queue
# The vault half of this subsystem is gone: DocumentProcessingOperation,
# execute_document_processing_operation, _async_process_document_storage
# (-> document_manager + EnhancedChunkedDocumentService) and
# add_document_to_processing_queue post-processed personal uploads and had no
# callers left. What remains is chat persistence, which /query/stream still
# uses via run_background_post_operations.

from collections import defaultdict

# Configuration for background workers
MAX_WORKERS_PER_DEVICE = int(os.getenv('MAX_WORKERS_PER_DEVICE', '1'))  # Keep at 1 for sequential processing
WORKER_IDLE_TIMEOUT = int(os.getenv('WORKER_IDLE_TIMEOUT', '300'))  # 5 minutes default
OPERATION_TIMEOUT = int(os.getenv('OPERATION_TIMEOUT', '1800'))  # 30 minutes max per operation

# Global background operation management with enhanced monitoring
background_operation_locks = defaultdict(threading.Lock)  # Per-device locks for sequential processing
background_operation_queues = defaultdict(Queue)  # Per-device operation queues
background_workers = {}  # Per-device worker threads
worker_stats = defaultdict(lambda: {
    'operations_processed': 0,
    'total_processing_time': 0,
    'last_operation_time': 0,
    'queue_size': 0,
    'worker_started': 0,
    'operation_types': {
        # subscription_tracking and usage_tracking removed for enterprise licensing
    }
})  # Per-device worker statistics with operation type tracking

# Global tracking for pending chat session operations to allow synchronization
pending_session_operations = defaultdict(set)  # chat_session_id -> set of operation_ids
pending_operations_lock = threading.Lock()

@dataclass
class BackgroundOperation:
    """Represents a background operation to be processed"""
    operation_id: str
    user_id: str
    chat_session_id: str
    query_text: str
    existing_bio: str
    answer: str
    summary: str
    edit_mode: bool
    message_pair_id: str
    chats: List[Dict[str, str]]
    timestamp: float
    suggestions: Optional[List[Dict[str, Any]]] = None

@dataclass
class PostProcessingOperation:
    """Represents post-processing operations for MongoDB operations"""
    operation_id: str
    operation_type: str  # "document_storage", "concept_map_update"
    user_id: str
    document_id: str
    data: Dict[str, Any]  # Flexible data storage for different operation types
    timestamp: float

def get_or_create_background_worker(user_id: str):
    """Get or create a background worker thread for a specific device with enhanced monitoring"""
    
    # Ensure queues are initialized
    if user_id not in background_operation_queues:
        logging.info(f"ðŸ”§ QUEUE_INIT: Creating new queue for device: {user_id}")
        # Force queue creation (defaultdict will auto-create)
        queue_size = background_operation_queues[user_id].qsize()
        logging.info(f"âœ… QUEUE_INIT: Queue created for device {user_id} - initial size: {queue_size}")
    else:
        logging.debug(f"ðŸ“‹ QUEUE_EXISTS: Queue already exists for device: {user_id}")
    
    if user_id not in worker_stats:
        worker_stats[user_id] = {
            'operations_processed': 0,
            'total_processing_time': 0,
            'worker_started': 0,
            'queue_size': 0,
            'last_operation_time': 0,
            'operation_types': {}
        }
        logging.info(f"ðŸ“Š STATS_INIT: Initialized worker stats for device: {user_id}")
    
    if user_id not in background_workers or not background_workers[user_id].is_alive():
        # Update worker stats
        worker_stats[user_id]['worker_started'] = time.time()
        worker_stats[user_id]['queue_size'] = background_operation_queues[user_id].qsize()
        
        logging.info(f"ðŸš€ WORKER_CREATE: Creating new background worker for device: {user_id}")
        logging.info(f"   - Current queue size: {worker_stats[user_id]['queue_size']}")
        logging.info(f"   - Worker thread name: bg_worker_{user_id}")
        
        worker = threading.Thread(
            target=process_background_operations_for_device,
            args=(user_id,),
            daemon=True,
            name=f"bg_worker_{user_id}"
        )
        background_workers[user_id] = worker
        worker.start()
        
        logging.info(f"âœ… WORKER_STARTED: Background worker started for device: {user_id}")
        logging.info(f"   - Thread ID: {worker.ident}")
        logging.info(f"   - Thread alive: {worker.is_alive()}")
        logging.info(f"   - Queue size: {worker_stats[user_id]['queue_size']}")
    else:
        # Update queue size for existing worker
        old_queue_size = worker_stats[user_id]['queue_size']
        worker_stats[user_id]['queue_size'] = background_operation_queues[user_id].qsize()
        new_queue_size = worker_stats[user_id]['queue_size']
        
        logging.debug(f"ðŸ“Š WORKER_EXISTS: Background worker active for device: {user_id}")
        logging.debug(f"   - Queue size changed: {old_queue_size} -> {new_queue_size}")
        logging.debug(f"   - Worker thread: {background_workers[user_id].name}")
    
    return background_workers[user_id]

def process_background_operations_for_device(user_id: str):
    """Process background operations for a specific device in order with enhanced monitoring and timeout handling"""
    from queue import Empty  # Import Empty exception for proper handling
    import signal
    
    queue = background_operation_queues[user_id]
    lock = background_operation_locks[user_id]
    
    logging.info(f"ðŸš€ WORKER_START: Background worker thread started for device: {user_id}")
    logging.info(f"   - Thread name: {threading.current_thread().name}")
    logging.info(f"   - Thread ID: {threading.current_thread().ident}")
    logging.info(f"   - Initial queue size: {queue.qsize()}")
    logging.info(f"   - Worker idle timeout: {WORKER_IDLE_TIMEOUT}s")
    logging.info(f"   - Operation timeout: {OPERATION_TIMEOUT}s")
    
    operation_count = 0
    
    def timeout_handler(signum, frame):
        logging.error(f"â° TIMEOUT: Operation timeout for device {user_id} - operation exceeded {OPERATION_TIMEOUT}s")
        raise TimeoutError(f"Operation timeout after {OPERATION_TIMEOUT} seconds")
    
    while True:
        try:
            logging.debug(f"ðŸ” QUEUE_WAIT: Worker {user_id} waiting for next operation (timeout: {WORKER_IDLE_TIMEOUT}s)")
            
            # Get the next operation (blocks if queue is empty)
            operation = queue.get(timeout=WORKER_IDLE_TIMEOUT)
            
            if operation is None:  # Shutdown signal
                logging.info(f"ðŸ›‘ SHUTDOWN_SIGNAL: Worker {user_id} received shutdown signal")
                break
                
            operation_count += 1
            # Update queue size stats
            worker_stats[user_id]['queue_size'] = queue.qsize()
            operation_start_time = time.time()
            
            logging.info(f"ðŸ”„ OPERATION_START: Processing operation #{operation_count} for device: {user_id}")
            logging.info(f"   - Operation ID: {operation.operation_id}")
            logging.info(f"   - Operation type: {getattr(operation, 'operation_type', 'unknown')}")
            logging.info(f"   - Queue remaining: {worker_stats[user_id]['queue_size']}")
            logging.info(f"   - Document ID: {getattr(operation, 'document_id', 'N/A')}")
            
            with lock:
                logging.debug(f"ðŸ”’ LOCK_ACQUIRED: Worker {user_id} acquired processing lock")
                
                try:
                    # Set operation timeout to prevent runaway processes - only in main thread
                    timeout_set = False
                    try:
                        if hasattr(signal, 'SIGALRM') and threading.current_thread() is threading.main_thread():  # Unix systems and main thread only
                            signal.signal(signal.SIGALRM, timeout_handler)
                            signal.alarm(OPERATION_TIMEOUT)
                            timeout_set = True
                            logging.debug(f"â° TIMEOUT_SET: Operation timeout set to {OPERATION_TIMEOUT}s")
                    except ValueError as signal_error:
                        logging.debug(f"â° TIMEOUT_SKIP: Cannot set signal in background thread: {signal_error}")
                    
                    logging.info(f"ðŸ”„ OPERATION_EXECUTE: Executing operation {operation.operation_id}")
                    execute_background_operation(operation)
                    logging.info(f"âœ… OPERATION_SUCCESS: Operation {operation.operation_id} executed successfully")
                    
                    # Clear timeout if it was set
                    if timeout_set:
                        signal.alarm(0)
                        logging.debug(f"â° TIMEOUT_CLEARED: Operation timeout cleared")
                        
                except TimeoutError as e:
                    logging.error(f"â° OPERATION_TIMEOUT: Operation {operation.operation_id} timed out for device {user_id}: {e}")
                except Exception as e:
                    logging.error(f"âŒ OPERATION_ERROR: Operation {operation.operation_id} failed for device {user_id}: {e}", exc_info=True)
                finally:
                    # Always clear timeout and update stats
                    if hasattr(signal, 'SIGALRM'):
                        signal.alarm(0)
                    
                    operation_time = time.time() - operation_start_time
                    worker_stats[user_id]['operations_processed'] += 1
                    worker_stats[user_id]['total_processing_time'] += operation_time
                    worker_stats[user_id]['last_operation_time'] = operation_time
                    
                    # Track operation type if it's a PostProcessingOperation
                    if isinstance(operation, PostProcessingOperation):
                        op_type = operation.operation_type
                        if op_type in worker_stats[user_id]['operation_types']:
                            worker_stats[user_id]['operation_types'][op_type] += 1
                        else:
                            worker_stats[user_id]['operation_types'][op_type] = 1
                    
                    logging.info(f"âœ… OPERATION_COMPLETE: Operation {operation.operation_id} completed in {operation_time:.3f}s")
                    
                    # Remove operation from pending session tracking if applicable
                    chat_session_id = getattr(operation, 'chat_session_id', None)
                    if chat_session_id:
                        with pending_operations_lock:
                            if operation.operation_id in pending_session_operations[chat_session_id]:
                                pending_session_operations[chat_session_id].remove(operation.operation_id)
                                logging.debug(f"ðŸ”„ SYNC: Removed operation {operation.operation_id} from pending set for session {chat_session_id}")
                            if not pending_session_operations[chat_session_id]:
                                del pending_session_operations[chat_session_id]
                    
                    logging.info(f"   - Total operations processed: {worker_stats[user_id]['operations_processed']}")
                    logging.info(f"   - Average processing time: {worker_stats[user_id]['total_processing_time'] / worker_stats[user_id]['operations_processed']:.3f}s")
                    
                logging.debug(f"ðŸ”“ LOCK_RELEASED: Worker {user_id} released processing lock")
                    
            queue.task_done()
            logging.debug(f"âœ… TASK_DONE: Marked task as done for device {user_id}")
            
        except Empty:
            # Normal timeout after idle period - shutdown worker gracefully
            uptime = time.time() - worker_stats[user_id]['worker_started']
            avg_time = worker_stats[user_id]['total_processing_time'] / max(1, worker_stats[user_id]['operations_processed'])
            
            logging.info(f"ðŸ˜´ WORKER_IDLE_TIMEOUT: Worker for {user_id} idle for {WORKER_IDLE_TIMEOUT}s - shutting down gracefully")
            logging.info(f"ðŸ“Š WORKER_FINAL_STATS for {user_id}:")
            logging.info(f"   - Total operations processed: {worker_stats[user_id]['operations_processed']}")
            logging.info(f"   - Total processing time: {worker_stats[user_id]['total_processing_time']:.2f}s")
            logging.info(f"   - Average operation time: {avg_time:.2f}s")
            logging.info(f"   - Worker uptime: {uptime:.2f}s")
            logging.info(f"   - Operation types: {dict(worker_stats[user_id]['operation_types'])}")
            break
        except Exception as e:
            logging.error(f"âŒ WORKER_ERROR: Critical error in background worker for {user_id}: {e}", exc_info=True)
                
    # Cleanup worker reference and all per-device data to free memory
    if user_id in background_workers:
        worker_thread = background_workers[user_id]
        del background_workers[user_id]
        logging.info(f"ðŸ—‘ï¸ WORKER_CLEANUP: Removed worker reference for device: {user_id}")
        logging.info(f"   - Final thread state: alive={worker_thread.is_alive()}")
    
    final_uptime = time.time() - worker_stats[user_id].get('worker_started', 0)
    logging.info(f"ðŸ›‘ WORKER_SHUTDOWN: Background worker stopped for device: {user_id}")
    logging.info(f"   - Final uptime: {final_uptime:.2f}s")
    logging.info(f"   - Final operation count: {operation_count}")
    logging.info(f"   - Thread name: {threading.current_thread().name}")

    # Evict per-device dicts so they don't leak memory after disconnect
    worker_stats.pop(user_id, None)
    background_operation_locks.pop(user_id, None)
    background_operation_queues.pop(user_id, None)

def execute_background_operation(operation):
    """Execute a single background operation - handles chat, document, and post-processing operations"""
    
    operation_type = "unknown"
    if isinstance(operation, PostProcessingOperation):
        operation_type = f"post_processing({operation.operation_type})"
    else:
        operation_type = "chat_operation"
    
    logging.info(f"ðŸ”§ EXECUTE_START: Starting execution of {operation_type}")
    logging.info(f"   - Operation ID: {getattr(operation, 'operation_id', 'N/A')}")
    
    # Handle post-processing operations (MongoDB, concept map)
    if isinstance(operation, PostProcessingOperation):
        logging.info(f"âš™ï¸ POST_PROC_EXEC: Executing post-processing operation: {operation.operation_type}")
        execute_post_processing_operation(operation)
        logging.info(f"âœ… POST_PROC_COMPLETE: Post-processing operation {operation.operation_type} completed")
        return
    
    # Handle chat operations (existing logic)
    # Check if MongoDB chat history is enabled
    enable_mongodb_chat_history = os.getenv('ENABLE_MONGODB_CHAT_HISTORY', 'false').lower() == 'true'
    
    if not enable_mongodb_chat_history:
        logging.info(f" Background operation {operation.operation_id} skipped - ENABLE_MONGODB_CHAT_HISTORY is disabled")
        return
    
    try:
        logging.info(f" Starting background operation {operation.operation_id}")
        start_time = time.time()
        
        # Update or insert chat history (simplified without summary updates)
        try:
            if operation.edit_mode:
                update_payload = {
                    "message_pair_id": operation.message_pair_id,
                    "user_id": operation.user_id,
                    "chat_session_id": operation.chat_session_id,
                    "user_query": operation.query_text,
                    "bot_reply": operation.answer,
                    "summary": operation.summary,  # Use existing summary without update
                }
                if getattr(operation, 'suggestions', None):
                    update_payload["suggestions"] = operation.suggestions
                update_message_pair(update_payload)
                logging.info("update_message_pair() completed.")
            else:
                # Generate dynamic chat title based on conversation content
                try:
                    chat_title = generate_chat_title(operation.query_text, operation.answer, user_id=operation.user_id)
                    logging.info(f"Generated chat title: {chat_title}")
                except Exception as e:
                    logging.error(f"Error generating chat title: {e}")
                    chat_title = "New Chat"
                
                cs_payload = {
                    "user_id": operation.user_id,
                    "chat_session_id": operation.chat_session_id,
                    "user_query": operation.query_text,
                    "bot_reply": operation.answer,
                    "summary": operation.summary,  # Use existing summary without update
                    "title": chat_title,  # Dynamic title generation restored
                    "message_pair_id": operation.message_pair_id,
                }
                if getattr(operation, 'suggestions', None):
                    cs_payload["suggestions"] = operation.suggestions
                store_chat(cs_payload)
                logging.info("store_chat() completed.")
        except Exception as e:
            logging.error(f"Error in chat history update/storage: {e}", exc_info=True)

        elapsed_time = time.time() - start_time
        logging.info(f" Background operation {operation.operation_id} completed in {elapsed_time:.3f}s")
        
    except Exception as e:
        logging.error(f" Critical error in execute_background_operation {operation.operation_id}: {e}", exc_info=True)

def execute_post_processing_operation(operation: PostProcessingOperation):
    """Execute post-processing operations for MongoDB chunked storage and concept maps"""
    import asyncio
    
    try:
        logging.info(f"ðŸ”„ POST_PROC_START: Starting post-processing operation: {operation.operation_type}")
        logging.info(f"   - Operation ID: {operation.operation_id}")
        logging.info(f"   - Device ID: {operation.user_id}")
        logging.info(f"   - Document ID: {operation.document_id}")
        logging.info(f"   - Data keys: {list(operation.data.keys())}")
        
        start_time = time.time()
        
        try:
            # Route to appropriate async handler based on operation type
            if operation.operation_type in ["subscription_tracking", "track_document_usage", "track_usage"]:
                # Subscription and usage tracking disabled in enterprise mode
                logging.info(f"ðŸ“‹ ENTERPRISE: Operation {operation.operation_type} disabled in enterprise mode")
            elif operation.operation_type == "test_operation":
                logging.info(f"ðŸ§ª TEST_OPERATION: Processing test operation")
                # Create a new event loop for each operation to avoid conflicts
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    new_loop.run_until_complete(_async_test_operation(operation))
                finally:
                    new_loop.close()
            elif operation.operation_type == "azure_upload":
                logging.info(f"â˜ï¸ AZURE_UPLOAD: Processing Azure storage upload operation")
                # Create a new event loop for each operation to avoid conflicts
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    new_loop.run_until_complete(_async_azure_upload(operation))
                finally:
                    new_loop.close()
            else:
                logging.error(f"âŒ UNKNOWN_OPERATION: Unknown post-processing operation type: {operation.operation_type}")
                return
                
        except Exception as e:
            logging.error(f"âŒ POST_PROC_ERROR: Error in post-processing {operation.operation_type}: {e}", exc_info=True)
            
        elapsed_time = time.time() - start_time
        logging.info(f"âœ… POST_PROC_COMPLETE: Post-processing operation {operation.operation_type} completed in {elapsed_time:.3f}s")
        
    except Exception as e:
        logging.error(f"âŒ POST_PROC_CRITICAL: Critical error in post-processing operation {operation.operation_type}: {e}", exc_info=True)

async def _async_azure_upload(operation: PostProcessingOperation):
    """Legacy Azure upload handler — no longer used (storage migrated to S3/MinIO)."""
    logging.info(f"[{operation.document_id}] Skipping Azure upload — storage backend is S3/MinIO")
    return

async def _async_test_operation(operation: PostProcessingOperation):
    """Handle test operations to verify background processing is working"""
    import asyncio
    
    try:
        logging.info(f"ðŸ§ª TEST_START: Starting test operation for device: {operation.user_id}")
        logging.info(f"   - Operation ID: {operation.operation_id}")
        logging.info(f"   - Document ID: {operation.document_id}")
        
        # Extract test parameters
        data = operation.data
        test_message = data.get('test_message', 'Default test message')
        sleep_time = data.get('sleep_time', 1)
        
        logging.info(f"ðŸ“ TEST_MESSAGE: {test_message}")
        logging.info(f"â³ TEST_SLEEP: Simulating {sleep_time}s of processing time...")
        
        # Log progress during sleep
        for i in range(sleep_time):
            await asyncio.sleep(1)
            logging.info(f"â±ï¸ TEST_PROGRESS: {i+1}/{sleep_time} seconds elapsed...")
        
        logging.info(f"âœ… TEST_COMPLETE: Test operation completed successfully!")
        logging.info(f"   - Operation ID: {operation.operation_id}")
        logging.info(f"   - Total sleep time: {sleep_time}s")
        
    except Exception as e:
        logging.error(f"âŒ TEST_ERROR: Failed in test operation {operation.operation_id}: {e}", exc_info=True)

async def _async_usage_tracking(operation: PostProcessingOperation):
    """Async background handler for usage tracking operations - removed for enterprise licensing"""
    # Usage tracking removed for enterprise licensing model
    logging.info(f"ðŸ“Š Usage tracking disabled in enterprise mode for operation: {operation.operation_type}")
    return
def add_post_processing_to_queue(
    operation_type: str,
    user_id: str,
    document_id: str,
    data: Dict[str, Any]
) -> str:
    """Add a post-processing operation to the background queue"""
    import uuid
    import time
    
    try:
        logging.info(f"ðŸ“¤ QUEUE_ADD: Adding {operation_type} operation to queue")
        logging.info(f"   - Device ID: {user_id}")
        logging.info(f"   - Document ID: {document_id}")
        logging.info("   - Data size calculated")
        
        # Generate operation ID
        operation_id = f"post_{operation_type}_{int(time.time() * 1000)}_{str(uuid.uuid4())[:8]}"
        logging.debug(f"ðŸ”‘ OPERATION_ID: Generated operation ID: {operation_id}")
        
        # Check queue status before adding
        current_queue_size = background_operation_queues[user_id].qsize()
        logging.info(f"ðŸ“Š QUEUE_BEFORE: Current queue size for {user_id}: {current_queue_size}")
        
        # Create PostProcessingOperation with required timestamp
        operation = PostProcessingOperation(
            operation_id=operation_id,
            operation_type=operation_type,
            user_id=user_id,
            document_id=document_id,
            data=data,
            timestamp=time.time()
        )
        logging.debug(f"âœ… OPERATION_CREATED: Created PostProcessingOperation with timestamp: {operation.timestamp}")
        
        # Add to device queue
        queue = background_operation_queues[user_id]
        queue.put(operation)
        new_queue_size = queue.qsize()
        
        logging.info(f"ðŸ“Š QUEUE_AFTER: Queue size for {user_id}: {current_queue_size} -> {new_queue_size}")
        
        # Ensure worker is running for this device
        logging.debug(f"ðŸ”§ WORKER_CHECK: Ensuring worker exists for device: {user_id}")
        worker = get_or_create_background_worker(user_id)
        logging.info(f"âœ… WORKER_READY: Worker for {user_id} - alive: {worker.is_alive()}, name: {worker.name}")
        
        logging.info(f"ðŸ“¤ QUEUE_SUCCESS: Added {operation_type} post-processing operation {operation_id} to queue for device: {user_id}")
        logging.info(f"   - Document: {document_id}")
        logging.info(f"   - Final queue size: {new_queue_size}")
        
        return operation_id
        
    except Exception as e:
        logging.error(f"âŒ QUEUE_ERROR: Failed to add {operation_type} post-processing to queue: {e}", exc_info=True)
        raise

def get_background_queue_status():
    """Get comprehensive status of all background queues and workers"""
    status = {
        'timestamp': time.time(),
        'total_devices': len(background_operation_queues),
        'active_workers': len([w for w in background_workers.values() if w.is_alive()]),
        'devices': {}
    }
    
    for user_id in background_operation_queues.keys():
        queue = background_operation_queues[user_id]
        worker = background_workers.get(user_id)
        stats = worker_stats[user_id]
        
        device_status = {
            'queue_size': queue.qsize(),
            'worker_active': worker.is_alive() if worker else False,
            'worker_name': worker.name if worker and worker.is_alive() else None,
            'stats': {
                'operations_processed': stats['operations_processed'],
                'total_processing_time': round(stats['total_processing_time'], 2),
                'average_operation_time': round(stats['total_processing_time'] / max(1, stats['operations_processed']), 2),
                'last_operation_time': round(stats['last_operation_time'], 2),
                'worker_uptime': round(time.time() - stats['worker_started'], 2) if stats['worker_started'] > 0 else 0
            }
        }
        
        # Performance indicators
        if stats['operations_processed'] > 0:
            avg_time = stats['total_processing_time'] / stats['operations_processed']
            if avg_time > 60:  # Operations taking more than 1 minute on average
                device_status['performance_warning'] = f"High average operation time: {avg_time:.1f}s"
            if queue.qsize() > 5:  # Queue backing up
                device_status['queue_warning'] = f"Queue backup: {queue.qsize()} operations pending"
        
        status['devices'][user_id] = device_status
    
    return status

def get_device_queue_status(user_id: str):
    """Get detailed status for a specific device queue"""
    if user_id not in background_operation_queues:
        return {"error": f"No queue found for device: {user_id}"}
    
    queue = background_operation_queues[user_id]
    worker = background_workers.get(user_id)
    stats = worker_stats[user_id]
    
    return {
        'user_id': user_id,
        'timestamp': time.time(),
        'queue_size': queue.qsize(),
        'worker_active': worker.is_alive() if worker else False,
        'worker_thread_id': worker.ident if worker and worker.is_alive() else None,
        'stats': stats,
        'recommendations': _get_performance_recommendations(user_id, stats, queue.qsize())
    }

def _get_performance_recommendations(user_id: str, stats: dict, queue_size: int):
    """Generate performance recommendations for a device queue"""
    recommendations = []
    
    if stats['operations_processed'] > 0:
        avg_time = stats['total_processing_time'] / stats['operations_processed']
        
        if avg_time > 120:  # 2 minutes average
            recommendations.append("âš ï¸ High average operation time - consider optimizing MongoDB operations")
        elif avg_time > 60:  # 1 minute average
            recommendations.append("ðŸ” Moderate operation time - monitor for potential optimizations")
    
    if queue_size > 10:
        recommendations.append("ðŸš¨ Queue backup detected - consider system resource optimization")
    elif queue_size > 5:
        recommendations.append("âš ï¸ Queue building up - monitor processing speed")
    
    if stats['last_operation_time'] > 300:  # 5 minutes
        recommendations.append("ðŸŒ Last operation was very slow - investigate potential issues")
    
    if not recommendations:
        recommendations.append("âœ… Queue performance looks healthy")
    
    return recommendations

# Subscription tracking functions removed for enterprise licensing

def add_background_operation(user_id: str, operation: PostProcessingOperation) -> str:
    """
    Add a pre-constructed PostProcessingOperation to the background queue
    
    Args:
        user_id: Device ID for the operation
        operation: Pre-constructed PostProcessingOperation object
        
    Returns:
        Operation ID for tracking
    """
    import time
    try:
        logging.info(f"ðŸ“¤ QUEUE_ADD: Adding {operation.operation_type} operation to queue")
        logging.info(f"   - Device ID: {user_id}")
        logging.info(f"   - Document ID: {operation.document_id}")
        logging.info(f"   - Operation ID: {operation.operation_id}")
        
        # Check queue status before adding
        current_queue_size = background_operation_queues[user_id].qsize()
        logging.info(f"ðŸ“Š QUEUE_BEFORE: Current queue size for {user_id}: {current_queue_size}")
        
        # Add to device queue
        queue = background_operation_queues[user_id]
        queue.put(operation)
        new_queue_size = queue.qsize()
        
        logging.info(f"ðŸ“Š QUEUE_AFTER: Queue size for {user_id}: {current_queue_size} -> {new_queue_size}")
        
        # Ensure worker is running for this device
        logging.debug(f"ðŸ”§ WORKER_CHECK: Ensuring worker exists for device: {user_id}")
        worker = get_or_create_background_worker(user_id)
        logging.info(f"âœ… WORKER_READY: Worker for {user_id} - alive: {worker.is_alive()}, name: {worker.name}")
        
        logging.info(f"ðŸ“¤ QUEUE_SUCCESS: Added {operation.operation_type} operation {operation.operation_id} to queue for device: {user_id}")
        logging.info(f"   - Document: {operation.document_id}")
        logging.info(f"   - Final queue size: {new_queue_size}")
        
        return operation.operation_id
        
    except Exception as e:
        logging.error(f"âŒ QUEUE_ERROR: Failed to add operation to queue for device {user_id}: {e}")
        logging.error(f"   - Operation ID: {operation.operation_id}")
        logging.error(f"   - Operation Type: {operation.operation_type}")
        raise e

# Multi-hop RAG is imported at the top of the file with error handling



# Query caches removed -- always query Milvus live for data freshness
import hashlib
from typing import Dict, Tuple, Optional
from datetime import datetime, timedelta

# ===================== CONFIGURATION =====================

# Import performance monitoring
from performance_monitor import performance_monitor, log_query_performance


# ===================== END OF API CONFIGURATIONS =====================



from datetime import timedelta
from bson import ObjectId

# Concept Map Integration - Disabled (module removed)
CONCEPT_AWARE_RAG_AVAILABLE = False
logging.info("â„¹ï¸ Concept map RAG system disabled - module removed")

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


#  2. New: Fetch Transcript from MongoDB 

# 2.1 MongoDB setup for transcripts
MONGO_CONN = os.getenv("MONGODB_CONN_STRING")
from citra_mongo import MONGODB_DATABASE as MONGO_DB_NAME
_mongo_client = None

# Use centralized MongoDB manager instead of local connection
from citra_mongo import get_mongo_client as get_centralized_mongo_client, get_sync_database

def get_mongo_client():
    """Backward compatibility wrapper for centralized MongoDB manager"""
    return get_centralized_mongo_client()

def get_mongo_collection():
    """Get transcripts collection using centralized manager"""
    db = get_sync_database()
    return db["transcripts"]

# Milvus-related code removed - migrated to Milvus/Zilliz
# All vector operations now handled by EnhancedChunkedDocumentService with Milvus

# Cache nano and pro clients to avoid recreating them on each call
_client_nano = None
_client_pro = None
_client_reasoning = None
_client_lock = threading.Lock()


index_name = os.getenv('INDEX_NAME')
embed_model = os.getenv('EMBED_MODEL')
CHAT_WINDOW_SIZE = int(os.getenv('CHAT_WINDOW_SIZE', 10))
MAX_TOKENS = int(os.getenv('MAX_TOKENS', 50000))


# --- chunk-level index (for RAG) - OPTIMIZED FOR SIMPLE QUERIES ------------
_chunk_index_cache = None
_chunk_lock = threading.Lock()
_index_last_checked = 0
_index_check_interval = 300  # Check index health every 5 minutes

def get_chunk_index():
    """
    DEPRECATED: Milvus chunk index - migrated to Milvus/Zilliz.
    This function is kept for backwards compatibility but should not be used.
    Use config/milvus_config.py functions instead.
    """
    raise NotImplementedError(
        "Milvus support removed. Use Milvus via config/milvus_config.py instead."
    )



def generate_topic_from_text(extracted_text: str, max_words: int = 20, user_id: str = None, user_email: str = None) -> str:
    """
    Generate a concise topic from the first 500 words of extracted text using the configured LLM.
    Args:
        extracted_text: The full extracted text from document
        max_words: Maximum number of words for the topic (default: 20)
        user_id: Optional user ID for token tracking
        user_email: Optional user email for token tracking
    Returns:
        Generated topic string (max 20 words)
    """
    try:
        if not extracted_text or not extracted_text.strip():
            return "Untitled Document"
        
        # Take first 500 words from extracted text
        words = extracted_text.strip().split()
        first_500_words = ' '.join(words[:500])
        
        if not first_500_words:
            return "Untitled Document"
        
        # System prompt for topic generation
        system_prompt = (
            f"You are a document title generator. Create a concise, descriptive title "
            f"from the given text content. The title must be maximum {max_words} words and "
            f"should capture the main topic or purpose of the document. "
            f"Return only the title, nothing else."
        )
        
        # User prompt with the text content
        user_prompt = (
            f"Generate a concise title (maximum {max_words} words) for a document "
            f"with the following content:\n\n{first_500_words}"
        )
        
        # Use configured LLM for fast topic generation
        generated_topic = reply(user_prompt, ai_model=None, system=system_prompt, user_id=user_id, user_email=user_email)
        
        # Ensure topic doesn't exceed max words
        topic_words = generated_topic.split()
        if len(topic_words) > max_words:
            generated_topic = ' '.join(topic_words[:max_words])
        
        # Remove quotes if present
        generated_topic = generated_topic.strip('"\'')
        
        logging.info(f"Generated topic from text: '{generated_topic}'")
        return generated_topic
        
    except Exception as e:
        logging.error(f"Error generating topic from text: {e}")
        raise RuntimeError(f"Topic generation failed: {e}") from e



def transcribe_audio(audio_bytes: bytes, mime_type: Optional[str] = None, filename: Optional[str] = None, user_id: str = None, user_email: str = None) -> str:
    """Transcribe audio using the configured audio-to-text API."""
    if not audio_bytes:
        raise ValueError("Audio content is empty; cannot transcribe")

    logging.info(
        "ðŸŽ§ Sending audio to Whisper for transcription: name=%s, size=%d bytes",
        filename or "audio-upload",
        len(audio_bytes),
    )

    transcription = whisper_transcribe_audio(
        audio_bytes=audio_bytes,
        mime_type=mime_type,
        filename=filename,
        user_id=user_id,
        user_email=user_email,
    )

    if not transcription:
        raise RuntimeError("Whisper returned an empty transcription response")

    transcription = transcription.strip()
    logging.info("ðŸŽ§ Whisper transcription complete; length=%d characters", len(transcription))
    return transcription

def _process_inline_citations(text: str, citations: List[str]) -> str:
    """
    Remove inline citation markers ([web:X], [post:X]) completely from the response text.
    
    Search APIs may insert bracketed markers like [web:0], [web:1], [post:2] directly into response text.
    These reference the citations array by index (0-based).
    
    Per user requirement: NO internet citations should be displayed (no URLs, no numbers, no markers).
    
    Args:
        text: Response text containing inline citation markers
        citations: List of citation URLs from search API response
        
    Returns:
        Text with all citation markers removed completely
    """
    if not citations:
        return text
    
    import re
    
    # Pattern to match [web:N] or [post:N] where N is a number
    citation_pattern = r'\[(web|post):(\d+)\]'
    
    # Remove all citation markers completely (replace with empty string)
    processed_text = re.sub(citation_pattern, '', text)
    
    # Clean up any double spaces that may result from marker removal
    processed_text = re.sub(r'\s+', ' ', processed_text).strip()
    
    # Count how many markers were removed
    num_removed = len(re.findall(citation_pattern, text))
    if num_removed > 0:
        logging.info(f"ðŸ§¹ [CITATIONS] Removed {num_removed} inline citation marker(s) completely (user requirement: no internet citations)")
    
    return processed_text


def _get_internet_grounding_pricing() -> float:
    """
    Get internet search pricing from cached pricing configuration.
    Falls back to 1.0 credits default.

    Returns:
        Internet search price per source (credits)
    """
    try:
        from middleware.credit_check_middleware import get_usage_service
        
        # Get pricing from cache (no DB call - already loaded at startup)
        usage_service = get_usage_service()
        pricing = usage_service.get_pricing()
        
        if pricing:
            token_pricing = pricing.get('token_pricing', {})
            

            # Use default tier pricing
            default_pricing = token_pricing.get('default', {})
            internet_price = default_pricing.get('internet_grounding_price')
            
            if internet_price is not None:
                internet_price = float(internet_price)
                logger.debug(f"[PRICING] Using cached internet search price: {internet_price:.2f} per source")
                return internet_price
        
        # Fallback if not found in cache
        logger.warning("⚠️ [PRICING] internet_grounding_price not found in cached pricing, using default: 1.0 credits per source")
        return 1.0
            
    except Exception as e:
        logger.error(f"âŒ [PRICING] Error accessing cached internet pricing: {e}")
        return 1.0  # Default fallback on error








def reply_llm(
    prompt: str,
    system: str = None,
    model: str = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    max_output_tokens: Optional[int] = None,
    enable_internet_search: bool = False,
    search_mode: str = "native",
    max_search_results: int = 5,
    user_id: str = None,
    user_email: str = None,
    profession: str = None,
    static_context: str = None,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    dynamic_context: str = None,
    temperature: Optional[float] = None,
    tier: str = "large",
) -> str:
    """
    Send a chat completion request via the configured LLM.
    
    Uses llm_call for all completions.
    Preserves the multi-message context building pattern for cache efficiency.
    
    Message order: System -> Static Context -> Chunks -> Conversation History -> Dynamic Context -> Current Query
    """
    _t_start = time.perf_counter()
    try:
        logging.info(f"⏱️ [REPLY_LLM] ENTER tier={tier} model={model}, prompt={len(prompt)} chars, "
                     f"internet={enable_internet_search}, attachments={len(attachments) if attachments else 0}, "
                     f"chunks={len(context_chunks) if context_chunks else 0}, "
                     f"history={len(conversation_history) if conversation_history else 0}")

        # Default system prompt
        if system is None:
            system = (
                "You are Citra AI, an intelligent assistant. "
                "Use any relevant context provided to answer questions accurately and concisely."
            )

        max_tokens = max_output_tokens if max_output_tokens else 16000

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Build the complete user prompt with context (collapsed into a single 
        # system + user message pair for llm_call)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        
        context_parts = []

        # 1. Static context (entire document)
        if static_context and static_context.strip() and not context_chunks:
            context_parts.append(
                f"=== DOCUMENT CONTEXT (from uploaded files) ===\n{static_context}\n=== END DOCUMENT CONTEXT ==="
            )

        # 2. Context chunks. The source_origin-typed formatter that used to run
        #    here lived in rag/retrieval_guard.py and went with the RAG package;
        #    it was reached through a try/except that quietly fell back to this
        #    same block anyway. The one live caller (api/reader.py) passes no
        #    context_chunks, so nothing changes shape — and the formatting is now
        #    unconditional instead of hiding behind a swallowed exception.
        if context_chunks and len(context_chunks) > 0:
            chunk_texts = []
            for idx, chunk in enumerate(context_chunks, 1):
                chunk_text = chunk.get('text', '')
                topic = chunk.get('topic', 'Unknown Document')
                score = chunk.get('score', 0.0)
                document_id = chunk.get('document_id', '')
                chunk_texts.append(
                    f"[Context Chunk {idx}/{len(context_chunks)}]\n"
                    f"Score: {score:.2f} | Document: {topic}\n"
                    f"Document ID: {document_id}\n\n{chunk_text}"
                )
            context_parts.append("\n\n".join(chunk_texts))
            logging.info(f"ðŸ“¦ Added {len(context_chunks)} context chunks")

        # 3. Conversation history
        if conversation_history and len(conversation_history) > 0:
            history_lines = []
            for msg in conversation_history:
                role = msg.get('role', 'user')
                content = msg.get('content', '').strip()
                if content:
                    history_lines.append(f"{'User' if role == 'user' else 'Assistant'}: {content}")
            if history_lines:
                context_parts.append(
                    "=== CONVERSATION HISTORY ===\n" + 
                    "\n\n".join(history_lines) + 
                    "\n=== END CONVERSATION HISTORY ==="
                )
                logging.info(f"ðŸ’¬ Added {len(history_lines)} conversation history messages")

        # 4. Dynamic context (project data)
        if dynamic_context and dynamic_context.strip():
            context_parts.append(
                f"=== DYNAMIC PROJECT DATA ===\n{dynamic_context}\n=== END DYNAMIC PROJECT DATA ==="
            )

        # 5. Handle attachments -- extract text from images using Qwen OCR
        attachments = attachments or []
        if attachments:
            logging.info(f"ðŸ“Ž Processing {len(attachments)} attachment(s) via Qwen OCR")
            for idx, attachment in enumerate(attachments, 1):
                base64_data = attachment.get("base64") or attachment.get("data") or attachment.get("base64Data")
                mime_type = attachment.get("mimeType") or attachment.get("type")
                att_name = attachment.get("name", f"attachment-{idx}")

                if not base64_data or not mime_type:
                    logging.warning(f"âš ï¸ Attachment #{idx} missing data or mime; skipping")
                    continue

                if isinstance(base64_data, str):
                    import base64 as b64mod
                    image_bytes = b64mod.b64decode(base64_data)
                elif isinstance(base64_data, bytes):
                    image_bytes = base64_data
                else:
                    continue

                try:
                    description = extract_text_from_image(
                        image_bytes=image_bytes,
                        filename=att_name,
                        mime_type=mime_type,
                        user_id=user_id,
                        user_email=user_email,
                    )
                    context_parts.append(f"=== ATTACHMENT: {att_name} ===\n{description}\n=== END ATTACHMENT ===")
                    logging.info(f"âœ… Extracted text from attachment {att_name}: {len(description)} chars")
                except Exception as e:
                    logging.error(f"âŒ Failed to process attachment {att_name}: {e}")

        # Build final user prompt
        if context_parts:
            full_user_prompt = "\n\n".join(context_parts) + f"\n\n--- CURRENT QUERY ---\n{prompt}"
        else:
            full_user_prompt = prompt
        logging.info(f"â±ï¸ [REPLY_LLM] context_built in {time.perf_counter()-_t_start:.2f}s (parts={len(context_parts)}, full_prompt={len(full_user_prompt)} chars)")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Make the LLM call
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        _t_llm = time.perf_counter()
        if enable_internet_search:
            logging.info("ðŸŒ Internet search enabled -- using llm_call_with_internet")
            answer = llm_call_with_internet(
                system_prompt=system,
                user_prompt=full_user_prompt,
                model=None,
                user_id=user_id,
                user_email=user_email,
                max_tokens=max_tokens,
                temperature=_resolve_temp(temperature, profession),
                tier=tier,
            )
        else:
            answer = llm_call(
                system_prompt=system,
                user_prompt=full_user_prompt,
                model=None,
                user_id=user_id,
                user_email=user_email,
                max_tokens=max_tokens,
                temperature=_resolve_temp(temperature, profession),
                tier=tier,
            )

        _llm_dur = time.perf_counter() - _t_llm
        _total_dur = time.perf_counter() - _t_start
        logging.info(f"âœ… [REPLY_LLM] DONE: response={len(answer)} chars, llm_call={_llm_dur:.2f}s, total={_total_dur:.2f}s")
        return answer

    except Exception as e:
        logging.error(f"âŒ Error in reply_llm (llm_oss): {e}")
        return f"Sorry, I encountered an error: {str(e)}"

def reply(prompt: str, ai_model: str = None, system: str = None, attachments: Optional[List[Dict[str, Any]]] = None, max_output_tokens: Optional[int] = None, enable_internet_search: bool = False, user_id: str = None, user_email: str = None, profession: str = None, static_context: str = None, context_chunks: Optional[List[Dict[str, Any]]] = None, conversation_history: Optional[List[Dict[str, str]]] = None, dynamic_context: str = None, tier: str = "large") -> str:
    """
    Route all requests to the configured LLM.
    All model names are accepted for backward compat but all route to the same backend.
    """
    _t_reply = time.perf_counter()
    logging.info(f"ðŸ” [REPLY] reply() called: ai_model={ai_model}, prompt={len(prompt)} chars, internet={enable_internet_search}")

    # # All model names route to the same LLM backend
    _result = reply_llm(
        prompt, system=system, model=None,
        attachments=attachments, max_output_tokens=max_output_tokens,
        enable_internet_search=enable_internet_search,
        user_id=user_id, user_email=user_email, profession=profession,
        static_context=static_context, context_chunks=context_chunks,
        conversation_history=conversation_history, dynamic_context=dynamic_context,
        tier=tier,
    )
    logging.info(f"â±ï¸ [REPLY] EXIT total={time.perf_counter()-_t_reply:.2f}s, response={len(_result) if _result else 0} chars")
    return _result


def generate_chat_title(query: str, answer: str, ai_model: str = None, user_id: str = None) -> str:
    """
    Ask the LLM to propose a short (<=5 words) title for this chat session.
    """
    try:
        prompt = (
            "Generate a concise title (max 5 words) for a chat session based on:\n"
            f"User: {query}\nBot: {answer}\n\n"
            "Title should capture the main topic or purpose. Return only the title."
        )

        return reply(prompt, ai_model, user_id=user_id, tier="large")

    except Exception as e:
        logging.error(f"Error in generate_chat_title(): {e}")
        return "New Chat"



# Simple query detection for ultra-fast path
def is_simple_generic_query(query: str) -> bool:
    """
    Detect simple generic queries that can bypass complex processing
    """
    if not query or len(query.strip()) == 0:
        return True
    
    # Simple greetings and test queries
    simple_patterns = [
        "hi", "hello", "hey", "test", "ping", "ok", "yes", "no",
        "thanks", "thank you", "bye", "goodbye", "help"
    ]
    
    query_lower = query.lower().strip()
    
    # Single word queries from simple patterns
    if query_lower in simple_patterns:
        return True
    
    # Very short queries (less than 10 characters)
    if len(query_lower) < 10 and any(pattern in query_lower for pattern in simple_patterns):
        return True
    
    return False


def _infer_profession(persona_system_prompt: Optional[str]) -> Optional[str]:
    """
    Infer profession from persona system prompt.
    
    Updated for cache-optimized persona structure:
    - Looks for profession in USER-SPECIFIC CONTEXT section (most reliable)
    - Falls back to keyword search if USER-SPECIFIC CONTEXT not found
    - Uses case-insensitive matching with proper precedence
    """
    if not persona_system_prompt:
        return None
    
    # First, try to extract from USER-SPECIFIC CONTEXT section (most accurate)
    # This section contains: "- Profession: Legal Professional"
    if "USER-SPECIFIC CONTEXT:" in persona_system_prompt:
        # Extract the section after USER-SPECIFIC CONTEXT
        context_section = persona_system_prompt.split("USER-SPECIFIC CONTEXT:")[1]
        
        # Look for "Profession: ..." line in this section
        import re
        profession_match = re.search(r'Profession:\s*(.+?)(?:\n|$)', context_section, re.IGNORECASE)
        if profession_match:
            profession_value = profession_match.group(1).strip()
            logging.info(f"âš¡ [PROFESSION_INFER] Found profession in USER-SPECIFIC CONTEXT: {profession_value}")
            
            # Map exact profession values to response keys
            profession_map = {
                "Legal Professional": "legal",
                "Arbitrator": "arbitrator",
                "Contract Professional": "contract_manager",
                "Engineering Professional": "engineering_manager",
                "Pharmaceutical Professional": "pharmaceutical_manager"
            }
            
            # Return mapped value if found
            if profession_value in profession_map:
                mapped = profession_map[profession_value]
                logging.info(f"âš¡ [PROFESSION_INFER] Mapped to: {mapped}")
                return mapped
            else:
                logging.warning(f"âš¡ [PROFESSION_INFER] Profession '{profession_value}' not in mapping - falling back to keyword search")
    
    # Fallback: keyword search in entire prompt (less reliable due to static content)
    prompt_lower = persona_system_prompt.lower()
    
    # Check more specific terms first to avoid false matches
    if "pharmaceutical professional" in prompt_lower or "drug development" in prompt_lower or "clinical trial" in prompt_lower:
        return "pharmaceutical_manager"
    if "engineering professional" in prompt_lower:
        return "engineering_manager"
    if "contract professional" in prompt_lower:
        return "contract_manager"
    if "legal professional" in prompt_lower or "legal assistant" in prompt_lower:
        return "legal"
    # Check "arbitrator" last since it might appear in legal content
    if "arbitrator" in prompt_lower:
        return "arbitrator"
    if "healthcare" in prompt_lower:
        return "healthcare"
    if "business" in prompt_lower:
        return "business"
    
    return None


def handle_simple_query(query: str, persona_system_prompt: str = None) -> dict:
    """
    Handle simple queries with minimal processing
    Optionally uses persona context for personalized responses
    """
    query_lower = query.lower().strip()
    profession = _infer_profession(persona_system_prompt)
    
    # Log profession inference for debugging
    logging.info(f"âš¡ [SIMPLE_QUERY] Inferred profession: {profession}")

    greeting_map = {
        "legal": "Hello! I'm ready to dive into your legal research, filings, or case strategy. What should we work on first?",
        "arbitrator": "Hello! I'm here to support your arbitration work--from procedural orders to award drafting. How can I assist you today?",
        "contract_manager": "Hello! I'm ready to help you compare clauses, flag risks, and prep contract summaries. What do you need?",
        "engineering_manager": "Hello! I'm set to help plan projects, validate compliance, and keep your engineering documentation sharp. Where should we begin?",
        "pharmaceutical_manager": "Hello! I'm ready to assist with drug development, clinical trials, regulatory submissions, and compliance. What can I help you with today?",
        "healthcare": "Hello! I'm ready to assist you with medical information and healthcare guidance. What can I help you with?",
        "business": "Hello! I'm here to help with your business analysis and professional tasks. How can I assist you?",
    }

    help_map = {
        "legal": "I can draft pleadings, summarize judgments with citations, prep checklists, or compare precedents. Tell me what your case needs.",
        "arbitrator": "I can summarize submissions, draft procedural or interim orders, compare awards, and track timelines. Which matter are we focusing on?",
        "contract_manager": "I can review clauses, draft amendments, build risk notes, and highlight compliance obligations. Point me to the contract you're handling.",
        "engineering_manager": "I can draft specs, outline QA checklists, summarize site reports, or prep approval notes. Which project should we tackle?",
        "pharmaceutical_manager": "I can review formulations, summarize clinical trial data, draft regulatory submissions, assess safety reports, and check compliance with FDA/CDSCO/ICH guidelines. What project are you working on?",
        "healthcare": "I can surface clinical guidance, summarize cases, and prepare patient-ready explanations. How can I support you?",
        "business": "I can analyze data, prepare summaries, and outline next steps for your team. What should we prioritize?",
    }

    default_map = {
        "legal": "I'm on standby for research memos, motion drafts, compliance notes, and precedent comparisons--just let me know the matter.",
        "arbitrator": "Whether you need to evaluate evidence, draft an order, or outline reasoning, I'm ready--share the dispute details.",
        "contract_manager": "I can keep your pipeline organized with clause comparisons, renewal reminders, and negotiation briefs. What should we look at?",
        "engineering_manager": "From project briefs to safety narratives and risk registers, I can keep your engineering stack organized. What's next?",
        "pharmaceutical_manager": "I'm ready to help with formulation reviews, trial protocols, regulatory documentation, safety assessments, and compliance checks. Share your drug development needs.",
        "healthcare": "Let me know the medical topic and I'll bring focused guidance or patient-friendly explanations.",
        "business": "Share the business question and I'll provide a structured, actionable response.",
    }

    # Generate appropriate responses for simple queries
    if query_lower in ["hi", "hello", "hey"]:
        response = greeting_map.get(profession, "Hello! How can I assist you today?")
    elif query_lower in ["test", "ping"]:
        response = "System is working perfectly! I'm ready to help you."
    elif query_lower in ["thanks", "thank you"]:
        response = "You're welcome! Feel free to ask if you need anything else."
    elif query_lower in ["bye", "goodbye"]:
        response = "Goodbye! Have a great day!"
    elif query_lower == "help":
        response = help_map.get(profession, "I'm here to help! You can ask me questions, upload documents, or start conversations. What would you like to do?")
    else:
        response = default_map.get(profession, "I'm here to help! Please feel free to ask me any questions.")
    
    return {
        "response": response,
        "edit_mode": False,
        "message_pair_id": str(uuid.uuid4()),
        "fast_path": True
    }


router = APIRouter()

# 
# COMMON FUNCTIONS FOR QUERY ENDPOINTS
# 

def validate_query_payload(payload: Dict[str, Any]) -> tuple:
    """Validate and extract common fields from query payload (conversation_history only).

    Returns tuple:
        (
            user_id,
            chat_session_id,
            conversation_history,
            multimodal_attachments,
            edit_mode,
            message_pair_id,
            query_text,
            use_personal_data,
            None,  # Legacy use_internet slot (deprecated)
            None,  # Legacy use_india_kanoon slot (deprecated)
            persona_data,
            selected_folder_ids,
            folder_search_enabled,
            enterprise_entity_id,
            enterprise_entity_name,
            enterprise_entity_type,
            enterprise_entity_enabled,
            skip_post_processing,
            enterprise_enabled,
        )

    Legacy 'chats' field is deprecated and ignored (only logged once if present).
    """
    user_id: Optional[str] = payload.get("user_id")
    chat_session_id: Optional[str] = payload.get("chat_session_id")
    edit_mode: bool = payload.get("edit_mode", False)
    message_pair_id: Optional[str] = payload.get("message_pair_id")

    # Prefer conversation_history supplied by UI; enforce max length if provided
    conversation_history: Optional[List[Dict[str, Any]]] = payload.get("conversation_history") or []

    raw_multimodal_attachments = payload.get("multimodal_attachments")
    multimodal_attachments: List[Dict[str, Any]] = []
    if isinstance(raw_multimodal_attachments, list):
        for attachment in raw_multimodal_attachments:
            if isinstance(attachment, dict):
                multimodal_attachments.append(attachment)
            else:
                logging.warning("âš ï¸ Ignoring multimodal attachment with unexpected structure (expected dict)")
    elif raw_multimodal_attachments is not None:
        logging.warning("âš ï¸ multimodal_attachments field present but not a list; ignoring value")
    
    # Extract core fields
    query_text: Optional[str] = payload.get("query")
    # ai_model parameter removed - using fixed model in orchestrator

    # Toggle flags
    use_personal_data: bool = payload.get("use_personal_data", True)
    # use_internet removed - legacy code. LLM native search is enabled when configured
    # use_india_kanoon removed - legacy legal search feature, no longer used
    
    # Enterprise flag — accept both old and new key names for backward compat
    enterprise_enabled: bool = payload.get("use_enterprise_data", payload.get("enterprise_enabled", False))

    # Persona data passed from UI
    persona_data: Optional[Dict[str, Any]] = payload.get("persona_data")

    # Folder filtering
    selected_folder_ids: Optional[List[str]] = payload.get("selected_folder_ids")
    folder_search_enabled: bool = payload.get("folder_search_enabled", False)

    # Enterprise entity filtering
    enterprise_entity_id: Optional[str] = payload.get("enterprise_entity_id")
    enterprise_entity_name: Optional[str] = payload.get("enterprise_entity_name")
    enterprise_entity_type: Optional[str] = payload.get("enterprise_entity_type")
    enterprise_entity_enabled: bool = payload.get("enterprise_entity_enabled", False)

    # Composer optimization flag
    skip_post_processing: bool = payload.get("skip_post_processing", False)

    logging.info(
        f"Query validation - Device: {user_id}, Session: {chat_session_id}, "
        f"Data Sources: Personal={use_personal_data}, Enterprise={enterprise_enabled}, ConvHistory={len(conversation_history)}, Attachments={len(multimodal_attachments)}"
    )

    # Validation
    if not user_id:
        raise HTTPException(status_code=400, detail="Device ID is required")
    if not chat_session_id:
        raise HTTPException(status_code=400, detail="Chat session ID is required")
    if not message_pair_id:
        raise HTTPException(status_code=400, detail="Message pair ID is required")
    
    # Check if there are audio attachments
    has_audio_attachments = any(
        (att.get('mimeType') or att.get('type') or '').lower().startswith('audio/')
        for att in (multimodal_attachments or [])
    )
    
    # Allow empty query text if there are audio attachments (audio-only questions)
    if not query_text and not has_audio_attachments:
        logging.error(f"ðŸ” VALIDATION FAILED: Query text missing. Value: '{query_text}'")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query text is required")
    
    # Enforce maximum query length (including any client-side combined attachment text)
    MAX_QUERY_CHARS = 100000
    if isinstance(query_text, str) and len(query_text) > MAX_QUERY_CHARS:
        logging.error(
            f"ðŸ” VALIDATION FAILED: Query text too long. Length: {len(query_text)} > {MAX_QUERY_CHARS}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query text exceeds maximum allowed length of {MAX_QUERY_CHARS} characters"
        )

    # ai_model validation removed - using fixed model in orchestrator

    logging.info("ðŸ” All validation checks passed!")
    return (
        user_id,
        chat_session_id,
        conversation_history,
        multimodal_attachments,
        edit_mode,
        message_pair_id,
        query_text,
        use_personal_data,
        None,  # Legacy use_internet slot - kept for backward compatibility with tuple unpacking
        None,  # Legacy use_india_kanoon slot - kept for backward compatibility with tuple unpacking
        persona_data,
        selected_folder_ids,
        folder_search_enabled,
        enterprise_entity_id,
        enterprise_entity_name,
        enterprise_entity_type,
        enterprise_entity_enabled,
        skip_post_processing,
        enterprise_enabled,
    )

def build_enhanced_prompt_with_persona(existing_bio: str, contexts: List[str], summary: str, 
                                     query_text: str, persona_system_prompt: str = None, 
                                     persona_context: dict = None, conversation_history: List[dict] = None) -> tuple:
    """
    Build enhanced prompt with simplified context presentation and conversation history.
    Returns tuple of (system_context, full_prompt_with_system).
    """
    
    # Simplified approach - return minimal system context and raw content only
    minimal_system = persona_system_prompt or "You are a helpful assistant."
    
    # Build prompt components
    prompt_parts = []
    
    # Add relevant context sources if available
    if contexts:
        # Note: Citation cleaning now handled in UI layer for better separation of concerns
        # Service preserves all original data, UI controls display presentation
        raw_context = "\n\n".join(contexts)
        prompt_parts.append(f"=== RELEVANT INFORMATION ===\n{raw_context}")
    
    # Add conversation history for context continuity
    if conversation_history and len(conversation_history) > 0:
        conversation_text = []
        for msg in conversation_history:
            role = msg.get('role', '').title()
            content = msg.get('content', '').strip()
            if role and content:
                conversation_text.append(f"{role}: {content}")
        
        if conversation_text:
            conversation_context = "\n".join(conversation_text)
            prompt_parts.append(f"=== PREVIOUS CONVERSATION ===\n{conversation_context}")
    
    # Add current query
    prompt_parts.append(f"=== CURRENT QUESTION ===\n{query_text}")
    
    # Combine all parts
    if prompt_parts:
        minimal_prompt = "\n\n".join(prompt_parts)
    else:
        minimal_prompt = query_text
        
    return minimal_system, minimal_prompt


def run_background_post_operations(user_id: str, chat_session_id: str, query_text: str, 
                                  existing_bio: str, answer: str, summary: str, edit_mode: bool, 
                                  message_pair_id: str, chats: List[Dict[str, str]],
                                  suggestions: Optional[List[Dict[str, Any]]] = None):
    """
    Queue background post-operations for device-specific processing.
    Uses a per-device queue system to prevent race conditions and ensure ordered processing.
    """
    # Create background operation
    operation = BackgroundOperation(
        operation_id=str(uuid.uuid4())[:8],  # Short UUID for logging
        user_id=user_id,
        chat_session_id=chat_session_id,
        query_text=query_text,
        existing_bio=existing_bio,
        answer=answer,
        summary=summary,
        edit_mode=edit_mode,
        message_pair_id=message_pair_id,
        chats=chats,
        timestamp=time.time(),
        suggestions=suggestions,
    )
    
    # Ensure worker exists for this device
    get_or_create_background_worker(user_id)
    
    # Add operation to device-specific queue
    queue = background_operation_queues[user_id]
    
    # Register operation for session tracking before putting it in queue
    if chat_session_id:
        with pending_operations_lock:
            pending_session_operations[chat_session_id].add(operation.operation_id)
            logging.debug(f"ðŸ”„ SYNC: Registered operation {operation.operation_id} for session {chat_session_id}")
            
    queue.put(operation)
    
    logging.info(f" Queued background operation {operation.operation_id} for device: {user_id} (queue size: {queue.qsize()})")

def wait_for_chat_persistence(chat_session_id: str, timeout: float = 10.0):
    """
    Wait until all pending background operations for a specific chat session are completed.
    Used to ensure data consistency before operations like sharing.
    """
    if not chat_session_id:
        return
        
    start_wait = time.time()
    while time.time() - start_wait < timeout:
        with pending_operations_lock:
            pending_ops = pending_session_operations.get(chat_session_id)
            if not pending_ops:
                logging.info(f"âœ… SYNC: No pending operations for session {chat_session_id}. Persistence complete.")
                return
            
            op_count = len(pending_ops)
            
        logging.info(f"â³ SYNC: Waiting for {op_count} background operations for session {chat_session_id}... ({time.time() - start_wait:.1f}s)")
        time.sleep(0.5)
        
    logging.warning(f"âš ï¸ SYNC: Timeout waiting for chat persistence for session {chat_session_id} after {timeout}s.")



    






# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ðŸš€ STREAMING QUERY ENDPOINT - Server-Sent Events (SSE) for real-time response streaming
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.post("/query/stream")
async def query_stream(request: Request):
    """
    POST /query/stream - Streaming query endpoint using Server-Sent Events (SSE)
    
    This endpoint returns a stream of events as the response is generated:
    - context_ready: Context retrieval complete
    - chunk: Text chunk from LLM
    - mermaid_block: Complete mermaid diagram
    - citations: Citation data
    - usage: Token usage stats
    - done: Stream complete
    - error: Error occurred
    """
    from streaming_response import StreamEvent, StreamEventType, stream_llm_response, extract_citations_from_response
    
    start_time = time.time()
    request_id = str(uuid.uuid4())[:8]
    
    logger.info(f"ðŸš€ [STREAM_QUERY] Request {request_id} - Streaming query endpoint called")
    
    # âš ï¸ CRITICAL: Read request body BEFORE creating the async generator
    # Request body cannot be read inside a generator after StreamingResponse is returned
    try:
        content_type = request.headers.get("content-type", "").lower()
        if not content_type.startswith("application/json"):
            return JSONResponse(
                status_code=400,
                content={"error": "Content-Type must be application/json"}
            )
        
        body_bytes = await request.body()
        payload = json.loads(body_bytes)
        logger.debug(f"ðŸ”µ [STREAM_DEBUG] Payload parsed before generator, keys: {list(payload.keys())}")
    except Exception as e:
        logger.error(f"ðŸ”´ [STREAM_DEBUG] Failed to parse request body: {e}")
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid request: {e}"}
        )
    
    async def generate_sse_stream(payload: dict):
        """Async generator for SSE stream"""
        logger.debug(f"ðŸ”µ [STREAM_DEBUG] ========== GENERATOR STARTED ==========")
        logger.debug(f"ðŸ”µ [STREAM_DEBUG] Request ID: {request_id}")
        logger.debug(f"ðŸ”µ [STREAM_DEBUG] Payload keys: {list(payload.keys())}")
        
        full_response = ""
        
        try:
            # ðŸ”’ SECURITY: Get authenticated user_id from JWT token
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] Getting authenticated user...")
            authenticated_user_id = get_secure_user_id(request)
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] Authenticated user: {authenticated_user_id}")
            if not authenticated_user_id:
                logger.error(f"ðŸ”´ [STREAM_DEBUG] Authentication failed!")
                yield StreamEvent(StreamEventType.ERROR, {"message": "Authentication required"}).to_sse()
                return
            
            payload["user_id"] = authenticated_user_id
            user_email = get_user_email(request)
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] User email: {user_email}")
            
            # --- CREDIT CHECK ---
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] Checking credits...")
            try:
                from middleware.credit_check_middleware import check_user_credits
                credit_check = check_user_credits(authenticated_user_id, 0)
                if not credit_check['success']:
                    yield StreamEvent(StreamEventType.ERROR, {
                        "message": "Insufficient credits",
                        "error_type": "insufficient_credits",
                        "balance": credit_check.get('balance', 0)
                    }).to_sse()
                    return
            except Exception as e:
                logger.error(f"âš ï¸ [STREAM_QUERY] Credit check system error -- blocking request: {e}")
                yield StreamEvent(StreamEventType.ERROR, {
                    "message": "Credit verification temporarily unavailable. Please retry.",
                    "error_type": "credit_check_unavailable"
                }).to_sse()
                return
            
            # Extract query parameters
            query_text = payload.get("query", "").strip()
            multimodal_attachments = payload.get("multimodal_attachments", [])
            
            logger.info(f"ðŸ“‹ [STREAM_QUERY] Query text length: {len(query_text)}, Attachments: {len(multimodal_attachments)}")
            
            if not query_text and not multimodal_attachments:
                yield StreamEvent(StreamEventType.ERROR, {"message": "Query text or attachments required"}).to_sse()
                return
            
            # Validate and extract all parameters (similar to /query endpoint)
            (
                user_id,
                chat_session_id,
                conversation_history,
                multimodal_attachments,
                edit_mode,
                message_pair_id,
                query_text,
                use_personal_data,
                _,
                _,
                persona_data,
                selected_folder_ids,
                folder_search_enabled,
                enterprise_entity_id,
                enterprise_entity_name,
                enterprise_entity_type,
                enterprise_entity_enabled,
                skip_post_processing,
                _,
            ) = validate_query_payload(payload)
            
            # ── MAIN CHAT IS ENTERPRISE-ONLY ────────────────────────────────
            # This surface searches dept MCP sources and the enterprise SOP
            # library. The personal vault (uploaded personal folders) is NOT a
            # source here: the UI has no upload "+", no folder panel and no
            # Data Store toggle (Citra-UI/config/featureFlags.js), and the
            # vault tools are not offered to the chat LLM downstream
            # (streaming_response.py). A stale client bundle can still send
            # use_personal_data=true — we ignore it rather than 400, so an
            # un-reloaded tab keeps working, just without the vault. The
            # request is logged so this is visible, never silent.
            if use_personal_data or selected_folder_ids or folder_search_enabled:
                logger.warning(
                    "🚫 [STREAM_QUERY] Ignoring personal-vault fields on the "
                    "enterprise-only main chat surface "
                    f"(use_personal_data={use_personal_data}, "
                    f"selected_folder_ids={selected_folder_ids}, "
                    f"folder_search_enabled={folder_search_enabled}) — "
                    "likely a stale UI bundle."
                )
            use_personal_data = False
            selected_folder_ids = None
            folder_search_enabled = False

            enable_internet_search = payload.get('enable_internet_search', False)
            logger.info(f"🌐 [STREAM_QUERY] enable_internet_search={enable_internet_search} (from payload key present: {'enable_internet_search' in payload})")

            # Optional per-request output-token override (clamped server-side
            # to MAX_OUTPUT_TOKENS_CEILING inside stream_llm_response_impl).
            _raw_max_out = payload.get('max_output_tokens')
            try:
                stream_max_output_tokens = int(_raw_max_out) if _raw_max_out is not None else None
                if stream_max_output_tokens is not None and stream_max_output_tokens <= 0:
                    stream_max_output_tokens = None
            except (TypeError, ValueError):
                stream_max_output_tokens = None
            if stream_max_output_tokens is not None:
                logger.info(f"🎯 [STREAM_QUERY] max_output_tokens override requested: {stream_max_output_tokens}")

            # Enterprise sources are ALWAYS ON for main chat — accept the old
            # payload keys but do not let them switch discovery off. With the
            # personal vault gone (see the block above) the dept_* MCP tools and
            # the enterprise SOP library are the ONLY data sources this surface
            # has; honouring a stored `use_enterprise_data=false` would leave a
            # chat that cannot search anything and cannot explain why. The UI
            # shows this as an "Always On" row, not a toggle.
            #
            # The ONE exception is General Query / model-only mode, which the
            # client marks with an explicit `model_only` flag. That is a
            # deliberate "answer from the model alone" request, not a stale
            # preference, so it is honoured.
            _client_enterprise_flag = bool(
                payload.get('use_enterprise_data', False)
                or payload.get('enterprise_enabled', False)
            )
            _model_only = bool(payload.get('model_only', False))
            enterprise_enabled = not _model_only
            if _model_only:
                logger.info(
                    "🤖 [STREAM_QUERY] model_only=True — General Query mode, enterprise "
                    "sources intentionally not discovered."
                )
            elif not _client_enterprise_flag:
                logger.info(
                    "🏢 [STREAM_QUERY] Client sent enterprise sources OFF without "
                    "model_only — overriding to ON: dept MCP + SOP library are the "
                    "only sources on this surface."
                )
            # Org identity from JWT claims (injected by auth_middleware)
            req_org_id = getattr(request.state, 'org_id', None)
            req_dept_id = getattr(request.state, 'dept_id', None)
            req_dept_ids = getattr(request.state, 'dept_ids', None) or (
                [req_dept_id] if req_dept_id else []
            )
            req_roles = getattr(request.state, 'roles', ['user'])
            # Raw JWT (Bearer …) — needed by dept_* tools to mint a service
            # token so each dept-mcp can enforce per-source visibility.
            _auth_header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
            _parts = _auth_header.split()
            req_jwt_token = _parts[1] if len(_parts) == 2 and _parts[0].lower() == "bearer" else None
            
            # Check for simple generic queries (skip if query is empty - attachments may contain the query)
            query_lower = query_text.lower().strip() if query_text else ""
            if query_lower and is_simple_generic_query(query_lower):
                logger.info(f"âš¡ [STREAM_QUERY] Simple query detected - fast path")
                simple_response = handle_simple_query(query_lower)
                yield StreamEvent(StreamEventType.CHUNK, {"text": simple_response.get("response", "")}).to_sse()
                yield StreamEvent(StreamEventType.DONE, {"processing_time": time.time() - start_time}).to_sse()
                return

            # Instant feedback: tell the UI we've started BEFORE any prefetch
            # (context retrieval + enterprise discovery) so the first SSE lands
            # in <100ms instead of only after the orchestrator round-trips. The
            # agent itself narrates "checking your documents / dept sources…"
            # once it starts; this is the immediate pre-stream acknowledgement.
            yield StreamEvent(StreamEventType.STAGE, {
                "stage": "starting", "label": "Looking at your request…"
            }).to_sse()

            # NOTE: Main chat no longer redirects any request to action-chat,
            # and it no longer opens the presentation / printable composers
            # either — the make_presentation / make_report builder tools were
            # removed from streaming_response.py, so "create a presentation" or
            # "write a report" is answered inline in chat and launches no UI.
            # Deep-research asks are likewise answered inline by the pipeline.

            # === CONTEXT PREP ===
            # Main chat is PURE AGENTIC and enterprise-only: nothing is
            # pre-fetched into the prompt. The model is handed the dept_* MCP
            # tools and retrieves on demand, so there are no context chunks to
            # build and no retrieval round-trip to wait on. Prep is therefore
            # local and synchronous — persona, recent turns, temperature — and
            # the only concurrent work left is enterprise tool discovery.

            # Kick off enterprise dept_* discovery FIRST so its round-trip
            # overlaps prep instead of following it. Bounded by a hard timeout
            # inside discover_enterprise_tools (fails loud, not silent). The
            # result is handed to stream_llm_response so it skips its own
            # (serial) discovery.
            import asyncio as _asyncio
            enterprise_prefetch_task = None
            if enterprise_enabled:
                from streaming_response import discover_enterprise_tools as _discover_ent
                enterprise_prefetch_task = _asyncio.create_task(
                    _discover_ent(
                        org_id=req_org_id, dept_id=req_dept_id, dept_ids=req_dept_ids,
                        roles=req_roles, jwt_token=req_jwt_token, query=query_text,
                    )
                )

            yield StreamEvent(StreamEventType.STAGE, {
                "stage": "analyzing_query", "label": "Reading your request"
            }).to_sse()

            # Persona system prompt. persona_data comes straight off the
            # request payload, so this needs no retrieval state.
            persona_system_prompt = None
            profession = None
            if persona_data:
                try:
                    from persona import generate_persona_system_prompt
                    persona_system_prompt = generate_persona_system_prompt(persona_data)
                    _persona_info = persona_data.get('persona', {})
                    profession = _persona_info.get('profession', persona_data.get('profession'))
                    logger.info(f"[STREAM_QUERY] Persona prompt generated for: {profession}")
                except Exception as _p_err:  # noqa: BLE001
                    logger.warning(f"[STREAM_QUERY] Persona prompt failed: {_p_err}")

            # Recent turns: last 5, user queries in full, bot replies capped at
            # 400 words. Same shaping the orchestrator applied.
            conversation_messages = []
            for _msg in (conversation_history or [])[-5:]:
                if not isinstance(_msg, dict):
                    continue
                _role = _msg.get('role', 'user')
                _content = (
                    _msg.get('content')
                    or _msg.get('message')
                    or _msg.get('user_query')
                    or ''
                ).strip()
                if not _content:
                    continue
                if _role != 'user':
                    _words = _content.split()
                    if len(_words) > 400:
                        _content = ' '.join(_words[:400]) + "..."
                conversation_messages.append({'role': _role, 'content': _content})

            # Grounding is POINTER-based, not prefetch-based: the user "has
            # context" because enterprise sources are on and the agent will
            # fetch through them. The no-context wrapper is added only when
            # there is genuinely nothing to search (General Query mode).
            grounding_meta = {
                "has_context": bool(enterprise_enabled),
                "agentic": True,
                "internet_used": bool(enable_internet_search),
                "user_enable_internet": bool(enable_internet_search),
            }
            if not enterprise_enabled:
                from prompts.grounding import no_context_wrapper
                persona_system_prompt = (
                    (persona_system_prompt or "")
                    + "\n\n---\n\n"
                    + no_context_wrapper(internet_available=bool(enable_internet_search))
                )
                logger.info("[STREAM_QUERY] No enterprise sources — no-context wrapper added")

            effective_internet_search = enable_internet_search

            from llm.llm_defaults import temperature_for
            stream_temperature = temperature_for()

            try:
                from metrics import record_grounding
                record_grounding(
                    feature='main_chat_stream',
                    chunks_used=0,
                    context_empty=not grounding_meta["has_context"],
                    temperature=stream_temperature,
                    internet_used=bool(effective_internet_search),
                    internet_reason='',
                )
            except Exception as _m_err:  # noqa: BLE001
                logger.debug(f"record_grounding (stream) skipped: {_m_err}")

            yield StreamEvent(StreamEventType.STAGE, {
                "stage": "context_ready", "label": "Ready"
            }).to_sse()
            yield StreamEvent(StreamEventType.CONTEXT_READY, {
                "context_count": 0, "strategies_used": [],
            }).to_sse()
            yield StreamEvent(StreamEventType.GROUNDING, grounding_meta).to_sse()

            # Collect the concurrently-discovered enterprise tools. On any
            # failure, mark unreachable so the model tells the user rather than
            # answering as if there were no data.
            enterprise_prefetch = None
            if enterprise_prefetch_task is not None:
                try:
                    _ent_schemas, _ent_name_map, _ent_failed = await enterprise_prefetch_task
                    enterprise_prefetch = {
                        "schemas": _ent_schemas, "name_map": _ent_name_map, "failed": _ent_failed,
                    }
                except Exception as _ent_exc:
                    logger.error(f"[STREAM_QUERY] enterprise prefetch failed: {_ent_exc}")
                    enterprise_prefetch = {"schemas": [], "name_map": {}, "failed": True}

            # === STREAMING LLM RESPONSE ===
            logger.info(f"ðŸš€ [STREAM_QUERY] Starting LLM streaming...")
            
            # Build response prompt based on whether there's text query or only attachments
            if query_text:
                response_prompt = f"""Based on the document context provided, answer the user's question comprehensively.

User Query: {query_text}"""
            else:
                # Audio/image only query - LLM will analyze the attachment
                response_prompt = """Based on the document context provided and the attached media, provide a comprehensive response."""
            
            # ðŸŽ¯ MODEL SELECTION: Choose model based on attachment types
            # Text-only queries use configured LLM
            # Multimodal queries use configured vision model
            has_image_attachments = any(
                (att.get('mimeType') or att.get('type') or '').lower().startswith('image/')
                for att in (multimodal_attachments or [])
            )
            has_audio_attachments = any(
                (att.get('mimeType') or att.get('type') or '').lower().startswith('audio/')
                for att in (multimodal_attachments or [])
            )
            
            if has_image_attachments or has_audio_attachments:
                stream_model = None
                attachment_type = "image" if has_image_attachments else "audio"
                logger.info(f"ðŸŽ¬ [STREAM_MODEL] {attachment_type.capitalize()} attachment detected - using vision model (multimodal)")
            else:
                stream_model = None
                logger.info(f"ðŸ“ [STREAM_MODEL] Text-only query - using configured LLM")
            
            # Stream LLM response
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] Calling stream_llm_response...")
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] - prompt len: {len(response_prompt) if response_prompt else 0}")
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] - model: {stream_model}")
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] - system len: {len(persona_system_prompt) if persona_system_prompt else 0}")
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] - conversation_messages: {len(conversation_messages) if conversation_messages else 0}")
            
            event_count = 0
            stream_usage_data = None  # Captured from USAGE event for post-loop billing
            collected_suggestions: List[Dict[str, Any]] = []  # SUGGESTION_CHIPS payloads
            _t_stream_start = time.time()
            logger.info(f"⏱️ [STREAM_QUERY] About to enter stream_llm_response async-for loop (prompt={len(response_prompt)} chars, conv={len(conversation_messages) if conversation_messages else 0}, internet={effective_internet_search})")
            async for event in stream_llm_response(
                prompt=response_prompt,
                model=stream_model,
                system=persona_system_prompt,
                conversation_history=conversation_messages,
                attachments=multimodal_attachments,
                enable_internet_search=effective_internet_search,
                user_id=user_id,
                user_email=user_email,
                profession=profession,
                chat_session_id=chat_session_id,
                temperature=stream_temperature,
                use_enterprise_data=enterprise_enabled,
                org_id=req_org_id,
                dept_id=req_dept_id,
                dept_ids=req_dept_ids,
                roles=req_roles,
                jwt_token=req_jwt_token,
                max_output_tokens=stream_max_output_tokens,
                use_personal_data=use_personal_data,
                selected_folder_ids=selected_folder_ids,
                persona_data=persona_data,
                # dept_* tool schemas discovered concurrently with context prep.
                enterprise_prefetch=enterprise_prefetch,
                # NOTE: personal_sa_id is no longer passed — it existed solely to
                # ground the make_presentation / make_report builder tools, which
                # were removed from main chat (see streaming_response.py).
            ):
                event_count += 1
                if event_count == 1:
                    logger.info(f"⏱️ [STREAM_QUERY] First event received in {time.time() - _t_stream_start:.2f}s (type={event.event_type.value})")
                logger.debug(f"ðŸ”µ [STREAM_DEBUG] Received event #{event_count}: type={event.event_type.value}")
                
                # Accumulate full response for citation extraction
                if event.event_type == StreamEventType.CHUNK:
                    chunk_text = event.data.get("text", "")
                    logger.debug(f"ðŸ”µ [STREAM_DEBUG] CHUNK content: '{chunk_text[:50]}...' (len={len(chunk_text)})")
                    full_response += chunk_text
                elif event.event_type == StreamEventType.USAGE:
                    stream_usage_data = event.data  # Save for post-loop credit tracking
                elif event.event_type == StreamEventType.SUGGESTION_CHIPS:
                    collected_suggestions.append(event.data)
                
                yield event.to_sse()
            
            logger.info(f"⏱️ [STREAM_QUERY] stream_llm_response loop complete in {time.time() - _t_stream_start:.2f}s. Total events: {event_count}, response={len(full_response)} chars")
            
            # ðŸ’° CREDIT TRACKING: Charge for streaming LLM usage
            if user_id and stream_usage_data:
                try:
                    from middleware.credit_check_middleware import track_query_usage
                    input_tokens = stream_usage_data.get("actual_input_tokens", 0)
                    output_tokens = stream_usage_data.get("actual_output_tokens", 0)
                    if not output_tokens:  # Fall back to estimate if SDK didn't provide actual counts
                        output_tokens = stream_usage_data.get("estimated_output_tokens", 0)
                    model_key = None
                    track_result = track_query_usage(
                        user_id=user_id,
                        email=user_email or user_id,
                        model=model_key,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    if track_result.get('success'):
                        logger.info(f"âœ… [STREAM_BILLING] Usage tracked: {user_email} - {input_tokens} input + {output_tokens} output tokens ({stream_model}). New balance: â‚¹{track_result.get('new_balance', 0):.2f}")
                    else:
                        logger.warning(f"âš ï¸ [STREAM_BILLING] Failed to track usage: {track_result.get('message')}")
                except Exception as tracking_err:
                    logger.error(f"âŒ [STREAM_BILLING] Error tracking streaming usage: {tracking_err}")
            elif user_id and not stream_usage_data:
                logger.warning(f"âš ï¸ [STREAM_BILLING] No usage data received from stream -- skipping credit deduction for {user_email}")
            elif not user_id:
                logger.warning(f"âš ï¸ [STREAM_BILLING] BILLING SKIPPED -- user_id is None/empty. Streaming tokens went UNBILLED. model={stream_model}")
            
            # Extract citations from complete response
            if full_response:
                cleaned_response, citations = extract_citations_from_response(full_response)
                if citations:
                    yield StreamEvent(StreamEventType.CITATIONS, {"citations": citations}).to_sse()
            
            # Run background post-processing only if we have a response
            if not skip_post_processing and full_response.strip():
                run_background_post_operations(
                    user_id=user_id,
                    chat_session_id=chat_session_id,
                    query_text=query_text,
                    existing_bio="",
                    answer=full_response,
                    summary="",
                    edit_mode=edit_mode,
                    message_pair_id=message_pair_id,
                    chats=conversation_history or [],
                    suggestions=collected_suggestions or None,
                )
            elif not full_response.strip():
                logger.warning(f"âš ï¸ [STREAM_QUERY] Skipping chat storage - empty response (likely due to streaming error)")
            
            logger.info(f"âœ… [STREAM_QUERY] Stream complete in {time.time() - start_time:.2f}s")
            
        except HTTPException as http_exc:
            logger.error(f"ðŸ”´ [STREAM_DEBUG] HTTPException: {http_exc.detail}")
            yield StreamEvent(StreamEventType.ERROR, {
                "message": http_exc.detail,
                "status_code": http_exc.status_code
            }).to_sse()
        except Exception as e:
            logger.error(f"âŒ [STREAM_QUERY] Error: {e}", exc_info=True)
            yield StreamEvent(StreamEventType.ERROR, {"message": str(e)}).to_sse()
        finally:
            logger.debug(f"ðŸ”µ [STREAM_DEBUG] ========== GENERATOR EXITING ==========")
    
    logger.debug(f"ðŸ”µ [STREAM_DEBUG] Creating StreamingResponse...")
    response = StreamingResponse(
        generate_sse_stream(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable proxy buffering
            "Access-Control-Allow-Origin": "*",
        }
    )
    logger.debug(f"ðŸ”µ [STREAM_DEBUG] StreamingResponse created, returning...")
    return response


# Add performance monitoring endpoints
@router.get("/performance/metrics")
async def get_performance_metrics():
    """Get performance metrics and recommendations"""
    try:
        metrics = performance_monitor.get_performance_metrics()
        return JSONResponse(content={
            "status": "success",
            "metrics": metrics,
            "timestamp": time.time()
        })
    except Exception as e:
        logging.error(f"Error getting performance metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve performance metrics")


@router.get("/performance/health")
async def get_performance_health():
    """Get performance health status and optimization recommendations"""
    try:
        health_status = performance_monitor.get_health_status()
        return JSONResponse(content={
            "status": "success",
            "health": health_status,
            "timestamp": time.time()
        })
    except Exception as e:
        logging.error(f"Error getting performance health: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve performance health")


@router.post("/performance/clear-cache")
async def clear_performance_cache():
    """Clear all performance caches for fresh benchmarking"""
    try:
        cleared_caches = []
        
        # Clear embedding cache
        global embedding_cache
        if embedding_cache:
            cache_size = len(embedding_cache)
            embedding_cache.clear()
            cleared_caches.append(f"embedding_cache ({cache_size} items)")
        
        # Clear context cache
        global context_cache
        if context_cache:
            cache_size = len(context_cache)
            context_cache.clear()
            cleared_caches.append(f"context_cache ({cache_size} items)")
        
        # Clear Milvus connection cache
        global _Milvus_client, _last_health_check
        _Milvus_client = None
        _last_health_check = 0
        cleared_caches.append("Milvus_connection_cache")
        
        return JSONResponse(content={
            "status": "success",
            "message": "Performance caches cleared successfully",
            "cleared_caches": cleared_caches,
            "timestamp": time.time()
        })
    except Exception as e:
        logging.error(f"Error clearing performance cache: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to clear performance cache")



from redis_progress_manager import (
    get_progress_manager, 
    ProgressType,
    get_research_progress as redis_get_research_progress,
    clear_research_progress as redis_clear_research_progress
)









