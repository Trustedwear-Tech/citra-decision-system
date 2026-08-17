// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * WebViewer Component
 * Displays web page content with AI chat interface
 * Also used for personal documents with AI chat
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
  KeyboardAvoidingView,
  Dimensions,
  Clipboard,
  PanResponder
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import internetSearchService from '../services/InternetSearchService';
import readerService from '../services/ReaderService';
import RichMessageRenderer from './message/RichMessageRenderer';
import authService from '../services/authService';
import CONFIG from '../config/config';

// Add CSS animation for progress bar
if (Platform.OS === 'web') {
  const style = document.createElement('style');
  style.textContent = `
    @keyframes progress-indeterminate {
      0% {
        transform: translateX(-100%);
      }
      100% {
        transform: translateX(400%);
      }
    }
  `;
  document.head.appendChild(style);
}

// Conditionally import WebView only for mobile
let WebView = null;
if (Platform.OS !== 'web') {
  WebView = require('react-native-webview').WebView;
}

// Import streaming renderer for real-time chat
import StreamingRichRenderer from './message/StreamingRichRenderer';

const WebViewer = ({
  url,
  title,
  content,
  onBack,
  onClose = null, // Optional close button to exit reader completely
  theme,
  type = 'internet', // 'internet' or 'document'
  documentId = null,
  userId = null, // User ID for context (auth handled via JWT)
  isPDF = false, // New prop to indicate if this is a PDF document
  isXAIFile = false, // New prop to indicate if this is an xAI collection file
  enableStreaming = true, // Enable SSE streaming for real-time responses
  onEmbedToVault = null, // Callback to embed current page content into selected vault
  folderId = null, // Current vault folder ID for context
  folderName = null // Current vault folder name for display
}) => {
  // State management
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [extractedContent, setExtractedContent] = useState(content); // Store extracted text for AI (cached)
  const [extracting, setExtracting] = useState(false); // Only show when actively extracting
  const [proxyUrl, setProxyUrl] = useState(null); // Store proxy URL for iframe

  // Conversation history tracking per document/URL for xAI prompt caching
  // Key format: documentId for personal docs, URL for internet pages
  const [conversationHistories, setConversationHistories] = useState({});

  // Navigation state for in-iframe browsing
  const [navigationHistory, setNavigationHistory] = useState([url]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const [currentUrl, setCurrentUrl] = useState(url);
  const [isNavigating, setIsNavigating] = useState(type === 'internet'); // Start true for internet pages to show initial loading
  const [pdfBlobUrl, setPdfBlobUrl] = useState(null); // Blob URL for PDFs on web to avoid forced download

  // Mobile mode: toggle between content view and chat view
  const [mobileActiveTab, setMobileActiveTab] = useState('content'); // 'content' or 'chat'

  // Embed to vault state
  const [embedding, setEmbedding] = useState(false);
  const [embedSuccess, setEmbedSuccess] = useState(false);

  const chatScrollRef = useRef(null);
  const webViewRef = useRef(null);
  const navigationTimeoutRef = useRef(null); // Prevent stuck progress bar
  const { width } = Dimensions.get('window');
  const isMobile = width < 768;

  // Draggable splitter state
  const [chatPanelWidth, setChatPanelWidth] = useState(0.35); // 35% default width for chat panel
  const [chatPanelHeight, setChatPanelHeight] = useState(0.4); // 40% default height for chat panel on mobile
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef(null);

  // Streaming state for real-time SSE responses
  const [isStreaming, setIsStreaming] = useState(false);
  const streamingTextRef = useRef(''); // Accumulates streaming text for 60fps rendering
  const shouldScrollRef = useRef(false); // Control auto-scroll behavior

  // Theme colors
  const isDarkMode = theme?.isDark ?? theme === 'dark';
  const bgColor = theme?.background || (isDarkMode ? '#1a1a1a' : '#ffffff');
  const panelBg = theme?.surface || (isDarkMode ? '#2d2d2d' : '#f5f5f5');
  const textColor = theme?.text || (isDarkMode ? '#ffffff' : '#000000');
  const textSecondary = theme?.textSecondary || (isDarkMode ? '#a0a0a0' : '#666666');
  const borderColor = theme?.border || (isDarkMode ? '#404040' : '#e0e0e0');
  const highlightColor = type === 'internet' ? '#3b82f6' : '#10B981'; // Blue for internet, Green for personal
  const userMsgBg = isDarkMode ? '#1e40af' : '#dbeafe';
  const aiMsgBg = isDarkMode ? '#374151' : '#f3f4f6';

  // Utility functions for copy message functionality
  const cleanMarkdownForCopy = useCallback((text) => {
    // Remove bold (**)
    let cleanedText = text.replace(/\*\*(.*?)\*\*/g, '$1');
    // Remove italic (*)
    cleanedText = cleanedText.replace(/\*(.*?)\*/g, '$1');
    // Remove underline (__)
    cleanedText = cleanedText.replace(/__(.*?)__/g, '$1');
    // Remove strikethrough (~~)
    cleanedText = cleanedText.replace(/~~(.*?)~~/g, '$1');
    // Remove inline code (`)
    cleanedText = cleanedText.replace(/`(.*?)`/g, '$1');

    // Handle images - convert to descriptive text
    cleanedText = cleanedText.replace(/!\[([^\]]*)\]\s*\([^)]+\)/g, (match, alt) => {
      return alt ? `[Image: ${alt}]` : '[Image]';
    });

    // Convert tables to tab-separated format for better Word compatibility
    const lines = cleanedText.split('\n');
    const processedLines = [];

    for (const line of lines) {
      if (line.includes('|') && line.trim().length > 1) {
        // This is a table row - convert to tab-separated
        const cells = line.split('|').map(cell => cell.trim()).filter(cell => cell.length > 0);
        if (cells.length > 1) {
          processedLines.push(cells.join('\t'));
        } else {
          processedLines.push(line);
        }
      } else if (line.match(/^\s*[\|\-\s:]+\s*$/)) {
        // Skip separator lines (dashes and pipes)
        continue;
      } else {
        processedLines.push(line);
      }
    }

    return processedLines.join('\n').replace(/\n\s*\n\s*\n/g, '\n\n');
  }, []);

  // Helper to escape HTML entities
  const escapeHtml = useCallback((text) => {
    if (typeof document !== 'undefined') {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }
    // Fallback for non-browser environments
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }, []);

  const createRichHTML = useCallback((text) => {
    if (!text || typeof text !== 'string') return '';

    // Pre-process: Collapse multiple consecutive empty lines
    const normalizedText = text.replace(/\n{3,}/g, '\n\n');

    let html = normalizedText;

    // Convert bold and italic
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    html = html.replace(/__(.*?)__/g, '<u>$1</u>');
    html = html.replace(/~~(.*?)~~/g, '<del>$1</del>');

    // Convert inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Convert code blocks
    html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');

    // Process line by line to handle headers, lists, and tables
    const lines = html.split('\n');
    const processedLines = [];
    let inTable = false;
    let inUL = false;
    let inOL = false;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      // Handle headers first
      if (/^### (.*)/.test(line)) {
        // Close any open lists
        if (inUL) {
          processedLines.push('</ul>');
          inUL = false;
        }
        if (inOL) {
          processedLines.push('</ol>');
          inOL = false;
        }
        if (inTable) {
          processedLines.push('</table>');
          inTable = false;
        }
        const headerText = line.replace(/^### (.*)/, '$1');
        processedLines.push(`<h3>${headerText}</h3>`);
        continue;
      } else if (/^## (.*)/.test(line)) {
        // Close any open lists
        if (inUL) {
          processedLines.push('</ul>');
          inUL = false;
        }
        if (inOL) {
          processedLines.push('</ol>');
          inOL = false;
        }
        if (inTable) {
          processedLines.push('</table>');
          inTable = false;
        }
        const headerText = line.replace(/^## (.*)/, '$1');
        processedLines.push(`<h2>${headerText}</h2>`);
        continue;
      } else if (/^# (.*)/.test(line)) {
        // Close any open lists
        if (inUL) {
          processedLines.push('</ul>');
          inUL = false;
        }
        if (inOL) {
          processedLines.push('</ol>');
          inOL = false;
        }
        if (inTable) {
          processedLines.push('</table>');
          inTable = false;
        }
        const headerText = line.replace(/^# (.*)/, '$1');
        processedLines.push(`<h1>${headerText}</h1>`);
        continue;
      }

      // Handle tables
      if (line.includes('|') && line.trim().length > 1) {
        const cells = line.split('|').map(cell => cell.trim()).filter(cell => cell.length > 0);

        if (cells.length > 1) {
          // Check if this is a header separator line
          if (line.match(/^\s*[\|\-\s:]+\s*$/)) {
            continue; // Skip separator lines
          }

          // Close any open lists
          if (inUL) {
            processedLines.push('</ul>');
            inUL = false;
          }
          if (inOL) {
            processedLines.push('</ol>');
            inOL = false;
          }

          if (!inTable) {
            processedLines.push('<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">');
            inTable = true;
          }

          // Determine if this is likely a header row (first table row)
          const isFirstRow = processedLines[processedLines.length - 1].includes('<table');
          const tag = isFirstRow ? 'th' : 'td';

          const cellsHtml = cells.map(cell => `<${tag}>${escapeHtml(cell)}</${tag}>`).join('');
          processedLines.push(`<tr>${cellsHtml}</tr>`);
          continue;
        }
      }

      // Handle unordered lists
      if (/^\s*[-*+]\s+/.test(line)) {
        if (inTable) {
          processedLines.push('</table>');
          inTable = false;
        }
        if (!inUL) {
          processedLines.push('<ul>');
          inUL = true;
        }
        if (inOL) {
          processedLines.push('</ol>');
          inOL = false;
        }
        const content = line.replace(/^\s*[-*+]\s+/, '');
        processedLines.push(`<li>${content}</li>`);
        continue;
      }

      // Handle ordered lists
      if (/^\s*\d+\.\s+/.test(line)) {
        if (inTable) {
          processedLines.push('</table>');
          inTable = false;
        }
        if (!inOL) {
          processedLines.push('<ol>');
          inOL = true;
        }
        if (inUL) {
          processedLines.push('</ul>');
          inUL = false;
        }
        const content = line.replace(/^\s*\d+\.\s+/, '');
        processedLines.push(`<li>${content}</li>`);
        continue;
      }

      // Regular line - close any open structures if it's not empty
      if (line.trim() !== '') {
        if (inUL) {
          processedLines.push('</ul>');
          inUL = false;
        }
        if (inOL) {
          processedLines.push('</ol>');
          inOL = false;
        }
        if (inTable) {
          processedLines.push('</table>');
          inTable = false;
        }
      }

      // Add the line (empty lines become <br>, content lines get wrapped in <p> if they're not already HTML)
      if (line.trim() === '') {
        processedLines.push('<br>');
      } else if (!line.includes('<') || !line.includes('>')) {
        // Only wrap in <p> if it doesn't already contain HTML tags
        processedLines.push(`<p>${line}</p>`);
      } else {
        processedLines.push(line);
      }
    }

    // Close any remaining open structures
    if (inUL) processedLines.push('</ul>');
    if (inOL) processedLines.push('</ol>');
    if (inTable) processedLines.push('</table>');

    return processedLines.join('\n');
  }, [escapeHtml]);

  const handleCopyMessage = useCallback(async (text) => {
    try {
      if (Platform.OS === 'web') {
        // Check if content has rich formatting (tables, images, headers, etc.)
        const hasRichContent = text && (
          text.includes('|') ||
          text.includes('![') ||
          text.includes('#') ||
          text.includes('```') ||
          text.includes('**') ||
          text.includes('*') ||
          text.includes('~~')
        );

        if (hasRichContent && navigator.clipboard && navigator.clipboard.write) {
          try {
            const htmlContent = createRichHTML(text);
            const plainContent = cleanMarkdownForCopy(text);

            console.log('📋 Copying rich content to clipboard...');
            console.log('   - Content length:', text.length);
            console.log('   - Has tables:', text.includes('|'));
            console.log('   - Has code blocks:', text.includes('```'));
            console.log('   - Has headers:', text.includes('#'));

            await navigator.clipboard.write([
              new ClipboardItem({
                'text/html': new Blob([htmlContent], { type: 'text/html' }),
                'text/plain': new Blob([plainContent], { type: 'text/plain' })
              })
            ]);

            console.log('✅ Rich content copied to clipboard');
            return;
          } catch (richCopyError) {
            console.warn('⚠️ Rich copy failed, falling back to plain text:', richCopyError);
          }
        }

        // Fallback to plain text copy
        const plainContent = cleanMarkdownForCopy(text);
        await navigator.clipboard.writeText(plainContent);
        console.log('✅ Plain text copied to clipboard');
      } else {
        // Mobile platforms - use plain text
        const plainContent = cleanMarkdownForCopy(text);
        Clipboard.setString(plainContent);
        console.log('✅ Text copied to clipboard (mobile)');
      }
    } catch (error) {
      console.error('❌ Failed to copy message:', error);
      // Last resort fallback
      try {
        if (Platform.OS === 'web') {
          await navigator.clipboard.writeText(text);
        } else {
          Clipboard.setString(text);
        }
        console.log('✅ Fallback copy successful');
      } catch (fallbackError) {
        console.error('❌ Even fallback copy failed:', fallbackError);
      }
    }
  }, [cleanMarkdownForCopy, createRichHTML]);

  // Handle embed current page/document to vault
  const handleEmbedToVault = useCallback(async () => {
    if (!onEmbedToVault || embedding) return;

    setEmbedding(true);
    setEmbedSuccess(false);

    try {
      let contentToEmbed = extractedContent;

      // If content is not yet extracted, fetch it
      if (!contentToEmbed && type === 'internet' && currentUrl) {
        console.log('📥 Fetching page content for embedding...');
        const result = await internetSearchService.fetchPageContent(currentUrl);
        if (result.success && result.content) {
          contentToEmbed = result.content;
          setExtractedContent(result.content);
        } else {
          throw new Error('Failed to fetch page content');
        }
      }

      if (!contentToEmbed) {
        throw new Error('No content available to embed');
      }

      const embedTitle = title || currentUrl || 'Untitled Web Page';
      await onEmbedToVault(embedTitle, contentToEmbed);
      setEmbedSuccess(true);

      // Reset success state after 3 seconds
      setTimeout(() => setEmbedSuccess(false), 3000);
    } catch (error) {
      console.error('❌ Embed to vault error:', error);
    } finally {
      setEmbedding(false);
    }
  }, [onEmbedToVault, embedding, extractedContent, type, currentUrl, title]);

  // Draggable splitter handlers (web only)
  const handleSplitterMouseDown = (e) => {
    if (Platform.OS !== 'web' || isMobile) return;
    e.preventDefault();
    setIsDragging(true);
  };

  // Mobile vertical splitter handler
  const updateChatPanelHeight = (touchY) => {
    if (!containerRef.current) return;

    const rect = containerRef.current.getBoundingClientRect();
    const containerHeight = rect.height;
    const relativeY = touchY - rect.top;

    // Calculate chat panel height from bottom
    const newChatHeight = (containerHeight - relativeY) / containerHeight;

    // Clamp between 30% and 70%
    const clampedHeight = Math.min(0.7, Math.max(0.3, newChatHeight));
    setChatPanelHeight(clampedHeight);
  };

  // PanResponder for mobile vertical dragging
  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        setIsDragging(true);
      },
      onPanResponderMove: (evt, gestureState) => {
        const touchY = gestureState.moveY;
        updateChatPanelHeight(touchY);
      },
      onPanResponderRelease: () => {
        setIsDragging(false);
      },
    })
  );

  // Sync extractedContent with content prop when it changes (for text documents)
  useEffect(() => {
    if (content && !isPDF) {
      setExtractedContent(content);
    }
  }, [content, isPDF]);

  // Clear conversation history and chat messages when switching documents/pages
  useEffect(() => {
    // Generate conversation key based on type
    const conversationKey = type === 'internet' ? currentUrl : documentId;

    // Clear chat messages when switching documents/pages
    setChatMessages([]);

    // Note: We don't clear conversationHistories here to maintain history across sessions
    // History is only cleared when starting a truly new conversation
    console.log(`🔄 Switched to ${type} - ${conversationKey}`);
  }, [documentId, currentUrl, type]);

  const updateChatPanelWidth = (clientX) => {
    if (!containerRef.current) return;

    const rect = containerRef.current.getBoundingClientRect();
    const containerWidth = rect.width;
    const mouseX = clientX - rect.left;

    // Calculate new chat panel width (from right side)
    const newChatWidth = (containerWidth - mouseX) / containerWidth;

    // Clamp between 20% and 60%
    const clampedWidth = Math.min(0.6, Math.max(0.2, newChatWidth));
    setChatPanelWidth(clampedWidth);
  };

  useEffect(() => {
    if (Platform.OS !== 'web' || isMobile) return;

    const handleMouseMove = (e) => {
      if (!isDragging) return;
      updateChatPanelWidth(e.clientX);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, isMobile]);

  const clearNavigationTimeout = () => {
    if (navigationTimeoutRef.current) {
      clearTimeout(navigationTimeoutRef.current);
      navigationTimeoutRef.current = null;
    }
  };

  const startNavigationTimeout = () => {
    clearNavigationTimeout();
    navigationTimeoutRef.current = setTimeout(() => {
      console.warn('⏰ Navigation timeout reached, stopping spinner');
      setIsNavigating(false);
    }, 30000); // 30s timeout to avoid stuck progress bar
  };

  const isLikelyPdfUrl = useCallback((maybeUrl) => {
    if (!maybeUrl) return false;
    return maybeUrl.toLowerCase().includes('.pdf');
  }, []);

  const pdfMode = isPDF || (type === 'internet' && isLikelyPdfUrl(currentUrl || url));

  // For web PDF documents (including internet-proxied PDFs), fetch as blob and create object URL to prevent forced download/XFO
  useEffect(() => {
    let revokedUrl = null;

    const sourceUrl = type === 'internet'
      ? proxyUrl // internet PDFs must use proxied URL to avoid CORS/XFO
      : url;

    const shouldBlob = Platform.OS === 'web' && pdfMode && !!sourceUrl;
    if (!shouldBlob) {
      setPdfBlobUrl(null);
      return undefined;
    }

    const fetchPdf = async () => {
      try {
        setIsNavigating(true);
        startNavigationTimeout();
        const resp = await fetch(sourceUrl, { credentials: 'include' });
        const blob = await resp.blob();
        const objectUrl = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }));
        revokedUrl = objectUrl;
        setPdfBlobUrl(objectUrl);
      } catch (err) {
        console.error('❌ WEBVIEWER: Failed to fetch PDF as blob', err);
      } finally {
        clearNavigationTimeout();
        setIsNavigating(false);
      }
    };

    fetchPdf();

    return () => {
      if (revokedUrl) {
        URL.revokeObjectURL(revokedUrl);
      }
    };
  }, [url, proxyUrl, pdfMode, type]);

  // Render draggable splitter
  const renderSplitter = () => {
    if (Platform.OS === 'web' && !isMobile) {
      // Web horizontal splitter
      return (
        <View
          onMouseDown={handleSplitterMouseDown}
          style={{
            width: 6,
            backgroundColor: isDragging ? highlightColor : borderColor,
            cursor: 'col-resize',
            justifyContent: 'center',
            alignItems: 'center',
            transition: 'background-color 0.2s',
          }}
          onMouseEnter={(e) => {
            if (!isDragging) e.currentTarget.style.backgroundColor = isDarkMode ? '#606060' : '#c0c0c0';
          }}
          onMouseLeave={(e) => {
            if (!isDragging) e.currentTarget.style.backgroundColor = isDragging ? highlightColor : (isDarkMode ? '#404040' : '#e0e0e0');
          }}
        >
          <View style={{
            width: 2,
            height: 40,
            backgroundColor: isDarkMode ? '#606060' : '#a0a0a0',
            borderRadius: 1
          }} />
        </View>
      );
    } else if (isMobile) {
      // Mobile vertical splitter
      return (
        <View
          {...panResponder.current.panHandlers}
          style={{
            height: 6,
            backgroundColor: isDragging ? highlightColor : borderColor,
            justifyContent: 'center',
            alignItems: 'center',
          }}
        >
          <View style={{
            height: 2,
            width: 40,
            backgroundColor: isDarkMode ? '#606060' : '#a0a0a0',
            borderRadius: 1
          }} />
        </View>
      );
    }
    return null;
  };

  // Overlay to capture drag events over iframe (prevents iframe from swallowing mousemove)
  const renderDragOverlay = () => {
    if (Platform.OS !== 'web' || isMobile || !isDragging) return null;

    return (
      <View
        onMouseMove={(e) => updateChatPanelWidth(e.clientX)}
        onMouseUp={() => setIsDragging(false)}
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          zIndex: 9999,
          cursor: 'col-resize',
          backgroundColor: 'transparent'
        }}
      />
    );
  };

  // DISABLED: Don't auto-scroll - let user stay at reading position during streaming
  // useEffect(() => {
  //   // Only scroll if explicitly allowed (when user sends a message)
  //   if (shouldScrollRef.current && chatScrollRef.current) {
  //     chatScrollRef.current.scrollToEnd({ animated: true });
  //     shouldScrollRef.current = false; // Reset after scrolling
  //   }
  // }, [chatMessages]);

  // Load proxy URL for iframe rendering on web platform
  useEffect(() => {
    if (Platform.OS === 'web' && type === 'internet' && url) {
      loadProxyUrl();
    }
  }, [url, type]);

  const loadProxyUrl = async () => {
    if (Platform.OS === 'web' && type === 'internet' && url) {
      setIsNavigating(true);
      startNavigationTimeout();
      try {
        const proxiedUrl = await internetSearchService.getProxyUrl(url);
        setProxyUrl(proxiedUrl);
      } catch (error) {
        console.error('❌ Failed to load proxy URL:', error);
        setError('Failed to load page');
      }
      // Note: isNavigating will be cleared by iframe onLoad event
    }
  };

  // Navigate to a new URL within the iframe
  const navigateToUrl = async (newUrl) => {
    if (!newUrl) return;

    // Don't block if already navigating - allow the new navigation to override
    console.log('🔗 Navigating to:', newUrl);

    try {
      setIsNavigating(true);
      startNavigationTimeout();

      // Get proxy URL for the new page
      const newProxyUrl = await internetSearchService.getProxyUrl(newUrl);

      // Update navigation history (trim forward history if navigating from middle)
      setNavigationHistory(prev => [...prev.slice(0, historyIndex + 1), newUrl]);
      setHistoryIndex(prev => prev + 1);
      setCurrentUrl(newUrl);
      setProxyUrl(newProxyUrl);

      // Clear extracted content so AI gets fresh content from new page
      setExtractedContent(null);
      setExtracting(false);

      console.log('✅ Navigation complete to:', newUrl);
    } catch (error) {
      console.error('❌ Navigation error:', error);
    }
  };

  // Go back in navigation history
  const goBack = async () => {
    console.log('⬅️ goBack called, historyIndex:', historyIndex, 'history:', navigationHistory);
    if (historyIndex <= 0) {
      console.log('⬅️ Cannot go back - at start of history');
      return;
    }

    const newIndex = historyIndex - 1;
    const prevUrl = navigationHistory[newIndex];

    console.log('⬅️ Going back to:', prevUrl);

    try {
      const newProxyUrl = await internetSearchService.getProxyUrl(prevUrl);
      setHistoryIndex(newIndex);
      setCurrentUrl(prevUrl);
      setProxyUrl(newProxyUrl);
      setExtractedContent(null);
      setExtracting(false);
      setIsNavigating(true);
      startNavigationTimeout();
    } catch (error) {
      console.error('❌ Back navigation error:', error);
    }
  };

  // Go forward in navigation history
  const goForward = async () => {
    console.log('➡️ goForward called, historyIndex:', historyIndex, 'historyLength:', navigationHistory.length);
    if (historyIndex >= navigationHistory.length - 1) {
      console.log('➡️ Cannot go forward - at end of history');
      return;
    }

    const newIndex = historyIndex + 1;
    const nextUrl = navigationHistory[newIndex];

    console.log('➡️ Going forward to:', nextUrl);

    try {
      const newProxyUrl = await internetSearchService.getProxyUrl(nextUrl);
      setHistoryIndex(newIndex);
      setCurrentUrl(nextUrl);
      setProxyUrl(newProxyUrl);
      setExtractedContent(null);
      setExtracting(false);
      setIsNavigating(true);
      startNavigationTimeout();
    } catch (error) {
      console.error('❌ Forward navigation error:', error);
    }
  };

  // Create a ref to hold the latest navigateToUrl function
  const navigateToUrlRef = useRef(navigateToUrl);
  navigateToUrlRef.current = navigateToUrl;

  // Handle link clicks from proxied iframe (Option 3: Hybrid Approach)
  useEffect(() => {
    if (Platform.OS !== 'web') return;

    const handleIframeMessage = (event) => {
      try {
        const data = event.data;

        // Handle download links - open in new tab for browser to handle download
        if (data && data.type === 'PROXY_DOWNLOAD_LINK') {
          console.log('📥 Download link clicked:', data.url);
          // Open in new tab so browser can handle the download
          window.open(data.url, '_blank', 'noopener,noreferrer');
          return;
        }

        // Handle regular link clicks from proxied content - navigate within iframe
        if (data && data.type === 'PROXY_LINK_CLICK') {
          console.log('🔗 Link clicked in proxied page:', data.url);

          // Navigate within the iframe instead of opening new tab
          if (data.url) {
            navigateToUrlRef.current(data.url);
          }
        }

        // Handle form submissions - navigate to the form action URL
        if (data && data.type === 'PROXY_FORM_SUBMIT') {
          console.log('📝 Form submission in proxied page:', data.action, data.method, data.formData);

          if (data.action) {
            // For GET forms, append form data as query params
            if (data.method?.toUpperCase() === 'GET' && data.formData) {
              try {
                const url = new URL(data.action);
                // Parse form data and append to URL
                if (typeof data.formData === 'string') {
                  const params = new URLSearchParams(data.formData);
                  params.forEach((value, key) => url.searchParams.append(key, value));
                } else if (typeof data.formData === 'object') {
                  Object.entries(data.formData).forEach(([key, value]) => {
                    url.searchParams.append(key, value);
                  });
                }
                navigateToUrlRef.current(url.toString());
              } catch (e) {
                console.error('Error building form URL:', e);
                navigateToUrlRef.current(data.action);
              }
            } else {
              // For POST forms or forms without data, just navigate to the action URL
              navigateToUrlRef.current(data.action);
            }
          }
        }
      } catch (error) {
        // Ignore non-JSON messages from other sources
      }
    };

    window.addEventListener('message', handleIframeMessage);

    return () => {
      window.removeEventListener('message', handleIframeMessage);
    };
  }, []);

  // Clear navigation timeout on unmount
  useEffect(() => () => clearNavigationTimeout(), []);

  // JavaScript to inject into WebView to extract text content
  const extractTextScript = `
    (function() {
      try {
        // Check if it's a PDF
        const isPDF = document.contentType === 'application/pdf' || 
                      window.location.href.toLowerCase().endsWith('.pdf') ||
                      document.querySelector('embed[type="application/pdf"]') !== null;
        
        if (isPDF) {
          window.ReactNativeWebView.postMessage(JSON.stringify({
            type: 'PDF_DETECTED',
            url: window.location.href
          }));
          return;
        }
        
        // Remove scripts, styles, and other non-content elements
        const elementsToRemove = ['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript', 'svg'];
        
        // Clone document to avoid modifying the actual page
        const bodyClone = document.body.cloneNode(true);
        
        // Remove unwanted elements from clone
        elementsToRemove.forEach(tag => {
          const elements = bodyClone.querySelectorAll(tag);
          elements.forEach(el => el.remove());
        });
        
        // Try to find main content area
        let mainContent = bodyClone.querySelector('main, article, [role="main"], .content, #content, .article, .post, .entry-content');
        if (!mainContent) {
          mainContent = bodyClone;
        }
        
        // Extract text
        let text = mainContent.innerText || mainContent.textContent || '';
        
        // Clean up text
        text = text.replace(/\\n\\s*\\n\\s*\\n+/g, '\\n\\n'); // Remove excessive newlines
        text = text.replace(/ +/g, ' '); // Remove excessive spaces
        text = text.trim();
        
        // Limit to approximately 500 pages worth of text (500 pages * 500 words/page * 5 chars/word)
        const maxChars = 500 * 500 * 5; // ~1,250,000 characters
        if (text.length > maxChars) {
          text = text.substring(0, maxChars) + '\n\n[Content truncated to first 500 pages...]';
        }
        
        // Count approximate pages (500 words per page, ~5 chars per word)
        const approxPages = Math.ceil(text.length / (500 * 5));
        
        // Send message back to React Native
        window.ReactNativeWebView.postMessage(JSON.stringify({
          type: 'TEXT_EXTRACTED',
          content: text,
          title: document.title,
          approxPages: approxPages,
          truncated: text.length >= maxChars
        }));
      } catch (error) {
        window.ReactNativeWebView.postMessage(JSON.stringify({
          type: 'EXTRACTION_ERROR',
          error: error.message
        }));
      }
    })();
    true; // Required for iOS
  `;

  // Handle messages from WebView
  const handleWebViewMessage = (event) => {
    try {
      const data = JSON.parse(event.nativeEvent.data);

      if (data.type === 'TEXT_EXTRACTED') {
        console.log('✅ Text extracted from webpage:', data.content.length, 'characters');
        console.log(`📄 Approximate pages: ${data.approxPages}${data.truncated ? ' (truncated to 500 pages)' : ''}`);
        setExtractedContent(data.content);
        setExtracting(false);
      } else if (data.type === 'PDF_DETECTED') {
        console.log('📄 PDF detected:', data.url);
        // For PDFs, we need backend processing with same headers as proxy
        fetchPDFContent(data.url);
      } else if (data.type === 'EXTRACTION_ERROR') {
        console.error('❌ Text extraction error:', data.error);
        setExtracting(false);
        setError('Failed to extract page content. AI chat may not work correctly.');
      }
    } catch (error) {
      console.error('❌ Error parsing WebView message:', error);
    }
  };

  // Fetch PDF content from backend
  const fetchPDFContent = async (pdfUrl) => {
    try {
      console.log('🔄 Fetching PDF content from backend...');
      const result = await internetSearchService.fetchPageContent(pdfUrl);

      if (result.success) {
        setExtractedContent(result.content);
        console.log('✅ PDF content extracted:', result.content.length, 'characters');
      } else {
        console.error('❌ Failed to extract PDF content');
        setError('Failed to extract PDF content. AI chat may not work correctly.');
      }
      setExtracting(false);
    } catch (err) {
      console.error('❌ Error fetching PDF:', err);
      setError('Failed to process PDF. AI chat may not work correctly.');
      setExtracting(false);
    }
  };

  // Send chat message
  const handleSendMessage = async (overrideMessage = null) => {
    const messageText = (typeof overrideMessage === 'string' ? overrideMessage : null) || chatInput.trim();
    if (!messageText || loading) return;

    const userMessage = messageText;
    setChatInput('');

    // Scroll to show user's message when they send it
    shouldScrollRef.current = true;

    // Add user message to chat
    setChatMessages(prev => [...prev, {
      type: 'user',
      text: userMessage,
      timestamp: new Date()
    }]);

    setLoading(true);
    setError(null);

    try {
      let contentToUse = extractedContent;

      // Generate conversation key based on type
      const conversationKey = type === 'internet' ? currentUrl : documentId;

      // Get conversation history for this document/page (for xAI prompt caching)
      const conversationHistory = conversationHistories[conversationKey] || [];

      // If content not yet extracted (first query), extract it now
      if (!extractedContent) {
        setExtracting(true);

        if (type === 'internet') {
          // Extract internet page content - backend uses same browser headers as proxy
          console.log('🔄 Extracting internet page content on first query:', currentUrl);
          const result = await internetSearchService.fetchPageContent(currentUrl);

          if (result.success) {
            contentToUse = result.content;
            setExtractedContent(result.content); // Cache for future queries
            console.log('✅ Internet content extracted and cached:', result.content.length, 'characters');
          } else {
            setExtracting(false);
            throw new Error('Failed to extract page content');
          }
        } else if (type === 'document' && documentId) {
          // Check if this is an xAI collection file
          if (isXAIFile && documentId.startsWith('file_')) {
            // Extract content from xAI file using backend endpoint
            console.log('🔮 Extracting xAI file content on first query:', documentId);

            const serviceBaseUrl = CONFIG.CITRA_SERVICE_URL || 'http://localhost:8085';
            const xaiContentUrl = `${serviceBaseUrl}/api/xai-files/content/${documentId}`;

            const token = await authService.getToken();
            const response = await fetch(xaiContentUrl, {
              method: 'GET',
              headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
              }
            });

            if (!response.ok) {
              setExtracting(false);
              throw new Error(`Failed to extract xAI file content: ${response.status}`);
            }

            const xaiResult = await response.json();

            if (xaiResult.success && xaiResult.text) {
              contentToUse = xaiResult.text;
              setExtractedContent(xaiResult.text); // Cache for future queries
              console.log('✅ xAI file content extracted and cached:', xaiResult.text.length, 'characters');
            } else {
              setExtracting(false);
              throw new Error('Failed to extract text from xAI file');
            }
          } else {
            // Regular document - extract content from backend
            console.log('🔄 Extracting document content on first query:', documentId);
            const result = await readerService.getDocument(documentId);

            if (result.success && result.document) {
              // Combine chunks into full text
              const chunks = result.document.chunks || [];
              const sortedChunks = [...chunks].sort((a, b) =>
                (a.chunk_index || 0) - (b.chunk_index || 0)
              );
              const fullText = sortedChunks.map((chunk) => chunk.text).join('\n\n');

              contentToUse = fullText;
              setExtractedContent(fullText); // Cache for future queries
              console.log('✅ Document content extracted and cached:', fullText.length, 'characters');
            } else {
              setExtracting(false);
              throw new Error('Failed to extract document content');
            }
          }
        }

        setExtracting(false);
      }

      let result;

      // Cap conversation history to last 8 messages to maintain context while preventing unbounded growth
      const MAX_HISTORY_MESSAGES = 8;
      const cappedHistory = conversationHistory.length > MAX_HISTORY_MESSAGES
        ? conversationHistory.slice(-MAX_HISTORY_MESSAGES)
        : conversationHistory;

      // Use streaming if enabled
      if (enableStreaming) {
        // Create a SHARED ref object that persists across message updates
        // This ref connects onChunk updates to StreamingRichRenderer
        const sharedStreamingRef = { current: '' };
        
        // Add placeholder AI message for streaming with shared ref
        setChatMessages(prev => [...prev, {
          type: 'ai',
          text: '',
          timestamp: new Date(),
          isStreaming: true,
          streamingRef: sharedStreamingRef // Pass shared ref for real-time updates
        }]);

        setIsStreaming(true);
        streamingTextRef.current = '';

        // Track first chunk for triggering component mount
        let firstChunkReceived = false;
        
        // Streaming callbacks
        const callbacks = {
          onChunk: (chunk, fullText) => {
            streamingTextRef.current = fullText;
            // Update the SHARED streaming ref for real-time rendering
            sharedStreamingRef.current = fullText;
            
            // On first chunk, trigger re-render so StreamingTextRenderer mounts
            if (!firstChunkReceived) {
              firstChunkReceived = true;
              console.log('🎯 [Reader] First chunk! Triggering re-render');
              setChatMessages(prev => {
                const updated = [...prev];
                const lastIdx = updated.length - 1;
                if (lastIdx >= 0 && updated[lastIdx].isStreaming) {
                  updated[lastIdx] = { ...updated[lastIdx], _firstChunk: true };
                }
                return updated;
              });
            }
            // NO setChatMessages on every chunk - StreamingTextRenderer polls the ref
          },
          onMermaid: (data) => {
            console.log('📊 Mermaid diagram detected:', data.index);
            // Mermaid will be rendered after streaming completes
          },
          onComplete: ({ fullText }) => {
            console.log('✅ Streaming complete:', fullText?.length, 'chars');
            // Finalize the message - text was already shown via streaming
            setChatMessages(prev => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0) {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  text: fullText || streamingTextRef.current,
                  isStreaming: false,
                  shouldAnimate: false, // NO animation - text was already shown via streaming
                  isUpdated: true // Mark as updated so it displays immediately
                };
              }
              return updated;
            });
            setIsStreaming(false);

            // Update conversation history
            setConversationHistories(prev => ({
              ...prev,
              [conversationKey]: [
                ...conversationHistory,
                { role: 'user', content: userMessage },
                { role: 'assistant', content: fullText || streamingTextRef.current }
              ]
            }));
          },
          onError: ({ message }) => {
            console.error('❌ Streaming error:', message);
            setError(message);
            setIsStreaming(false);
            // Update the streaming message to show error
            setChatMessages(prev => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].isStreaming) {
                updated[lastIdx] = {
                  type: 'error',
                  text: `Error: ${message}`,
                  timestamp: new Date(),
                  isStreaming: false
                };
              }
              return updated;
            });
          }
        };

        console.log(`💬 Streaming ${cappedHistory.length} previous messages for caching optimization${conversationHistory.length > MAX_HISTORY_MESSAGES ? ' (capped from ' + conversationHistory.length + ')' : ''}`);

        if (type === 'internet') {
          result = await internetSearchService.streamChatWithPage(
            currentUrl,
            userMessage,
            contentToUse,
            title,
            cappedHistory,
            callbacks
          );
        } else {
          result = await internetSearchService.streamChatWithDocument(
            documentId,
            userMessage,
            contentToUse,
            title,
            cappedHistory,
            callbacks
          );
        }

        // Streaming handles its own message updates, so we just check for errors
        if (!result.success) {
          throw new Error(result.error || 'Failed to get AI response');
        }

      } else {
        // Non-streaming fallback
        if (type === 'internet') {
          console.log(`💬 Sending ${cappedHistory.length} previous messages for caching optimization${conversationHistory.length > MAX_HISTORY_MESSAGES ? ' (capped from ' + conversationHistory.length + ')' : ''}`);
          result = await internetSearchService.chatWithPage(
            currentUrl,
            userMessage,
            contentToUse,
            title,
            cappedHistory
          );
        } else {
          console.log(`💬 Sending ${cappedHistory.length} previous messages for caching optimization${conversationHistory.length > MAX_HISTORY_MESSAGES ? ' (capped from ' + conversationHistory.length + ')' : ''}`);
          result = await internetSearchService.chatWithDocument(
            documentId,
            userMessage,
            contentToUse,
            title,
            cappedHistory
          );
        }

        if (!result.success) {
          throw new Error(result.error || 'Failed to get AI response');
        }

        // Add AI response to chat
        setChatMessages(prev => [...prev, {
          type: 'ai',
          text: result.response,
          timestamp: new Date()
        }]);

        // Update conversation history for this document/page (for future caching)
        setConversationHistories(prev => ({
          ...prev,
          [conversationKey]: [
            ...conversationHistory,
            { role: 'user', content: userMessage },
            { role: 'assistant', content: result.response }
          ]
        }));

        // Log cache hit status if available
        if (result.cache_hit_expected !== undefined) {
          console.log(`💰 Cache status: ${result.cache_hit_expected ? 'HIT (saving ~80% on tokens!)' : 'MISS (building cache for next query)'}`);
        }
      }

    } catch (err) {
      console.error('❌ Chat error:', err);
      setError(err.message);

      // Add error message to chat
      setChatMessages(prev => [...prev, {
        type: 'error',
        text: `Error: ${err.message}`,
        timestamp: new Date()
      }]);
    } finally {
      setLoading(false);
    }
  };

  // Quick action buttons
  const handleQuickAction = (action) => {
    let query = '';
    switch (action) {
      case 'summarize':
        query = 'Please provide a concise summary of this content.';
        break;
      case 'keypoints':
        query = 'What are the key points or main takeaways?';
        break;
      case 'explain':
        query = 'Can you explain this in simpler terms?';
        break;
      case 'diagram':
        query = 'Please explain this content using a diagram. Create a visual flowchart, timeline, or hierarchy diagram that illustrates the key concepts and relationships.';
        break;
    }
    handleSendMessage(query);
  };

  // Render chat message
  const renderMessage = (message, index) => {
    const isUser = message.type === 'user';
    const isError = message.type === 'error';

    // For AI responses, use StreamingRichRenderer during streaming, RichMessageRenderer when complete
    if (!isUser && !isError && message.text !== undefined) {
      return (
        <View
          key={index}
          style={{
            alignSelf: 'flex-start',
            maxWidth: '80%',
            marginBottom: 12,
            padding: 12,
            borderRadius: 12,
            backgroundColor: aiMsgBg
          }}
        >
          <StreamingRichRenderer
            content={message.text}
            isStreaming={message.isStreaming}
            streamingRef={message.streamingRef}
            theme={theme}
          />
          {!message.isStreaming && (
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
              <Text
                style={{
                  fontSize: 10,
                  color: textSecondary
                }}
              >
                {message.timestamp.toLocaleTimeString()}
              </Text>
              <TouchableOpacity
                onPress={async () => {
                  await handleCopyMessage(message.text);
                }}
                style={{
                  padding: 6,
                  borderRadius: 6,
                  backgroundColor: isDarkMode ? '#4b5563' : '#e5e7eb'
                }}
              >
                <Ionicons name="copy-outline" size={14} color={textSecondary} />
              </TouchableOpacity>
            </View>
          )}
        </View>
      );
    }

    // For user messages and errors, render as plain text
    return (
      <View
        key={index}
        style={{
          alignSelf: isUser ? 'flex-end' : 'flex-start',
          maxWidth: '80%',
          marginBottom: 12,
          padding: 12,
          borderRadius: 12,
          backgroundColor: isError ? '#fee' : (isUser ? userMsgBg : aiMsgBg)
        }}
      >
        <Text
          style={{
            fontSize: 14,
            color: isError ? '#ef4444' : (isUser ? (isDarkMode ? '#fff' : '#1e40af') : textColor),
            lineHeight: 20
          }}
        >
          {message.text}
        </Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
          <Text
            style={{
              fontSize: 10,
              color: isUser ? (isDarkMode ? '#cbd5e1' : '#64748b') : textSecondary
            }}
          >
            {message.timestamp.toLocaleTimeString()}
          </Text>
          <TouchableOpacity
            onPress={async () => {
              await handleCopyMessage(message.text);
            }}
            style={{
              padding: 6,
              borderRadius: 6,
              backgroundColor: isDarkMode ? '#4b5563' : '#e5e7eb'
            }}
          >
            <Ionicons name="copy-outline" size={14} color={textSecondary} />
          </TouchableOpacity>
        </View>
      </View>
    );
  };

  // Render content panel
  const renderContentPanel = () => {
    // Navigation bar for in-iframe browsing
    const renderNavigationBar = () => (
      <View style={{ position: 'relative' }}>
        <View style={{
          flexDirection: 'row',
          alignItems: 'center',
          backgroundColor: isDarkMode ? '#1f2937' : '#f8fafc',
          borderBottomWidth: 1,
          borderBottomColor: borderColor,
          paddingHorizontal: isMobile ? 4 : 8,
          paddingVertical: isMobile ? 4 : 6,
          gap: isMobile ? 4 : 8
        }}>
          {/* Back Button */}
          <TouchableOpacity
            onPress={goBack}
            disabled={historyIndex <= 0}
            style={{
              padding: isMobile ? 6 : 8,
              borderRadius: 6,
              backgroundColor: historyIndex <= 0
                ? (isDarkMode ? '#374151' : '#e5e7eb')
                : (isDarkMode ? '#4b5563' : '#e2e8f0'),
              opacity: historyIndex <= 0 ? 0.5 : 1
            }}
          >
            <Ionicons
              name="chevron-back"
              size={isMobile ? 16 : 18}
              color={isDarkMode ? '#9ca3af' : '#6b7280'}
            />
          </TouchableOpacity>

          {/* Forward Button */}
          <TouchableOpacity
            onPress={goForward}
            disabled={historyIndex >= navigationHistory.length - 1}
            style={{
              padding: isMobile ? 6 : 8,
              borderRadius: 6,
              backgroundColor: historyIndex >= navigationHistory.length - 1
                ? (isDarkMode ? '#374151' : '#e5e7eb')
                : (isDarkMode ? '#4b5563' : '#e2e8f0'),
              opacity: historyIndex >= navigationHistory.length - 1 ? 0.5 : 1
            }}
          >
            <Ionicons
              name="chevron-forward"
              size={isMobile ? 16 : 18}
              color={isDarkMode ? '#9ca3af' : '#6b7280'}
            />
          </TouchableOpacity>

          {/* Refresh Button */}
          <TouchableOpacity
            onPress={() => navigateToUrl(currentUrl)}
            style={{
              padding: isMobile ? 6 : 8,
              borderRadius: 6,
              backgroundColor: isDarkMode ? '#4b5563' : '#e2e8f0'
            }}
          >
            <Ionicons
              name="refresh"
              size={isMobile ? 16 : 18}
              color={isDarkMode ? '#9ca3af' : '#6b7280'}
            />
          </TouchableOpacity>

          {/* Editable URL Bar */}
          <View style={{
            flex: 1,
            backgroundColor: isDarkMode ? '#374151' : '#ffffff',
            borderRadius: 6,
            paddingHorizontal: isMobile ? 8 : 12,
            height: isMobile ? 32 : 36,
            borderWidth: 1,
            borderColor: isDarkMode ? '#4b5563' : '#d1d5db',
            flexDirection: 'row',
            alignItems: 'center',
            gap: isMobile ? 4 : 8
          }}>
            {isNavigating && (
              <ActivityIndicator size="small" color={highlightColor} />
            )}
            <TextInput
              defaultValue={currentUrl}
              key={currentUrl}
              onSubmitEditing={(e) => {
                const text = e.nativeEvent.text.trim();
                if (!text) return;
                // If it looks like a URL, navigate directly
                const urlPattern = /^(https?:\/\/|www\.)/i;
                const domainPattern = /^[a-zA-Z0-9]([a-zA-Z0-9-]*\.)+[a-zA-Z]{2,}/;
                let targetUrl = text;
                if (urlPattern.test(text)) {
                  targetUrl = text.startsWith('http') ? text : `https://${text}`;
                } else if (domainPattern.test(text)) {
                  targetUrl = `https://${text}`;
                } else {
                  // Treat as search query — use current search flow
                  targetUrl = `https://www.google.com/search?q=${encodeURIComponent(text)}`;
                }
                navigateToUrl(targetUrl);
              }}
              selectTextOnFocus
              style={{
                flex: 1,
                fontSize: isMobile ? 11 : 12,
                color: isDarkMode ? '#9ca3af' : '#6b7280',
                fontFamily: Platform.OS === 'web' ? 'monospace' : undefined,
                height: '100%',
                ...(Platform.OS === 'web' ? { outlineStyle: 'none' } : {})
              }}
              placeholder="Enter URL or search..."
              placeholderTextColor={isDarkMode ? '#6b7280' : '#9ca3af'}
              keyboardType="url"
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="go"
            />
          </View>

          {/* Open in New Tab */}
          <TouchableOpacity
            onPress={() => window.open(currentUrl, '_blank', 'noopener,noreferrer')}
            style={{
              padding: isMobile ? 6 : 8,
              borderRadius: 6,
              backgroundColor: isDarkMode ? '#4b5563' : '#e2e8f0'
            }}
            title="Open in new tab"
          >
            <Ionicons
              name="open-outline"
              size={isMobile ? 16 : 18}
              color={isDarkMode ? '#9ca3af' : '#6b7280'}
            />
          </TouchableOpacity>
        </View>

        {/* Progress bar at bottom of navigation bar */}
        {isNavigating && (
          <View style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            height: 3,
            backgroundColor: isDarkMode ? '#374151' : '#e5e7eb',
            overflow: 'hidden'
          }}>
            <View style={{
              height: '100%',
              backgroundColor: highlightColor,
              width: '100%',
              animation: Platform.OS === 'web' ? 'progress-indeterminate 1.5s infinite linear' : undefined
            }} />
          </View>
        )}
      </View>
    );

    return (
      <View style={{ flex: isMobile ? 1 : (1 - chatPanelWidth), backgroundColor: bgColor }}>
        {(type === 'internet' || (type === 'document' && pdfMode)) && url ? (
          Platform.OS === 'web' ? (
            // For web platform, use iframe with proxy for internet pages or direct URL for PDFs
            <>
              {type === 'internet' && renderNavigationBar()}
              <View style={{ flex: 1, position: 'relative', backgroundColor: bgColor }}>
                {(type === 'internet'
                  ? (pdfMode ? (pdfBlobUrl || proxyUrl || url) : proxyUrl)
                  : (pdfMode ? pdfBlobUrl : url)) ? (
                  <>
                    <iframe
                      src={type === 'internet'
                        ? (pdfMode ? (pdfBlobUrl || proxyUrl || url) : proxyUrl)
                        : (pdfMode ? pdfBlobUrl : url)}
                      style={{
                        flex: 1,
                        width: '100%',
                        height: '100%',
                        border: 'none',
                        backgroundColor: bgColor,
                        pointerEvents: isDragging ? 'none' : 'auto'
                      }}
                      title={title}
                      // Enable browser default HTTP caching
                      loading="lazy"
                      referrerPolicy="no-referrer-when-downgrade"
                      onLoad={() => {
                        console.log('✅ iframe loaded');
                        clearNavigationTimeout();
                        setIsNavigating(false);
                      }}
                      onError={(e) => {
                        console.error('❌ iframe failed to load', e);
                        clearNavigationTimeout();
                        setIsNavigating(false);
                      }}
                    />
                  </>
                ) : (
                  /* Loading state - shown while fetching proxy URL */
                  <View style={{
                    flex: 1,
                    backgroundColor: bgColor,
                    justifyContent: 'center',
                    alignItems: 'center'
                  }}>
                    <ActivityIndicator size="large" color={highlightColor} />
                    <Text style={{
                      marginTop: 12,
                      fontSize: 14,
                      color: textSecondary,
                      fontWeight: '500'
                    }}>
                      {isPDF ? 'Preparing PDF...' : 'Preparing page...'}
                    </Text>
                  </View>
                )}
              </View>
            </>
          ) : WebView ? (
            // For mobile platforms, use WebView
            <WebView
              ref={webViewRef}
              source={{ uri: url }}
              style={{ flex: 1 }}
              startInLoadingState={true}
              onLoadEnd={() => {
                if (webViewRef.current && type === 'internet') {
                  webViewRef.current.injectJavaScript(extractTextScript);
                }
              }}
              onMessage={handleWebViewMessage}
              renderLoading={() => (
                <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: bgColor }}>
                  <ActivityIndicator size="large" color={highlightColor} />
                  <Text style={{ marginTop: 12, fontSize: 14, color: textSecondary }}>
                    {type === 'internet' ? 'Loading webpage...' : 'Loading PDF...'}
                  </Text>
                </View>
              )}
            />
          ) : (
            <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: bgColor }}>
              <Text style={{ color: textColor }}>WebView not supported on this platform</Text>
            </View>
          )
        ) : (
          // For personal documents, show text content
          <ScrollView
            style={{ flex: 1, backgroundColor: bgColor }}
            contentContainerStyle={{ padding: 20, backgroundColor: bgColor }}
          >
            <Text
              style={{
                fontSize: 15,
                lineHeight: 24,
                color: textColor,
                fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace'
              }}
            >
              {extractedContent}
            </Text>
          </ScrollView>
        )}
      </View>
    );
  };

  // Render chat panel
  const renderChatPanel = () => (
    <View
      style={{
        flex: isMobile ? 1 : chatPanelWidth,
        backgroundColor: panelBg,
        borderTopWidth: isMobile ? 1 : 0,
        borderColor: borderColor
      }}
    >
      {/* Chat Header */}
      <View
        style={{
          padding: isMobile ? 10 : 12,
          borderBottomWidth: 1,
          borderBottomColor: borderColor,
          backgroundColor: bgColor
        }}
      >
        <Text style={{ fontSize: isMobile ? 14 : 16, fontWeight: '700', color: textColor }}>
          💬 AI Chat
        </Text>
        <Text style={{ fontSize: isMobile ? 11 : 12, color: textSecondary, marginTop: 2 }}>
          {extracting
            ? 'Extracting page content...'
            : extractedContent
              ? `Ask questions about this ${type === 'internet' ? 'page' : 'document'}`
              : `Ask a question to analyze this ${type === 'internet' ? 'page' : 'document'}`
          }
        </Text>
      </View>

      {/* Quick Actions */}
      <View
        style={{
          flexDirection: 'row',
          flexWrap: 'wrap',
          padding: isMobile ? 6 : 8,
          gap: isMobile ? 6 : 8,
          borderBottomWidth: 1,
          borderBottomColor: borderColor,
          backgroundColor: panelBg
        }}
      >
        <TouchableOpacity
          onPress={() => handleQuickAction('summarize')}
          style={{
            paddingHorizontal: isMobile ? 10 : 12,
            paddingVertical: isMobile ? 5 : 6,
            borderRadius: 16,
            backgroundColor: highlightColor,
            opacity: loading || extracting ? 0.5 : 1
          }}
          disabled={loading || extracting}
        >
          <Text style={{ fontSize: isMobile ? 11 : 12, color: '#fff', fontWeight: '600' }}>
            Summarize
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => handleQuickAction('keypoints')}
          style={{
            paddingHorizontal: isMobile ? 10 : 12,
            paddingVertical: isMobile ? 5 : 6,
            borderRadius: 16,
            backgroundColor: highlightColor,
            opacity: loading || extracting ? 0.5 : 1
          }}
          disabled={loading || extracting}
        >
          <Text style={{ fontSize: isMobile ? 11 : 12, color: '#fff', fontWeight: '600' }}>
            Key Points
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => handleQuickAction('explain')}
          style={{
            paddingHorizontal: isMobile ? 10 : 12,
            paddingVertical: isMobile ? 5 : 6,
            borderRadius: 16,
            backgroundColor: highlightColor,
            opacity: loading || extracting ? 0.5 : 1
          }}
          disabled={loading || extracting}
        >
          <Text style={{ fontSize: isMobile ? 11 : 12, color: '#fff', fontWeight: '600' }}>
            Explain
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => handleQuickAction('diagram')}
          style={{
            paddingHorizontal: isMobile ? 10 : 12,
            paddingVertical: isMobile ? 5 : 6,
            borderRadius: 16,
            backgroundColor: '#8B5CF6',
            opacity: loading || extracting ? 0.5 : 1
          }}
          disabled={loading || extracting}
        >
          <Text style={{ fontSize: isMobile ? 11 : 12, color: '#fff', fontWeight: '600' }}>
            📊 Diagram
          </Text>
        </TouchableOpacity>
      </View>

      {/* Chat Messages */}
      <ScrollView
        ref={chatScrollRef}
        style={{ flex: 1, backgroundColor: panelBg }}
        contentContainerStyle={{ padding: 12, backgroundColor: panelBg }}
      >
        {extracting && (
          <View style={{ alignItems: 'center', paddingVertical: 20 }}>
            <ActivityIndicator size="small" color={highlightColor} />
            <Text style={{ marginTop: 8, fontSize: 12, color: textSecondary, textAlign: 'center' }}>
              {type === 'internet' ? 'Extracting page content (max 100 pages)...' : 'Loading...'}
            </Text>
          </View>
        )}

        {!extracting && chatMessages.length === 0 ? (
          <View style={{ alignItems: 'center', paddingVertical: 40 }}>
            <Ionicons name="chatbubbles-outline" size={48} color={textSecondary} />
            <Text style={{ marginTop: 12, fontSize: 14, color: textSecondary, textAlign: 'center' }}>
              Start a conversation{'\n'}Ask me anything about this {type === 'internet' ? 'page' : 'document'}
            </Text>
          </View>
        ) : (
          chatMessages.map(renderMessage)
        )}

        {loading && !isStreaming && (
          <View style={{ alignItems: 'center', paddingVertical: 12 }}>
            <ActivityIndicator size="small" color={highlightColor} />
            <Text style={{ marginTop: 8, fontSize: 12, color: textSecondary }}>
              AI is thinking...
            </Text>
          </View>
        )}
      </ScrollView>

      {/* Chat Input */}
      <View
        style={{
          padding: 12,
          borderTopWidth: 1,
          borderTopColor: borderColor,
          backgroundColor: bgColor
        }}
      >
        <View
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: panelBg,
            borderRadius: 24,
            paddingHorizontal: 16,
            paddingVertical: 8
          }}
        >
          <TextInput
            value={chatInput}
            onChangeText={setChatInput}
            placeholder="Ask a question..."
            placeholderTextColor={textSecondary}
            multiline
            maxLength={50000}
            style={{
              flex: 1,
              fontSize: 14,
              color: textColor,
              maxHeight: 100
            }}
            onSubmitEditing={handleSendMessage}
            onKeyPress={handleKeyPress}
          />
          <TouchableOpacity
            onPress={handleSendMessage}
            disabled={!chatInput.trim() || loading || extracting}
            style={{
              marginLeft: 8,
              width: 36,
              height: 36,
              borderRadius: 18,
              backgroundColor: chatInput.trim() && !loading && !extracting ? highlightColor : borderColor,
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            <Ionicons
              name="send"
              size={18}
              color="#fff"
            />
          </TouchableOpacity>
        </View>
      </View>
    </View>
  );

  // Handle Enter-to-send on web, Shift+Enter for newline
  const handleKeyPress = (e) => {
    if (Platform.OS === 'web') {
      const key = e?.nativeEvent?.key;
      if (key === 'Enter') {
        const shift = e?.shiftKey || e?.nativeEvent?.shiftKey;
        if (!shift && chatInput.trim() && !loading && !extracting) {
          e.preventDefault();
          e.stopPropagation();
          handleSendMessage();
        }
      }
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: bgColor }}>
      {/* Header - Compact on mobile */}
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          padding: isMobile ? 10 : 16,
          borderBottomWidth: 1,
          borderBottomColor: borderColor,
          backgroundColor: bgColor
        }}
      >
        <TouchableOpacity
          onPress={onBack}
          style={{ marginRight: isMobile ? 8 : 12 }}
        >
          <Ionicons name="arrow-back" size={isMobile ? 20 : 24} color={textColor} />
        </TouchableOpacity>

        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ fontSize: isMobile ? 14 : 18, fontWeight: '700', color: textColor }} numberOfLines={1}>
            {title || 'Content Viewer'}
          </Text>
          {url && !isMobile && (
            <Text style={{ fontSize: 12, color: textSecondary, marginTop: 2 }} numberOfLines={1}>
              {url}
            </Text>
          )}
        </View>

        <View
          style={{
            backgroundColor: highlightColor,
            paddingHorizontal: isMobile ? 8 : 10,
            paddingVertical: isMobile ? 3 : 4,
            borderRadius: 6,
            marginRight: isMobile ? 8 : 12
          }}
        >
          <Text style={{ fontSize: isMobile ? 10 : 11, color: '#fff', fontWeight: '600' }}>
            {type === 'internet' ? '🌐 Internet' : '📄 Personal'}
          </Text>
        </View>

        {/* Embed to Vault Button */}
        {onEmbedToVault && type === 'internet' && (
          <TouchableOpacity
            onPress={handleEmbedToVault}
            disabled={embedding}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              paddingHorizontal: isMobile ? 10 : 14,
              paddingVertical: isMobile ? 6 : 8,
              borderRadius: 8,
              backgroundColor: embedSuccess
                ? (isDarkMode ? '#065f46' : '#d1fae5')
                : (isDarkMode ? '#1e3a5f' : '#dbeafe'),
              marginRight: onClose ? (isMobile ? 8 : 12) : 0,
              gap: 6,
              opacity: embedding ? 0.7 : 1
            }}
          >
            {embedding ? (
              <ActivityIndicator size="small" color={isDarkMode ? '#93c5fd' : '#2563eb'} />
            ) : (
              <Ionicons
                name={embedSuccess ? 'checkmark-circle' : 'download-outline'}
                size={isMobile ? 16 : 18}
                color={embedSuccess
                  ? (isDarkMode ? '#6ee7b7' : '#059669')
                  : (isDarkMode ? '#93c5fd' : '#2563eb')}
              />
            )}
            <Text style={{
              fontSize: isMobile ? 11 : 12,
              fontWeight: '600',
              color: embedSuccess
                ? (isDarkMode ? '#6ee7b7' : '#059669')
                : (isDarkMode ? '#93c5fd' : '#2563eb')
            }}>
              {embedding ? 'Embedding...' : embedSuccess ? 'Embedded!' : (isMobile ? 'Embed' : 'Embed to Vault')}
            </Text>
          </TouchableOpacity>
        )}

        {onClose && (
          <TouchableOpacity
            onPress={onClose}
            style={{
              padding: isMobile ? 6 : 8,
              borderRadius: 8,
              backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.05)'
            }}
          >
            <Ionicons name="close" size={isMobile ? 20 : 24} color={textColor} />
          </TouchableOpacity>
        )}
      </View>

      {/* Mobile Tab Toggle - Switch between Content and Chat */}
      {isMobile && (
        <View style={{
          flexDirection: 'row',
          borderBottomWidth: 1,
          borderBottomColor: borderColor,
          backgroundColor: panelBg
        }}>
          <TouchableOpacity
            onPress={() => setMobileActiveTab('content')}
            style={{
              flex: 1,
              paddingVertical: 10,
              alignItems: 'center',
              borderBottomWidth: 2,
              borderBottomColor: mobileActiveTab === 'content' ? highlightColor : 'transparent',
              backgroundColor: mobileActiveTab === 'content' ? bgColor : 'transparent'
            }}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Ionicons
                name={type === 'internet' ? 'globe-outline' : 'document-text-outline'}
                size={16}
                color={mobileActiveTab === 'content' ? highlightColor : textSecondary}
              />
              <Text style={{
                fontSize: 13,
                fontWeight: '600',
                color: mobileActiveTab === 'content' ? highlightColor : textSecondary
              }}>
                {type === 'internet' ? 'Page' : 'Document'}
              </Text>
            </View>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={() => setMobileActiveTab('chat')}
            style={{
              flex: 1,
              paddingVertical: 10,
              alignItems: 'center',
              borderBottomWidth: 2,
              borderBottomColor: mobileActiveTab === 'chat' ? highlightColor : 'transparent',
              backgroundColor: mobileActiveTab === 'chat' ? bgColor : 'transparent'
            }}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Ionicons
                name="chatbubbles-outline"
                size={16}
                color={mobileActiveTab === 'chat' ? highlightColor : textSecondary}
              />
              <Text style={{
                fontSize: 13,
                fontWeight: '600',
                color: mobileActiveTab === 'chat' ? highlightColor : textSecondary
              }}>
                AI Chat
              </Text>
              {chatMessages.length > 0 && (
                <View style={{
                  backgroundColor: highlightColor,
                  borderRadius: 10,
                  minWidth: 18,
                  height: 18,
                  alignItems: 'center',
                  justifyContent: 'center',
                  paddingHorizontal: 4
                }}>
                  <Text style={{ fontSize: 10, color: '#fff', fontWeight: '700' }}>
                    {chatMessages.length}
                  </Text>
                </View>
              )}
            </View>
          </TouchableOpacity>
        </View>
      )}

      {/* Content + Chat Layout */}
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
      >
        {isMobile ? (
          /* Mobile: Tab-based full-screen switching */
          <View style={{ flex: 1 }}>
            {mobileActiveTab === 'content' && renderContentPanel()}
            {mobileActiveTab === 'chat' && renderChatPanel()}
          </View>
        ) : (
          /* Desktop: Side-by-side split pane */
          <View
            ref={containerRef}
            style={{
              flex: 1,
              flexDirection: 'row',
              userSelect: isDragging ? 'none' : 'auto',
              position: 'relative'
            }}
          >
            {renderContentPanel()}
            {renderDragOverlay()}
            {renderSplitter()}
            {renderChatPanel()}
          </View>
        )}
      </KeyboardAvoidingView>
    </View>
  );
};

export default WebViewer;
