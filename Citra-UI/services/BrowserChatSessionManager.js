// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Browser Chat Session Manager
 * 
 * Manages chat sessions and messages in browser memory when MongoDB storage is disabled.
 * Provides the same interface as the MongoDB backend but stores data only in browser session.
 */

import { CONFIG } from '../config/config';

class BrowserChatSessionManager {
  constructor() {
    this.isEnabled = !CONFIG.features.mongodbChatHistory;
    this.sessions = new Map(); // sessionId -> session data
    this.messages = new Map(); // sessionId -> array of messages
    this.maxMessages = 20; // Maximum messages to keep per session
    
    if (this.isEnabled) {
      console.log('🧠 Browser Chat Session Manager initialized - MongoDB storage disabled');
    }
  }

  // Check if browser-only mode is enabled
  isBrowserOnlyMode() {
    return this.isEnabled;
  }

  // Create or update a chat session
  createSession(sessionId, title = 'New Chat', summary = '') {
    if (!this.isEnabled) return;
    
    this.sessions.set(sessionId, {
      id: sessionId,
      title,
      summary,
      timestamp: new Date().toISOString(),
      isActive: true,
      createdAt: new Date().toISOString(),
      lastUpdatedAt: new Date().toISOString()
    });
    
    if (!this.messages.has(sessionId)) {
      this.messages.set(sessionId, []);
    }
    
    console.log(`💾 Created browser session: ${sessionId}`);
  }

  // Add a message to a session
  addMessage(sessionId, userMessage, botReply, messageId = null) {
    if (!this.isEnabled) return;
    
    if (!this.sessions.has(sessionId)) {
      this.createSession(sessionId);
    }

    const messages = this.messages.get(sessionId) || [];
    
    const newMessage = {
      id: messageId || `msg_${Date.now()}_${Math.random()}`,
      user: userMessage,
      bot: botReply,
      timestamp: new Date().toISOString()
    };

    messages.push(newMessage);

    // Keep only the last N messages
    if (messages.length > this.maxMessages) {
      messages.splice(0, messages.length - this.maxMessages);
    }

    this.messages.set(sessionId, messages);

    // Update session timestamp
    const session = this.sessions.get(sessionId);
    if (session) {
      session.lastUpdatedAt = new Date().toISOString();
    }

    console.log(`💾 Added message to browser session ${sessionId}, total messages: ${messages.length}`);
    return newMessage;
  }

  // Get recent messages for a session (for sending to query API)
  getRecentMessages(sessionId, limit = 20) {
    if (!this.isEnabled) return [];
    
    const messages = this.messages.get(sessionId) || [];
    const recentMessages = messages.slice(-limit);
    
    // Convert to the format expected by the query API
    return recentMessages.map(msg => ({
      user: msg.user,
      bot: msg.bot
    }));
  }

  // Get all sessions for history display
  getAllSessions() {
    if (!this.isEnabled) return [];
    
    const sessionsArray = Array.from(this.sessions.values());
    // Sort by last updated, newest first
    return sessionsArray.sort((a, b) => new Date(b.lastUpdatedAt) - new Date(a.lastUpdatedAt));
  }

  // Get messages for a specific session
  getSessionMessages(sessionId) {
    if (!this.isEnabled) return [];
    
    return this.messages.get(sessionId) || [];
  }

  // Update session title
  updateSessionTitle(sessionId, title) {
    if (!this.isEnabled) return;
    
    const session = this.sessions.get(sessionId);
    if (session) {
      session.title = title;
      session.lastUpdatedAt = new Date().toISOString();
      console.log(`💾 Updated browser session title: ${sessionId} -> ${title}`);
    }
  }

  // Delete a session
  deleteSession(sessionId) {
    if (!this.isEnabled) return;
    
    this.sessions.delete(sessionId);
    this.messages.delete(sessionId);
    console.log(`🗑️ Deleted browser session: ${sessionId}`);
  }

  // Clear all sessions (when browser is closed or user logs out)
  clearAllSessions() {
    if (!this.isEnabled) return;
    
    this.sessions.clear();
    this.messages.clear();
    console.log('🗑️ Cleared all browser sessions');
  }

  // Get session by ID
  getSession(sessionId) {
    if (!this.isEnabled) return null;
    
    return this.sessions.get(sessionId) || null;
  }

  // Check if a session exists
  hasSession(sessionId) {
    if (!this.isEnabled) return false;
    
    return this.sessions.has(sessionId);
  }

  // Get session count
  getSessionCount() {
    if (!this.isEnabled) return 0;
    
    return this.sessions.size;
  }

  // Export sessions for debugging
  exportSessions() {
    if (!this.isEnabled) return { sessions: {}, messages: {} };
    
    return {
      sessions: Object.fromEntries(this.sessions),
      messages: Object.fromEntries(this.messages)
    };
  }
}

// Create singleton instance
const browserChatManager = new BrowserChatSessionManager();

export default browserChatManager;
