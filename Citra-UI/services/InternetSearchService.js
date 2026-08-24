// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Internet Search Service
 * Handles API calls for internet search using Serper API
 */

import authService from './authService';
import CONFIG from '../config/config';

// Normalize service base so prod env vars that omit /citra-ai still work
const normalizeServiceBase = (url) => {
  if (!url || typeof url !== 'string') return null;
  const trimmed = url.replace(/\/$/, '');
  if (/\/citra-ai(\b|\/)/.test(trimmed)) return trimmed;
  return `${trimmed}/citra-ai`;
};

const buildApiBaseUrl = () => {
  try {
    const configuredBase = normalizeServiceBase(CONFIG?.CITRA_SERVICE_URL);
    if (configuredBase) {
      // CONFIG.CITRA_SERVICE_URL should include /citra-ai, but normalize in case it does not
      return `${configuredBase}/api`;
    }
  } catch (err) {
    console.warn('⚠️ InternetSearchService: Unable to resolve CONFIG.CITRA_SERVICE_URL, falling back to default.', err);
  }
  // Fallback should never happen if CONFIG is properly imported, but kept for safety
  console.error('❌ InternetSearchService: CONFIG.CITRA_SERVICE_URL not found! Using hardcoded fallback.');
  return 'http://localhost:8085/citra-ai/api';
};

const API_BASE_URL = buildApiBaseUrl();

class InternetSearchService {
  constructor() {
    this.baseUrl = API_BASE_URL;
  }

  /**
   * Get proxy URL for rendering web pages in iframe (bypasses CORS)
   * @param {string} url - Original URL to proxy
   * @param {boolean} fastMode - Use fast mode (skip URL rewriting, rely on base tag) - 2-5x faster
   * @returns {Promise<string>} Proxied URL with auth token
   */
  async getProxyUrl(url, fastMode = true) {
    // Use regex with $ anchor to only strip trailing /api, not /api in hostname (e.g. api.citra-ai.com)
    const proxyBaseUrl = this.baseUrl.replace(/\/api$/, '');
    const token = await authService.getToken();
    const fastParam = fastMode ? '&fast=true' : '';
    return `${proxyBaseUrl}/proxy?url=${encodeURIComponent(url)}&token=${encodeURIComponent(token)}${fastParam}`;
  }

