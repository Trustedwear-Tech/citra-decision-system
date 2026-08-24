// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import AsyncStorage from '@react-native-async-storage/async-storage';
import axios from 'axios';
import { cachedAxios, apiCache } from './utils/apiCache';

class AsyncStorageManager {
  constructor(baseUrl, documentUrl, chatCrudUrl, milvusCrudUrl, transcriptUrl, deviceId) {
    this.baseUrl = baseUrl;
    this.documentUrl = documentUrl;
    this.chatCrudUrl = chatCrudUrl;
    this.milvusCrudUrl = milvusCrudUrl;
    this.transcriptUrl = transcriptUrl;
    this.deviceId = deviceId;

    // Storage keys
    this.STORAGE_KEYS = {
      CHAT_SESSIONS: 'chat_sessions',
      NOTES: 'notes',
      TRANSCRIPTS: 'transcripts',
      DOCUMENTS: 'documents',
      USER_DETAIL: 'user_detail',
      MESSAGE_PAIRS: 'message_pairs_', // Will be suffixed with session ID
      QUERY_SOURCES: 'query_sources_preferences'
    };
  }

  // Update device ID (to use user email instead of UUID)
  updateDeviceId(newDeviceId) {
    this.deviceId = newDeviceId;
    if (process.env.NODE_ENV === 'development') {
      console.log('📱 [STORAGE] Device ID updated to:', newDeviceId);
    }
  }

  // Clear all storage on app start
  async clearAllStorage() {
    try {
      console.log('🗑️ [STORAGE] Starting AsyncStorage clear...');
      await AsyncStorage.clear();
      console.log('✅ [STORAGE] AsyncStorage cleared successfully');
    } catch (error) {
      console.error('❌ [STORAGE] Error clearing AsyncStorage:', error);
      throw error; // Re-throw to be handled by caller
    }
  }

  // Initialize storage with fresh data from APIs
  async initializeStorage() {
    try {
      console.log('📦 [STORAGE] Initializing storage with minimal setup...');
      
      // Only initialize basic storage structure, don't load user details
      // User details will be loaded when user navigates to personal info screen
      
      console.log('✅ [STORAGE] Basic storage initialization completed');
    } catch (error) {
      console.error('❌ [STORAGE] Error during storage initialization:', error);
      throw error;
    }
  }

  // Chat Sessions Operations
  async loadChatSessionsFromAPI() {
    try {
      console.log('🚀 [CHAT_SESSIONS_API] Starting to load chat sessions from API');
      console.log('🔗 [CHAT_SESSIONS_API] Device ID:', this.deviceId);
      
      const url = `${this.chatCrudUrl}?operation=chat_sessions&user_id=${this.deviceId}&limit=50&skip=0`;
      console.log('🌐 [CHAT_SESSIONS_API] Request URL:', url);
      
      const response = await cachedAxios.get(url);
      console.log('📡 [CHAT_SESSIONS_API] Response status:', response.status);
      console.log('📡 [CHAT_SESSIONS_API] Response data:', response.data);
      
      if (response.status === 200) {
        const rawSessions = response.data.data || [];
        console.log('📋 [CHAT_SESSIONS_API] Raw sessions count:', rawSessions.length);
        
        const sessions = rawSessions.map((session, index) => {
          console.log(`📝 [CHAT_SESSIONS_API] Processing session ${index + 1}:`, session);
          return {
            id: session._id, // Use _id instead of chat_session_id for consistency
            title: session.title || 'New Chat',
            summary: session.summary || '',
            timestamp: session.lastUpdatedAt || session.createdAt,
            isActive: session.isActive,
            mongoId: session._id
          };
        });
        
        console.log('✅ [CHAT_SESSIONS_API] Processed sessions:', sessions);
        await this.storeChatSessions(sessions);
        return sessions;
      }
      console.log('❌ [CHAT_SESSIONS_API] Non-200 response, returning empty array');
      return [];
    } catch (error) {
      console.error('❌ [CHAT_SESSIONS_API] Error loading chat sessions from API:', error);
      console.error('❌ [CHAT_SESSIONS_API] Error details:', error.message);
      console.error('❌ [CHAT_SESSIONS_API] Error response:', error.response?.data);
      return [];
    }
  }

