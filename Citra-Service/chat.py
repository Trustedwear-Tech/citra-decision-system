# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

from typing import Dict, Optional, List, Any
from fastapi import APIRouter, Request, Query, Body, HTTPException, status
from fastapi.responses import JSONResponse
from citra_auth import get_secure_user_id

import logging
import json

import os
from datetime import datetime

from bson import ObjectId
from citra_mongo import get_mongo_client

try:
    client = get_mongo_client()
except Exception as e:
    logging.error(f"Error while connecting to MongoDB: {str(e)}")
    raise

# MongoDB Configuration - read from .env only
from citra_mongo import MONGODB_DATABASE
db = client[MONGODB_DATABASE]
chat_sessions_col = db['ChatSessions']
message_pairs_col = db['MessagePairs']
# Note: UserDetails collection removed - personal info now stored in Milvus vector store

# Utility functions
def serialize_mongo_doc(doc):
    """Convert MongoDB document to JSON serializable format"""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [serialize_mongo_doc(item) for item in doc]
    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if key == '_id':
                result[key] = str(value)
            elif isinstance(value, ObjectId):
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
    elif isinstance(doc, datetime):
        return doc.isoformat()
    elif isinstance(doc, ObjectId):
        return str(doc)
    return doc

def validate_object_id(oid_str):
    """Validate if string is a valid ObjectId"""
    try:
        ObjectId(oid_str)
        return True
    except (ValueError, TypeError):  # best-effort: invalid ObjectId string/type is the expected False case
        return False

# Chat Sessions CRUD
def get_all_chat_sessions(user_id: str, limit: int = 50, skip: int = 0):
    """Get all chat sessions for a device"""
    try:
        logging.info(f"🔍 [CHAT_SESSIONS_DEBUG] Fetching sessions for user_id: {user_id}, limit: {limit}, skip: {skip}")
        query = {'user_id': user_id} if user_id else {}
        sessions = chat_sessions_col.find(query).sort('lastUpdatedAt', -1).skip(skip).limit(limit)
        session_list = list(sessions)
        logging.info(f"✅ [CHAT_SESSIONS_DEBUG] Found {len(session_list)} sessions for device: {user_id}")
        for i, session in enumerate(session_list):
            logging.info(f"📋 [CHAT_SESSIONS_DEBUG] Session {i+1}: {session.get('_id', 'no_id')}, title: {session.get('title', 'no_title')}")
        return session_list
    except Exception as e:
        logging.error(f"❌ [CHAT_SESSIONS_DEBUG] Error getting chat sessions: {str(e)}")
        raise

def get_chat_session_by_id(user_id: str, chat_session_id: str):
    """Get specific chat session"""
    try:
        logging.info(f"🔍 [CHAT_SESSION_DEBUG] Looking for session: user_id={user_id}, chat_session_id={chat_session_id}")
        session = chat_sessions_col.find_one({
            'user_id': user_id,
            '_id': chat_session_id
        })
        if session:
            logging.info(f"✅ [CHAT_SESSION_DEBUG] Found session: {session.get('_id', 'no_id')}, created: {session.get('createdAt', 'unknown')}")
        else:
            logging.warning(f"❌ [CHAT_SESSION_DEBUG] Session not found in MongoDB: {chat_session_id}")
        return session
    except Exception as e:
        logging.error(f"❌ [CHAT_SESSION_DEBUG] Error getting chat session: {str(e)}")
        raise

