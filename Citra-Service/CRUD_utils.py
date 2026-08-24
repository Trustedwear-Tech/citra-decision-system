# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Requirement: pymongo bson
import os
import logging
from typing import Dict, Any, Optional

from datetime import datetime
from citra_mongo import get_mongo_client, MONGODB_DATABASE
from citra_ai_config import config

try:
    client = get_mongo_client()
except Exception as e:
    logging.error(f"Error while connecting to MongoDB: {str(e)}")
    raise

# MongoDB Configuration - using centralized manager
db = client[MONGODB_DATABASE]
chat_sessions_col = db['ChatSessions']
message_pairs_col = db['MessagePairs']
user_personas_col = db['user_personas']
users_col = db['users']  # Add users collection reference
# Note: UserDetails collection removed - personal info now stored in Milvus

def get_user_collection():
    """Get the users collection for usage tracking"""
    return users_col

# STORE CHAT 👇

def create_chat_session(user_id, chat_session_id, summary, title=None):
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - skipping chat session creation for {chat_session_id}")
        return chat_session_id
    
    try:
        existing_chat_session = chat_sessions_col.find_one({
            'user_id': user_id,
            '_id': chat_session_id  # Changed from chat_session_id field to _id
        })
        if not existing_chat_session:
            chat_session = {
                '_id': chat_session_id,  # Use chat_session_id as the document _id
                'user_id': user_id,
                'summary': summary,
                'title': title if title else 'New Chat Session',
                'isActive': True,
                'createdAt': datetime.now().isoformat(),
                'lastUpdatedAt': datetime.now().isoformat(),
                'metadata': {}
            }
            result = chat_sessions_col.insert_one(chat_session)
            logging.info(f"Chat session successfully created with id: {result.inserted_id}")
            return result.inserted_id
        else:
            chat_sessions_col.update_one(
                {'user_id': user_id, '_id': chat_session_id},  # Changed from chat_session_id field to _id
                {'$set': {'summary': summary, 'lastUpdatedAt': datetime.now().isoformat()}}
            )
            logging.info("Summary successfully updated in existing chat session.")
        
    except Exception as e:
        logging.error(f"Error when creating chat session: {str(e)}")
        raise

def store_message_pair(user_id, chat_session_id, user_message, bot_reply, message_pair_id=None, suggestions=None):
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - skipping message pair storage for {message_pair_id}")
        return message_pair_id if message_pair_id else "disabled"
    
    try:
        message_pair = {
            '_id': message_pair_id,  # Use provided ID instead of MongoDB ObjectId
            'user_id': user_id,
            'chat_session_id': chat_session_id,
            'userMessage': {
                'content': str(user_message),
                'createdAt': datetime.now().isoformat()
            },
            'botReply': {
                'content': str(bot_reply),
                'createdAt': datetime.now().isoformat()
            },
            'metadata': {}
        }
        if suggestions:
            message_pair['suggestions'] = suggestions
        # Use replace_one with upsert=True to handle duplicate key errors
        result = message_pairs_col.replace_one(
            {'_id': message_pair_id}, 
            message_pair, 
            upsert=True
        )
        return message_pair_id if message_pair_id else result.upserted_id
    except Exception as e:
        logging.error(f"Error when storing message pair: {str(e)}")
        raise

def store_chat(data):
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - skipping chat storage for session {data.get('chat_session_id')}")
        return {'message': 'Chat storage disabled - session maintained in browser only.'}
    
    user_id = data['user_id']
    chat_session_id = data['chat_session_id']
    user_query = data['user_query']
    bot_reply = data['bot_reply']
    message_pair_id = data.get('message_pair_id')  # Get message_pair_id from data
    if not user_id or not chat_session_id or not user_query or not bot_reply:
        raise ValueError("Missing required parameters.")
    try:
        summary = data.get('summary')
        title = data.get('title')
        suggestions = data.get('suggestions')
        
        create_chat_session(user_id, chat_session_id, summary, title)
        store_message_pair(user_id, chat_session_id, user_query, bot_reply, message_pair_id, suggestions=suggestions)
        return {'message': 'Chat successfully stored.'}
    except Exception as e:
        logging.error(f"Error while storing chat: {str(e)}")
        raise

# RETRIEVE CHAT 👇

def get_chat_sessions_for_user(user_id):
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - returning empty sessions list for {user_id}")
        return []
    
    sessions = chat_sessions_col.find({'user_id': user_id}).sort('lastUpdatedAt', -1)
    return list(sessions)

def get_message_pairs(user_id, chat_session_id, chat_window_size):
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - returning empty messages for session {chat_session_id}")
        return []
    
    try:
        # Ensure chat_window_size is an integer
        chat_window_size = int(chat_window_size)
        messages = message_pairs_col.find({'user_id': user_id, 'chat_session_id': chat_session_id}).sort('userMessage.createdAt', -1).limit(chat_window_size)
        return list(messages)
    except Exception as e:
        logging.error(f"Error while retreiving data from MongoDB: {str(e)}")
        return None