  async storeChatSessions(sessions) {
    try {
      console.log('Storing chat sessions:', sessions);
      await AsyncStorage.setItem(this.STORAGE_KEYS.CHAT_SESSIONS, JSON.stringify(sessions));
    } catch (error) {
      console.error('Error storing chat sessions:', error);
    }
  }

  async getChatSessions() {
    try {
      const data = await AsyncStorage.getItem(this.STORAGE_KEYS.CHAT_SESSIONS);
      return data ? JSON.parse(data) : [];
    } catch (error) {
      console.error('Error getting chat sessions:', error);
      return [];
    }
  }

  async addChatSession(session) {
    try {
      const sessions = await this.getChatSessions();
      sessions.unshift(session); // Add to beginning
      await this.storeChatSessions(sessions);
    } catch (error) {
      console.error('Error adding chat session:', error);
    }
  }

  async deleteChatSession(sessionId) {
    try {
      const sessions = await this.getChatSessions();
      const updatedSessions = sessions.filter(session => session.id !== sessionId);
      await this.storeChatSessions(updatedSessions);
      
      // Also delete associated message pairs
      await AsyncStorage.removeItem(this.STORAGE_KEYS.MESSAGE_PAIRS + sessionId);
    } catch (error) {
      console.error('Error deleting chat session:', error);
    }
  }

  async clearAllChatSessions() {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.CHAT_SESSIONS, JSON.stringify([]));
      