def update_chat_session(user_id: str, chat_session_id: str, update_data: Dict):
    """Update chat session"""
    try:
        update_data['lastUpdatedAt'] = datetime.now().isoformat()
        result = chat_sessions_col.update_one(
            {'user_id': user_id, '_id': chat_session_id},
            {'$set': update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logging.error(f"Error updating chat session: {str(e)}")
        raise

def delete_chat_session(user_id: str, chat_session_id: str):
    """Delete chat session and associated message pairs"""
    try:
        # Delete associated message pairs first
        message_pairs_col.delete_many({
            'user_id': user_id,
            'chat_session_id': chat_session_id
        })
        
        # Delete chat session
        result = chat_sessions_col.delete_one({
            'user_id': user_id,
            '_id': chat_session_id
        })
        return result.deleted_count > 0
    except Exception as e:
        logging.error(f"Error deleting chat session: {str(e)}")
        raise

# Message Pairs CRUD
def get_message_pairs_for_session(user_id: str, chat_session_id: str, limit: int = 50, skip: int = 0):
    """Get message pairs for a specific chat session"""
    try:
        logging.info(f"🔍 [MESSAGE_PAIRS_DEBUG] Fetching message pairs: user_id={user_id}, session={chat_session_id}, limit={limit}, skip={skip}")
        messages = message_pairs_col.find({
            'user_id': user_id,
            'chat_session_id': chat_session_id
        }).sort('userMessage.createdAt', 1)
        message_list = list(messages)
        logging.info(f"✅ [MESSAGE_PAIRS_DEBUG] Found {len(message_list)} message pairs for session: {chat_session_id}")
        for i, msg in enumerate(message_list):
            logging.info(f"📋 [MESSAGE_PAIRS_DEBUG] Message {i+1}: {msg.get('_id', 'no_id')}, created: {msg.get('createdAt', 'unknown')}")
        return message_list
    except Exception as e:
        logging.error(f"❌ [MESSAGE_PAIRS_DEBUG] Error getting message pairs: {str(e)}")
        raise

def get_message_pair_by_id(message_id: str, user_id: str):
    """Get specific message pair by MongoDB ID - filtered by user_id for security"""
    try:
        if not validate_object_id(message_id):
            return None
        message = message_pairs_col.find_one({'_id': ObjectId(message_id), 'user_id': user_id})
        return message
    except Exception as e:
        logging.error(f"Error getting message pair: {str(e)}")
        raise

def update_message_pair(message_id: str, user_id: str, update_data: Dict):
    """Update message pair - filtered by user_id for security"""
    try:
        if not validate_object_id(message_id):
            return False
        result = message_pairs_col.update_one(
            {'_id': ObjectId(message_id), 'user_id': user_id},
            {'$set': update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logging.error(f"Error updating message pair: {str(e)}")
        raise

def delete_message_pair(message_id: str, user_id: str):
    """Delete specific message pair - filtered by user_id for security"""
    try:
        if not validate_object_id(message_id):
            return False
        result = message_pairs_col.delete_one({'_id': ObjectId(message_id), 'user_id': user_id})
        return result.deleted_count > 0
    except Exception as e:
        logging.error(f"Error deleting message pair: {str(e)}")
        raise

# User Details CRUD
def delete_user_chat_data(user_id: str):
    """Delete only chat sessions and message pairs for a user"""
    try:
        # Delete message pairs
        message_result = message_pairs_col.delete_many({'user_id': user_id})
        
        # Delete chat sessions
        session_result = chat_sessions_col.delete_many({'user_id': user_id})
        
        return {
            'message_pairs_deleted': message_result.deleted_count,
            'chat_sessions_deleted': session_result.deleted_count
        }
    except Exception as e:
        logging.error(f"Error deleting user chat data: {str(e)}")
        raise

def delete_all_user_data(user_id: str):
    """Delete all data for a user (chat sessions, messages) - Personal info now stored in Milvus"""
    try:
        # Delete message pairs
        message_pairs_col.delete_many({'user_id': user_id})
        
        # Delete chat sessions
        session_result = chat_sessions_col.delete_many({'user_id': user_id})
        
        # Note: Personal info is now stored in Milvus, not MongoDB
        # Personal info cleanup should be handled via Milvus operations if needed
        
        return session_result.deleted_count > 0
    except Exception as e:
        logging.error(f"Error deleting all user data: {str(e)}")
        raise

def cleanup_old_chat_sessions(user_id: str):
    """Delete old chat sessions keeping only the latest 20 and their corresponding message pairs"""
    try:
        # Get all chat sessions for the device sorted by lastUpdatedAt (newest first)
        all_sessions = list(chat_sessions_col.find(
            {'user_id': user_id}
        ).sort('lastUpdatedAt', -1))
        
        if len(all_sessions) <= 20:
            # No cleanup needed
            return {
                'message': 'No cleanup needed',
                'total_sessions': len(all_sessions),
                'sessions_deleted': 0,
                'message_pairs_deleted': 0
            }
        
        # Keep latest 20, delete the rest
        sessions_to_keep = all_sessions[:20]
        sessions_to_delete = all_sessions[20:]
        
        # Extract session IDs to delete
        session_ids_to_delete = [session['_id'] for session in sessions_to_delete]
        
        # Delete message pairs for sessions to be deleted
        message_pairs_result = message_pairs_col.delete_many({
            'user_id': user_id,
            'chat_session_id': {'$in': session_ids_to_delete}
        })
        
        # Delete old chat sessions
        sessions_result = chat_sessions_col.delete_many({
            'user_id': user_id,
            '_id': {'$in': session_ids_to_delete}
        })
        
        return {
            'message': 'Cleanup completed successfully',
            'total_sessions': len(all_sessions),
            'sessions_kept': len(sessions_to_keep),
            'sessions_deleted': sessions_result.deleted_count,
            'message_pairs_deleted': message_pairs_result.deleted_count
        }
        
    except Exception as e:
        logging.error(f"Error cleaning up old chat sessions: {str(e)}")
        raise

# Assume these helper functions are defined elsewhere:
#   get_chat_session_by_id
#   get_all_chat_sessions
#   update_chat_session
#   delete_chat_session
#   get_message_pair_by_id
#   get_message_pairs_for_session
#   update_message_pair
#   delete_message_pair
#   delete_user_chat_data
#   delete_all_user_data
#   serialize_mongo_doc
# Note: user_details functions deprecated - personal info now managed via Milvus

router = APIRouter()


@router.get("/chat")
@router.put("/chat")
@router.post("/chat")
@router.delete("/chat")
async def chat(
    request: Request,
    operation: str = Query("", description="One of: chat_sessions, message_pairs, delete_chat_data, delete_all_user_data, cleanup_old_sessions"),
    chat_session_id: Optional[str] = Query(None, description="Chat session ID (UUID string)."),
    message_id: str = Query("", description="Message pair ID."),
    limit: int = Query(50, description="Paging limit, default=50."),
    skip: int = Query(0, description="Paging skip, default=0."),
    body: Optional[dict] = Body(None, description="JSON body for PUT/POST operations"),
):
    """
    Main endpoint to handle MongoDB CRUD operations. Supported operations:

    - chat_sessions: GET, PUT, DELETE
    - message_pairs: GET, PUT, DELETE
    - delete_chat_data: DELETE
    - delete_all_user_data: DELETE
    - cleanup_old_sessions: DELETE
    
    Note: user_details operations deprecated - personal info now managed via Milvus

    Query Parameters:
    - operation: identifies which resource ("chat_sessions", "message_pairs", "delete_chat_data", "delete_all_user_data")
    - user_id: obtained from JWT token automatically
    - chat_session_id: required for chat_sessions update/delete or message_pairs listing (UUID string)
    - message_id: required for message_pairs get/update/delete
    - limit, skip: for pagination (GET list endpoints)

    JSON Body:
    - For PUT/POST on chat_sessions, message_pairs, user_details:
      pass the update/create payload in the request body.
    """
    try:
        method = request.method.upper()
        user_id = get_secure_user_id(request)
        logging.info(f"🚀 [CHAT_ENDPOINT_DEBUG] {method} request - operation: {operation}, user_id: {user_id}, chat_session_id: {chat_session_id}")
        
        # -------- chat_sessions operations --------
        if operation == "chat_sessions":
            logging.info(f"📋 [CHAT_SESSIONS_ENDPOINT] Processing chat_sessions operation with method: {method}")
            # GET chat_sessions
            if method == "GET":
                if chat_session_id is not None:
                    # Get a specific chat session
                    logging.info(f"🔍 [CHAT_SESSIONS_ENDPOINT] Getting specific session: {chat_session_id}")
                    if not user_id:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="user_id is required",
                        )
                    session = get_chat_session_by_id(user_id, chat_session_id)
                    if not session:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail="Chat session not found",
                        )
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"data": serialize_mongo_doc(session)},
                    )
                else:
                    # Get all chat sessions for a device (with pagination)
                    logging.info(f"📋 [CHAT_SESSIONS_ENDPOINT] Getting all sessions for device: {user_id}")
                    if not user_id:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="user_id is required",
                        )
                    sessions = get_all_chat_sessions(user_id, limit, skip)
                    logging.info(f"✅ [CHAT_SESSIONS_ENDPOINT] Returning {len(sessions)} sessions")
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={
                            "data": serialize_mongo_doc(sessions),
                            "count": len(sessions),
                        },
                    )

            # PUT update chat_session
            elif method == "PUT":
                if not user_id or chat_session_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="user_id and chat_session_id are required",
                    )
                update_data = body or {}
                if not update_data:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Update data is required",
                    )
                success = update_chat_session(user_id, chat_session_id, update_data)
                if success:
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"message": "Chat session updated successfully"},
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Chat session not found",
                    )

            # DELETE chat_session
            elif method == "DELETE":
                if not user_id or chat_session_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="user_id and chat_session_id are required",
                    )
                success = delete_chat_session(user_id, chat_session_id)
                logging.info(f"Delete chat session success: {success}")
                if success:
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"message": "Chat session deleted successfully"},
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Chat session not found",
                    )

            else:
                raise HTTPException(
                    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                    detail="Method not allowed for chat_sessions",
                )

        # -------- message_pairs operations --------
        elif operation == "message_pairs":
            logging.info(f"📝 [MESSAGE_PAIRS_ENDPOINT] Processing message_pairs operation with method: {method}")
            # GET message_pairs
            if method == "GET":
                if message_id:
                    # Get a specific message pair
                    logging.info(f"🔍 [MESSAGE_PAIRS_ENDPOINT] Getting specific message: {message_id}")
                    message = get_message_pair_by_id(message_id, user_id)
                    if not message:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail="Message pair not found",
                        )
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"data": serialize_mongo_doc(message)},
                    )
                else:
                    # List message pairs for a chat session
                    logging.info(f"📝 [MESSAGE_PAIRS_ENDPOINT] Getting message pairs for session: {chat_session_id}")
                    if not user_id or chat_session_id is None:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="user_id and chat_session_id are required",
                        )
                    messages = get_message_pairs_for_session(user_id, chat_session_id, limit, skip)
                    logging.info(f"✅ [MESSAGE_PAIRS_ENDPOINT] Returning {len(messages)} message pairs")
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={
                            "data": serialize_mongo_doc(messages),
                            "count": len(messages),
                        },
                    )

            # PUT update message pair
            elif method == "PUT":
                if not message_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="message_id is required",
                    )
                update_data = body or {}
                if not update_data:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Update data is required",
                    )
                success = update_message_pair(message_id, user_id, update_data)
                if success:
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"message": "Message pair updated successfully"},
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Message pair not found",
                    )

            # DELETE message pair
            elif method == "DELETE":
                if not message_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="message_id is required",
                    )
                success = delete_message_pair(message_id, user_id)
                if success:
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"message": "Message pair deleted successfully"},
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Message pair not found",
                    )

            else:
                raise HTTPException(
                    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                    detail="Method not allowed for message_pairs",
                )

        # -------- user_details operations (DEPRECATED) --------
        elif operation == "user_details":
            # User details operations have been migrated to Milvus vector store
            # Personal information is now automatically extracted and stored during chat processing
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="User details operations deprecated. Personal info now stored in Milvus vector store and managed automatically during chat processing."
            )

        # -------- delete_chat_data operation --------
        elif operation == "delete_chat_data":
            if method == "DELETE":
                if not user_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="user_id is required",
                    )
                result = delete_user_chat_data(user_id)
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "message": "Chat data deleted successfully",
                        "details": result,
                    },
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                    detail="Only DELETE method allowed for delete_chat_data",
                )

        # -------- delete_all_user_data operation --------
        elif operation == "delete_all_user_data":
            if method == "DELETE":
                if not user_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="user_id is required",
                    )
                success = delete_all_user_data(user_id)
                if success:
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"message": "All user data deleted successfully"},
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_204_NO_CONTENT,
                        detail="User not found",
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                    detail="Only DELETE method allowed for delete_all_user_data",
                )

        # -------- cleanup_old_sessions operation --------
        elif operation == "cleanup_old_sessions":
            if method == "DELETE":
                if not user_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="user_id is required",
                    )
                result = cleanup_old_chat_sessions(user_id)
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content=result,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                    detail="Only DELETE method allowed for cleanup_old_sessions",
                )

        # -------- invalid operation --------
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid operation. Supported operations: "
                    "chat_sessions, message_pairs, user_details, "
                    "delete_chat_data, delete_all_user_data, cleanup_old_sessions"
                ),
            )

    except HTTPException:
        # Re‐raise HTTPExceptions so FastAPI handles them
        raise
    except Exception as e:
        logging.error(f"Error in mongodb: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}",
        )