  /**
   * Search the internet using Serper API
   * @param {string} query - Search query
   * @param {string} country - Country code (default: "in" for India)
   * @param {string} language - Language code (default: "en" for English)
   * @param {number} page - Page number for pagination
   */
  async searchInternet(query, country = 'in', language = 'en', page = 1) {
    try {
      console.log(`🔍 Internet Search: "${query}"`);

      const url = `${this.baseUrl}/reader/internet/search`;

      const response = await authService.authenticatedFetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          query: query,
          country: country,
          language: language,
          page: page
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error('Search failed');
      }

      console.log(`✅ Search completed: ${data.results?.organic?.length || 0} results`);

      return {
        success: true,
        query: data.query,
        results: data.results
      };

    } catch (error) {
      console.error('❌ Internet Search Error:', error);
      return {
        success: false,
        error: error.message,
        results: {}
      };
    }
  }

  /**
   * Fetch full content from a web page
   * @param {string} url - URL of the page to fetch (use proxy URL for anti-bot protection)
   */
  async fetchPageContent(url) {
    try {
      console.log(`🌐 Fetching page: ${url}`);

      const apiUrl = `${this.baseUrl}/reader/internet/fetch-page`;

      const response = await authService.authenticatedFetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ url })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error('Failed to fetch page content');
      }

      console.log(`✅ Page fetched: ${data.content_length} characters`);

      return {
        success: true,
        url: data.url,
        title: data.title,
        description: data.description,
        content: data.content,
        contentLength: data.content_length
      };

    } catch (error) {
      console.error('❌ Fetch Page Error:', error);
      return {
        success: false,
        error: error.message
      };
    }
  }

  /**
   * Chat with AI about a web page
   * @param {string} url - URL of the page
   * @param {string} query - User's question
   * @param {string} pageContent - Full page content
   * @param {string} pageTitle - Page title
   * @param {Array} conversationHistory - Previous conversation for caching [{role: 'user'|'assistant', content: '...'}]
   */
  async chatWithPage(url, query, pageContent, pageTitle = '', conversationHistory = []) {
    try {
      console.log(`💬 Chat with page: "${query}"`);

      const apiUrl = `${this.baseUrl}/reader/internet/chat`;

      const response = await authService.authenticatedFetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          url,
          query,
          page_content: pageContent,
          page_title: pageTitle,
          conversation_history: conversationHistory
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error('Chat failed');
      }

      console.log(`✅ Chat response received`);

      return {
        success: true,
        query: data.query,
        response: data.response,
        cache_hit_expected: data.cache_hit_expected // Pass through cache status
      };

    } catch (error) {
      console.error('❌ Chat Error:', error);
      return {
        success: false,
        error: error.message
      };
    }
  }

  /**
   * Chat with AI about a personal document
   * @param {string} documentId - Document ID
   * @param {string} query - User's question
   * @param {string} documentContent - Full document content
   * @param {string} documentTitle - Document title
   * @param {Array} conversationHistory - Previous conversation for caching [{role: 'user'|'assistant', content: '...'}]
   */
  async chatWithDocument(documentId, query, documentContent, documentTitle = '', conversationHistory = []) {
    try {
      console.log(`💬 Chat with document: "${query}"`);

      const apiUrl = `${this.baseUrl}/reader/document/chat`;

      const response = await authService.authenticatedFetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          document_id: documentId,
          query,
          document_content: documentContent,
          document_title: documentTitle,
          conversation_history: conversationHistory
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error('Chat failed');
      }

      console.log(`✅ Chat response received`);

      return {
        success: true,
        query: data.query,
        response: data.response,
        cache_hit_expected: data.cache_hit_expected // Pass through cache status
      };

    } catch (error) {
      console.error('❌ Chat Error:', error);
      return {
        success: false,
        error: error.message
      };
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════════════
  // STREAMING CHAT METHODS FOR READER
  // ═══════════════════════════════════════════════════════════════════════════════════

  /**
   * Streaming chat with internet page content
   * @param {string} url - Page URL
   * @param {string} query - User's question
   * @param {string} pageContent - Full page content
   * @param {string} pageTitle - Page title
   * @param {Array} conversationHistory - Previous conversation
   * @param {Object} callbacks - Event callbacks
   */
  async streamChatWithPage(url, query, pageContent, pageTitle = '', conversationHistory = [], callbacks = {}) {
    console.log(`🌊 Streaming chat with page: "${query}"`);

    const apiUrl = `${this.baseUrl}/reader/internet/chat/stream`;

    return this._streamChat(apiUrl, {
      url,
      query,
      page_content: pageContent,
      page_title: pageTitle,
      conversation_history: conversationHistory
    }, callbacks);
  }

  /**
   * Streaming chat with personal document
   * @param {string} documentId - Document ID
   * @param {string} query - User's question
   * @param {string} documentContent - Full document content
   * @param {string} documentTitle - Document title
   * @param {Array} conversationHistory - Previous conversation
   * @param {Object} callbacks - Event callbacks
   */
  async streamChatWithDocument(documentId, query, documentContent, documentTitle = '', conversationHistory = [], callbacks = {}) {
    console.log(`🌊 Streaming chat with document: "${query}"`);

    const apiUrl = `${this.baseUrl}/reader/document/chat/stream`;

    return this._streamChat(apiUrl, {
      document_id: documentId,
      query,
      document_content: documentContent,
      document_title: documentTitle,
      conversation_history: conversationHistory
    }, callbacks);
  }

  /**
   * Internal streaming chat handler
   * @private
   */
  async _streamChat(apiUrl, payload, callbacks) {
    const { onChunk, onMermaid, onComplete, onError } = callbacks;

    try {
      const response = await authService.authenticatedFetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream'
        },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let fullText = '';
      let completionCalled = false;
      
      // CRITICAL FIX: Collect mermaid blocks during streaming to inject into final text
      const mermaidBlocks = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process SSE events
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let currentEvent = { type: null, data: null };

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent.type = line.substring(7).trim();
          } else if (line.startsWith('data: ')) {
            currentEvent.data = line.substring(6);
          } else if (line === '' && currentEvent.type && currentEvent.data) {
            // Process complete event
            try {
              const data = JSON.parse(currentEvent.data);

              switch (currentEvent.type) {
                case 'chunk':
                  const chunkText = data.text || '';
                  fullText += chunkText;
                  onChunk?.(chunkText, fullText);
                  break;

                case 'mermaid_block':
                  console.log(`📊 [InternetSearchService] Mermaid block ${mermaidBlocks.length} received`);
                  mermaidBlocks.push(data);
                  onMermaid?.(data);
                  break;

                case 'done':
                  // CRITICAL FIX: Inject mermaid blocks into fullText before calling onComplete
                  // Replace [Diagram rendering...] placeholders with actual mermaid markdown
                  let processedText = fullText;
                  if (mermaidBlocks.length > 0) {
                    console.log(`📊 [InternetSearchService] Injecting ${mermaidBlocks.length} mermaid blocks into response`);
                    
                    // Replace placeholders with actual mermaid code blocks
                    mermaidBlocks.forEach((block, index) => {
                      const mermaidCode = block.code || '';
                      if (mermaidCode) {
                        const mermaidMarkdown = `\n\`\`\`mermaid\n${mermaidCode}\n\`\`\`\n`;
                        
                        // Try to replace the placeholder
                        // The placeholder could be: [Diagram rendering...] or similar variations
                        const placeholderPatterns = [
                          /\[Diagram rendering\.{3,}\]/gi,
                          /\[Diagram rendering\.\.\.\]/gi,
                          /\n\[Diagram rendering[^\]]*\]\n/gi,
                        ];
                        
                        let replaced = false;
                        for (const pattern of placeholderPatterns) {
                          if (pattern.test(processedText)) {
                            processedText = processedText.replace(pattern, mermaidMarkdown);
                            replaced = true;
                            console.log(`✅ [InternetSearchService] Replaced placeholder with mermaid block ${index}`);
                            break;
                          }
                        }
                        
                        // If no placeholder found, append at the end before the explanation
                        if (!replaced) {
                          // Find a good insertion point - after the intro text
                          const introEndMatch = processedText.match(/\n\n(?=This diagram|The diagram|Here's|Below)/i);
                          if (introEndMatch && introEndMatch.index !== undefined) {
                            processedText = processedText.slice(0, introEndMatch.index) + mermaidMarkdown + processedText.slice(introEndMatch.index);
                          } else {
                            // If nothing found, just append
                            processedText += mermaidMarkdown;
                          }
                          console.log(`⚠️ [InternetSearchService] No placeholder found, inserted mermaid block ${index}`);
                        }
                      }
                    });
                  }
                  completionCalled = true;
                  onComplete?.({ fullText: processedText, mermaidBlocks, ...data });
                  break;

                case 'error':
                  onError?.(data);
                  break;
              }
            } catch (e) {
              console.warn('Failed to parse SSE event:', e);
            }
            currentEvent = { type: null, data: null };
          }
        }
      }

      // Final completion if not already called (e.g. stream ended without 'done' event)
      if (fullText && !completionCalled) {
        // Also inject mermaid blocks in final completion
        let processedText = fullText;
        if (mermaidBlocks.length > 0) {
          mermaidBlocks.forEach((block, index) => {
            const mermaidCode = block.code || '';
            if (mermaidCode) {
              const mermaidMarkdown = `\n\`\`\`mermaid\n${mermaidCode}\n\`\`\`\n`;
              const placeholderPatterns = [
                /\[Diagram rendering\.{3,}\]/gi,
                /\[Diagram rendering\.\.\.\]/gi,
                /\n\[Diagram rendering[^\]]*\]\n/gi,
              ];
              
              for (const pattern of placeholderPatterns) {
                if (pattern.test(processedText)) {
                  processedText = processedText.replace(pattern, mermaidMarkdown);
                  break;
                }
              }
            }
          });
        }
        onComplete?.({ fullText: processedText, mermaidBlocks, success: true });
      }

      return { success: true, response: fullText };

    } catch (error) {
      console.error('❌ Streaming chat error:', error);
      onError?.({ message: error.message });
      return { success: false, error: error.message };
    }
  }
}

// Export singleton instance
const internetSearchService = new InternetSearchService();

/**
 * Clean raw HTML text using backend BeautifulSoup processing.
 * Used by WebViewer to clean content extracted from the rendered iframe.
 * @param {string} html - Raw HTML or text content to clean
 * @param {string} title - Optional page title
 * @returns {Promise<{success: boolean, content?: string, title?: string, contentLength?: number, error?: string}>}
 */
internetSearchService.cleanHtmlContent = async function(html, title = 'Untitled Web Page') {
  try {
    const apiUrl = `${this.baseUrl}/reader/internet/clean-html`;

    const response = await authService.authenticatedFetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ html, title })
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();
    if (!data.success) throw new Error('Failed to clean HTML content');

    return {
      success: true,
      content: data.content,
      title: data.title,
      contentLength: data.content_length
    };
  } catch (error) {
    console.error('❌ Clean HTML Error:', error);
    return { success: false, error: error.message };
  }
};

export default internetSearchService;