      // Clear all message pairs
      const sessions = await this.getChatSessions();
      for (const session of sessions) {
        await AsyncStorage.removeItem(this.STORAGE_KEYS.MESSAGE_PAIRS + session.id);
      }
    } catch (error) {
      console.error('Error clearing all chat sessions:', error);
    }
  }

  // Notes Operations
  async loadNotesFromAPI() {
    try {
      // Use the new /note endpoint with GET method and operation=list
      const url = `${this.milvusCrudUrl}?operation=list&user_id=${this.deviceId}&limit=100&skip=0`;
      const response = await cachedAxios.get(url);
      
      if (response.status === 200) {
        const notes = response.data.notes || [];
        
        const noteItems = notes
          .map(note => ({
            id: note._id || note.note_id,
            text: note.text,
            timestamp: note.created_at,
            title: note.title,
            vectorId: note.vector_id
          }))
          .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        
        await this.storeNotes(noteItems);
        return noteItems;
      }
      return [];
    } catch (error) {
      console.error('Error loading notes from API:', error);
      return [];
    }
  }

  async storeNotes(notes) {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.NOTES, JSON.stringify(notes));
    } catch (error) {
      console.error('Error storing notes:', error);
    }
  }

  async getNotes() {
    try {
      const data = await AsyncStorage.getItem(this.STORAGE_KEYS.NOTES);
      return data ? JSON.parse(data) : [];
    } catch (error) {
      console.error('Error getting notes:', error);
      return [];
    }
  }

  async addNote(note) {
    try {
      const notes = await this.getNotes();
      notes.unshift(note); // Add to beginning
      await this.storeNotes(notes);
    } catch (error) {
      console.error('Error adding note:', error);
    }
  }

  async deleteNote(noteId) {
    try {
      const notes = await this.getNotes();
      const updatedNotes = notes.filter(note => note.id !== noteId);
      await this.storeNotes(updatedNotes);
    } catch (error) {
      console.error('Error deleting note:', error);
    }
  }

  async clearAllNotes() {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.NOTES, JSON.stringify([]));
    } catch (error) {
      console.error('Error clearing all notes:', error);
    }
  }

  // User Detail Operations
  async loadUserDetailFromAPI() {
    try {
      console.log('🌐 [API] Making request to Chat API for user details...');
      const url = `${this.chatCrudUrl}?operation=user_details&user_id=${this.deviceId}`;
      console.log('🔗 [API] URL:', url);
      
      const response = await cachedAxios.get(url, {
        timeout: 8000, // 8 second timeout for API call
        headers: {
          'Content-Type': 'application/json'
        }
      });
      
      console.log('📡 [API] Response status:', response.status);
      console.log('📡 [API] Response data:', response.data);
      
      if (response.status === 200) {
        let userDetail = response.data.data?.bio || '';
        console.log('👤 [API] Raw user detail from API:', userDetail);
        
        // Clean up any invalid default values that might come from the API
        if (userDetail.trim() === '```' || userDetail.trim() === '...' || userDetail.trim() === '```   ``` ') {
          userDetail = '';
          console.log('🧹 [API] Cleaned invalid user detail value');
        }
        
        await this.storeUserDetail(userDetail);
        console.log('✅ [API] User detail processed and stored successfully');
        return userDetail;
      } else {
        console.warn('⚠️ [API] Unexpected response status:', response.status);
        return '';
      }
    } catch (error) {
      console.error('❌ [API] Error loading user detail from API:', error);
      console.error('❌ [API] Error details:', {
        message: error.message,
        code: error.code,
        response: error.response?.status,
        timeout: error.code === 'ECONNABORTED'
      });
      
      if (error.code === 'ECONNABORTED') {
        console.error('⏰ [API] Request timed out - network connection may be slow');
      } else if (error.response?.status) {
        console.error(`🔴 [API] Server responded with error status: ${error.response.status}`);
      } else if (error.message === 'Network Error') {
        console.error('🌐 [API] Network error - check internet connection');
      }
      
      throw error; // Re-throw to be handled by caller
    }
  }

  async storeUserDetail(userDetail) {
    try {
      console.log('👉 Storing user detail:', userDetail);
      await AsyncStorage.setItem(this.STORAGE_KEYS.USER_DETAIL, userDetail || '');
    } catch (error) {
      console.error('Error storing user detail:', error);
    }
  }

  async getUserDetail() {
    try {
      const detail = await AsyncStorage.getItem(this.STORAGE_KEYS.USER_DETAIL);
      console.log('👉 Retrieved user detail:', detail);
      
      // Clean up any invalid default values
      if (!detail || detail.trim() === '```' || detail.trim() === '...' || detail.trim() === '```   ``` ') {
        return '';
      }
      
      return detail;
    } catch (error) {
      console.error('Error getting user detail:', error);
      return '';
    }
  }

  async refreshUserDetail() {
    return await this.loadUserDetailFromAPI();
  }

  // Message Pairs Operations
  async loadMessagePairsFromAPI(chatSessionId) {
    try {
      console.log('🔄 [MESSAGE_PAIRS] Loading message pairs for session:', chatSessionId);
      const response = await axios.get(`${this.chatCrudUrl}?operation=message_pairs&user_id=${this.deviceId}&chat_session_id=${chatSessionId}&limit=100&skip=0`);
      
      console.log('📡 [MESSAGE_PAIRS] API Response status:', response.status);
      console.log('📡 [MESSAGE_PAIRS] API Response data:', response.data);
      
      if (response.status === 200) {
        const messagePairs = response.data.data;
        console.log('📝 [MESSAGE_PAIRS] Raw message pairs from API:', messagePairs);
        
        const messages = [
          { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' }
        ];
        
        // Create a flat array of all messages with timestamps for proper sorting
        const allMessages = [];
        
        messagePairs.forEach((pair, index) => {
          console.log(`📋 [MESSAGE_PAIRS] Processing pair ${index}:`, pair);
          console.log(`🔍 [MESSAGE_PAIRS] Pair keys:`, Object.keys(pair));
          console.log(`🔍 [MESSAGE_PAIRS] user_query exists:`, !!pair.user_query);
          console.log(`🔍 [MESSAGE_PAIRS] bot_reply exists:`, !!pair.bot_reply);
          console.log(`🔍 [MESSAGE_PAIRS] userMessage exists:`, !!pair.userMessage);
          console.log(`🔍 [MESSAGE_PAIRS] botReply exists:`, !!pair.botReply);
          console.log(`🔍 [MESSAGE_PAIRS] userMessage object:`, pair.userMessage);
          console.log(`🔍 [MESSAGE_PAIRS] botReply object:`, pair.botReply);
          
          // Handle user message - try different possible field names
          let userText = pair.user_query || pair.userMessage?.content || pair.userMessage?.text || (typeof pair.userMessage === 'object' ? pair.userMessage?.content : pair.userMessage);
          console.log(`🔍 [MESSAGE_PAIRS] User text extracted:`, userText);
          console.log(`🔍 [MESSAGE_PAIRS] User text type:`, typeof userText);
          if (userText) {
            const userMsg = {
              id: `user-${pair._id}`,
              text: userText,
              sender: 'user',
              messagePairId: pair._id,
              timestamp: pair.createdAt,
              sortTimestamp: new Date(pair.createdAt).getTime()
            };
            console.log('👤 [MESSAGE_PAIRS] Adding user message:', userMsg);
            allMessages.push(userMsg);
          }
          
          // Handle bot message - try different possible field names  
          let botText = pair.bot_reply || pair.botReply?.content || pair.botReply?.text || (typeof pair.botReply === 'object' ? pair.botReply?.content : pair.botReply);
          console.log(`🔍 [MESSAGE_PAIRS] Bot text extracted:`, botText);
          console.log(`🔍 [MESSAGE_PAIRS] Bot text type:`, typeof botText);
          if (botText) {
            const botMsg = {
              id: `bot-${pair._id}`,
              text: botText,
              sender: 'bot',
              messagePairId: pair._id,
              timestamp: pair.createdAt,
              sortTimestamp: new Date(pair.createdAt).getTime()
            };
            console.log('🤖 [MESSAGE_PAIRS] Adding bot message:', botMsg);
            allMessages.push(botMsg);
          }
        });
        
        console.log(`🔄 [MESSAGE_PAIRS] Total messages collected: ${allMessages.length}`);
        console.log('🔄 [MESSAGE_PAIRS] All messages before sorting:', allMessages);
        
        // Sort messages by timestamp
        allMessages.sort((a, b) => a.sortTimestamp - b.sortTimestamp);
        
        console.log('🔄 [MESSAGE_PAIRS] All messages after sorting:', allMessages);
        
        // Remove sortTimestamp and add to messages array
        allMessages.forEach(msg => {
          const { sortTimestamp, ...messageWithoutSort } = msg;
          messages.push(messageWithoutSort);
        });
        
        console.log(`🔄 [MESSAGE_PAIRS] Final messages array length: ${messages.length}`);
        console.log('✅ [MESSAGE_PAIRS] Final messages array:', messages);
        await this.storeMessagePairs(chatSessionId, messages);
        return messages;
      }
      
      console.log('⚠️ [MESSAGE_PAIRS] API response was not 200, returning default message');
      return [{ id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' }];
    } catch (error) {
      console.error('❌ [MESSAGE_PAIRS] Error loading message pairs from API:', error);
      return [{ id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' }];
    }
  }

  async storeMessagePairs(chatSessionId, messages) {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.MESSAGE_PAIRS + chatSessionId, JSON.stringify(messages));
    } catch (error) {
      console.error('Error storing message pairs:', error);
    }
  }

  async getMessagePairs(chatSessionId) {
    try {
      const data = await AsyncStorage.getItem(this.STORAGE_KEYS.MESSAGE_PAIRS + chatSessionId);
      return data ? JSON.parse(data) : [{ id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' }];
    } catch (error) {
      console.error('Error getting message pairs:', error);
      return [{ id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' }];
    }
  }

  async addMessagePair(chatSessionId, userMessage, botMessage) {
    try {
      const messages = await this.getMessagePairs(chatSessionId);
      if (userMessage) messages.push(userMessage);
      if (botMessage) messages.push(botMessage);
      console.log('Adding message pair:', { userMessage, botMessage });
      await this.storeMessagePairs(chatSessionId, messages);
    } catch (error) {
      console.error('Error adding message pair:', error);
    }
  }

  async updateMessagePair(chatSessionId, messageId, newText, messagePairId = null) {
    try {
      const messages = await this.getMessagePairs(chatSessionId);
      const updatedMessages = messages.map(msg => {
        if (msg.id === messageId) {
          return { ...msg, text: newText, isUpdated: true, messagePairId: messagePairId || msg.messagePairId };
        }
        return msg;
      });
      await this.storeMessagePairs(chatSessionId, updatedMessages);
      return updatedMessages;
    } catch (error) {
      console.error('Error updating message pair:', error);
      return messages;
    }
  }

  // Transcripts Operations
  async loadTranscriptsFromAPI() {
    try {
      // Import authService for authenticated requests
      const { authService } = await import('./services/authService');
      
      // Use V2 API endpoint with authenticated fetch
      const response = await authService.authenticatedFetch(`${this.transcriptUrl}s/${this.deviceId}`);
      console.log('Loading transcripts from API:::::::::::::::', response);
      
      if (response.ok) {
        const data = await response.json();
        if (data.transcripts) {
          const formattedTranscripts = data.transcripts.map(transcript => ({
            id: transcript.transcript_id,
            topic: transcript.topic,
            text: transcript.transcript,
            duration: transcript.duration,
            timestamp: transcript.utc_date,
            deviceId: transcript.user_id
          }));
          
          await this.storeTranscripts(formattedTranscripts);
          return formattedTranscripts;
        }
      }
      return [];
    } catch (error) {
      console.error('Error loading transcripts from API:', error);
      return [];
    }
  }

  async storeTranscripts(transcripts) {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.TRANSCRIPTS, JSON.stringify(transcripts));
    } catch (error) {
      console.error('Error storing transcripts:', error);
    }
  }

  async getTranscripts() {
    try {
      const data = await AsyncStorage.getItem(this.STORAGE_KEYS.TRANSCRIPTS);
      return data ? JSON.parse(data) : [];
    } catch (error) {
      console.error('Error getting transcripts:', error);
      return [];
    }
  }

  async deleteTranscript(transcriptId) {
    try {
      const transcripts = await this.getTranscripts();
      const updatedTranscripts = transcripts.filter(transcript => transcript.id !== transcriptId);
      await this.storeTranscripts(updatedTranscripts);
    } catch (error) {
      console.error('Error deleting transcript:', error);
    }
  }

  async clearAllTranscripts() {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.TRANSCRIPTS, JSON.stringify([]));
    } catch (error) {
      console.error('Error clearing all transcripts:', error);
    }
  }

  // Documents Operations
  async loadDocumentsFromAPI() {
    try {
      // Use V2 API endpoint
      const response = await axios.get(`${this.documentUrl}s/${this.deviceId}`);
      console.log('Loading documents from API:::::::::::::::', response.data);
      
      if (response.status === 200 && response.data.documents) {
        const formattedDocuments = response.data.documents.map(document => ({
          id: document.document_id,
          title: document.title,
          text: document.text,
          timestamp: document.utc_date,
          deviceId: document.user_id,
          filename: document.filename,
          fileType: document.file_type
        }));
        
        await this.storeDocuments(formattedDocuments);
        return formattedDocuments;
      }
      return [];
    } catch (error) {
      console.error('Error loading documents from API:', error);
      return [];
    }
  }

  async storeDocuments(documents) {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.DOCUMENTS, JSON.stringify(documents));
    } catch (error) {
      console.error('Error storing documents:', error);
    }
  }

  async getDocuments() {
    try {
      const data = await AsyncStorage.getItem(this.STORAGE_KEYS.DOCUMENTS);
      return data ? JSON.parse(data) : [];
    } catch (error) {
      console.error('Error getting documents:', error);
      return [];
    }
  }

  async deleteDocument(documentId) {
    try {
      const documents = await this.getDocuments();
      const updatedDocuments = documents.filter(document => document.id !== documentId);
      await this.storeDocuments(updatedDocuments);
    } catch (error) {
      console.error('Error deleting document:', error);
    }
  }

  async clearAllDocuments() {
    try {
      await AsyncStorage.setItem(this.STORAGE_KEYS.DOCUMENTS, JSON.stringify([]));
    } catch (error) {
      console.error('Error clearing all documents:', error);
    }
  }

  // Utility methods
  async refreshNotes() {
    return await this.loadNotesFromAPI();
  }

  async refreshChatSessions() {
    return await this.loadChatSessionsFromAPI();
  }

  async refreshUserDetail() {
    return await this.loadUserDetailFromAPI();
  }

  async refreshTranscripts() {
    return await this.loadTranscriptsFromAPI();
  }

  async refreshDocuments() {
    return await this.loadDocumentsFromAPI();
  }

  // Enhanced refresh methods with storage updates
  async refreshAndStoreChatSessions() {
    try {
      const refreshedSessions = await this.loadChatSessionsFromAPI();
      await this.storeChatSessions(refreshedSessions);
      return refreshedSessions;
    } catch (error) {
      console.error('Error refreshing and storing chat sessions:', error);
      return await this.getChatSessions(); // Return cached data on error
    }
  }
  async refreshAndStoreUserDetail() {
    try {
      const refreshedUserDetail = await this.loadUserDetailFromAPI();
      if (refreshedUserDetail) {
        await this.storeUserDetail(refreshedUserDetail);
      }
      return refreshedUserDetail;
    } catch (error) {
      console.error('Error refreshing and storing user detail:', error);
      return await this.getUserDetail(); // Return cached data on error
    }
  }
  // New method to refresh and store message pairs with correct Chat IDs
  async refreshAndStoreMessagePairs(chatSessionId) {
    try {
      console.log(`Refreshing message pairs for session ${chatSessionId}...`);
      const refreshedMessages = await this.loadMessagePairsFromAPI(chatSessionId);
      console.log('Refreshed messages with Chat IDs:', refreshedMessages);
      return refreshedMessages;
    } catch (error) {
      console.error('Error refreshing and storing message pairs:', error);
      // Return cached data on error
      return await this.getMessagePairs(chatSessionId);
    }
  }

  // Enhanced method to refresh and merge message pairs intelligently
  async refreshAndMergeMessagePairs(chatSessionId, currentMessages = []) {
    try {
      console.log(`Refreshing and merging message pairs for session ${chatSessionId}...`);
      const apiMessages = await this.loadMessagePairsFromAPI(chatSessionId);
      
      // If no current messages provided, just return API messages
      if (!currentMessages || currentMessages.length === 0) {
        console.log('No current messages to merge, returning API messages');
        return apiMessages;
      }
      
      // Get timestamps of current messages to identify newer local messages
      const currentTimestamps = new Map();
      currentMessages.forEach(msg => {
        if (msg.timestamp) {
          currentTimestamps.set(msg.id, new Date(msg.timestamp).getTime());
        }
      });
      
      // Find the latest timestamp in API messages
      let latestApiTimestamp = 0;
      apiMessages.forEach(msg => {
        if (msg.timestamp) {
          const msgTime = new Date(msg.timestamp).getTime();
          latestApiTimestamp = Math.max(latestApiTimestamp, msgTime);
        }
      });
        // Start with API messages and add any local messages that are newer
      const mergedMessages = [...apiMessages];
      currentMessages.forEach(msg => {
        const msgTimestamp = currentTimestamps.get(msg.id);
        
        // Add message if it's newer than the latest API message or has no timestamp (temporary)
        // Also check for messages with 'isUpdated' flag which indicates recent local changes
        const isNewerThanApi = !msgTimestamp || msgTimestamp > latestApiTimestamp;
        const isRecentlyUpdated = msg.isUpdated === true;
        const isTemporary = !msg.timestamp || msg.id.includes(Date.now().toString().slice(-6)); // Check for temp IDs
        
        if (isNewerThanApi || isRecentlyUpdated || isTemporary) {
          // Check if this message already exists in API messages
          const existsInApi = apiMessages.some(apiMsg => apiMsg.id === msg.id);
          if (!existsInApi) {
            console.log(`Adding newer/updated local message to merged results: ${msg.id} (newer: ${isNewerThanApi}, updated: ${isRecentlyUpdated}, temp: ${isTemporary})`);
            mergedMessages.push(msg);
          } else {
            console.log(`Message ${msg.id} already exists in API, skipping local version`);
          }
        }
      });
      
      // Sort by timestamp to maintain order
      mergedMessages.sort((a, b) => {
        const aTime = a.timestamp ? new Date(a.timestamp).getTime() : 0;
        const bTime = b.timestamp ? new Date(b.timestamp).getTime() : 0;
        return aTime - bTime;
      });
      
      console.log('Merged messages with local updates preserved:', mergedMessages);
      await this.storeMessagePairs(chatSessionId, mergedMessages);
      return mergedMessages;
    } catch (error) {
      console.error('Error refreshing and merging message pairs:', error);
      // Return current messages on error to avoid data loss
      return currentMessages.length > 0 ? currentMessages : await this.getMessagePairs(chatSessionId);
    }
  }
  // Enhanced method to update message IDs in-place without triggering full UI reload
  async updateMessageIdsInPlace(chatSessionId, currentMessages = []) {
    try {
      console.log(`Updating message IDs in-place for session ${chatSessionId}...`);
        // First check if we have any messages without proper Chat IDs
      const messagesNeedingIds = currentMessages.filter(msg => {
        if (msg.id === '1') return false; // Skip welcome message
        // More conservative check - only update messages that clearly need IDs
        const hasTemporaryId = msg.id.includes(Date.now().toString().slice(-6)) || 
                              msg.id.startsWith('temp_') ||
                              msg.id.startsWith('user-temp') ||
                              msg.id.startsWith('bot-temp');
        const missingMessagePairId = !msg.messagePairId && msg.sender === 'user';
        
        return (hasTemporaryId || missingMessagePairId) && !msg.isTyping; // Don't update typing messages
      });
      
      if (messagesNeedingIds.length === 0) {
        console.log('All messages have proper Chat IDs, skipping update');
        return currentMessages;
      }
      
      console.log(`Found ${messagesNeedingIds.length} messages needing ID updates`);
      
      // Get fresh data from API to get the correct Chat IDs
      const apiMessages = await this.loadMessagePairsFromAPI(chatSessionId);
        // Create a mapping of message content to Chat IDs (using first 100 chars for better uniqueness)
      const contentToIdMap = new Map();
      apiMessages.forEach(apiMsg => {
        if (apiMsg.messagePairId) {
          const contentKey = `${apiMsg.text.substring(0, 100).trim()}_${apiMsg.sender}`;
          if (!contentToIdMap.has(contentKey)) {
            contentToIdMap.set(contentKey, {
              messagePairId: apiMsg.messagePairId,
              expectedId: apiMsg.id,
              timestamp: apiMsg.timestamp
            });
          }
        }
      });
      
      let hasChanges = false;
        // Update current messages with correct IDs without changing other properties
      const updatedMessages = currentMessages.map(msg => {
        if (msg.id === '1' || msg.isTyping) return msg; // Skip welcome message and typing indicators
        
        const contentKey = `${msg.text.substring(0, 100).trim()}_${msg.sender}`;
        const idInfo = contentToIdMap.get(contentKey);
        
        // Only update if we have matching content AND the message needs updating
        if (idInfo && (!msg.messagePairId || msg.id !== idInfo.expectedId)) {
          console.log(`Updating message ID from ${msg.id} to ${idInfo.expectedId}`);
          hasChanges = true;
          
          return {
            ...msg,
            id: idInfo.expectedId,
            messagePairId: idInfo.messagePairId,
            timestamp: idInfo.timestamp,
            isUpdated: true, // Mark as updated to prevent re-animation
            key: `${idInfo.expectedId}_updated_${Date.now()}` // Force new React key
          };
        }
        
        return msg;
      });
      
      if (hasChanges) {
        // Store updated messages
        await this.storeMessagePairs(chatSessionId, updatedMessages);
        console.log('Message IDs updated in-place successfully');
        return updatedMessages;
      } else {
        console.log('No ID changes needed');
        return currentMessages;
      }
      
    } catch (error) {
      console.error('Error updating message IDs in-place:', error);
      // Return current messages unchanged on error
      return currentMessages;
    }
  }

  // Get storage info for debugging
  async getStorageInfo() {
    try {
      const chatSessions = await this.getChatSessions();
      const notes = await this.getNotes();
      const userDetail = await this.getUserDetail();
      
      console.log('Storage Info:', {
        chatSessionsCount: chatSessions.length,
        notesCount: notes.length,
        hasUserDetail: !!userDetail
      });
      
      return {
        chatSessionsCount: chatSessions.length,
        notesCount: notes.length,
        hasUserDetail: !!userDetail
      };
    } catch (error) {
      console.error('Error getting storage info:', error);
      return null;
    }
  }

  async refreshActiveChatSession(chatSessionId) {
    try {
      const response = await axios.get(`${this.chatCrudUrl}?operation=chat_sessions&user_id=${this.deviceId}&chat_session_id=${chatSessionId}&limit=1&skip=0`);
      
      if (response.status === 200 && response.data.data) {
        const updatedSession = response.data.data;
        
        // Get existing sessions from storage
        const existingSessions = await this.getChatSessions();
        
        // Update the specific session in the array
        const updatedSessions = existingSessions.map(session => 
          session.id === chatSessionId ? {
            id: updatedSession._id, // Use _id for consistency
            title: updatedSession.title || `Chat ${updatedSession._id}`,
            summary: updatedSession.summary || '',
            timestamp: updatedSession.lastUpdatedAt || updatedSession.createdAt,
            isActive: updatedSession.isActive,
            mongoId: updatedSession._id
          } : session
        );
        
        // If session wasn't found in existing sessions, add it
        if (!existingSessions.find(session => session.id === chatSessionId)) {
          updatedSessions.push({
            id: updatedSession._id,
            title: updatedSession.title || `Chat ${updatedSession._id}`,
            summary: updatedSession.summary || '',
            timestamp: updatedSession.lastUpdatedAt || updatedSession.createdAt,
            isActive: updatedSession.isActive,
            mongoId: updatedSession._id
          });
        }
        
        // Sort by timestamp (most recent first)
        updatedSessions.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        
        // Store updated sessions
        await this.storeChatSessions(updatedSessions);
        return updatedSessions;
      }
      
      // If API call fails, return existing sessions
      return await this.getChatSessions();
    } catch (error) {
      console.error('Error refreshing active chat session:', error);
      // Return existing sessions on error
      return await this.getChatSessions();
    }
  }

  // Query Sources Preferences Operations
  async saveQuerySourcesPreferences(preferences) {
    try {
      console.log('💾 [QUERY_SOURCES] Saving preferences:', preferences);
      await AsyncStorage.setItem(
        this.STORAGE_KEYS.QUERY_SOURCES,
        JSON.stringify(preferences)
      );
      console.log('✅ [QUERY_SOURCES] Preferences saved successfully');
    } catch (error) {
      console.error('❌ [QUERY_SOURCES] Error saving preferences:', error);
    }
  }

  async getQuerySourcesPreferences() {
    try {
      const data = await AsyncStorage.getItem(this.STORAGE_KEYS.QUERY_SOURCES);
      if (data) {
        const preferences = JSON.parse(data);
        console.log('📖 [QUERY_SOURCES] Loaded preferences:', preferences);
        return preferences;
      }
      console.log('📖 [QUERY_SOURCES] No saved preferences found, using defaults');
      return null;
    } catch (error) {
      console.error('❌ [QUERY_SOURCES] Error loading preferences:', error);
      return null;
    }
  }
}

export default AsyncStorageManager;