def recover_user_chats(user_id, chat_session_id, chat_window_size):
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - returning empty chats for session {chat_session_id}")
        return []
    
    try:
        texts = get_message_pairs(user_id, chat_session_id, chat_window_size)
        if texts is None:
            return []
        recovered_chats = []
        for pair in texts:
            userMessage = pair['userMessage']['content']
            botReply = pair['botReply']['content']

            current_chat = f"User: {userMessage}\nBot: {botReply}"
            recovered_chats.append(current_chat)
        return recovered_chats
    except Exception as e:
        logging.error(f"Error while processing user chats: {str(e)}")
        return []

def retrieve_chat(data):
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - returning empty chat data for session {data.get('chat_session_id')}")
        return {
            'chats': "No previous chats yet.",
            'summary': "No summary yet"
        }
    
    user_id = data['user_id']
    chat_session_id = data['chat_session_id']
    chat_window_size = data['chat_window_size']  # Fixed: was incorrectly using chat_session_id
    if not user_id or not chat_session_id or not chat_window_size:
        raise ValueError("Device ID, Chat Session ID, or Chat Window Size missing.")
    try:       
        chats = recover_user_chats(user_id, chat_session_id, chat_window_size)
        payload = {}
        if chats:
            response_string = '\n'.join(chat for chat in chats)
            payload['chats'] = response_string
        else:
            logging.info(f"No previous chat data found for Device ID: {user_id}")
            payload['chats'] = "No previous chats yet."
        
        chat_session = chat_sessions_col.find_one({'user_id': user_id, '_id': chat_session_id}) or {'summary': "No summary yet"}  # Changed from chat_session_id field to _id
        summary = chat_session.get('summary', "No summary yet.")
        payload['summary'] = summary
        logging.info(f"Chat retrieved payload: {payload}")
        return payload
    
    except Exception as e:
        logging.error(f"""Error while retreiving chat with details(main):
                        {user_id}-{chat_session_id}, cws={chat_window_size}
                        Error: {str(e)}""")
        raise

def update_message_pair(payload):
    """
    Updates an existing message pair in MongoDB with new user query and bot reply.
    Also updates the chat session summary.
    
    Args:
        payload (dict): Contains message_pair_id, user_id, chat_session_id, 
                       user_query, bot_reply, and summary
    """
    # Check if chat history storage is enabled
    if not config.is_chat_history_enabled():
        logging.info(f"Chat history storage is disabled - skipping message pair update for {payload.get('message_pair_id')}")
        return
    
    try:
        from datetime import datetime
        import traceback
        
        logging.info(f"Starting update_message_pair with payload: {payload}")
        
        message_pair_id = payload.get('message_pair_id')
        user_id = payload.get('user_id')
        chat_session_id = payload.get('chat_session_id')
        user_query = payload.get('user_query')
        bot_reply = payload.get('bot_reply')
        summary = payload.get('summary')
        
        # Validate required fields
        if not all([message_pair_id, user_id, chat_session_id, user_query, bot_reply]):
            logging.error("Missing required fields for message pair update")
            logging.error(f"message_pair_id: {message_pair_id}, user_id: {user_id}, chat_session_id: {chat_session_id}")
            logging.error(f"user_query: {user_query}, bot_reply: {bot_reply}")
            return
        
        # Use existing database connection and collections
        logging.info(f"Using database: {db.name}")
        
        # First, check if the message pair exists using message_pair_id field
        query = {
            "_id": message_pair_id,
            "user_id": user_id,
            "chat_session_id": chat_session_id
        }
        existing_pair = message_pairs_col.find_one(query)
        
        if not existing_pair:
            logging.warning(f"No message pair found with message_pair_id: {message_pair_id}, user_id: {user_id}, chat_session_id: {chat_session_id}")
            return
        
        logging.info(f"Found existing message pair with _id: {existing_pair['_id']}")
        
        # Update the message pair
        current_time = datetime.now().isoformat()
        suggestions = payload.get('suggestions')

        set_fields: Dict[str, Any] = {
            "userMessage.content": user_query,
            "userMessage.createdAt": current_time,
            "botReply.content": bot_reply,
            "botReply.createdAt": current_time,
            "metadata.updated_at": current_time,
        }
        if suggestions:
            set_fields["suggestions"] = suggestions

        update_result = message_pairs_col.update_one(
            query,
            {"$set": set_fields},
        )
        
        logging.info(f"Update result - matched: {update_result.matched_count}, modified: {update_result.modified_count}")
        
        if update_result.modified_count == 0:
            logging.warning(f"No message pair was modified. Matched: {update_result.matched_count}")
            return
        
        # Update chat session summary if provided
        if summary:
            session_update_result = chat_sessions_col.update_one(
                {
                    "user_id": user_id,
                    "chat_session_id": chat_session_id
                },
                {
                    "$set": {
                        "summary": summary,
                        "lastUpdatedAt": current_time
                    }
                }
            )
            logging.info(f"Chat session update result - matched: {session_update_result.matched_count}, modified: {session_update_result.modified_count}")
        
        logging.info(f"Successfully updated message pair {message_pair_id} for device {user_id}")
        
    except Exception as e:
        logging.error(f"Error updating message pair: {str(e)}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        logging.error(f"Traceback: {traceback.format_exc()}")
