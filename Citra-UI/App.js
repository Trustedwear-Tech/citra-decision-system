// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useState, useCallback, useEffect, useRef, useMemo, Suspense } from 'react';
import {
  View,
  Text,
  FlatList,
  ActivityIndicator,
  Animated,
  Image,
  TouchableOpacity,
  TextInput,
  Alert,
  Dimensions,
  Modal,
  ScrollView,
  Easing,
  Share,
  Platform,
  ActionSheetIOS,
  StyleSheet,
  KeyboardAvoidingView,
  Keyboard,
  Linking,
  BackHandler,
} from 'react-native';
import './styles/modernChatTextSelection.css';
import './styles/autoGrowingInput.css';
// Error tracker — initialised as early as possible so unhandled errors
// thrown during module evaluation of downstream imports are captured.
// No-op unless EXPO_PUBLIC_SENTRY_DSN is set.
import { initSentry } from './sentry';
initSentry();
import SignUpScreen from './components/SignUpScreen.js';
import SupportScreen from './components/SupportScreen.js';
import ModernAlert from './components/ModernAlert.js';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as Clipboard from 'expo-clipboard';
import * as FileSystem from 'expo-file-system';
import { Audio } from 'expo-av';
import axios from 'axios';
import { Ionicons, FontAwesome5 } from '@expo/vector-icons';
import { v4 as uuidv4 } from 'uuid';
import './cryptoPolyfill';
import './styles/textSelection.css';
import { styles } from './styles';
import NetInfo from '@react-native-community/netinfo';
import AsyncStorageManager from './AsyncStorageManager';
import { GestureHandlerRootView, PanGestureHandler, State } from 'react-native-gesture-handler';
import { useActionSheet } from '@expo/react-native-action-sheet';
import ErrorBoundary from './ErrorBoundary';
import WebRenderFix from './WebRenderFix';
import { CONFIG as API_CONFIG, API_ENDPOINTS, logApiCall } from './config/config';
import UI_TEXT from './config/uiText';
import { PERSONAL_VAULT_ENABLED } from './config/featureFlags';

let SEOService;
let DOMPurify;
if (Platform.OS === 'web') {
  try {
    SEOService = require('./services/SEOService.js');
  } catch (error) {
    console.log('SEO Service not available:', error.message);
  }
  try {
    DOMPurify = require('dompurify');
  } catch (error) {
    console.log('DOMPurify not available:', error.message);
  }
}
import browserChatManager from './services/BrowserChatSessionManager';
import { useTheme } from './hooks/useModernTheme';
import { useDebounce, useThrottle, usePerformanceMonitor, useAppState } from './hooks/simplePerformance';
import { uploadQueueManager } from './utils/uploadQueueManager';
import { getFileTypeInfo, isFileTypeSupported, getProcessingMethod } from './utils/fileTypeUtils';
import authService from './services/authService';
import AuthDebugHelper from './utils/AuthDebugHelper';
import { getCachedData, setCachedData, CACHE_TTL } from './utils/simpleCache';
import CurrentReportModal from './components/CurrentReportModal';
import ChatUploadBubble from './components/ChatUploadBubble';
import apiCache from './utils/apiCache';
import { formatTitle, parseMessageContent, processLaTeXForMobile } from './utils/textProcessing';
import {
  HistoryLogo,
  SimpleChatInput,
  StopGeneratingButton,
  ThemeToggle,
  ThemeToggleWeb,
  CodeBlock
} from './components/ui/BasicComponents';
import { ModernThemeToggle } from './components/ui/ModernComponents';
import { ModernChatInput, ModernMessageBubble } from './components/ui/ModernChatComponents';
import ModernSidebar from './components/ui/ModernSidebar';
import { ModernQueryToggles } from './components/ui/ModernInteractionComponents';
import RibbonMenu from './components/ui/RibbonMenu';
import UpcomingFeaturesScreen from './components/UpcomingFeaturesScreen';
import ProfessionSuggestionsBox from './components/ProfessionSuggestionsBox';
import ConnectionScopeModal from './components/chat/ConnectionScopeModal';
// The document viewer. Every uploaded PDF/Word/text/transcript opens here.
import ReaderPanel from './components/ReaderPanel';
// Kept through the Phase 0 OSS split: this is the chat markdown renderer for
// ```mermaid blocks, not part of the removed Diagram product surface.
import MermaidDiagram from './components/MermaidDiagram';
import Message from './components/message/Message';
import StreamingMessage from './components/message/StreamingMessage';
import EnhancedMessage from './components/message/EnhancedMessage';
import HomePanel from './components/HomePanel';

import { loadLegalFilters } from './utils/legalFilterStorage';
import { WorkspaceProvider, useWorkspace } from './contexts/WorkspaceContext';
// URL Routing for web (browser back button, deep links)
import {
  ROUTES,
  navigateToHome,
  navigateToChat,
  navigateToReader,
  onUrlChange,
  updateDocumentTitle,
  initializeRouting
} from './utils/urlRouter';
import DeptSourcesScreen from './screens/DeptSourcesScreen';
import PowerAppsScreen from './screens/PowerAppsScreen';
import MemoryScreen from './components/MemoryScreen';
import DeptLibraryPanel from './components/DeptLibraryPanel';
import AdminUsersScreen from './screens/admin/AdminUsersScreen';
import DeleteUserScreen from './screens/admin/DeleteUserScreen';
import ImpersonateUserScreen from './screens/admin/ImpersonateUserScreen';
import DeparturesScreen from './screens/admin/DeparturesScreen';
import AdminManagedResourcesScreen from './screens/admin/AdminManagedResourcesScreen';
import TypingIndicator from './components/ui/TypingIndicator';
// PreloadUploadModal replaced by UnifiedUploadModal
import HistoryItem from './components/HistoryItem';
import { useIsMobileWeb } from './hooks/useIsMobileWeb';
import MobileHomeScreen from './components/MobileHomeScreen';
import MobileWebHeader from './components/MobileWebHeader';
import QuickStartDialog from './components/QuickStartDialog';
import { TourProvider } from './components/ProductTour';
import ShareService from './services/ShareService';
import { ShareButton } from './components/ShareManager';
import { handleSSEStreamingResponse, shouldUseSSEStreaming } from './services/streamingQueryHandler';
import streamingService from './services/StreamingService';

import { formatDetailedTime, formatShortTime } from './utils/dateUtils';
import SearchIcon from './components/SearchIcon';
import HowToUseModal from './components/HowToUseModal';
import { formatFileSize } from './utils/formatFileSize';
import {
  TRANSCRIBE_URL,
  CITRA_SERVICE,
  AUDIO_UPLOAD_URL,
  AUDIO_DATA_URL,
  DOCUMENT_URL,
  CHAT_URL,
  NOTE_URL,
  QUERY_URL,
  TRANSCRIPT_URL,
  width
} from './utils/constants';

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const styleId = 'Citra AI-responsive-styles';
  if (!document.getElementById(styleId)) {
    const style = document.createElement('style');
    style.id = styleId;
    style.innerHTML = `
      /* Reset and base styles */
      * {
        box-sizing: border-box;
      }
      
      html, body {
        margin: 0;
        padding: 0;
        overflow: hidden;
        height: 100%;
        width: 100%;
      
      #root {
        height: 100%;
        width: 100%;
        display: flex;
        flex-direction: column;
      }
      
      /* Force all react-native-web wrapper divs under #root to fill height */
      #root > div {
        display: flex !important;
        flex-direction: column !important;
        height: 100% !important;
        flex: 1 !important;
      }
      
      #root > div > div {
        display: flex !important;
        flex-direction: column !important;
        height: 100% !important;
        flex: 1 !important;
      }
      
      /* Stop generating button animation */
      @keyframes pulseAnimation {
        0% {
          transform: scale(1);
          opacity: 1;
        }
        50% {
          transform: scale(1.05);
          opacity: 0.9;
        }
        100% {
          transform: scale(1);
          opacity: 1;
        }
      }
      
      .stopGeneratingButtonWeb:hover {
        transform: scale(1.1);
        background-color: #ff3333;
      }
      
      .stopGeneratingButtonWeb:active {
        transform: scale(0.95);
      }
      
      /* Responsive breakpoints */
      
      /* Small laptops (1024px - 1366px) */
      @media (max-width: 1366px) {
        .web-chat-container {
          max-width: 100% !important;
          padding: 0 15px !important;
        }
        
        .web-sidebar {
          width: 250px !important;
          min-width: 200px !important;
        }
        
        .web-header {
          height: 55px !important;
          padding: 0 15px !important;
        }
      }
      
      /* Small screens (768px - 1024px) */
      @media (max-width: 1024px) {
        .web-chat-container {
          max-width: 100% !important;
          padding: 0 10px !important;
        }
        
        .web-sidebar {
          width: 220px !important;
          min-width: 180px !important;
        }
        
        .web-header {
          height: 50px !important;
          padding: 0 10px !important;
          font-size: 14px !important;
        }
        
        .message-bubble {
          max-width: 90% !important;
          padding: 10px !important;
          font-size: 14px !important;
        }
      }
      
      /* Very small screens (less than 768px) - Mobile Web Layout */
      @media (max-width: 768px) {
        .web-container {
          flex-direction: column !important;
          height: 100dvh !important;
          height: -webkit-fill-available !important;
        }
        
        .web-sidebar,
        .modern-sidebar {
          display: none !important;
        }
        
        .web-chat-container {
          max-width: 100% !important;
          padding: 0 5px !important;
          height: auto !important;
          min-height: 0 !important;
          flex: 1 1 0% !important;
        }

        .web-main-content {
          margin-left: 0 !important;
          width: 100% !important;
          flex-direction: column !important;
          height: 0 !important;
          min-height: 0 !important;
          flex: 1 1 0% !important;
        }
        
        .web-header {
          height: 45px !important;
          padding: 0 5px !important;
          font-size: 13px !important;
        }
        
        .message-bubble {
          max-width: 95% !important;
          padding: 8px !important;
          font-size: 13px !important;
        }
        
        .attachment-icons {
          flex-wrap: wrap !important;
          max-height: 120px !important;
          overflow-y: auto !important;
        }
        
        .attachment-icon {
          width: 48px !important;
          height: 48px !important;
          margin: 2px !important;
        }
      }
      
      /* High DPI screens adjustments */
      @media (-webkit-min-device-pixel-ratio: 2), (min-resolution: 192dpi) {
        .web-container {
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
        }
      }
      
      /* Landscape orientation on tablets */
      @media (max-width: 1024px) and (orientation: landscape) {
        .web-sidebar {
          width: 200px !important;
        }
        
        .web-chat-container {
          max-width: 100% !important;
        }
      }
      
      /* Scroll behavior improvements */
      .scrollable-content {
        scrollbar-width: thin;
        scrollbar-color: rgba(255, 255, 255, 0.3) transparent;
      }
      
      .scrollable-content::-webkit-scrollbar {
        width: 6px;
      }
      
      .scrollable-content::-webkit-scrollbar-track {
        background: transparent;
      }
      
      .scrollable-content::-webkit-scrollbar-thumb {
        background-color: rgba(255, 255, 255, 0.3);
        border-radius: 3px;
      }
      
      .scrollable-content::-webkit-scrollbar-thumb:hover {
        background-color: rgba(255, 255, 255, 0.5);
      }
      
      /* Prevent text selection on UI controls only (not text content) */
      .non-selectable {
        -webkit-user-select: none;
        -moz-user-select: none;
        -ms-user-select: none;
        user-select: none;
      }
      
      /* Enable text selection for chat messages and text content */
      .chat-message,
      .message-text,
      .message-bubble,
      .bot-message,
      .user-message,
      .modernMessageText,
      .messageText,
      p, span, div[class*="text"],
      div[class*="message"],
      div[class*="content"] {
        -webkit-user-select: text !important;
        -moz-user-select: text !important;
        -ms-user-select: text !important;
        user-select: text !important;
      }
      
      /* Enable text selection in all text inputs */
      input[type="text"],
      input[type="email"],
      input[type="password"],
      textarea,
      .TextInput,
      .modernTextInput,
      [role="textbox"],
      [contenteditable="true"],
      div[data-focusable="true"] {
        -webkit-user-select: text !important;
        -moz-user-select: text !important;
        -ms-user-select: text !important;
        user-select: text !important;
        -webkit-touch-callout: default !important;
      }
      
      /* Focus styles for accessibility */
      .focusable:focus {
        outline: 2px solid #007acc;
        outline-offset: 2px;
      }
      
      /* Text selection highlighting - Enhanced visibility with maximum specificity */
      html ::selection,
      body ::selection,
      #root ::selection,
      * ::selection {
        background-color: #3B82F6 !important;
        color: #ffffff !important;
      }
      
      html ::-moz-selection,
      body ::-moz-selection,
      #root ::-moz-selection,
      * ::-moz-selection {
        background-color: #3B82F6 !important;
        color: #ffffff !important;
      }
      
      /* Dark mode selection with better contrast - maximum specificity */
      html[data-theme="dark"] ::selection,
      html[data-theme="dark"] * ::selection,
      body[data-theme="dark"] ::selection,
      [data-theme="dark"] ::selection,
      [data-theme="dark"] * ::selection,
      .dark-mode ::selection,
      .dark-mode * ::selection {
        background-color: #60A5FA !important;
        color: #000000 !important;
      }
      
      html[data-theme="dark"] ::-moz-selection,
      html[data-theme="dark"] * ::-moz-selection,
      body[data-theme="dark"] ::-moz-selection,
      [data-theme="dark"] ::-moz-selection,
      [data-theme="dark"] * ::-moz-selection,
      .dark-mode ::-moz-selection,
      .dark-mode * ::-moz-selection {
        background-color: #60A5FA !important;
        color: #000000 !important;
      }
      
      /* Loading states */
      .loading-shimmer {
        background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent);
        background-size: 200% 100%;
        animation: shimmer 1.5s infinite;
      }
      
      @keyframes shimmer {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
      }

      /* Additional responsive web layout classes */
      .web-container {
        display: flex;
        flex-direction: row;
        height: 100%;
        width: 100%;
        overflow: hidden;
      }

      .web-main-content {
        flex: 1;
        display: flex;
        flex-direction: column;
        min-width: 0;
        overflow: hidden;
      }

      .web-chat-container {
        flex: 1;
        display: flex;
        flex-direction: column;
        height: 100%;
        overflow: hidden;
      }

      .web-header {
        flex-shrink: 0;
        min-height: 60px;
        border-bottom: 1px solid;
        padding: 0 20px;
        display: flex;
        align-items: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      }

      /* Sidebar responsive styling */
      .modern-sidebar {
        width: 280px;
        min-width: 250px;
        max-width: 350px;
        height: 100%;
        overflow-y: auto;
        border-right: 1px solid rgba(0,0,0,0.1);
        flex-shrink: 0;
      }

      /* Enhanced responsive media queries */
      @media (max-width: 1366px) {
        .modern-sidebar {
          width: 260px;
          min-width: 240px;
        }
        
        .web-header {
          min-height: 55px;
          padding: 0 15px;
        }
      }

      @media (max-width: 1024px) {
        .web-container {
          flex-direction: column;
        }
        
        .modern-sidebar {
          width: 100%;
          height: auto;
          border-right: none;
          border-bottom: 1px solid rgba(0,0,0,0.1);
        }
        
        .web-header {
          min-height: 50px;
          padding: 0 10px;
        }
      }

      @media (max-width: 768px) {
        .web-header {
          min-height: 45px;
          padding: 0 8px;
        }
        .modern-sidebar {
          display: none !important;
        }
        .web-container {
          height: 100dvh !important;
          height: -webkit-fill-available !important;
        }
        .web-main-content {
          margin-left: 0 !important;
          flex-direction: column !important;
          height: 0 !important;
          min-height: 0 !important;
          flex: 1 1 0% !important;
        }
        .web-chat-container {
          height: auto !important;
          min-height: 0 !important;
          flex: 1 1 0% !important;
        }
      }

      /* Smooth transitions for responsive changes */
      .web-container,
      .web-main-content,
      .web-chat-container,
      .modern-sidebar {
        transition: all 0.3s ease;
      }
    `;
    document.head.appendChild(style);
  }
}

// Global error handling for React Native only
if (typeof global !== 'undefined' && global.ErrorUtils) {
  global.ErrorUtils.setGlobalHandler((error, isFatal) => {
    console.error('Global error (React Native):', error);
    if (isFatal) {
      console.error('This is a fatal error');
    }
  });
}

// Mobile browser detection now handled by useIsMobileWeb hook

// MobileBrowserRedirect REMOVED � mobile web browsers now enter the responsive app layout

// Helper to pick the best human-readable name for a document
const resolveDocumentPrimaryName = (document) => {
  if (!document) {
    return 'Untitled Document';
  }

  const candidates = [
    document.topic_or_filename,
    document.title,
    document.topic,
    document.display_name,
    document.original_filename,
    document.filename
  ];

  for (const candidate of candidates) {
    if (candidate && typeof candidate === 'string' && candidate.trim()) {
      return candidate.trim();
    }
  }

  if (document.document_id) {
    return `Document ${String(document.document_id).slice(0, 8)}`;
  }

  return 'Untitled Document';
};

// Helper to ensure we preserve or derive a filename with extension for downloads
const resolveDocumentFilenameWithExtension = (document) => {
  const baseName = resolveDocumentPrimaryName(document);
  if (!document) {
    return baseName;
  }

  const extensionCandidate = document.file_type || document.fileType;
  if (!extensionCandidate) {
    return baseName;
  }

  const normalizedExtension = extensionCandidate.startsWith('.')
    ? extensionCandidate
    : `.${extensionCandidate}`;

  if (baseName.toLowerCase().endsWith(normalizedExtension.toLowerCase())) {
    return baseName;
  }

  return `${baseName}${normalizedExtension}`;
};

// Global error handling for web
if (Platform.OS === 'web' && typeof window !== 'undefined') {
  // Handle unhandled promise rejections
  window.addEventListener('unhandledrejection', (event) => {
    // Filter out image loading errors (benign - missing resources)
    if (event.reason && typeof event.reason === 'object' &&
      (event.reason.target === 'img' || event.reason.type === 'error')) {
      console.warn('Image loading failed (non-critical):', event.reason);
      event.preventDefault();
      return;
    }

    console.error('Unhandled promise rejection:', event.reason);
    // Prevent the default behavior (showing error in console)
    event.preventDefault();
  });

  // Handle general errors
  window.addEventListener('error', (event) => {
    // Filter out image loading errors
    if (event.target && event.target.tagName === 'IMG') {
      console.warn('Image failed to load:', event.target.src);
      event.preventDefault();
      return;
    }

    console.error('Global error:', event.error);
  });
}

// Import markdown renderer for mobile only with error handling
let Markdown = null;
if (Platform.OS !== 'web') {
  try {
    Markdown = require('react-native-markdown-display').default;
  } catch (error) {
    console.warn('Failed to load react-native-markdown-display:', error.message);
    // Markdown will remain null and we'll handle it gracefully
  }
}

// Import CSS for web
if (Platform.OS === 'web') {
  require('./web.css');
}

// Simple chat interface - no enhanced features needed

// All component definitions moved to separate files - keeping only the main app structure


// Mobile-friendly math processor


const WebHTMLRenderer = ({ content, theme, style, shouldAnimate }) => {
  // Convert LaTeX math to HTML for better rendering
  const processContent = (text) => {
    try {
      // Sanitize input to prevent rendering issues
      if (!text || typeof text !== 'string') {
        return '';
      }

      text = text.replace(/###\s+(.*?)(?:\n|$)/g, (_, heading) => `
        <h3 style="
          font-size: 18px;
          font-weight: bold;
          color: ${theme.text};
          margin: 16px 0 8px 0;
          padding: 0;
          line-height: 1.4;
        ">${heading}</h3>
      `);


      return text


        // Handle display math \[...\]
        .replace(/\\\[([\s\S]*?)\\\]/g, (match, math) => {
          const processedMath = math
            .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '<span style="display: inline-block; text-align: center;"><span style="display: block; border-bottom: 1px solid; padding-bottom: 2px;">$1</span><span style="display: block; padding-top: 2px;">$2</span></span>')
            .replace(/\\sqrt\{([^}]+)\}/g, 'v($1)')
            .replace(/\\cdot/g, '�')
            .replace(/\\times/g, '�')
            .replace(/\\div/g, '�')
            .replace(/\\pm/g, '�')
            .replace(/\\leq/g, '=')
            .replace(/\\geq/g, '=')
            .replace(/\\neq/g, '?')
            .replace(/\\alpha/g, 'a').replace(/\\beta/g, '�').replace(/\\gamma/g, '?')
            .replace(/\\delta/g, 'd').replace(/\\epsilon/g, 'e').replace(/\\theta/g, '?')
            .replace(/\\lambda/g, '?').replace(/\\mu/g, '�').replace(/\\pi/g, 'p')
            .replace(/\\sigma/g, 's').replace(/\\phi/g, 'f').replace(/\\omega/g, '?')
            .replace(/\\sum/g, 'S').replace(/\\int/g, '?').replace(/\\infty/g, '8')
            .replace(/\\partial/g, '?').replace(/\\nabla/g, '?')
            .replace(/\{([^}]+)\}/g, '$1')
            .replace(/\\/g, '');

          return `<div style="
            background-color: ${theme.isDark ? '#2a2a2a' : '#f8f9fa'};
            color: ${theme.text};
            padding: 16px;
            margin: 12px 0;
            border-radius: 8px;
            border-left: 4px solid ${theme.isDark ? '#4a9eff' : '#007acc'};
            text-align: center;
            font-size: 18px;
            font-weight: 500;
            line-height: 1.5;
            font-family: 'Times New Roman', serif;
          ">${processedMath}</div>`;
        })
        // Handle inline math \(...\)
        .replace(/\\\(([\s\S]*?)\\\)/g, (match, math) => {
          const processedMath = math
            .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '$1/$2')
            .replace(/\\sqrt\{([^}]+)\}/g, 'v($1)')
            .replace(/\\cdot/g, '�')
            .replace(/\\times/g, '�')
            .replace(/\\div/g, '�')
            .replace(/\\pm/g, '�')
            .replace(/\\leq/g, '=')
            .replace(/\\geq/g, '=')
            .replace(/\\neq/g, '?')
            .replace(/\\alpha/g, 'a').replace(/\\beta/g, '�').replace(/\\gamma/g, '?')
            .replace(/\\delta/g, 'd').replace(/\\epsilon/g, 'e').replace(/\\theta/g, '?')
            .replace(/\\lambda/g, '?').replace(/\\mu/g, '�').replace(/\\pi/g, 'p')
            .replace(/\\sigma/g, 's').replace(/\\phi/g, 'f').replace(/\\omega/g, '?')
            .replace(/\\sum/g, 'S').replace(/\\int/g, '?').replace(/\\infty/g, '8')
            .replace(/\\partial/g, '?').replace(/\\nabla/g, '?')
            .replace(/\{([^}]+)\}/g, '$1')
            .replace(/\\/g, '');

          return `<span style="
          background-color: ${theme.isDark ? '#3a3a3a' : '#f0f0f0'};
          color: ${theme.text};
          padding: 4px 8px;
          border-radius: 4px;
          font-size: 16px;
          font-weight: 500;
          font-family: 'Times New Roman', serif;
        ">${processedMath}</span>`;
        })
        // Handle bold **text**
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        // Handle italic *text*
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        // Handle inline code `code`
        .replace(/`([^`]+)`/g, `<code style="
        background-color: ${theme.codeBackground};
        color: ${theme.codeText};
        padding: 2px 4px;
        border-radius: 3px;
        font-family: 'Courier New', monospace;
        font-size: 14px;
        border: 1px solid ${theme.codeBorder};
      ">$1</code>`)

      // Check for images before replacement
      const beforeImageCount = (text.match(/!\[([^\]]*)\]\s*\(([^)]+)\)/gs) || []).length;

      // Handle markdown images ![alt](src) - including multiline patterns
      text = text.replace(/!\[([^\]]*)\]\s*\(([^)]+)\)/gs, (match, alt, src) => {
        return `<img src="${src}" alt="${alt}" style="
        max-width: 100%;
        height: auto;
        border-radius: 8px;
        margin: 12px 0;
        display: block;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
      " />`;
      });

      // Check for images after replacement
      const afterImageCount = (text.match(/<img src=/g) || []).length;

      return text
        // Handle markdown links [text](url)
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, `<a href="$2" style="
        color: ${theme.isDark ? '#ffffff' : '#007acc'};
        text-decoration: none;
        border-bottom: 1px solid ${theme.isDark ? '#ffffff' : '#007acc'};
      " target="_blank" rel="noopener noreferrer">$1</a>`)
        // Convert line breaks to <br>
        .replace(/\n/g, '<br>');
    } catch (error) {
      console.error('Error processing content for WebHTMLRenderer:', error);
      // Return sanitized plain text as fallback
      return (text || '').replace(/\n/g, '<br>');
    }
  };

  const safeGetHtmlContent = () => {
    try {
      return processContent(content);
    } catch (error) {
      console.error('Error generating HTML content:', error);
      // Return plain text as fallback
      return (content || '').replace(/\n/g, '<br>');
    }
  };

  const htmlContent = safeGetHtmlContent();
  // Sanitize HTML to prevent XSS attacks
  const sanitizedHtml = DOMPurify ? DOMPurify.sanitize(htmlContent, { ADD_ATTR: ['target', 'rel'], ADD_TAGS: ['img'] }) : htmlContent;

  if (Platform.OS === 'web') {
    try {
      return (
        <div
          className={`web-bot-message ${shouldAnimate ? 'web-bot-message-content' : ''}`}
          style={{
            color: style?.color || theme.text,
            fontSize: style?.fontSize || 16,
            fontWeight: style?.fontWeight || '400',
            lineHeight: 1.6,
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            opacity: style?.opacity ?? 1,
          }}
          dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
        />
      );
    } catch (error) {
      console.error('Error rendering HTML content:', error);
      // Fallback to plain text rendering
      return (
        <div className="web-bot-message">
          {content || ''}
        </div>
      );
    }
  } else {
    // Fallback for non-web platforms
    return <Text style={style}>{content}</Text>;
  }
};

const WebMarkdownText = ({ text, theme, style }) => {
  console.log('?? [WEBMARKDOWN_DEBUG] WebMarkdownText called with text length:', text?.length || 0);
  console.log('?? [WEBMARKDOWN_DEBUG] Text preview:', text?.substring(0, 100) + '...');

  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={text}
        theme={theme}
        style={style}
      />
    );
  } else {
    const formattedText = formatTitle(text, theme);

    if (typeof formattedText === 'string') {
      return <Text style={style}>{formattedText}</Text>;
    }

    return <Text style={style}>{formattedText}</Text>;
  }
};

const FormattedMessageContent = ({ content, theme, formatTitle, textColor, isUserMessage = false }) => {
  // Safety check for undefined content and convert to string if needed
  if (content === null || content === undefined) {
    return null;
  }

  // Convert content to string if it's not already
  const contentStr = typeof content === 'string' ? content : String(content);

  let parts;

  try {
    parts = parseMessageContent(contentStr);
  } catch (error) {
    console.error('Error parsing message content:', error);
    // Fallback to treating entire content as plain text
    parts = [{ type: 'text', content: contentStr || '' }];
  }

  return (
    <View style={styles.formattedMessageContainer}>
      {parts.map((part, index) => {
        try {
          if (part.type === 'mermaid') {
            // Render Mermaid diagram
            return (
              <MermaidDiagram
                key={index}
                diagramCode={part.content}
                theme={theme}
              />
            );
          } else if (part.type === 'codeblock') {
            return (
              <CodeBlock
                key={index}
                code={part.content}
                language={part.language}
                theme={theme}
              />
            );
          } else {
            if (Platform.OS === 'web') {
              return (
                <WebMarkdownText
                  key={index}
                  text={part.content}
                  theme={theme}
                  style={[styles.messageText, {
                    color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText),
                    fontWeight: isUserMessage ? '500' : '400',
                    marginBottom: index < parts.length - 1 ? 8 : 0
                  }]}
                />
              );
            } else {
              // Mobile: Use Markdown for bot responses, plain text for user messages
              if (!isUserMessage && Markdown) {
                try {
                  return (
                    <Markdown
                      key={index}
                      style={{
                        body: {
                          color: textColor || theme.botMessageText,
                          fontWeight: '400',
                          fontSize: 16,
                          marginBottom: index < parts.length - 1 ? 8 : 0,
                          fontFamily: 'System'
                        },
                        paragraph: {
                          marginBottom: 8,
                          marginTop: 0,
                        },
                        strong: {
                          fontWeight: 'bold',
                        },
                        em: {
                          fontStyle: 'italic',
                        },
                        code_inline: {
                          backgroundColor: theme.isDark ? '#2a2a2a' : '#f5f5f5',
                          color: theme.text,
                          paddingHorizontal: 6,
                          paddingVertical: 3,
                          borderRadius: 4,
                          fontFamily: 'monospace',
                          fontSize: 14,
                        },
                        code_block: {
                          backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                          color: theme.text,
                          paddingHorizontal: 12,
                          paddingVertical: 8,
                          borderRadius: 6,
                          fontFamily: 'monospace',
                          fontSize: 14,
                          marginVertical: 8,
                          borderWidth: 1,
                          borderColor: theme.isDark ? '#444444' : '#e1e4e8',
                        },
                        fence: {
                          backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                          color: theme.text,
                          paddingHorizontal: 12,
                          paddingVertical: 8,
                          borderRadius: 6,
                          fontFamily: 'monospace',
                          fontSize: 14,
                          marginVertical: 8,
                          borderWidth: 1,
                          borderColor: theme.isDark ? '#444444' : '#e1e4e8',
                        },
                        pre: {
                          backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                          color: theme.text,
                          paddingHorizontal: 12,
                          paddingVertical: 8,
                          borderRadius: 6,
                          fontFamily: 'monospace',
                          fontSize: 14,
                          marginVertical: 8,
                          borderWidth: 1,
                          borderColor: theme.isDark ? '#444444' : '#e1e4e8',
                        },
                        blockquote: {
                          backgroundColor: theme.isDark ? '#1a1a1a' : '#f5f5f5',
                          borderLeftColor: theme.isDark ? '#4a9eff' : '#ddd',
                          borderLeftWidth: 4,
                          paddingLeft: 12,
                          paddingVertical: 8,
                          marginVertical: 8,
                          borderRadius: 4,
                        },
                        heading1: {
                          fontSize: 20,
                          fontWeight: 'bold',
                          marginBottom: 8,
                          marginTop: 8,
                        },
                        heading2: {
                          fontSize: 18,
                          fontWeight: 'bold',
                          marginBottom: 6,
                          marginTop: 6,
                        },
                        heading3: {
                          fontSize: 16,
                          fontWeight: 'bold',
                          marginBottom: 4,
                          marginTop: 4,
                        },
                        list_item: {
                          marginBottom: 4,
                        },
                        bullet_list: {
                          marginBottom: 8,
                        },
                        ordered_list: {
                          marginBottom: 8,
                        },
                      }}
                    >
                      {processLaTeXForMobile(part.content)}
                    </Markdown>
                  );
                } catch (markdownError) {
                  console.warn('Error rendering Markdown component:', markdownError);
                  // Fallback to plain text rendering
                  return (
                    <Text
                      key={index}
                      style={[styles.messageText, {
                        color: textColor || theme.botMessageText,
                        fontWeight: '400',
                        marginBottom: index < parts.length - 1 ? 8 : 0
                      }]}
                    >
                      {part.content || ''}
                    </Text>
                  );
                }
              } else {
                // User messages or fallback - use plain text
                return (
                  <Text
                    key={index}
                    style={[styles.messageText, {
                      color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText),
                      fontWeight: isUserMessage ? '500' : '400',
                      marginBottom: index < parts.length - 1 ? 8 : 0
                    }]}
                  >
                    {part.content}
                  </Text>
                );
              }
            }
          }
        } catch (renderError) {
          console.error('Error rendering message part:', renderError);
          // Fallback to plain text rendering
          return (
            <Text
              key={index}
              style={[styles.messageText, {
                color: textColor || (isUserMessage ? theme.userMessageText : theme.botMessageText),
                fontWeight: isUserMessage ? '500' : '400',
                marginBottom: index < parts.length - 1 ? 8 : 0
              }]}
            >
              {part.content || ''}
            </Text>
          );
        }
      })}
    </View>
  );
};

const AnimatedFormattedContent = ({
  text,
  theme,
  shouldAnimate,
  isUpdated,
  onAnimationComplete,
  onProgress,
  formatTitle
}) => {
  // Safety check for text and convert to string if needed
  const safeText = (text === null || text === undefined) ? '' : String(text);

  const [displayedText, setDisplayedText] = useState('');
  const [currentIndex, setCurrentIndex] = useState(0);
  const [animationDone, setAnimationDone] = useState(!shouldAnimate);

  useEffect(() => {
    // If the text was updated or shouldn't animate, show it immediately
    if (isUpdated || !shouldAnimate) {
      setDisplayedText(safeText);
      setCurrentIndex(text.length);
      setAnimationDone(true);
      onProgress(text.length);
      onAnimationComplete();
      return;
    }
  }, [text, isUpdated, shouldAnimate]);

  useEffect(() => {
    if (isUpdated || !shouldAnimate || animationDone) return;

    if (currentIndex < safeText.length) {
      const timer = setTimeout(() => {
        // Render multiple characters at once for faster animation
        const chunkSize = Math.max(1, Math.ceil(safeText.length / 100)); // Divide into ~100 chunks
        const nextIndex = Math.min(currentIndex + chunkSize, safeText.length);
        setDisplayedText(safeText.slice(0, nextIndex));
        setCurrentIndex(nextIndex);
        if (onProgress) onProgress(nextIndex);
      }, 0); // Instant animation with chunking

      return () => clearTimeout(timer);
    } else {
      setAnimationDone(true);
      if (onAnimationComplete) onAnimationComplete();
    }
  }, [currentIndex, safeText, isUpdated, shouldAnimate, animationDone]);

  const textToRender = shouldAnimate && !isUpdated ? displayedText : safeText;

  // If animation is done or not needed, render formatted content
  if (animationDone || !shouldAnimate || isUpdated) {
    return (
      <FormattedMessageContent
        content={textToRender}
        theme={theme}
        formatTitle={formatTitle}
        isUserMessage={false}
      />
    );
  }

  // During animation, render plain text on mobile, formatted on web
  if (Platform.OS === 'web') {
    return (
      <FormattedMessageContent
        content={textToRender}
        theme={theme}
        formatTitle={formatTitle}
        isUserMessage={false}
      />
    );
  } else {
    // Mobile: Use Markdown for bot responses during animation
    if (Markdown) {
      try {
        return (
          <Markdown
            style={{
              body: {
                color: theme.botMessageText,
                fontWeight: '400',
                fontSize: 16,
                fontFamily: 'System'
              },
              paragraph: {
                marginBottom: 8,
                marginTop: 0,
              },
              strong: {
                fontWeight: 'bold',
              },
              em: {
                fontStyle: 'italic',
              },
              code_inline: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f5f5f5',
                color: theme.isDark ? '#ff6b6b' : '#d73a49',
                paddingHorizontal: 6,
                paddingVertical: 3,
                borderRadius: 4,
                fontFamily: 'monospace',
                fontSize: 14,
              },
              code_block: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
              fence: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
              pre: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
              blockquote: {
                backgroundColor: theme.isDark ? '#1a1a1a' : '#f5f5f5',
                borderLeftColor: theme.isDark ? '#4a9eff' : '#ddd',
                borderLeftWidth: 4,
                paddingLeft: 12,
                paddingVertical: 8,
                marginVertical: 8,
                borderRadius: 4,
              },
              heading1: {
                fontSize: 20,
                fontWeight: 'bold',
                marginBottom: 8,
                marginTop: 8,
              },
              heading2: {
                fontSize: 18,
                fontWeight: 'bold',
                marginBottom: 6,
                marginTop: 6,
              },
              heading3: {
                fontSize: 16,
                fontWeight: 'bold',
                marginBottom: 4,
                marginTop: 4,
              },
              list_item: {
                marginBottom: 4,
              },
              bullet_list: {
                marginBottom: 8,
              },
              ordered_list: {
                marginBottom: 8,
              },
            }}
          >
            {processLaTeXForMobile(textToRender)}
          </Markdown>
        );
      } catch (markdownError) {
        console.warn('Error rendering Markdown during animation:', markdownError);
        // Fallback to plain text
        return (
          <Text style={[styles.messageText, { color: theme.botMessageText, fontWeight: '400' }]}>
            {textToRender}
          </Text>
        );
      }
    } else {
      return (
        <Text style={[styles.messageText, { color: theme.botMessageText, fontWeight: '400' }]}>
          {textToRender}
        </Text>
      );
    }
  }
};

const AnimatedText = ({ text, style, theme, onAnimationComplete = () => { }, isUpdated = false, shouldAnimate = false, formatTitle, startIndex = 0,
  onProgress = () => { }, }) => {
  // Safety check for text and convert to string if needed
  const safeText = (text === null || text === undefined) ? '' : String(text);

  const [displayedText, setDisplayedText] = useState('');
  const [currentIndex, setCurrentIndex] = useState(startIndex);

  useEffect(() => {
    // If the text was updated (like from an edit) or shouldn't animate, show it immediately
    if (isUpdated || !shouldAnimate) {
      setDisplayedText(safeText);
      setCurrentIndex(safeText.length);
      onProgress(safeText.length);
      onAnimationComplete();
      return;
    }
  }, [safeText, isUpdated, shouldAnimate]);

  useEffect(() => {
    if (isUpdated || !shouldAnimate) return; // Skip animation for updated text or when not needed

    if (currentIndex < safeText.length) {
      const timer = setTimeout(() => {
        // Render multiple characters at once for faster animation
        const chunkSize = Math.max(1, Math.ceil(safeText.length / 100)); // Divide into ~100 chunks
        const nextIndex = Math.min(currentIndex + chunkSize, safeText.length);
        setDisplayedText(safeText.slice(0, nextIndex));
        setCurrentIndex(nextIndex);
        onProgress(nextIndex);
      }, 0); // Instant animation with chunking

      return () => clearTimeout(timer);
    } else {
      onAnimationComplete();
    }
  }, [currentIndex, safeText, isUpdated, shouldAnimate]);
  const displayed = safeText.slice(0, currentIndex);

  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={displayed}
        theme={theme}
        style={style}
      />
    );
  } else {
    // Mobile: Use Markdown for bot responses if theme is provided
    if (theme && Markdown) {
      try {
        return (
          <Markdown
            style={{
              body: {
                color: theme.botMessageText,
                fontWeight: '400',
                fontSize: 16,
                fontFamily: 'System'
              },
              paragraph: {
                marginBottom: 8,
                marginTop: 0,
              },
              strong: {
                fontWeight: 'bold',
              },
              em: {
                fontStyle: 'italic',
              },
              code_inline: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f5f5f5',
                color: theme.isDark ? '#ff6b6b' : '#d73a49',
                paddingHorizontal: 6,
                paddingVertical: 3,
                borderRadius: 4,
                fontFamily: 'monospace',
                fontSize: 14,
              },
              code_block: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
              fence: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
              pre: {
                backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f8f8',
                color: theme.isDark ? '#ffffff' : '#000000',
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 14,
                marginVertical: 8,
                borderWidth: 1,
                borderColor: theme.isDark ? '#444444' : '#e1e4e8',
              },
              blockquote: {
                backgroundColor: theme.isDark ? '#1a1a1a' : '#f5f5f5',
                borderLeftColor: theme.isDark ? '#4a9eff' : '#ddd',
                borderLeftWidth: 4,
                paddingLeft: 12,
                paddingVertical: 8,
                marginVertical: 8,
                borderRadius: 4,
              },
              heading1: {
                fontSize: 20,
                fontWeight: 'bold',
                marginBottom: 8,
                marginTop: 8,
              },
              heading2: {
                fontSize: 18,
                fontWeight: 'bold',
                marginBottom: 6,
                marginTop: 6,
              },
              heading3: {
                fontSize: 16,
                fontWeight: 'bold',
                marginBottom: 4,
                marginTop: 4,
              },
              list_item: {
                marginBottom: 4,
              },
              bullet_list: {
                marginBottom: 8,
              },
              ordered_list: {
                marginBottom: 8,
              },
            }}
          >
            {processLaTeXForMobile(displayed)}
          </Markdown>
        );
      } catch (markdownError) {
        console.warn('Error rendering Markdown in AnimatedText:', markdownError);
        // Fallback to plain text
        return <Text style={style}>{displayed}</Text>;
      }
    } else {
      return <Text style={style}>{displayed}</Text>;
    }
  }
};

const MessageActions = ({ message, theme, onEdit, onCopy, onShare }) => {
  const isUserMessage = message.sender === 'user';

  return (
    <View style={[
      styles.messageActionsContainer,
      !isUserMessage && { justifyContent: 'flex-start' }
    ]}>
      <TouchableOpacity
        style={[styles.messageActionButton, { backgroundColor: theme.borderColor }]}
        onPress={() => onCopy(message.text)}
      >
        <Ionicons name="copy-outline" size={16} color={theme.text} />
      </TouchableOpacity>
      {isUserMessage && (
        <TouchableOpacity
          style={[styles.messageActionButton, { backgroundColor: theme.borderColor }]}
          onPress={() => onEdit(message)}
        >
          <Ionicons name="create-outline" size={16} color={theme.text} />
        </TouchableOpacity>
      )}
      <TouchableOpacity
        style={[styles.messageActionButton, { backgroundColor: theme.borderColor }]}
        onPress={() => onShare(message.text)}
      >
        <Ionicons name="share-outline" size={16} color={theme.text} />
      </TouchableOpacity>
    </View>
  );
};

const NoteItem = ({ item, theme, onDelete, onView, onEdit, isSharedVault = false }) => {
  return (
    <View style={[styles.noteItem, { backgroundColor: theme.botMessage }]}>
      <TouchableOpacity onPress={() => {
        if (isSharedVault) {
          console.log('?? [DEBUG] NoteItem clicked (shared), calling onView with item:', item);
          if (onView) onView(item);
        } else {
          console.log('?? [DEBUG] NoteItem clicked, calling onEdit with item:', item);
          onEdit(item);
        }
      }} style={{ flex: 1 }}>
        <Text style={[styles.noteText, { color: theme.text }]} numberOfLines={1}>
          {item.title}
        </Text>
        <Text style={[styles.notePreview, { color: theme.placeholderText }]} numberOfLines={2}>
          {item.text}
        </Text>
        <Text style={[styles.noteTimestamp, { color: theme.placeholderText }]}>
          {new Date(item.timestamp).toLocaleString()}
        </Text>
      </TouchableOpacity>
      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        {isSharedVault ? (
          <TouchableOpacity
            style={styles.deleteNoteButton}
            onPress={() => onView && onView(item)}>
            <Ionicons name="eye-outline" size={24} color={theme.text} />
          </TouchableOpacity>
        ) : (
          <>
            <TouchableOpacity
              style={styles.deleteNoteButton}
              onPress={() => onEdit(item)}>
              <Ionicons name="create-outline" size={24} color={theme.text} />
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.deleteNoteButton}
              onPress={() => {
                console.log('??? Delete button pressed for note:', item.id, 'Item:', item);
                onDelete(item.id);
              }}>
              <Ionicons name="trash-outline" size={24} color={theme.text} />
            </TouchableOpacity>
          </>
        )}
      </View>
    </View>
  );
};

const NoteViewModal = ({ isVisible, onClose, note, theme, onEdit }) => {
  const displayName = resolveDocumentPrimaryName(note);

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={onClose}
    >
      <View style={[styles.modalContainer, { backgroundColor: 'rgba(0, 0, 0, 0.5)' }]}>
        <View style={[styles.modalContent, { backgroundColor: theme.background }]}>
          <View style={styles.modalHeader}>
            <Text style={[styles.modalTitle, { color: theme.text }]}>Note Details</Text>
            <View style={styles.modalHeaderActions}>
              {onEdit && (
                <TouchableOpacity onPress={() => onEdit(note)} style={styles.modalHeaderIconButton}>
                  <Ionicons name="create" size={24} color={theme.primary} />
                </TouchableOpacity>
              )}
              <TouchableOpacity onPress={onClose} style={styles.modalHeaderIconButton}>
                <Ionicons name="close" size={24} color={theme.text} />
              </TouchableOpacity>
            </View>
          </View>

          <Text style={[styles.noteViewTitle, { color: theme.text }]} numberOfLines={2}>
            {displayName}
          </Text>

          <ScrollView style={styles.noteViewScrollContainer}>
            <Text style={[styles.noteViewText, { color: theme.text }]}>{note?.text}</Text>
          </ScrollView>
          <Text style={[styles.noteViewTimestamp, { color: theme.placeholderText }]}>
            {note ? new Date(note.utc_date || note.created_at || note.timestamp).toLocaleString() : ''}
          </Text>
        </View>
      </View>
    </Modal>
  );
};

const SearchBar = ({
  value,
  onChangeText,
  placeholder = "Search...",
  theme,
  onClear,
  style = {}
}) => {
  return (
    <View style={[{
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: theme.inputBackground,
      borderRadius: 8,
      borderWidth: 1,
      borderColor: theme.borderColor,
      marginHorizontal: 16,
      marginBottom: 8,
      paddingHorizontal: 12,
      paddingVertical: 8,
    }, style]}>
      <Ionicons name="search" size={20} color={theme.placeholderText} style={{ marginRight: 8 }} />
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={theme.placeholderText}
        style={{
          flex: 1,
          color: theme.text,
          fontSize: 16,
          paddingVertical: 0,
        }}
        returnKeyType="search"
        autoCapitalize="none"
        autoCorrect={false}
      />
      {value ? (
        <TouchableOpacity onPress={onClear} style={{ padding: 4 }}>
          <Ionicons name="close-circle" size={20} color={theme.placeholderText} />
        </TouchableOpacity>
      ) : null}
    </View>
  );
};

const TranscriptItem = ({ item, theme, onDelete, onView, onEdit, onDownload }) => {
  const formatDuration = (seconds) => {
    if (!seconds) return 'N/A'; // Handle null/undefined duration for video transcripts
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  // Get icon based on transcript type

  return (
    <View style={[styles.enhancedContainer, { backgroundColor: theme.botMessageFallback, borderColor: theme.border }]}>
      {/* Header with title and indicators */}
      <TouchableOpacity onPress={() => onView(item)} style={styles.enhancedHeader}>
        <View style={styles.titleContainer}>
          <Text style={[styles.enhancedTitle, { color: theme.text }]} numberOfLines={2}>
            {item.topic}
          </Text>
          <View style={styles.indicatorsContainer}>
            {/* Shared/Personal indicator */}
            <View style={styles.typeIndicator}>
              <Ionicons
                name={item.is_enterprise ? 'people' : 'person'}
                size={14}
                color={item.is_enterprise ? '#FF6B35' : '#4285F4'}
              />
              <Text style={[styles.typeText, {
                color: item.is_enterprise ? '#FF6B35' : '#4285F4'
              }]}>
                {item.is_enterprise ? 'Shared' : 'Personal'}
              </Text>
            </View>

            {/* Entity indicator - show when transcript belongs to an entity */}
            {item.entity_id && (
              <View style={styles.entityIndicator}>
                <Ionicons
                  name="briefcase"
                  size={14}
                  color="#9C27B0"
                />
                <Text style={[styles.entityText, { color: '#9C27B0' }]}>
                  {item.entity_name && item.entity_id
                    ? `${item.entity_name} (${item.entity_id})`
                    : (item.entity_name || item.entity_id)}
                </Text>
              </View>
            )}

            {/* Content type indicator */}
            <View style={styles.storageIndicator}>
              <Ionicons
                name={item.type === 'video' ? 'videocam-outline' : 'mic-outline'}
                size={14}
                color={item.type === 'video' ? '#FF9500' : '#007AFF'}
              />
              <Text style={[styles.storageText, {
                color: item.type === 'video' ? '#FF9500' : '#007AFF'
              }]}>
                {item.type === 'video' ? 'Video' : 'Audio'}
              </Text>
            </View>
          </View>
        </View>
      </TouchableOpacity>

      {/* Content preview */}
      <Text style={[styles.contentPreview, { color: theme.placeholderText }]} numberOfLines={2}>
        {item.type === 'video' ? 'Video Recording' : 'Audio Recording'} � Tap to view content
      </Text>

      {/* Metadata */}
      <View style={styles.enhancedMetadata}>
        <Text style={[styles.metadataText, { color: theme.placeholderText }]}>
          ?? {item.utc_date ? new Date(item.utc_date.replace(/\s[A-Z]{2,4}$/, '')).toLocaleDateString() : item.timestamp ? new Date(item.timestamp.replace(/\s[A-Z]{2,4}$/, '')).toLocaleDateString() : 'Unknown Date'}
        </Text>
        <Text style={[styles.metadataText, { color: theme.placeholderText }]}>
          ?? {item.type === 'video' ? 'Video' : formatDuration(item.duration)}
        </Text>
      </View>

      {/* Action buttons */}
      <View style={styles.enhancedActions}>
        <TouchableOpacity
          style={styles.actionButton}
          onPress={() => onView(item)}>
          <Ionicons name="eye-outline" size={20} color="#007AFF" />
          <Text style={[styles.actionText, { color: '#007AFF' }]}>View</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.actionButton}
          onPress={() => onEdit(item)}>
          <Ionicons name="create-outline" size={20} color="#FF9500" />
          <Text style={[styles.actionText, { color: '#FF9500' }]}>Edit</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.actionButton}
          onPress={() => onDelete(item.id)}>
          <Ionicons name="trash-outline" size={20} color="#FF3B30" />
          <Text style={[styles.actionText, { color: '#FF3B30' }]}>Delete</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
};

const TranscriptEditModal = ({ isVisible, onClose, transcript, onSave, theme }) => {
  const [editedTopic, setEditedTopic] = useState('');
  const [editedText, setEditedText] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (transcript) {
      setEditedTopic(transcript.topic || '');
      setEditedText(transcript.text || '');
    }
  }, [transcript]);

  const handleSave = async () => {
    // Validate inputs
    if (!editedTopic.trim()) {
      Alert.alert('Validation Error', 'Topic is required');
      return;
    }

    if (!editedText.trim()) {
      Alert.alert('Validation Error', 'Transcript text is required');
      return;
    }

    setIsSaving(true);
    try {
      console.log('Attempting to save transcript:', {
        id: transcript.id,
        topic: editedTopic.trim(),
        text: editedText.trim()
      });

      await onSave(transcript.id, editedTopic.trim(), editedText.trim());
      onClose();
    } catch (error) {
      console.error('Save failed in modal:', error);
      // Error is already handled in onSave, so we don't need to show another alert
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    // Reset to original values
    setEditedTopic(transcript?.topic || '');
    setEditedText(transcript?.text || '');
    onClose();
  };

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={onClose}
    >
      <View style={[styles.modalContainer, { backgroundColor: 'rgba(0, 0, 0, 0.5)' }]}
      >
        <View style={[styles.modalContent, { backgroundColor: theme.background }]}>
          <View style={styles.modalHeader}>
            <Text style={[styles.modalTitle, { color: theme.text }]}>Edit Transcript</Text>
            <TouchableOpacity onPress={handleCancel} disabled={isSaving}>
              <Ionicons name="close" size={24} color={theme.text} />
            </TouchableOpacity>
          </View>

          <Text style={[styles.modalLabel, { color: theme.text, marginBottom: 8 }]}>Topic:</Text>
          <TextInput
            style={[
              styles.noteInput,
              {
                color: theme.text,
                backgroundColor: theme.inputBackground,
                height: 40,
                marginBottom: 16,
                borderWidth: 1,
                borderColor: theme.borderColor
              },
            ]}
            placeholder="Enter topic..."
            placeholderTextColor={theme.placeholderText}
            value={editedTopic}
            onChangeText={setEditedTopic}
            editable={!isSaving}
            maxLength={200} // Add reasonable limit
          />

          <Text style={[styles.modalLabel, { color: theme.text, marginBottom: 8 }]}>Transcript:</Text>
          <ScrollView style={{ maxHeight: 200, marginBottom: 16 }}>
            <TextInput
              style={[
                styles.noteInput,
                {
                  color: theme.text,
                  backgroundColor: theme.inputBackground,
                  minHeight: 120,
                  borderWidth: 1,
                  borderColor: theme.borderColor
                }
              ]}
              multiline
              placeholder="Enter transcript text..."
              placeholderTextColor={theme.placeholderText}
              value={editedText}
              onChangeText={setEditedText}
              editable={!isSaving}
              textAlignVertical="top"
            />
          </ScrollView>

          <View style={styles.modalFooter}>
            <TouchableOpacity
              onPress={handleCancel}
              style={[styles.modalButton, { backgroundColor: theme.borderColor }]
              }
              disabled={isSaving}
            >
              <Text style={[styles.modalButtonText, { color: theme.text }]}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={handleSave}
              style={[styles.modalButton, styles.saveButton, isSaving && { opacity: 0.6 }]}
              disabled={isSaving}
            >
              {isSaving ? (
                <ActivityIndicator size="small" color="#FFFFFF" />
              ) : (
                <Text style={[styles.modalButtonText, { color: '#FFFFFF' }]}>Save</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
};

const ChatInput = ({
  theme,
  cancelTokenSource,
  sendMessage,
  startRecording,
  stopRecording,
  isRecording,
  isLoading,
  isBotTyping,
  stopGenerating,
  isWaitingForRecordingTitle,
  isWaitingForImageTitle,
  isWaitingForDocumentTitle,
  isWaitingForPhotoTitle,
  isGenerating,
  inputText,
  setInputText,
  questionAttachments,
  setQuestionAttachments,
  handleClipboardPaste,
  getFileIconForWeb,
  detectFileType,
  attachmentProgress,
  removeAttachment,
  formatFileSize,
  areAllAttachmentsProcessed,
  // Toggle states for AI Model functionality - Deep Research removed (auto-decided by LLM)
  // isDeepResearchEnabled,
  // handleDeepResearchToggle,
  // setIsDeepResearchEnabled,
  useUploadedData,
  setUseUploadedData,
  isModelOnlyMode,
  setIsModelOnlyMode,
  handleModelOnlyToggle,
  useEnterprise,
  setUseEnterprise,
  hasOrgId,
  personaText,
}) => {
  const isWaitingForAnyTitle = isWaitingForRecordingTitle || isWaitingForImageTitle || isWaitingForDocumentTitle || isWaitingForPhotoTitle;
  // Remove all disabled conditions - async chat system allows full user control
  const inputDisabled = false; // Always allow input in async system
  // const optionsDisabled = isLoading || isBotTyping || isWaitingForAnyTitle; // Disable options during title prompts
  const micDisabled = false; // Always allow mic in async system
  // Create a blinkAnim value inside this component
  const blinkAnim = useRef(new Animated.Value(0)).current;
  const lastSendTime = useRef(0);

  // Whenever isRecording toggles, start/stop the blink loop
  useEffect(() => {
    if (isRecording) {
      const blinkLoop = Animated.loop(
        Animated.sequence([
          Animated.timing(blinkAnim, {
            toValue: 1,
            duration: 500,
            easing: Easing.linear,
            useNativeDriver: false, // since we're animating backgroundColor
          }),
          Animated.timing(blinkAnim, {
            toValue: 0,
            duration: 500,
            easing: Easing.linear,
            useNativeDriver: false,
          }),
        ])
      );
      blinkLoop.start();
      return () => blinkLoop.stop();
    } else {
      // If not recording, reset to 0
      blinkAnim.stopAnimation();
      blinkAnim.setValue(0);
    }
  }, [isRecording]);

  const [message, setMessage] = useState('');

  const handleSend = useCallback(() => {
    // ?? DEBUGGING: Log function entry with call stack
    console.log('?? [HANDLE_SEND] Function called');
    console.log('?? [HANDLE_SEND] Call stack trace:');
    console.trace('handleSend call stack');

    const now = Date.now();
    const timeSinceLastSend = now - lastSendTime.current;

    console.log('?? [HANDLE_SEND] State check:', {
      inputText: inputText.trim(),
      isBotTyping,
      isLoading,
      isGenerating,
      timeSinceLastSend,
      lastSendTime: lastSendTime.current,
      now,
      timestamp: new Date().toISOString()
    });

    // Prevent duplicate calls by checking both loading states, bot typing, and time throttle (500ms minimum)
    if (inputText.trim() && !isBotTyping && !isLoading && !isGenerating && timeSinceLastSend >= 500) {
      console.log('?? [HANDLE_SEND] Conditions met - calling sendMessage');
      lastSendTime.current = now;
      sendMessage(inputText);
      setInputText('');
    } else {
      console.log('?? [HANDLE_SEND] Conditions NOT met - skipping send');
    }
  }, [inputText, isBotTyping, isLoading, isGenerating, sendMessage, setInputText]);



  return (
    <>
      {/* Input Container */}
      <View
        style={[
          styles.inputContainer,
          { backgroundColor: theme.inputContainerBackground },
        ]}>

        {/* ========== MOBILE/NATIVE QUERY ENHANCEMENT TOGGLES ========== */}
        {/* Note: For web version, see ModernQueryToggles component usage around line 18038 */}
        <View
          style={[
            styles.toggleContainer,
            {
              backgroundColor: theme.isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
              marginBottom: -4,
              paddingVertical: 0,
              paddingHorizontal: 6,
            }
          ]}
        >
          {console.log('?? [AI_MODEL_DEBUG] Rendering AI Model button:', { isModelOnlyMode, handleModelOnlyToggle: !!handleModelOnlyToggle })}


          {/* General Query Button - Mobile */}
          <TouchableOpacity
            style={[
              styles.queryToggleButton,
              isModelOnlyMode && styles.queryToggleButtonActive
            ]}
            onPress={() => {
              console.log('?? [AI_MODEL_DEBUG] Button pressed!');
              handleModelOnlyToggle();
            }}
          >
            <Ionicons
              name={isModelOnlyMode ? "cube" : "cube-outline"}
              size={16}
              color={isModelOnlyMode ? theme.buttonText : theme.text}
            />
            <Text style={[
              styles.queryToggleButtonText,
              isModelOnlyMode && styles.queryToggleButtonTextActive,
              { color: isModelOnlyMode ? theme.buttonText : theme.text, fontSize: 14, fontWeight: '600' }
            ]}>
              General Query
            </Text>
          </TouchableOpacity>

          {/* Deep Research toggle removed - now automatically decided by LLM orchestrator */}

          {/* Vault Button - Mobile. Main chat is an ENTERPRISE-only search
              surface (dept MCP + enterprise SOP library), so the personal
              Data Store source toggle is gone. See config/featureFlags.js. */}

          {/* Enterprise Data Store — ALWAYS ON, so there is nothing to toggle.
              Shown as a static chip for users who belong to an org, matching the
              "Always On" row in the Connection Scope modal. */}
          {hasOrgId ? (
            <View style={[styles.queryToggleButton, styles.queryToggleButtonActive]}>
              <Ionicons name="business" size={16} color={theme.buttonText} />
              <Text style={[
                styles.queryToggleButtonText,
                styles.queryToggleButtonTextActive,
                { color: theme.buttonText }
              ]}>
                {personaText?.menuEnterpriseDrive || 'Enterprise Data Store'}
              </Text>
            </View>
          ) : null}

          {/* Internet and Judgement toggles removed as external search is no longer user-controlled */}
        </View>

        {/* Input Row */}
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          {/* "+" opens the unified upload modal, and EVERY upload path behind
              it lands in a personal vault folder. Main chat takes no file
              input on an enterprise-only surface — hidden, not deleted. */}

          <View
            style={[
              styles.inputWrapper,
              Platform.OS === "ios" && styles.iosInputWrapper,
              Platform.OS === "android" && styles.androidInputWrapper,
              { backgroundColor: theme.inputBackground },
            ]}
          >

            <TextInput
              style={[styles.input, { color: theme.text }]}
              value={inputText}
              onChangeText={setInputText}
              placeholder="Type a message... (Long press to paste)"
              placeholderTextColor={theme.placeholderText}
              multiline
              editable={!inputDisabled}
              textAlignVertical="center"
              scrollEnabled={true}
              returnKeyType="send"
              autoFocus
              onSubmitEditing={(e) => {
                console.log('?? [TEXT_INPUT] onSubmitEditing triggered');
                console.log('?? [TEXT_INPUT] Call stack trace:');
                console.trace('TextInput onSubmitEditing call stack');
                e.preventDefault();
                handleSend();
              }}
              blurOnSubmit={false}
              onLongPress={handleClipboardPaste}
            />

            {inputText.length > 0 && !isLoading && (
              <TouchableOpacity
                style={styles.sendButton}
                onPress={() => {
                  console.log('?? [SEND_BUTTON] Button pressed');
                  console.log('?? [SEND_BUTTON] Call stack trace:');
                  console.trace('Send button onPress call stack');
                  handleSend();
                }}>
                <Ionicons name="send" size={24} color={theme.sendButton} />
              </TouchableOpacity>
            )}

            {/* Mic Button with Timer */}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              {isRecording && (
                <View style={{
                  backgroundColor: theme.isDark ? 'rgba(0, 0, 0, 0.8)' : 'rgba(255, 255, 255, 0.95)',
                  paddingHorizontal: 12,
                  paddingVertical: 6,
                  borderRadius: 16,
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 6,
                  shadowColor: '#000',
                  shadowOffset: { width: 0, height: 2 },
                  shadowOpacity: 0.2,
                  shadowRadius: 4,
                  elevation: 4,
                }}>
                  <Text style={{
                    color: '#FF3B30',
                    fontSize: 14,
                    fontWeight: '600',
                    fontFamily: 'monospace',
                  }}>
                    {Math.floor(audioRecordingDuration / 60)}:{(audioRecordingDuration % 60).toString().padStart(2, '0')}
                  </Text>
                  <Text style={{
                    color: theme.placeholderText,
                    fontSize: 10,
                  }}>
                    / 2:00:00
                  </Text>
                </View>
              )}
              <Animated.View
                style={[
                  styles.micButton,
                  {
                    opacity: micDisabled ? 0.5 : 1,
                    backgroundColor: isRecording
                      ? blinkAnim.interpolate({
                        inputRange: [0, 1],
                        outputRange: ['#8B0000', '#FF0000'], // dark red ? bright red
                      })
                      : '#eee', // idle color
                    borderRadius: 25,
                  },
                ]}
              >
                <TouchableOpacity
                  onPress={isRecording ? stopRecording : startRecording}
                  style={{ padding: 0 }}
                >
                  <Ionicons
                    name={isRecording ? 'stop' : 'mic'}
                    size={24}
                    color={isRecording ? theme.buttonText : theme.text}
                  />
                </TouchableOpacity>
              </Animated.View>
            </View>
          </View> {/* Close inputWrapper */}
        </View> {/* Close Input Row */}
      </View> {/* Close Input Container */}

      {/* Options backdrop - only show when options are visible */}


      {(cancelTokenSource || isWaitingForRecordingTitle || isRecording || isWaitingForImageTitle || isWaitingForDocumentTitle || isWaitingForPhotoTitle || isBotTyping) && (
        <StopGeneratingButton onPress={stopGenerating} theme={theme} />
      )}
    </>
  );
};

const UserDetailModal = ({ isVisible, onClose, theme, fetchPersonalInfo }) => {
  const [personalInfo, setPersonalInfo] = useState('');
  const [hasLoaded, setHasLoaded] = useState(false);
  const [isLoading, setIsLoading] = useState(false); // Modal's own loading state

  // Fetch personal info when modal opens
  useEffect(() => {
    if (isVisible && !hasLoaded) {
      const loadPersonalInfo = async () => {
        try {
          setIsLoading(true); // Set modal's loading state
          const info = await fetchPersonalInfo();
          setPersonalInfo(info);
          setHasLoaded(true);
        } catch (error) {
          console.error('Error loading personal info:', error);
          setPersonalInfo(`Error loading personal information: ${error.message}`);
          setHasLoaded(true);
        } finally {
          setIsLoading(false); // Clear modal's loading state
        }
      };
      loadPersonalInfo();
    } else if (!isVisible) {
      // Reset when modal closes
      setHasLoaded(false);
      setPersonalInfo('');
      setIsLoading(false);
    }
  }, [isVisible, hasLoaded, fetchPersonalInfo]);

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={onClose}
      statusBarTranslucent={true}
    >
      <View style={styles.userDetailModal}>
        <View style={[styles.userDetailContent, {
          backgroundColor: theme.background,
          maxHeight: '90%', // Increase height
          minHeight: 400, // Ensure minimum height
        }]}>
          <View style={styles.userDetailHeader}>
            <Text style={[styles.userDetailTitle, { color: theme.text }]}>
              Your Personal Information
            </Text>
            <TouchableOpacity onPress={onClose}>
              <Ionicons name="close" size={28} color={theme.text} />
            </TouchableOpacity>
          </View>

          <ScrollView style={{ flex: 1 }}>
            {isLoading ? (
              <View style={{
                flex: 1,
                justifyContent: 'center',
                alignItems: 'center',
                paddingVertical: 80,
                minHeight: 200
              }}>
                <ActivityIndicator size="large" color={theme.sendButton} />
                <Text style={[
                  styles.userDetailViewText,
                  {
                    color: theme.placeholderText,
                    marginTop: 20,
                    textAlign: 'center',
                    fontSize: 16,
                    fontWeight: '400'
                  }
                ]}>
                  Loading your personal information...
                </Text>
              </View>
            ) : (
              <View style={{ paddingVertical: 10 }}>
                <Text style={[
                  styles.userDetailViewText,
                  {
                    color: theme.text,
                    lineHeight: 22,
                    fontSize: 16,
                    textAlign: 'left',
                  }
                ]}>
                  {personalInfo || 'No personal information found yet. Start chatting to build your personal profile!'}
                </Text>
              </View>
            )}
          </ScrollView>

          <View style={styles.userDetailButtons}>
            <TouchableOpacity
              onPress={onClose}
              style={[styles.userDetailButton, { backgroundColor: theme.sendButton }]}
            >
              <Text style={[styles.userDetailButtonText, { color: theme.buttonText }]}>
                Close
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
};

const NoteEditModal = ({ isVisible, onClose, note, onSave, theme }) => {
  const [editedTitle, setEditedTitle] = useState('');
  const [editedText, setEditedText] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (note) {
      setEditedTitle(note.title || '');
      setEditedText(note.text || '');
    }
  }, [note]);

  const handleSave = async () => {
    if (!editedTitle.trim()) {
      Alert.alert('Validation Error', 'Title is required');
      return;
    }

    if (!editedText.trim()) {
      Alert.alert('Validation Error', 'Note text is required');
      return;
    }

    setIsSaving(true);
    try {
      await onSave(note.id, editedTitle.trim(), editedText.trim());
      onClose();
    } catch (error) {
      console.error('Save failed in modal:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setEditedTitle(note?.title || '');
    setEditedText(note?.text || '');
    onClose();
  };

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={onClose}
    >
      <View style={[styles.modalContainer, { backgroundColor: 'rgba(0, 0, 0, 0.5)' }]}>
        <View style={[styles.modalContent, { backgroundColor: theme.background }]}>
          <View style={styles.modalHeader}>
            <Text style={[styles.modalTitle, { color: theme.text }]}>Edit Note</Text>
            <TouchableOpacity onPress={handleCancel} disabled={isSaving}>
              <Ionicons name="close" size={24} color={theme.text} />
            </TouchableOpacity>
          </View>

          <Text style={[styles.modalLabel, { color: theme.text, marginBottom: 8 }]}>Title:</Text>
          <TextInput
            style={[
              styles.noteInput,
              {
                color: theme.text,
                backgroundColor: theme.inputBackground,
                height: 40,
                marginBottom: 16,
                borderWidth: 1,
                borderColor: theme.borderColor
              },
            ]}
            placeholder="Enter title..."
            placeholderTextColor={theme.placeholderText}
            value={editedTitle}
            onChangeText={setEditedTitle}
            editable={!isSaving}
            maxLength={200}
          />

          <Text style={[styles.modalLabel, { color: theme.text, marginBottom: 8 }]}>Content:</Text>
          <ScrollView style={{ maxHeight: 200, marginBottom: 16 }}>
            <TextInput
              style={[
                styles.noteInput,
                {
                  color: theme.text,
                  backgroundColor: theme.inputBackground,
                  minHeight: 120,
                  borderWidth: 1,
                  borderColor: theme.borderColor
                }
              ]}
              multiline
              placeholder="Enter note content..."
              placeholderTextColor={theme.placeholderText}
              value={editedText}
              onChangeText={setEditedText}
              editable={!isSaving}
              textAlignVertical="top"
            />
          </ScrollView>

          <View style={styles.modalFooter}>
            <TouchableOpacity
              onPress={handleCancel}
              style={[styles.modalButton, { backgroundColor: theme.borderColor }]}
              disabled={isSaving}
            >
              <Text style={[styles.modalButtonText, { color: theme.text }]}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={handleSave}
              style={[styles.modalButton, styles.saveButton, isSaving && { opacity: 0.6 }]}
              disabled={isSaving}
            >
              {isSaving ? (
                <ActivityIndicator size="small" color={theme.buttonText} />
              ) : (
                <Text style={[styles.modalButtonText, { color: theme.buttonText }]}>Save</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
};

/*
const DocumentItem = ({ item, theme, onDelete, onView, onEdit, onDownload }) => {
  const getFileTypeColor = (fileType) => {
    switch (fileType?.toLowerCase()) {
      case 'pdf': return '#FF6B6B';
      case 'txt': return '#4ECDC4';
      case 'doc':
      case 'docx': return '#45B7D1';
      default: return theme.placeholderText;
    }
  };

  return (
    <View style={[styles.noteItem, { backgroundColor: theme.botMessageFallback }]}>
      <TouchableOpacity onPress={() => onView(item)} style={{ flex: 1 }}>
        <Text style={[styles.noteText, { color: theme.text }]} numberOfLines={1}>
          {item.title}
        </Text>
        <Text style={[styles.documentPreview, { color: theme.placeholderText }]} numberOfLines={2}>
          {item.topic_or_filename || 'Document'}
        </Text>
        <View style={styles.documentMeta}>
          <Text style={[styles.noteTimestamp, { color: theme.placeholderText }]}>
            {new Date(item.timestamp).toLocaleString()}
          </Text>
          <Text style={[styles.documentFileType, { 
            color: getFileTypeColor(item.fileType),
            backgroundColor: `${getFileTypeColor(item.fileType)}20`
          }]}>
            {item.fileType || 'DOC'}
          </Text>
        </View>
      </TouchableOpacity>
      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        <TouchableOpacity
          style={styles.deleteNoteButton}
          onPress={() => {
            console.log('Edit button pressed for document:', item.id);
            onEdit(item);
          }}>
          <Ionicons name="create-outline" size={24} color={theme.text} />
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.deleteNoteButton}
          onPress={() => {
            console.log('Download button pressed for document:', item.id);
            onDownload(item);
          }}>
          <Ionicons name="download-outline" size={24} color={theme.text} />
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.deleteNoteButton}
          onPress={() => {
            console.log('Delete button pressed for document:', item.id);
            onDelete(item.id);
          }}>
          <Ionicons name="trash-outline" size={24} color={theme.text} />
        </TouchableOpacity>
      </View>
    </View>
  );
};
*/

const DocumentEditModal = ({ isVisible, onClose, document, onSave, theme }) => {
  const [editedTitle, setEditedTitle] = useState('');
  const [editedText, setEditedText] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (document) {
      setEditedTitle(document.title || '');
      setEditedText(document.text || '');
    }
  }, [document]);

  const handleSave = async () => {
    if (!editedTitle.trim()) {
      Alert.alert('Validation Error', 'Title is required');
      return;
    }

    if (!editedText.trim()) {
      Alert.alert('Validation Error', 'Document text is required');
      return;
    }

    setIsSaving(true);
    try {
      await onSave(document.id, editedTitle.trim(), editedText.trim());
      onClose();
    } catch (error) {
      console.error('Save failed in modal:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setEditedTitle(document?.title || '');
    setEditedText(document?.text || '');
    onClose();
  };

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={onClose}
    >
      <View style={[styles.modalContainer, { backgroundColor: 'rgba(0, 0, 0, 0.5)' }]}
      >
        <View style={[styles.modalContent, { backgroundColor: theme.background }]}
        >
          <View style={styles.modalHeader}>
            <Text style={[styles.modalTitle, { color: theme.text }]}>Edit Document</Text>
            <TouchableOpacity onPress={handleCancel} disabled={isSaving}>
              <Ionicons name="close" size={24} color={theme.text} />
            </TouchableOpacity>
          </View>

          <Text style={[styles.modalLabel, { color: theme.text, marginBottom: 8 }]}>Title:</Text>
          <TextInput
            style={[
              styles.noteInput,
              {
                color: theme.text,
                backgroundColor: theme.inputBackground,
                height: 40,
                marginBottom: 16,
                borderWidth: 1,
                borderColor: theme.borderColor
              },
            ]}
            placeholder="Enter title..."
            placeholderTextColor={theme.placeholderText}
            value={editedTitle}
            onChangeText={setEditedTitle}
            editable={!isSaving}
            maxLength={200}
          />

          <Text style={[styles.modalLabel, { color: theme.text, marginBottom: 8 }]}>Content:</Text>
          <ScrollView style={{ maxHeight: 200, marginBottom: 16 }}>
            <TextInput
              style={[
                styles.noteInput,
                {
                  color: theme.text,
                  backgroundColor: theme.inputBackground,
                  minHeight: 120,
                  borderWidth: 1,
                  borderColor: theme.borderColor
                }
              ]}
              multiline
              placeholder="Enter document content..."
              placeholderTextColor={theme.placeholderText}
              value={editedText}
              onChangeText={setEditedText}
              editable={!isSaving}
              textAlignVertical="top"
            />
          </ScrollView>

          <View style={styles.modalFooter}>
            <TouchableOpacity
              onPress={handleCancel}
              style={[styles.modalButton, { backgroundColor: theme.borderColor }]
              }
              disabled={isSaving}
            >
              <Text style={[styles.modalButtonText, { color: theme.text }]}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={handleSave}
              style={[styles.modalButton, styles.saveButton, isSaving && { opacity: 0.6 }]}
              disabled={isSaving}
            >
              {isSaving ? (
                <ActivityIndicator size="small" color="#FFFFFF" />
              ) : (
                <Text style={[styles.modalButtonText, { color: '#FFFFFF' }]}>Save</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
};

const DocumentViewModal = ({ isVisible, onClose, document, theme }) => {
  const handleCopyDocument = useCallback(async () => {
    try {
      const textToCopy = document?.text || document?.content || '';
      if (!textToCopy || !textToCopy.trim()) {
        Alert.alert('Nothing to copy', 'This document has no text content available to copy.');
        return;
      }

      await Clipboard.setStringAsync(textToCopy);
      Alert.alert('Copied', 'Document content copied to your clipboard.');
    } catch (error) {
      console.error('Error copying document content:', error);
      Alert.alert('Error', 'Unable to copy the document content. Please try again.');
    }
  }, [document]);

  const headerIconBackground = theme?.isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.06)';

  const getFileTypeColor = (fileType) => {
    switch (fileType?.toLowerCase()) {
      case 'pdf': return '#FF6B6B';
      case 'txt': return '#4ECDC4';
      case 'doc':
      case 'docx': return '#45B7D1';
      default: return theme.placeholderText;
    }
  };

  const displayName = resolveDocumentPrimaryName(document);
  const filenameDisplay = resolveDocumentFilenameWithExtension(document);
  const showDistinctFilename =
    filenameDisplay && filenameDisplay !== displayName;

  // Check if this is a PDF document
  const isPDF = document?.isPDF || false;
  const pdfUrl = document?.download_url || '';
  const iframeHeight = Math.min(Dimensions.get('window').height * 0.8, 900); // numeric height for web iframe

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={onClose}
    >
      <View style={[styles.modalContainer, { backgroundColor: 'rgba(0, 0, 0, 0.5)' }]}>
        <View style={[styles.modalContent, { backgroundColor: theme.background }]}>
          <View style={styles.modalHeader}>
            <Text style={[styles.modalTitle, { color: theme.text }]}>Document Details</Text>
            <View style={styles.modalHeaderActions}>
              {!isPDF && (
                <TouchableOpacity
                  onPress={handleCopyDocument}
                  style={[styles.modalHeaderIconButton, { backgroundColor: headerIconBackground }]}
                  accessibilityRole="button"
                  accessibilityLabel="Copy document content"
                >
                  <Ionicons name="copy-outline" size={20} color={theme.text} />
                </TouchableOpacity>
              )}
              <TouchableOpacity
                onPress={onClose}
                style={[styles.modalHeaderIconButton, { backgroundColor: headerIconBackground }]}
                accessibilityRole="button"
                accessibilityLabel="Close document viewer"
              >
                <Ionicons name="close" size={22} color={theme.text} />
              </TouchableOpacity>
            </View>
          </View>

          <Text style={[styles.documentViewTitle, { color: theme.text }]}>
            {displayName}
          </Text>

          {showDistinctFilename && (
            <Text style={[styles.documentViewFilename, { color: theme.placeholderText }]}>
              {filenameDisplay}
            </Text>
          )}

          <View style={styles.documentViewMeta}>
            <Text style={[styles.noteTimestamp, { color: theme.placeholderText }]}>
              {document ? new Date(document.utc_date || document.created_at || document.timestamp).toLocaleString() : ''}
            </Text>
            <Text style={[styles.documentFileType, {
              color: getFileTypeColor(document?.file_type || document?.fileType),
              backgroundColor: `${getFileTypeColor(document?.file_type || document?.fileType)}20`
            }]}>
              {document?.file_type || document?.fileType || 'DOC'}
            </Text>
          </View>

          {/* Conditional rendering: iframe for PDF, ScrollView for text */}
          {isPDF && pdfUrl ? (
            <View style={{ width: '100%', marginTop: 12, height: iframeHeight }}>
              {Platform.OS === 'web' ? (
                <>
                  {console.log('??? MODAL: Rendering PDF iframe with URL:', pdfUrl)}
                  <iframe
                    src={pdfUrl}
                    style={{
                      width: '100%',
                      height: '100%',
                      border: 'none',
                      borderRadius: '8px'
                    }}
                    title={displayName}
                  />
                </>
              ) : (
                <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', padding: 20 }}>
                  <Ionicons name="document-text-outline" size={64} color={theme.placeholderText} />
                  <Text style={{ marginTop: 16, fontSize: 16, color: theme.text, textAlign: 'center' }}>
                    PDF viewing is only available on web platform
                  </Text>
                  <TouchableOpacity
                    onPress={() => {
                      if (pdfUrl) {
                        Linking.openURL(pdfUrl);
                      }
                    }}
                    style={{
                      marginTop: 20,
                      paddingVertical: 10,
                      paddingHorizontal: 20,
                      backgroundColor: '#10B981',
                      borderRadius: 8
                    }}
                  >
                    <Text style={{ color: '#FFFFFF', fontSize: 16, fontWeight: '600' }}>
                      Open PDF in Browser
                    </Text>
                  </TouchableOpacity>
                </View>
              )}
            </View>
          ) : (
            <ScrollView style={styles.noteViewScrollContainer}>
              <Text style={[styles.noteViewText, { color: theme.text }]}>
                {document?.text || 'No content available'}
              </Text>
            </ScrollView>
          )}
        </View>
      </View>
    </Modal>
  );
};

// URL Fetch Modal Component - Import content from web URLs
const URLFetchModal = ({ isVisible, onClose, onSubmit, isLoading, theme }) => {
  const [url, setUrl] = useState('');
  const [customTopic, setCustomTopic] = useState('');

  const handleSubmit = () => {
    if (onSubmit) {
      onSubmit(url, customTopic);
    }
  };

  const handleClose = () => {
    setUrl('');
    setCustomTopic('');
    onClose();
  };

  if (!isVisible) return null;

  return (
    <Modal
      animationType="slide"
      transparent={true}
      visible={isVisible}
      onRequestClose={handleClose}
    >
      <View style={styles.comprehensiveModalOverlay}>
        <View style={[styles.comprehensiveModalContent, { backgroundColor: theme.background, maxWidth: 600, width: '90%' }]}>
          {/* Header */}
          <View style={styles.comprehensiveModalHeader}>
            <Text style={[styles.comprehensiveModalTitle, { color: theme.text }]}>
              Import from URL
            </Text>
            <TouchableOpacity onPress={handleClose} style={styles.closeButton}>
              <Ionicons name="close" size={24} color={theme.text} />
            </TouchableOpacity>
          </View>

          <View style={{ padding: 20 }}>
            <Text style={[styles.optionsDescription, { color: theme.placeholderText, marginBottom: 12 }]}>
              Enter a web page URL to fetch and import its content into your knowledge base.
            </Text>

            {/* URL Input */}
            <Text style={[{ color: theme.text, fontWeight: '600', marginBottom: 6 }]}>
              Web Page URL *
            </Text>
            <TextInput
              style={[
                {
                  backgroundColor: theme.inputBackground,
                  color: theme.text,
                  borderColor: theme.borderColor,
                  borderWidth: 1,
                  borderRadius: 8,
                  padding: 12,
                  marginBottom: 12,
                  height: 44,
                  fontSize: 14,
                }
              ]}
              placeholder="https://example.com/article"
              placeholderTextColor={theme.placeholderText}
              value={url}
              onChangeText={setUrl}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              editable={!isLoading}
            />

            {/* Custom Topic Input (Optional) */}
            <Text style={[{ color: theme.text, fontWeight: '600', marginBottom: 6 }]}>
              Custom Name (Optional)
            </Text>
            <TextInput
              style={[
                {
                  backgroundColor: theme.inputBackground,
                  color: theme.text,
                  borderColor: theme.borderColor,
                  borderWidth: 1,
                  borderRadius: 8,
                  padding: 12,
                  marginBottom: 12,
                  height: 44,
                  fontSize: 14,
                }
              ]}
              placeholder="Leave blank to use page title"
              placeholderTextColor={theme.placeholderText}
              value={customTopic}
              onChangeText={setCustomTopic}
              editable={!isLoading}
            />

            {/* Info Text */}
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 16, backgroundColor: theme.inputBackground, padding: 10, borderRadius: 8 }}>
              <Ionicons name="information-circle" size={18} color={theme.sendButton} style={{ marginRight: 8 }} />
              <Text style={[{ color: theme.placeholderText, flex: 1, fontSize: 12, lineHeight: 16 }]}>
                Content will be extracted, cleaned, and processed for AI-powered search.
              </Text>
            </View>

            {/* Submit Button */}
            <TouchableOpacity
              style={[
                styles.optionButton,
                {
                  backgroundColor: !url.trim() || isLoading ? theme.borderColor : theme.sendButton,
                  justifyContent: 'center',
                  alignItems: 'center',
                  paddingVertical: 14,
                  borderRadius: 8,
                }
              ]}
              onPress={handleSubmit}
              disabled={!url.trim() || isLoading}
            >
              {isLoading ? (
                <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                  <ActivityIndicator size="small" color="#FFFFFF" style={{ marginRight: 8 }} />
                  <Text style={[{ color: '#FFFFFF', fontWeight: '600', fontSize: 16 }]}>
                    Fetching Content...
                  </Text>
                </View>
              ) : (
                <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                  <Ionicons name="cloud-download" size={20} color="#FFFFFF" style={{ marginRight: 8 }} />
                  <Text style={[{ color: '#FFFFFF', fontWeight: '600', fontSize: 16 }]}>
                    Import Content
                  </Text>
                </View>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
};

export default function ChatScreen({ initialScreen = 'signup', pendingPlanId = null, onClearPendingPlan = null, onBackToIntro, initialUrlRoute = null }) {
  // State that needs to be shared with WorkspaceProvider
  const [currentUserEmail, setCurrentUserEmail] = useState(null);
  // Personal vault as a chat data source. Starts OFF and has no UI to turn it
  // back on while PERSONAL_VAULT_ENABLED is false — main chat searches dept MCP
  // sources and the enterprise SOP library only. The composers (report /
  // presentation / printable) read the vault through their own state, not this.
  const [useUploadedData, setUseUploadedData] = useState(PERSONAL_VAULT_ENABLED); // Default enabled for Vault
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [authCheckComplete, setAuthCheckComplete] = useState(false); // Track if initial auth check has finished
  // Enterprise sources (dept MCP + SOP library) are ALWAYS ON in main chat —
  // they are the only data sources left now that the personal Data Store is
  // gone. The Connection Scope modal shows them as an "Always On" row, and
  // Citra-Service forces the flag true on /query/stream regardless. State is
  // kept (other surfaces read it) but it starts on and is never restored off.
  const [useEnterprise, setUseEnterprise] = useState(true);
  const [enterpriseEntityId, setEnterpriseEntityId] = useState('');
  const [enterpriseEntityName, setEnterpriseEntityName] = useState('');
  const [enterpriseEntityType, setEnterpriseEntityType] = useState('');

  // Initialize SEO for web platform
  useEffect(() => {
    if (Platform.OS === 'web' && SEOService) {
      try {
        const seoService = new SEOService();
        const shouldContinue = seoService.init();

        // If SEO service detects a bot or redirects, don't continue with app initialization
        if (!shouldContinue) {
          console.log('?? SEO: Serving optimized content to search engine bot');
          return;
        }

        console.log('? SEO: Service initialized for Citra AI');
      } catch (error) {
        console.warn('?? SEO: Failed to initialize SEO service:', error);
      }
    }
  }, []);

  // Mobile browser redirect REMOVED � mobile web now uses responsive layout

  return (
    <ErrorBoundary>
      <TourProvider>
        <WorkspaceProvider
          useUploadedData={useUploadedData}
          setUseUploadedData={setUseUploadedData}
        >
          <ChatScreenContent
            initialScreen={initialScreen}
            pendingPlanId={pendingPlanId}
            onClearPendingPlan={onClearPendingPlan}
            onBackToIntro={onBackToIntro}
            currentUserEmail={currentUserEmail}
            setCurrentUserEmail={setCurrentUserEmail}
            useUploadedData={useUploadedData}
            setUseUploadedData={setUseUploadedData}
            isAuthenticated={isAuthenticated}
            setIsAuthenticated={setIsAuthenticated}
            authCheckComplete={authCheckComplete}
            setAuthCheckComplete={setAuthCheckComplete}
            useEnterprise={useEnterprise}
            setUseEnterprise={setUseEnterprise}
            enterpriseEntityId={enterpriseEntityId}
            setEnterpriseEntityId={setEnterpriseEntityId}
            enterpriseEntityName={enterpriseEntityName}
            setEnterpriseEntityName={setEnterpriseEntityName}
            enterpriseEntityType={enterpriseEntityType}
            setEnterpriseEntityType={setEnterpriseEntityType}
            initialUrlRoute={initialUrlRoute}
          />
        </WorkspaceProvider>
      </TourProvider>
    </ErrorBoundary>
  );
}

// Load More Button Component
const LoadMoreButton = ({ onPress, isLoading, theme, hasMore, hasInitialData = true }) => {
  // Don't show if no more data or if initial data hasn't been loaded yet
  if (!hasMore || !hasInitialData) return null;

  return (
    <View style={styles.loadMoreContainer}>
      <TouchableOpacity
        style={[styles.loadMoreButton, { backgroundColor: theme.sendButton }]}
        onPress={onPress}
        disabled={isLoading}
      >
        {isLoading ? (
          <ActivityIndicator size="small" color={theme.buttonText} />
        ) : (
          <>
            <Ionicons name="chevron-down" size={20} color={theme.buttonText} />
            <Text style={styles.loadMoreButtonText}>Load More</Text>
          </>
        )}
      </TouchableOpacity>
    </View>
  );
};

// Loading Modal Component for individual item content loading
const ContentLoadingModal = ({ isVisible, theme, loadingText = "Loading content..." }) => {
  return (
    <Modal
      animationType="fade"
      transparent={true}
      visible={isVisible}
    >
      <View style={[styles.loadingModalContainer]}>
        <View style={[styles.loadingModalContent, { backgroundColor: theme.background }]}>
          <ActivityIndicator size="large" color={theme.sendButton} />
          <Text style={[styles.loadingModalText, { color: theme.text }]}>
            {loadingText}
          </Text>
        </View>
      </View>
    </Modal>
  );
};

// Component to handle upload messages with progress polling
const UploadProgressMessage = ({ message, onProgressUpdate }) => {
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('analyzing');
  const [error, setError] = useState(null);
  const [transcriptionData, setTranscriptionData] = useState(null);
  const intervalRef = useRef(null);
  const timeoutRef = useRef(null);
  const startTimeRef = useRef(Date.now());
  const redisDownRetryCountRef = useRef(0); // Track redis_down retries

  // Check if transcription data is available directly from upload response (message.upload.transcriptionData)
  // This is set when the upload API returns the transcription directly
  useEffect(() => {
    if (message.upload?.transcriptionData) {
      console.log('?? Transcription data available from upload response:', message.upload.transcriptionData);
      setTranscriptionData(message.upload.transcriptionData);
      setProgress(100);
      setStage('complete');
      // Stop polling since we have the data
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    }
    if (message.upload?.status === 'completed') {
      setProgress(100);
      setStage('complete');
    }
    if (message.upload?.status === 'error') {
      setError(message.upload.error || 'Upload failed');
      setStage('error');
      // Stop all polling immediately when error state is detected
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    }
  }, [message.upload?.transcriptionData, message.upload?.status, message.upload?.error]);

  useEffect(() => {
    // Skip polling if we already have transcription data from upload response
    if (message.upload?.transcriptionData || message.upload?.status === 'completed') {
      return;
    }
    if (!message.upload?.progressEndpoint) return;

    const MAX_POLLING_DURATION = 5 * 60 * 1000; // 5 minutes maximum polling
    startTimeRef.current = Date.now();

    const pollProgress = async () => {
      try {
        // Check if we've been polling too long
        const elapsedTime = Date.now() - startTimeRef.current;
        if (elapsedTime > MAX_POLLING_DURATION) {
          console.warn('Upload progress polling timeout reached (5 minutes), stopping polling');
          setError('Upload timeout - please try again');
          setStage('error');
          clearInterval(intervalRef.current);
          clearTimeout(timeoutRef.current);
          return;
        }

        const response = await authService.authenticatedFetch(`${API_CONFIG.CITRA_SERVICE_URL}${message.upload.progressEndpoint}`);
        if (response.ok) {
          const data = await response.json();

          setProgress(data.progress || 0);
          setStage(data.stage || 'analyzing');

          if (data.status === 'error') {
            setError(data.message || 'Upload failed');
            clearInterval(intervalRef.current);
            clearTimeout(timeoutRef.current);
            console.log('?? Upload error detected, stopped polling');
          } else if (data.status === 'completed' || data.stage === 'complete') {
            setProgress(100);
            setStage('complete');

            clearInterval(intervalRef.current);
            clearTimeout(timeoutRef.current);
            console.log('? Upload completed, stopped polling');
            // Let ChatUploadBubble handle auto-removal after 5 seconds
            // No onProgressUpdate call needed for completion
          } else if (data.status === 'redis_down') {
            // Handle case where Redis monitoring is down
            // Retry a few times before giving up
            redisDownRetryCountRef.current += 1;

            if (redisDownRetryCountRef.current <= 3) {
              console.warn(`Redis monitoring unavailable (attempt ${redisDownRetryCountRef.current}/3), retrying in 2 seconds...`);
              // Continue polling - the regular 2s interval will handle retry
            } else {
              console.error('Redis monitoring failed after 3 retries - stopping polling');
              // Clear intervals and set error state
              clearInterval(intervalRef.current);
              clearTimeout(timeoutRef.current);

              // Set error state to stop polling
              setError('Progress tracking service unavailable. Please try again.');
              setStage('error');
              console.log('?? Progress tracking unavailable, stopped polling');
            }
          } else if (data.status === 'not_found') {
            // Handle case where progress data is not found (e.g., Redis is down)
            // Stop polling and show error instead of assuming success
            console.warn('Progress tracking not available, stopping polling');
            setError('Progress tracking not found');
            setStage('error');
            clearInterval(intervalRef.current);
            clearTimeout(timeoutRef.current);
            console.log('?? Progress not found, stopped polling');
          }
        } else {
          // HTTP error - stop polling
          console.error('Progress API error:', response.status);
          setError(`Upload status check failed (${response.status})`);
          clearInterval(intervalRef.current);
          clearTimeout(timeoutRef.current);
          console.log('?? HTTP error, stopped polling');
        }
      } catch (err) {
        console.error('Progress polling error:', err);
        setError('Failed to check upload progress');
        clearInterval(intervalRef.current);
        clearTimeout(timeoutRef.current);
        console.log('?? Polling exception, stopped polling');
      }
    };

    // Start polling with initial delay to allow backend to set progress
    // Add a 5-second delay before first poll to prevent race condition
    setTimeout(() => {
      pollProgress();
      intervalRef.current = setInterval(pollProgress, 10000); // Poll every 10 seconds (increased from 2s)
    }, 5000);

    // Set overall timeout � timeout should NOT assume success
    timeoutRef.current = setTimeout(() => {
      console.warn('Upload progress polling timeout, setting error state');
      setError('Upload timeout - please try again');
      setStage('error');
      clearInterval(intervalRef.current);
    }, MAX_POLLING_DURATION);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [message.upload?.progressEndpoint, message.id, onProgressUpdate]);

  return (
    <ChatUploadBubble
      stage={stage}
      progress={progress}
      documentId={message.uploadId}
      filename={message.upload?.name || 'Video Upload'}
      uploadType={message.upload?.type || 'document'}
      error={error}
      transcriptionPreview={transcriptionData?.preview}
      transcriptId={transcriptionData?.transcriptId}
      topic={transcriptionData?.topic}
      folderId={transcriptionData?.folderId}
      onDismiss={() => {
        if (onProgressUpdate) {
          onProgressUpdate(message.id, 'dismissed');
        }
      }}
    />
  );
};

function ChatScreenContent({ initialScreen = 'signup', pendingPlanId = null, onClearPendingPlan = null, onBackToIntro, currentUserEmail, setCurrentUserEmail, useUploadedData, setUseUploadedData, isAuthenticated, setIsAuthenticated, authCheckComplete, setAuthCheckComplete, useEnterprise, setUseEnterprise, enterpriseEntityId, setEnterpriseEntityId, enterpriseEntityName, setEnterpriseEntityName, enterpriseEntityType, setEnterpriseEntityType, initialUrlRoute }) {
  // Debug all imported components
  // console.log('[DEBUG] Component types in ChatScreenContent:', {
  //   ModernSidebar: typeof ModernSidebar,
  //   ModernChatInput: typeof ModernChatInput,
  //   CurrentReportModal: typeof CurrentReportModal,
  //   GoogleDrivePickerSimple: typeof GoogleDrivePickerSimple,
  //   ModernAlert: typeof ModernAlert,
  //   FolderSetupModal: typeof FolderSetupModal,
  // });

  // Add component validation
  const validateComponent = (component, name) => {
    // Accept both regular functions and React.forwardRef components
    const isValid = typeof component === 'function' ||
      (component && typeof component === 'object' && component.$$typeof);
    if (!isValid) {
      console.error(`[ERROR] ${name} is not a valid React component:`, component);
      return false;
    }
    return true;
  };

  // Validate all components before render
  validateComponent(ModernSidebar, 'ModernSidebar');
  validateComponent(ModernChatInput, 'ModernChatInput');
  validateComponent(CurrentReportModal, 'CurrentReportModal');
  // GoogleDrivePickerSimple removed
  validateComponent(ModernAlert, 'ModernAlert');
  validateComponent(ChatUploadBubble, 'ChatUploadBubble');

  // Simple performance monitoring
  const performanceMonitor = usePerformanceMonitor('ChatScreenContent');

  // App state management for performance optimizations
  const { isActive } = useAppState();

  // Use modern theme system
  const { theme, isDarkMode, toggleTheme } = useTheme();

  // Mobile web detection for responsive layout
  const { isMobileWeb } = useIsMobileWeb();

  // Team/workspace context for filtering content by team

  // Auth token state for enterprise features
  const [authToken, setAuthToken] = useState(null);

  // Load auth token on mount
  useEffect(() => {
    const loadToken = async () => {
      try {
        const token = await authService.getToken();
        setAuthToken(token);
      } catch (err) {
        console.error('Failed to load auth token:', err);
      }
    };

    if (isAuthenticated) {
      loadToken();
    }
  }, [isAuthenticated]);

  // Decode JWT to determine if this user belongs to an org (controls enterprise toggle visibility)
  const hasOrgId = useMemo(() => {
    if (!authToken) return false;
    try {
      const parts = authToken.split('.');
      if (parts.length !== 3) return false;
      const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
      return !!payload.org_id;
    } catch {
      return false;
    }
  }, [authToken]);

  // Use workspace context for global folder management
  const {
    folders,
    selectedFolderIds,
    showFolderSetup,
    isFoldersLoading,
    setFolders,
    setSelectedFolderIds,
    setShowFolderSetup,
    setIsFoldersLoading,
    toggleFolderSelection,
    selectSingleFolder,
    fetchFolders,
    getSelectedWorkspaceFolder,
    getSelectedFolders,
    isDocumentsSelected,
    hasSelectedFolders,
    nonDefaultSelectedFolders,
    userEmail: workspaceUserEmail
  } = useWorkspace();

  const [isInitializing, setIsInitializing] = useState(true);
  const [initializationTimeout, setInitializationTimeout] = useState(false);
  const [messages, setMessages] = useState([
    { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' },
  ]);
  const [bufferedMessages, setBufferedMessages] = useState(null);
  const [isTransitioningChat, setIsTransitioningChat] = useState(false);
  const [chatSessions, setChatSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isRecordingForQuestion, setIsRecordingForQuestion] = useState(false);
  const [audioRecordingDuration, setAudioRecordingDuration] = useState(0);
  const audioRecordingTimerRef = useRef(null);
  const videoRecordingTimerRef = useRef(null);
  const recordingFolderIdRef = useRef(null); // Track folder ID for audio recording
  const videoRecordingFolderIdRef = useRef(null); // Track folder ID for video recording
  const hasRestoredVaultSelectionRef = useRef(false); // Prevent repeated vault selection restores
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [activeScreen, setActiveScreen] = useState(initialScreen);
  // Home vs Chat view state - 'home' shows HomePanel, 'chat' shows chat interface
  const [currentView, setCurrentView] = useState('home');
  // Resource tier the user picked in HomePanel's TierPickerModal. Defaults
  // to 'quick' on direct deep-link entry / refresh; HomePanel overwrites
  // when the user goes through the picker. ActionChatScreen reads this on
  useEffect(() => {
    setActiveScreen(initialScreen);
  }, [initialScreen]);

  // Ensure folder panel stays visible when navigating to drive screens
  useEffect(() => {
    if (activeScreen === 'preload' || activeScreen === 'enterprise-upload') {
      setIsFolderPanelVisible(true);
    }
  }, [activeScreen]);

  // Track if user just completed a purchase to prevent signup loop
  const [hasCompletedPurchase, setHasCompletedPurchase] = useState(false);
  const [autoOpenCloudBrowser, setAutoOpenCloudBrowser] = useState(false);
  // Teams removed — TeamContext/useTeam() is gone. Every remaining consumer of
  // activeTeamId already guards with `if (activeTeamId)` before using it (the
  // multi-workspace `formData.append('team_id', activeTeamId)` upload-tagging
  // sites), so a permanent null reaches that existing falsy-skip branch rather
  // than needing each call site edited individually.
  const activeTeamId = null;
  // Quick Start Dialog for streamlined first-time user onboarding
  const [showQuickStartDialog, setShowQuickStartDialog] = useState(false);
  const [quickStartIntent, setQuickStartIntent] = useState(null); // 'presentation' | 'report' | 'diagram'
  const [welcomeBonusGranted, setWelcomeBonusGranted] = useState(false);
  // Vault Required Modal - shows when user tries to upload without selecting a vault
  const [showURLFetchModal, setShowURLFetchModal] = useState(false);
  const [urlFetchLoading, setUrlFetchLoading] = useState(false);
  const [selectedEntityForUpload, setSelectedEntityForUpload] = useState(null);
  // Pre-fill payloads from a chat `open_builder` handoff — {goal, slide_count,
  // prefetched_corpus}. When set, the composer's GoalInput auto-starts outline
  // generation grounded on the corpus instead of waiting for manual entry.
  // Flag to skip list modal and go directly to new presentation/report goal screen
  const [skipToNewPresentation, setSkipToNewPresentation] = useState(false);
  const [skipToNewReport, setSkipToNewReport] = useState(false);
  const [showGoogleDrivePicker, setShowGoogleDrivePicker] = useState(false);
  const [googleDriveAttachmentMode, setGoogleDriveAttachmentMode] = useState(false);
  const [isEnterpriseGoogleDriveUpload, setIsEnterpriseGoogleDriveUpload] = useState(false);
  const [googleDriveDocumentDetails, setGoogleDriveDocumentDetails] = useState('');
  // Enterprise entity search (top search bar)
  const [enterpriseSearchText, setEnterpriseSearchText] = useState('');
  const [entitySuggestions, setEntitySuggestions] = useState([]);
  const [isEntitySearching, setIsEntitySearching] = useState(false);
  const [showEntitySuggestions, setShowEntitySuggestions] = useState(false);
  const [selectedEntityDetails, setSelectedEntityDetails] = useState(null); // Store selected entity details
  const entitySearchTimeoutRef = useRef(null);
  const isSelectingEntityRef = useRef(false); // Track suggestion selection to coordinate blur timing

  // Attachment state - moved here to fix hoisting issue
  const [questionAttachments, setQuestionAttachments] = useState([]);

  // Legal filter state - loaded from AsyncStorage and sent with every query
  const [legalFilters, setLegalFilters] = useState(null);
  const [filterRefreshTrigger, setFilterRefreshTrigger] = useState(0);

  // Mindmap Modal State (replacing old panel)

  // Diagram Panel State
  const [diagramData, setDiagramData] = useState([]); // Array containing single unified diagram (wrapped for backward compatibility)
  const [diagramType, setDiagramType] = useState(null);
  const [isDiagramLoading, setIsDiagramLoading] = useState(false);
  const [diagramError, setDiagramError] = useState(null);
  const [diagramQuery, setDiagramQuery] = useState('');
  const [selectedDiagramDocuments, setSelectedDiagramDocuments] = useState(null); // Store selected docs without auto-generating

  // Enhanced Diagram Feature State
  const [diagramMode, setDiagramMode] = useState(null); // 'open', 'create', or 'generate'
  const [currentEditingDiagram, setCurrentEditingDiagram] = useState(null);
  const [currentDiagramCode, setCurrentDiagramCode] = useState('');
  // Track where diagram was launched from: 'homepanel' | 'presentation' | 'report' | 'printable'
  // This affects close behavior and whether "Insert" button shows


  // Draft Panel State

  // Project Management State (URL-driven, not modal)
  const [projectRouteProjectId, setProjectRouteProjectId] = useState(null);
  const [projectRouteTab, setProjectRouteTab] = useState(null);
  const [projectRouteTaskId, setProjectRouteTaskId] = useState(null);

  // Report Composer State
  const [showComposer, setShowComposer] = useState(false);

  // Vault Warning Popup State
  const [showVaultWarning, setShowVaultWarning] = useState(false);
  const [vaultWarningDismissed, setVaultWarningDismissed] = useState(false);

  // Page Builder State
  const [selectedPage, setSelectedPage] = useState(null);
  const [showIntegrations, setShowIntegrations] = useState(false);

  // Workflow Builder State
  // When set, WorkflowBuilderScreen jumps straight into the canvas with
  // this id. Cleared on modal close. Used by AdminManagedResourcesScreen's
  // "Open" affordance to deep-link IT users into a specific workflow —
  // including smart_app_action workflows that are hidden from the
  // default list.
  // Pending per-workflow Runs/History deep link, parsed from the URL at the
  // app root and handed to WorkflowBuilderScreen as a prop (the module mounts
  // after the app boots, by which point the URL params are gone).
  // Shape: { view: 'runs'|'run-detail', workflowId, executionId } | null
  // Ensures a captured deep link opens the builder only once per page load
  // (so re-running the effect on auth changes doesn't re-open it after the
  // user navigates within / closes the module). Resets on a real reload.

  // Profession Suggestions Box State
  const [showProfessionSuggestions, setShowProfessionSuggestions] = useState(true);
  const [isProfessionSuggestionsMinimized, setIsProfessionSuggestionsMinimized] = useState(false);
  const [userProfession, setUserProfession] = useState('general'); // Always use general
  const professionSuggestionsStateLoadedRef = useRef(false);

  // Mindmap Panel State (added near diagram state)

  // Reader Panel State
  const [initialReaderDocumentId, setInitialReaderDocumentId] = useState(null);
  const [showReader, setShowReader] = useState(false);
  const [readerLaunchContext, setReaderLaunchContext] = useState('chat');
  // Track where reader was launched from: 'homepanel' | 'chat' | 'folder'

  // Knowledge Graph Panel State

  // Workspace Management State

  // Dept Data Sources admin
  const [showDeptSources, setShowDeptSources] = useState(false);
  const [showPowerApps, setShowPowerApps] = useState(false);
  // How the PowerApps surface opens: 'builder' (full CRUD + Build new, for the
  // flagship card) or 'consumer' (list-only, filtered to one kind, for the
  // "My Decision Apps" / "My Dashboards" cards). kind = 'app' | 'dashboard' |
  // null. Set by the opening handler right before setShowPowerApps(true).
  const [powerAppsView, setPowerAppsView] = useState({ mode: 'builder', kind: null });
  const openPowerAppsBuilder = useCallback(() => {
    setPowerAppsView({ mode: 'builder', kind: null });
    setShowPowerApps(true);
  }, []);
  const openDecisionAppsList = useCallback(() => {
    setPowerAppsView({ mode: 'consumer', kind: 'app' });
    setShowPowerApps(true);
  }, []);
  const openDashboardsList = useCallback(() => {
    setPowerAppsView({ mode: 'consumer', kind: 'dashboard' });
    setShowPowerApps(true);
  }, []);
  // App Memory screen (rubrics / precedents / stats). memorySlug preselects an
  // app when deep-linked from a Decision App card's Learning modal.
  const [showMemoryScreen, setShowMemoryScreen] = useState(false);
  const [memorySlug, setMemorySlug] = useState(null);
  // Department SOP Library (admin surface). userDeptIds feeds the create picker.
  const [showDeptLibrary, setShowDeptLibrary] = useState(false);
  const [userDeptIds, setUserDeptIds] = useState([]);

  // Customization State (Branding)
  const [isAdmin, setIsAdmin] = useState(false);
  // super_admin gates more sensitive admin actions (impersonation,
  // org-level config). Computed alongside isAdmin from the JWT roles
  // claim. Always implies isAdmin.
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);

  // Who may BUILD & publish Decision Apps/APIs (the pink flagship builder
  // card + the /build API). True for any admin (org/dept/super) OR the
  // dedicated `decision-app-builder` role. Consumers (everyone else) see only
  // the "My Decision Apps" / "My Dashboards" list cards. Mirrors the backend
  // gate on POST /build in smart-app-service.
  const [canBuildApps, setCanBuildApps] = useState(false);

  // Workflow surface is IT-owned. Access requires one of:
  // super_admin | org_admin | IT-workflow | (dept_admin AND dept_ids
  // includes the IT department slug). Mirrors the backend gate in
  // citra-workflow/citra_workflow/router.py _has_workflow_access and
  // Citra-User-Service/src/middleware/authMiddleware.js requireWorkflowRole.

  // Admin section (HomePanel admin cards)
  const [showAdminUsers, setShowAdminUsers] = useState(false);
  const [showDepartures, setShowDepartures] = useState(false);
  const [showAdminResources, setShowAdminResources] = useState(false);
  const [showImpersonateUser, setShowImpersonateUser] = useState(false);
  // When a user is picked for deletion from AdminUsersScreen, we stash
  // the target userId here and open the picker overlay.
  const [adminDeleteTargetUserId, setAdminDeleteTargetUserId] = useState(null);

  // Track auto-applied data source toggles so manual user changes are respected
  const hasAutoAppliedWorkspaceDefaultsRef = useRef(null);
  const lastAutoAppliedEntityIdRef = useRef(null);

  // Helper function to fetch full printable data by ID (for URL routing)

  // Helper function to fetch full presentation data by ID (for URL routing)

  // Helper function to fetch full report data by ID (for URL routing)

  // Helper function to fetch full diagram data by ID (for URL routing)

  // Route guard: prevent navigating to chat (or other protected screens) when unauthenticated
  useEffect(() => {
    // Don't enforce until initial auth check has completed
    // Otherwise this fires before async auth verification and incorrectly kicks to signup
    if (!authCheckComplete) return;
    
    // Only enforce on web and iOS/Android when not authenticated
    if (!isAuthenticated && activeScreen !== 'signup') {
      setActiveScreen('signup');
    }
  }, [isAuthenticated, activeScreen, authCheckComplete]);

  // Check if the current user is an admin (for enterprise branding access)
  useEffect(() => {
    if (!isAuthenticated) {
      setIsAdmin(false);
      setIsSuperAdmin(false);
      setCanBuildApps(false);
      return;
    }
    (async () => {
      // Fast path: check JWT roles first (org_admin / dept_admin / super_admin)
      const adminRoles = ['org_admin', 'dept_admin', 'super_admin'];
      // IT-dept slug is carried in the JWT (it_dept_id claim, minted by
      // user-service tokenService.js). Falls back to "it" if the claim
      // is missing — handles older tokens during a rolling deploy.
      let IT_DEPT_ID = 'it';
      let jwtRoles = [];
      let jwtDeptIds = [];
      let jwtEmail = null;
      let jwtUserId = null;
      try {
        const token = authService.token;
        if (token && typeof token === 'string') {
          const parts = token.split('.');
          if (parts.length === 3) {
            const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
            const decoded = JSON.parse(
              typeof atob === 'function'
                ? atob(payload)
                : Buffer.from(payload, 'base64').toString('utf-8')
            );
            jwtRoles = decoded.roles || [];
            jwtDeptIds = decoded.dept_ids || [];
            setUserDeptIds(jwtDeptIds);
            jwtEmail = decoded.email || null;
            jwtUserId = decoded.user_id || decoded.sub || null;
            if (decoded.it_dept_id) {
              IT_DEPT_ID = String(decoded.it_dept_id).toLowerCase();
            }
            console.log('[ADMIN_CHECK] JWT claims →', {
              user_id: jwtUserId,
              email: jwtEmail,
              roles: jwtRoles,
              dept_ids: jwtDeptIds,
            });
            // super_admin is computed independently so impersonation
            // (which the JWT swap retains the target user's roles for)
            // immediately hides the picker from the impersonated user.
            setIsSuperAdmin(jwtRoles.includes('super_admin'));
            // Build access — any admin role OR the dedicated builder role.
            // Set BEFORE the admin early-return so the builder role is
            // captured even for a non-admin builder.
            const isAdminRole = jwtRoles.some(r => adminRoles.includes(r));
            setCanBuildApps(isAdminRole || jwtRoles.includes('decision-app-builder'));
            if (isAdminRole) {
              console.log('[ADMIN_CHECK] ✅ Admin via JWT role:', jwtRoles);
              setIsAdmin(true);
              return;
            }
          }
        }
      } catch (e) {
        console.warn('[ADMIN_CHECK] JWT decode failed:', e?.message || e);
      }
      // Fallback: check MongoDB admins collection (legacy super-admin entries)
      try {
        const resp = await authService.authenticatedFetch(`${API_CONFIG.CITRA_SERVICE_URL}/api/admin/check`);
        if (resp.ok) {
          const data = await resp.json();
          console.log('[ADMIN_CHECK] Server /api/admin/check →', data, '(roles were:', jwtRoles, ')');
          setIsAdmin(!!data.is_admin);
          setIsSuperAdmin(!!data.is_super_admin);
          // Server-confirmed admins can build; the builder role itself only
          // arrives via the JWT block above (already applied).
          if (data.is_admin) setCanBuildApps(true);
        } else {
          console.log('[ADMIN_CHECK] Server returned', resp.status, '— not admin (roles:', jwtRoles, ')');
          setIsAdmin(false);
          setIsSuperAdmin(false);
        }
      } catch (e) {
        console.error('[ADMIN_CHECK] Admin check failed:', e);
        setIsAdmin(false);
        setIsSuperAdmin(false);
      }
    })();
  }, [isAuthenticated, authToken]);

  // URL Routing: Handle initial URL and browser back/forward navigation
  // This allows deep linking to /presentation/123, /report/456, etc.
  const urlInitializedRef = useRef(false);

  // Handle initial URL route on mount
  useEffect(() => {
    if (Platform.OS !== 'web' || urlInitializedRef.current) return;
    if (!initialUrlRoute) return;

    urlInitializedRef.current = true;

    // Referral code capture moved to MainApp.js (runs before routing/auth)

    console.log('?? [URL_ROUTING] Processing initial URL route:', initialUrlRoute);

    const { route, id } = initialUrlRoute;

    // Async function to handle routes that need data fetching
    const handleRouteWithFetch = async () => {
      // Get user email - try multiple sources
      let userId = currentUserEmail;
      if (!userId) {
        try {
          userId = await authService.getCurrentUserEmail();
        } catch (e) {
          console.warn('?? [URL_ROUTING] Could not get user email:', e);
        }
      }

      // Handle route-based navigation
      switch (route) {
        case ROUTES.CHAT:
          // Show chat interface
          console.log('?? [URL_ROUTING] Showing chat interface');
          setCurrentView('chat');
          updateDocumentTitle(ROUTES.CHAT);
          break;



        case ROUTES.HOME:
        default:
          // Stay on home view
          setCurrentView('home');
          updateDocumentTitle(ROUTES.HOME);
          break;
      }
    };

    // Execute the async route handler
    handleRouteWithFetch();
  }, [initialUrlRoute, currentUserEmail]);

  // Listen for browser back/forward navigation
  useEffect(() => {
    if (Platform.OS !== 'web') return;

    const cleanup = onUrlChange(async (routeInfo) => {
      console.log('?? [URL_ROUTING] Browser navigation detected:', routeInfo);

      const { route, id } = routeInfo;

      // Get user email for fetching
      let userId = currentUserEmail;
      if (!userId) {
        try {
          userId = await authService.getCurrentUserEmail();
        } catch (e) {
          console.warn('?? [URL_ROUTING] Could not get user email:', e);
        }
      }

      // Close all editors/modals first
      const closeAllEditors = () => {
        // Also close diagram modals
        setCurrentEditingDiagram(null);
        setCurrentDiagramCode('');
        // Reset project route state
        setProjectRouteProjectId(null);
        setProjectRouteTab(null);
        setProjectRouteTaskId(null);
      };

      // Handle route
      switch (route) {
        case ROUTES.CHAT:
          closeAllEditors();
          console.log('?? [URL_ROUTING] Browser nav: Chat interface');
          setCurrentView('chat');
          updateDocumentTitle(ROUTES.CHAT);
          break;


        case ROUTES.HOME:
        default:
          closeAllEditors();
          setCurrentView('home');
          updateDocumentTitle(ROUTES.HOME);
          break;
      }
    });

    return cleanup;
  }, []);

  // Load legal filters from AsyncStorage on mount
  useEffect(() => {
    const loadFilters = async () => {
      try {
        const filters = await loadLegalFilters();
        setLegalFilters(filters);
        console.log('?? [LEGAL_FILTERS_UI] Loaded filters from AsyncStorage:', filters);
      } catch (error) {
        console.error('?? [LEGAL_FILTERS_UI] Failed to load filters:', error);
        // Filters will remain null, backend will use defaults
      }
    };
    loadFilters();
  }, []);

  // Load user profession from storage - always use 'general'
  useEffect(() => {
    const loadProfession = async () => {
      // Always use 'general' for General Professional - no profession selection
      setUserProfession('general');
      console.log('? Using General Professional for all users');
    };
    loadProfession();
  }, []);

  // Helper for deep linking to shared content
  useEffect(() => {
    const handleDeepLink = async () => {
      // Platform check: web only
      if (Platform.OS !== 'web' || typeof window === 'undefined') return;

      const params = new URLSearchParams(window.location.search);
      const collabType = params.get('collaborate_type');
      const collabToken = params.get('collaborate_token');
      const collabId = params.get('collaborate_id');

      if (collabType && (collabToken || collabId)) {
        console.log('?? [DEEP_LINK] Collaboration link detected:', { collabType, collabId });

        // Remove params to clean URL
        const newUrl = window.location.pathname;
        window.history.replaceState({}, '', newUrl);

        if (!isAuthenticated || !currentUserEmail) {
          // Store for post-auth processing
          console.log('?? [DEEP_LINK] User not authenticated, storing for post-auth');
          try {
            await AsyncStorage.setItem('@pendingDeepLink', JSON.stringify({
              type: collabType,
              token: collabToken,
              id: collabId,
              timestamp: Date.now()
            }));
          } catch (e) {
            console.error('?? [DEEP_LINK] Failed to store pending link:', e);
          }
          return;
        }

        // User is authenticated, process immediately
        await processDeepLink(collabType, collabToken, collabId);
      }
    };

    const processDeepLink = async (collabType, collabToken, collabId) => {
      try {
        if (collabToken) {
          console.log('?? [DEEP_LINK] Accepting share...');
          await ShareService.acceptShare(collabType, collabToken);
        }

        // Open the content
        if (collabType === 'report' && collabId) {
          const userId = currentUserEmail;
          const url = `${API_CONFIG.CITRA_SERVICE_URL}/v2/note?operation=get_metadata&note_id=${encodeURIComponent(collabId)}&user_id=${encodeURIComponent(userId)}`;
          const response = await authService.authenticatedFetchJson(url);
          if (response) {
            handleLoadReport(response);
          }
        } else if (collabType === 'presentation' && collabId) {
          const userId = currentUserEmail;
          const url = `${API_CONFIG.CITRA_SERVICE_URL}/presentation/load/${encodeURIComponent(collabId)}?user_id=${encodeURIComponent(userId)}`;
          const response = await authService.authenticatedFetchJson(url);
          if (response && response.presentation) {
            handleLoadPresentation(response.presentation);
          }
        }
      } catch (error) {
        console.error('?? [DEEP_LINK] Error processing share:', error);
        ModernAlert.alert('Collaboration Error', 'Failed to join collaboration: ' + error.message);
      }
    };

    const processPendingDeepLink = async () => {
      try {
        const stored = await AsyncStorage.getItem('@pendingDeepLink');
        if (stored) {
          const pending = JSON.parse(stored);

          // Check if link is not too old (24 hours)
          if (Date.now() - pending.timestamp < 24 * 60 * 60 * 1000) {
            console.log('?? [DEEP_LINK] Processing stored link:', pending);
            await processDeepLink(pending.type, pending.token, pending.id);
          } else {
            console.log('?? [DEEP_LINK] Stored link expired, clearing');
          }

          // Clear stored link after processing
          await AsyncStorage.removeItem('@pendingDeepLink');
        }
      } catch (e) {
        console.error('?? [DEEP_LINK] Failed to process pending link:', e);
      }
    };

    // Check URL for new deep link
    handleDeepLink();

    // Also check for stored pending link if authenticated
    if (isAuthenticated && currentUserEmail) {
      processPendingDeepLink();
    }
  }, [isAuthenticated, currentUserEmail]);


  // Load profession suggestions visibility/minimize state from storage
  useEffect(() => {
    const loadSuggestionsState = async () => {
      try {
        let stored;
        if (Platform.OS === 'web' && typeof window !== 'undefined' && window.localStorage) {
          stored = window.localStorage.getItem('@profession_suggestions_state');
        } else {
          stored = await AsyncStorage.getItem('@profession_suggestions_state');
        }

        if (stored) {
          const parsed = JSON.parse(stored);
          if (typeof parsed.visible === 'boolean') {
            setShowProfessionSuggestions(parsed.visible);
          }
          if (typeof parsed.minimized === 'boolean') {
            setIsProfessionSuggestionsMinimized(parsed.minimized);
          }
        }
      } catch (error) {
        console.warn('?? [SUGGESTIONS] Failed to load suggestions state:', error);
      } finally {
        professionSuggestionsStateLoadedRef.current = true;
      }
    };

    loadSuggestionsState();
  }, []);

  const persistSuggestionsState = useCallback(async (visible, minimized) => {
    const payload = JSON.stringify({ visible, minimized });
    try {
      if (Platform.OS === 'web' && typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem('@profession_suggestions_state', payload);
      } else {
        await AsyncStorage.setItem('@profession_suggestions_state', payload);
      }
    } catch (error) {
      console.warn('?? [SUGGESTIONS] Failed to persist suggestions state:', error);
    }
  }, []);

  useEffect(() => {
    if (!professionSuggestionsStateLoadedRef.current) return;
    persistSuggestionsState(showProfessionSuggestions, isProfessionSuggestionsMinimized);
  }, [showProfessionSuggestions, isProfessionSuggestionsMinimized, persistSuggestionsState]);

  const handleProfessionSuggestionsClose = useCallback(() => {
    setShowProfessionSuggestions(false);
    setIsProfessionSuggestionsMinimized(false);
    persistSuggestionsState(false, false);
  }, [persistSuggestionsState]);

  const handleProfessionSuggestionsMinimizeToggle = useCallback((next) => {
    setIsProfessionSuggestionsMinimized(next);
    persistSuggestionsState(showProfessionSuggestions, next);
  }, [persistSuggestionsState, showProfessionSuggestions]);








  // Utility function to get file type icon based on file extension and type
  const getFileIcon = useCallback((fileName, type) => {
    if (type === 'image') {
      return { name: 'image', color: '#4CAF50' };
    }

    if (type === 'audio') {
      return { name: 'musical-note', color: '#FF9500' };
    }

    if (type === 'video') {
      return { name: 'videocam', color: '#8B5CF6' };
    }

    if (type === 'pdf') {
      return { name: 'document-text', color: '#FF5722' };
    }

    if (type === 'spreadsheet') {
      return { name: 'grid', color: '#4CAF50' };
    }

    if (type === 'presentation') {
      return { name: 'easel', color: '#FF9500' };
    }

    if (type === 'archive') {
      return { name: 'archive', color: '#6366F1' };
    }

    if (type === 'document') {
      if (!fileName) return { name: 'document-text', color: '#2196F3' };

      const extension = fileName.toLowerCase().split('.').pop();

      switch (extension) {
        case 'doc':
        case 'docx':
          return { name: 'document', color: '#2196F3' };
        case 'txt':
          return { name: 'document-text', color: '#795548' };
        default:
          return { name: 'document-text', color: '#2196F3' };
      }
    }

    // Default fallback
    return { name: 'attach', color: '#9E9E9E' };
  }, []);

  // Utility function to truncate filename for display
  const truncateFileName = useCallback((fileName, maxLength = 12) => {
    if (!fileName || fileName.length <= maxLength) return fileName;

    const extension = fileName.split('.').pop();
    const nameWithoutExt = fileName.substring(0, fileName.lastIndexOf('.'));

    if (nameWithoutExt.length <= maxLength - extension.length - 1) {
      return fileName;
    }

    const truncatedName = nameWithoutExt.substring(0, maxLength - extension.length - 4);
    return `${truncatedName}...${extension}`;
  }, []);

  // Helper function to get file icon for web platform
  const getFileIconForWeb = useCallback((attachment) => {
    const fileType = detectFileType(attachment);

    // Using emoji icons for better web compatibility
    switch (fileType) {
      case 'pdf':
        return { icon: '??', color: '#3B82F6', label: 'PDF' };
      case 'document':
        return { icon: '??', color: '#3B82F6', label: 'DOC' };
      case 'spreadsheet':
        return { icon: '??', color: '#10B981', label: 'XLS' };
      case 'presentation':
        return { icon: '??', color: '#F59E0B', label: 'PPT' };
      case 'image':
        return { icon: '???', color: '#10B981', label: 'IMG' };
      case 'audio':
        return { icon: '??', color: '#F97316', label: 'AUDIO' };
      case 'video':
        return { icon: '??', color: '#8B5CF6', label: 'VIDEO' };
      case 'archive':
        return { icon: '??', color: '#6366F1', label: 'ZIP' };
      case 'text':
        return { icon: '??', color: '#64748B', label: 'TXT' };
      default:
        return { icon: '??', color: '#64748B', label: 'FILE' };
    }
  }, []);

  // Helper function to remove attachment
  const removeAttachment = useCallback((attachmentId) => {
    setQuestionAttachments(prev => prev.filter(a => a.id !== attachmentId));
    setAttachmentProgress(prev => {
      const newProgress = { ...prev };
      delete newProgress[attachmentId];
      return newProgress;
    });
  }, []);

  // Enhanced file type detection function
  const detectFileType = useCallback((attachment) => {
    // First check the explicit type
    if (attachment.type) {
      return attachment.type;
    }

    // Check MIME type
    if (attachment.mimeType) {
      if (attachment.mimeType.startsWith('image/')) return 'image';
      if (attachment.mimeType.startsWith('audio/')) return 'audio';
      if (attachment.mimeType.startsWith('video/')) return 'video';
      if (attachment.mimeType.includes('pdf')) return 'pdf';
      if (attachment.mimeType.includes('document')) return 'document';
      if (attachment.mimeType.includes('sheet')) return 'spreadsheet';
      if (attachment.mimeType.includes('presentation')) return 'presentation';
    }

    // Check file extension
    if (attachment.name) {
      const ext = attachment.name.toLowerCase().split('.').pop();
      const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'];
      const audioExts = ['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'];
      const videoExts = ['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm'];
      const documentExts = ['doc', 'docx', 'txt', 'rtf'];
      const spreadsheetExts = ['xls', 'xlsx', 'csv'];
      const presentationExts = ['ppt', 'pptx'];
      const archiveExts = ['zip', 'rar', '7z', 'tar', 'gz'];

      if (ext === 'pdf') return 'pdf';
      if (imageExts.includes(ext)) return 'image';
      if (audioExts.includes(ext)) return 'audio';
      if (videoExts.includes(ext)) return 'video';
      if (documentExts.includes(ext)) return 'document';
      if (spreadsheetExts.includes(ext)) return 'spreadsheet';
      if (presentationExts.includes(ext)) return 'presentation';
      if (archiveExts.includes(ext)) return 'archive';
    }

    return 'document'; // Default fallback
  }, []);

  const shouldIncludeAttachmentForQuery = useCallback((attachment) => {
    if (!attachment) return false;
    const fileType = detectFileType(attachment) || '';
    const mimeType = (attachment.mimeType || '').toLowerCase();

    if (attachment.isScreenshot) {
      return true;
    }

    if (fileType === 'image' || fileType === 'audio' || fileType === 'video' || fileType === 'screenshot') {
      return true;
    }

    if (mimeType.startsWith('image/') || mimeType.startsWith('audio/') || mimeType.startsWith('video/')) {
      return true;
    }

    return false;
  }, [detectFileType]);

  const prepareAttachmentsForQuery = useCallback(async (attachments) => {
    if (!attachments || attachments.length === 0) {
      return [];
    }

    const convertBlobToBase64 = async (blob) => {
      return new Promise((resolve, reject) => {
        try {
          const reader = new FileReader();
          reader.onloadend = () => {
            const result = reader.result;
            if (typeof result === 'string') {
              const commaIndex = result.indexOf(',');
              resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
            } else {
              resolve('');
            }
          };
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(blob);
        } catch (error) {
          reject(error);
        }
      });
    };

    const results = [];

    for (const attachment of attachments) {
      try {
        if (!shouldIncludeAttachmentForQuery(attachment)) {
          continue;
        }

        let base64Data = attachment.base64 || attachment.base64Data || null;
        const mimeType = attachment.mimeType || 'application/octet-stream';
        const detectedType = detectFileType(attachment);

        if (!base64Data) {
          if (Platform.OS === 'web') {
            if (attachment.uri && attachment.uri.startsWith('data:')) {
              const commaIndex = attachment.uri.indexOf(',');
              base64Data = commaIndex >= 0 ? attachment.uri.slice(commaIndex + 1) : attachment.uri;
            } else if (attachment.file instanceof File) {
              base64Data = await convertBlobToBase64(attachment.file);
            } else if (attachment.blob instanceof Blob) {
              base64Data = await convertBlobToBase64(attachment.blob);
            } else if (attachment.uri) {
              const response = await fetch(attachment.uri);
              const blob = await response.blob();
              base64Data = await convertBlobToBase64(blob);
            }
          } else if (attachment.uri) {
            const encoding = FileSystem.EncodingType ? FileSystem.EncodingType.Base64 : 'base64';
            base64Data = await FileSystem.readAsStringAsync(attachment.uri, { encoding });
          }
        }

        if (!base64Data) {
          console.warn('?? Skipping attachment without data for multimodal payload:', attachment.name);
          continue;
        }

        let size = attachment.size || attachment.file?.size || attachment.blob?.size;
        if (!size && base64Data) {
          // Estimate size from base64 length
          size = Math.floor((base64Data.length * 3) / 4);
        }

        results.push({
          id: attachment.id,
          name: attachment.name,
          type: detectedType,
          mimeType: mimeType,
          size: size || null,
          extractedText: attachment.extractedText || null,
          isScreenshot: Boolean(attachment.isScreenshot),
          isQuestionRecording: Boolean(attachment.isQuestionRecording),
          base64: base64Data
        });
      } catch (error) {
        console.error('? Failed to prepare attachment for query:', attachment?.name, error);
      }
    }

    if (results.length > 0) {
      console.log('?? Prepared multimodal attachments for query:', results.map(a => ({ name: a.name, mimeType: a.mimeType, size: a.size })));
    }

    return results;
  }, [detectFileType, shouldIncludeAttachmentForQuery]);

  // Function to check attachment limit and add attachment
  // Helper function to show attachment success toast instead of chat message

  // Helper function to show upload success toast notifications
  const showUploadToast = useCallback((message, customFolderName = null, customIsDefaultFolder = null) => {
    // Determine folder information
    let folderName = 'Citra AI';
    let isDefaultFolder = true;

    if (customFolderName !== null && customIsDefaultFolder !== null) {
      // Use provided custom values
      folderName = customFolderName;
      isDefaultFolder = customIsDefaultFolder;
    } else {
      // Try to determine from current selection
      const selectedFolder = selectedFolderIds.length === 1 ? selectedFolderIds[0] : null;
      if (selectedFolder && selectedFolder !== 'documents') {
        const folder = folders.find(f => f.id === selectedFolder);
        if (folder) {
          folderName = folder.name;
          isDefaultFolder = false;
        }
      }
    }

    setUploadSuccess({
      visible: true,
      documentTitle: message,
      folderName: folderName,
      isDefaultFolder: isDefaultFolder,
    });
  }, [selectedFolderIds, folders]);

  // Helper function to validate if content type is allowed in the selected folder
  // Helper function to determine folder routing (mirrors backend logic)
  const determineUploadFolder = useCallback((contentType, uploadSource = null, selectedFolder = null) => {
    // Force audio recordings to 'meetings' folder
    if (contentType === 'recording' || uploadSource === 'recording' || uploadSource === 'meeting_recording') {
      return 'meetings';
    }

    // Force notes to 'notes' folder
    if (contentType === 'note') {
      return 'notes';
    }

    // Handle audio file uploads (not recordings)
    if (contentType === 'audio' && uploadSource !== 'recording' && uploadSource !== 'meeting_recording') {
      if (selectedFolder && selectedFolder.trim()) {
        return selectedFolder;
      }
      return 'documents'; // Default for audio file uploads
    }

    // Video uploads: always go to 'meetings' folder
    if (contentType === 'video') {
      return 'meetings';
    }

    // For documents, images, and other content types, check if folder is selected
    if (selectedFolder && selectedFolder.trim()) {
      return selectedFolder;
    }

    // Default fallback for documents, images, and other uploads
    return 'documents';
  }, []);

  // Helper function to show folder routing information in toast
  const showFolderRoutingToast = useCallback((contentType, uploadSource, fileName, resolvedFolderId = null) => {
    // Skip informational toast for audio recordings to avoid redundant popups when starting from the ribbon
    if (contentType === 'recording' || uploadSource === 'recording' || uploadSource === 'meeting_recording') {
      return;
    }

    const selectedFolder = selectedFolderIds.length === 1 ? selectedFolderIds[0] : null;
    const targetFolder = resolvedFolderId || determineUploadFolder(contentType, uploadSource, selectedFolder);

    // Get folder display name
    let folderDisplayName = 'General';
    if (targetFolder === 'meetings') {
      folderDisplayName = 'Meetings';
    } else if (targetFolder === 'notes') {
      folderDisplayName = 'Notes';
    } else if (targetFolder !== 'documents') {
      const folder = folders.find(f => f.id === targetFolder);
      folderDisplayName = folder ? folder.name : 'Unknown Folder';
    }

    // Create informative message based on content type and routing
    let message = '';
    let isForced = false;

    if (contentType === 'recording' || uploadSource === 'recording' || uploadSource === 'meeting_recording') {
      // Audio meeting recordings always go to Meetings folder within the vault
      if (selectedFolder && selectedFolder !== 'general' && selectedFolder !== 'documents') {
        message = `?? Audio meeting recording will be saved to "Meetings" folder in "${folderDisplayName}" data store`;
      } else {
        message = `?? Audio meeting recording will be saved to "Meetings" folder in "General" data store`;
      }
      isForced = false;
    } else if (contentType === 'video') {
      message = selectedFolder && selectedFolder !== 'general'
        ? `?? Video "${fileName}" will be saved to "${folderDisplayName}" folder (as selected)`
        : `?? Video "${fileName}" will be saved to "${folderDisplayName}" folder (default)`;
      isForced = false;
    } else if (contentType === 'note') {
      message = selectedFolder && selectedFolder !== 'general'
        ? `?? Note will be saved to "${folderDisplayName}" folder (as selected)`
        : `?? Note will be saved to "${folderDisplayName}" folder (default)`;
      isForced = false;
    } else if (targetFolder && targetFolder !== 'documents' && targetFolder !== 'meetings' && targetFolder !== 'notes') {
      // If we have an explicit non-default folder target, treat as selected
      message = `?? "${fileName}" will be saved to "${folderDisplayName}" folder (as selected)`;
      isForced = false;
    } else {
      message = `?? "${fileName}" will be saved to "${folderDisplayName}" folder (default)`;
      isForced = false;
    }

    setUploadSuccess({
      visible: true,
      documentTitle: message,
      folderName: isForced ? 'Auto-routed' : 'User selection',
      isDefaultFolder: !selectedFolder,
    });
  }, [selectedFolderIds, folders, determineUploadFolder]);

  const addAttachmentWithLimit = useCallback((newAttachment) => {
    const MAX_ATTACHMENTS = 10;

    if (questionAttachments.length >= MAX_ATTACHMENTS) {
      Alert.alert(
        'Attachment Limit Reached',
        `You can only attach up to ${MAX_ATTACHMENTS} files per query. Please remove some attachments before adding new ones.`,
        [{ text: 'OK' }]
      );
      return false;
    }

    setQuestionAttachments(prev => [...prev, newAttachment]);
    return true;
  }, [questionAttachments.length]);
  const [preloadUploadStatus, setPreloadUploadStatus] = useState(null);

  // Handle template upload status changes

  // Enhanced attachment processing states
  const [attachmentProcessingQueue, setAttachmentProcessingQueue] = useState([]);
  const [isProcessingAttachments, setIsProcessingAttachments] = useState(false);
  const [attachmentProgress, setAttachmentProgress] = useState({});

  // Floating notification system
  const [floatingNotifications, setFloatingNotifications] = useState([]);

  const [isLoading, setIsLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isBotTyping, setIsBotTyping] = useState(false);
  const [enableInternetSearch, setEnableInternetSearch] = useState(true); // Internet search toggle state - default ON
  const [showInternetWarningModal, setShowInternetWarningModal] = useState(false); // Warning modal for disabling internet search

  // Connection scope state
  const [showConnectionScope, setShowConnectionScope] = useState(false);
  const [querySourcesLoaded, setQuerySourcesLoaded] = useState(false);
  const loadedQuerySourcesRef = useRef(null); // remembers initially loaded preferences to avoid overwriting user choice

  const [noteText, setNoteText] = useState('');
  const [notes, setNotes] = useState([]);
  const [transcripts, setTranscripts] = useState([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [isLoadingNotes, setIsLoadingNotes] = useState(false);
  const [isLoadingTranscripts, setIsLoadingTranscripts] = useState(false);
  const [isLoadingDocuments, setIsLoadingDocuments] = useState(false);

  // Lazy loading state for each history type
  const [chatHistoryPage, setChatHistoryPage] = useState(0);
  const [notesPage, setNotesPage] = useState(0);
  const [transcriptsPage, setTranscriptsPage] = useState(0);
  const [documentsPage, setDocumentsPage] = useState(0);

  const [allChatSessions, setAllChatSessions] = useState([]);
  const [allNotes, setAllNotes] = useState([]);

  const [hasMoreChatHistory, setHasMoreChatHistory] = useState(true);
  const [hasMoreNotes, setHasMoreNotes] = useState(true);
  const [hasMoreTranscripts, setHasMoreTranscripts] = useState(true);
  const [hasMoreDocuments, setHasMoreDocuments] = useState(true);

  const [isLoadingMoreChat, setIsLoadingMoreChat] = useState(false);
  const [isLoadingMoreNotes, setIsLoadingMoreNotes] = useState(false);
  const [isLoadingMoreTranscripts, setIsLoadingMoreTranscripts] = useState(false);
  const [isLoadingMoreDocuments, setIsLoadingMoreDocuments] = useState(false);

  // Search state for different screens
  const [documentsSearchQuery, setDocumentsSearchQuery] = useState('');
  const [transcriptsSearchQuery, setTranscriptsSearchQuery] = useState('');
  const [folderContentSearchQuery, setFolderContentSearchQuery] = useState('');

  // Loading states for individual items
  const [isLoadingNoteContent, setIsLoadingNoteContent] = useState(false);
  const [isLoadingTranscriptContent, setIsLoadingTranscriptContent] = useState(false);
  const [isLoadingDocumentContent, setIsLoadingDocumentContent] = useState(false);

  // Lazy loading constants
  const ITEMS_PER_PAGE = 10;
  // Inline editing states
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [editingText, setEditingText] = useState('');
  const [isWaitingForRecordingTitle, setIsWaitingForRecordingTitle] = useState(false);
  const [recordingTitle, setRecordingTitle] = useState('');
  const [recordingType, setRecordingType] = useState(''); // Store whether it's 'memory' or 'question'
  // Audio Meeting overlay (HomePanel "Audio Meeting" card) — when true, the
  // recording flow renders inline as a floating panel instead of switching
  // to chat. Side-effects that would push chat messages are guarded on this.
  const [isWaitingForImageTitle, setIsWaitingForImageTitle] = useState(false);
  const [pendingImage, setPendingImage] = useState(null);
  const [isWaitingForDocumentTitle, setIsWaitingForDocumentTitle] = useState(false);
  const [pendingDocument, setPendingDocument] = useState(null);
  const [isWaitingForPhotoTitle, setIsWaitingForPhotoTitle] = useState(false);
  const [pendingPhoto, setPendingPhoto] = useState(null);
  // --- Meeting Title State ---
  const [pendingMeetingRecording, setPendingMeetingRecording] = useState(false);
  const [recordingStartTime, setRecordingStartTime] = useState(null);
  const [cancelTokenSource, setCancelTokenSource] = useState(null);
  const [shouldScrollToMessage, setShouldScrollToMessage] = useState(null);
  const [pendingScrollToBottom, setPendingScrollToBottom] = useState(false);
  const [scrollAction, setScrollAction] = useState(null);
  const [sessionName, setSessionName] = useState('Citra AI');
  const [newSessionName, setNewSessionName] = useState(sessionName);
  const { showActionSheetWithOptions } = useActionSheet();
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(false);

  const [isUserDetailModalVisible, setIsUserDetailModalVisible] = useState(false);
  const [isLoadingUserDetail, setIsLoadingUserDetail] = useState(false);

  // ModernAlert state management
  const [showModernAlert, setShowModernAlert] = useState(false);
  const [modernAlertConfig, setModernAlertConfig] = useState({
    title: '',
    message: '',
    type: 'info',
    buttons: []
  });

  // Toast notification function for enterprise features

  // No-op function to prevent errors from remaining setShowAiModelDropdown calls
  const setShowAiModelDropdown = () => { };
  const [showHistoryDropdown, setShowHistoryDropdown] = useState(false);
  const [showPersonalInfoDropdown, setShowPersonalInfoDropdown] = useState(false);
  const [showCustomizationDropdown, setShowCustomizationDropdown] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(true); // Default to collapsed
  const [inputText, setInputText] = useState('');
  const [message, setMessage] = useState('');

  // Deep Research State - Toggle removed, LLM orchestrator now decides automatically
  // const [isDeepResearchEnabled, setIsDeepResearchEnabled] = useState(false);

  const [showDeepResearchPanel, setShowDeepResearchPanel] = useState(false); // Panel visibility control
  const [showCurrentReportModal, setShowCurrentReportModal] = useState(false); // Current report modal
  const [showHowToUseModal, setShowHowToUseModal] = useState(false); // How to Use modal
  // SaaS Data Connection modal removed � SaaS analytics moved to Citra Agent desktop
  const [isPendingClarificationResponse, setIsPendingClarificationResponse] = useState(false); // Track if waiting for clarification
  const [deepResearchState, setDeepResearchState] = useState({
    isResearching: false,
    stage: 'ready', // ready, planning, searching, analyzing, synthesizing, questioning, complete, error
    currentStep: 1,
    totalSteps: 5,
    substeps: [],
    progress: 0,
    query: '',
    findings: [],
    questions: [],
    isAwaitingUserInput: false,
    researchId: null,
    citations: [],
    researchMetadata: {}
  });

  // --- Meeting Recording State and Handlers (Web Only) ---
  const mediaRecorderRef = useRef(null);
  const recordedChunksRef = useRef([]);

  // Refs to store recording streams for cleanup
  const microphoneStreamRef = useRef(null);
  const displayStreamRef = useRef(null);
  const combinedStreamRef = useRef(null);
  const audioContextRef = useRef(null);
  const audioStreamRef = useRef(null); // For audio-only recordings
  // When true, the next audio recording will also try to capture system (PC)
  // audio via getDisplayMedia. Default false so the screen-share picker never
  // appears unless the user explicitly opts in.
  const includeSystemAudioRef = useRef(false);

  // --- Recording Storage Manager (IndexedDB) ---
  const recordingStorageManager = useRef(null);
  const currentRecordingSession = useRef(null);

  // Audio recording progress state (for question mode recordings)
  const [audioRecordingProgress, setAudioRecordingProgress] = useState({
    duration: 0,
    isRecording: false,
    isPaused: false
  });


  // --- Audio Extraction Progress State ---

  // --- Recording Started Notification State ---

  // --- Query Enhancement Toggles ---
  // Toggle states are NOT persisted - they reset to defaults on each page load
  // Vault is enabled by default, General Query and Enterprise are disabled by default
  const [isModelOnlyMode, setIsModelOnlyMode] = useState(false); // AI Model mode toggle - default disabled (Vault enabled by default)

  // --- Folder Management State --- MOVED TO WORKSPACE CONTEXT
  // const [folders, setFolders] = useState([]);
  // const [selectedFolderIds, setSelectedFolderIds] = useState([]);
  // Right-rail personal folder panel. Starts hidden and has no toggle while
  // PERSONAL_VAULT_ENABLED is false — the rail is not mounted either way.
  const [isFolderPanelVisible, setIsFolderPanelVisible] = useState(PERSONAL_VAULT_ENABLED);
  // const [showFolderSetup, setShowFolderSetup] = useState(false);
  // const [isFoldersLoading, setIsFoldersLoading] = useState(true);

  // --- Folder Content State ---
  const [folderContent, setFolderContent] = useState(null);
  const [isLoadingFolderContent, setIsLoadingFolderContent] = useState(false);
  const [openFolderContent, setOpenFolderContent] = useState(null);
  const [folderContentLoading, setFolderContentLoading] = useState(false);
  const [isLoadingMoreFolderContent, setIsLoadingMoreFolderContent] = useState(false);
  const [hasMoreFolderContent, setHasMoreFolderContent] = useState(true);
  const [folderContentPage, setFolderContentPage] = useState(0);

  // Reset folder content when workspace changes
  useEffect(() => {
    if (activeTeamId) {
      // Clear folder content state when switching workspaces
      setFolderContent(null);
      setOpenFolderContent(null);
      setFolderContentPage(0);
      setHasMoreFolderContent(true);
      setIsLoadingFolderContent(false);
      setFolderContentLoading(false);
    }
  }, [activeTeamId]);

  // --- Upload Success Toast State ---
  const [uploadSuccess, setUploadSuccess] = useState({
    visible: false,
    documentTitle: '',
    folderName: '',
    isDefaultFolder: false,
  });

  // --- Folder Panel Animation ---
  const folderButtonScale = useRef(new Animated.Value(1)).current;
  const folderButtonRotation = useRef(new Animated.Value(0)).current;

  const inputRef = useRef(null);



  // Auto-focus logic for web
  useEffect(() => {
    if (Platform.OS === 'web' && isActive && inputRef.current) {
      const node = inputRef.current;
      const focusInput = () => {
        try {
          if (node && node.focus) {
            node.focus();
          } else if (node && node._nativeTag) {
            const domNode = document.getElementById(node._nativeTag);
            if (domNode) domNode.focus();
          }
        } catch (e) {
          console.error("Focus error", e);
        }
      };

      // Small delay to ensure component is rendered
      setTimeout(focusInput, 50);
    }
  }, [isActive]);

  // Load query sources preferences from async storage on mount
  useEffect(() => {
    const loadQuerySourcesPreferences = async () => {
      try {
        const preferences = await storageManager.getQuerySourcesPreferences();
        console.log('?? [QUERY_SOURCES] Loaded preferences:', JSON.stringify(preferences));
        if (preferences) {
          loadedQuerySourcesRef.current = preferences;
          console.log('?? [QUERY_SOURCES] Restoring values:', {
            useVault: preferences.useVault,
            useInternet: preferences.useInternet,
            useEnterprise: preferences.useEnterprise
          });

          // A stored useVault=true from an older build must not resurrect the
          // personal vault as a chat source.
          if (PERSONAL_VAULT_ENABLED && preferences.useVault !== undefined) {
            console.log('? Setting useVault to:', preferences.useVault);
            setUseUploadedData(preferences.useVault);
          }
          // Always default Internet Search to ON (User Request)
          /*
          if (preferences.useInternet !== undefined) {
            console.log('? Setting useInternet to:', preferences.useInternet);
            setEnableInternetSearch(preferences.useInternet);
          }
          */
          // Enterprise sources are Always On — a stored `false` from a build
          // that still had the personal Data Store must not leave this chat
          // with no searchable source at all.
          if (preferences.useEnterprise === false) {
            console.log('🏢 Ignoring stored useEnterprise=false — enterprise sources are Always On');
          } else if (preferences.useEnterprise !== undefined) {
            console.log('? Setting useEnterprise to:', preferences.useEnterprise);
            setUseEnterprise(preferences.useEnterprise);
          }
          if (preferences.enterpriseEntityId) setEnterpriseEntityId(preferences.enterpriseEntityId);
          if (preferences.enterpriseEntityName) setEnterpriseEntityName(preferences.enterpriseEntityName);
          if (preferences.enterpriseEntityType) setEnterpriseEntityType(preferences.enterpriseEntityType);
        } else {
          console.log('?? [QUERY_SOURCES] No saved preferences found, using defaults');
          loadedQuerySourcesRef.current = null;
        }
      } catch (error) {
        console.error('? [QUERY_SOURCES] Error loading preferences:', error);
      } finally {
        console.log('? [QUERY_SOURCES] Setting querySourcesLoaded to true');
        setQuerySourcesLoaded(true);
      }
    };

    loadQuerySourcesPreferences();
  }, []);

  // When vault is toggled on, restore last selected vault from AsyncStorage if nothing is selected
  useEffect(() => {
    // Reset guard when vault is turned off so we can restore again on next enable
    if (!useUploadedData) {
      hasRestoredVaultSelectionRef.current = false;
      // Clear in-memory vault selection when Vault is turned off
      // BUT preserve AsyncStorage so we can restore when vault is re-enabled
      if (selectedFolderIds.length > 0) {
        setSelectedFolderIds([]);
        console.log('?? [VAULT] Cleared in-memory folder selection after disabling Vault (AsyncStorage preserved for re-enable)');
      }
      return;
    }

    // Already restored or a folder is actively selected
    if (hasRestoredVaultSelectionRef.current || selectedFolderIds.length > 0) {
      return;
    }

    const restoreLastVaultSelection = async () => {
      try {
        const savedSelection = await AsyncStorage.getItem('selectedFolderIds');
        if (!savedSelection) {
          return;
        }

        const parsedSelection = JSON.parse(savedSelection);
        const validSelection = parsedSelection.filter(id =>
          id === 'documents' || folders.some(f => f.id === id || (f.unique_id && f.unique_id === id))
        );

        if (validSelection.length > 0) {
          setSelectedFolderIds(validSelection);
          console.log('?? [VAULT] Restored saved vault selection after enabling Vault:', validSelection);
        }
      } catch (error) {
        console.error('? [VAULT] Failed to restore saved vault selection:', error);
      } finally {
        hasRestoredVaultSelectionRef.current = true;
      }
    };

    restoreLastVaultSelection();
  }, [useUploadedData, selectedFolderIds.length, folders, setSelectedFolderIds]);

  // Save query sources preferences whenever they change
  useEffect(() => {
    // Only save after initial load to avoid overwriting with defaults
    if (!querySourcesLoaded) {
      console.log('?? [QUERY_SOURCES] Skipping save - not loaded yet');
      return;
    }

    const saveQuerySourcesPreferences = async () => {
      try {
        const preferences = {
          useVault: useUploadedData,
          useInternet: enableInternetSearch,
          useEnterprise,
          enterpriseEntityId,
          enterpriseEntityName,
          enterpriseEntityType,
        };
        console.log('?? [QUERY_SOURCES] Saving preferences:', JSON.stringify(preferences));
        await storageManager.saveQuerySourcesPreferences(preferences);
        console.log('? [QUERY_SOURCES] Preferences saved successfully');
      } catch (error) {
        console.error('? [QUERY_SOURCES] Error saving preferences:', error);
      }
    };

    saveQuerySourcesPreferences();
  }, [useUploadedData, enableInternetSearch, useEnterprise, enterpriseEntityId, enterpriseEntityName, enterpriseEntityType, querySourcesLoaded]);

  // Add this effect to refocus after sending message
  useEffect(() => {
    if (Platform.OS === "web" && message === "") {
      // Message was just cleared (sent), refocus
      const timeoutId = setTimeout(() => {
        if (inputRef.current) {
          const node = inputRef.current;
          if (node && node.focus) {
            node.focus();
          } else if (node && node._nativeTag) {
            const domNode = document.getElementById(node._nativeTag);
            if (domNode) domNode.focus();
          }
        }
      }, 50);

      return () => clearTimeout(timeoutId);
    }
  }, [message]);

  // Vault Warning Popup - Show/hide based on Vault status and folder selection
  useEffect(() => {
    if (vaultWarningDismissed) {
      setShowVaultWarning(false);
      return;
    }

    if (useUploadedData) {
      setShowVaultWarning(true);
    } else {
      setShowVaultWarning(false);
      setVaultWarningDismissed(false); // Reset dismissed state when Vault is disabled
    }
  }, [useUploadedData, vaultWarningDismissed, selectedFolderIds]);

  // --- Web Audio Recording State ---
  const webAudioRecorderRef = useRef(null);
  const webAudioChunksRef = useRef([]);



  // New function to handle compressed recording uploads (with separate audio for videos)




  // Platform optimization configuration
  const platformOptimization = useMemo(() => ({
    shouldUseNativeDriver: Platform.OS !== 'web', // Disable native driver for web
    shouldUseFlatListOptimizations: Platform.OS !== 'web',
    shouldUseLayoutAnimations: Platform.OS !== 'web'
  }), []);

  // Performance optimizations for input
  const debouncedInputText = useDebounce(inputText, 300); // Debounce for better performance
  const throttledScrollToBottom = useThrottle(useCallback(() => {
    flatListRef.current?.scrollToEnd({ animated: platformOptimization.shouldUseNativeDriver });
  }, []), 100);

  // Removed smart intent analysis state variables - keeping simple interface

  // Background upload tracking
  const [backgroundUploads, setBackgroundUploads] = useState(new Map());
  const backgroundOperationsRef = useRef(new Map());

  const menuAnimation = useRef(new Animated.Value(-width * 0.8)).current;
  const flatListRef = useRef(null);
  const recordingRef = useRef(null);
  const animationCompleteRef = useRef(null);

  const [selectedNote, setSelectedNote] = useState(null);
  const [isNoteViewModalVisible, setIsNoteViewModalVisible] = useState(false);
  const [isNoteEditModalVisible, setIsNoteEditModalVisible] = useState(false);
  const [editingNote, setEditingNote] = useState(null);
  const [selectedTranscript, setSelectedTranscript] = useState(null);
  const [isTranscriptViewModalVisible, setIsTranscriptViewModalVisible] = useState(false);
  const [isTranscriptEditModalVisible, setIsTranscriptEditModalVisible] = useState(false);
  const [editingTranscript, setEditingTranscript] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [isDocumentViewModalVisible, setIsDocumentViewModalVisible] = useState(false);
  const [isDocumentEditModalVisible, setIsDocumentEditModalVisible] = useState(false);
  const [editingDocument, setEditingDocument] = useState(null);
  const [userDeviceId, setUserDeviceId] = useState(null); // Start with null, set to email after login
  const [userType, setUserType] = useState('free'); // Track user type: 'free' or 'paid'

  // Static UI copy. Was persona-keyed, but the config had exactly one
  // profile, so every lookup already resolved to these strings.
  const personaText = UI_TEXT;

  // isLegalProfessional removed with persona: it was `return false` with a
  // [persona] dependency, and nothing read it.
  // showFirstTimeTutorial / isFirstTimePersonaSetup removed with the
  // FirstTimeUserTutorial — nothing ever set them true.

  // The profession-sync effect is gone with persona. It only ever called
  // setUserProfession('general'), which is already the useState default, so it
  // was a no-op before this change too. The commented-out persona debug effect
  // beside it went with it.

  // User plan management - use email for plan data

  // Track if login was just completed to prevent mount effect interference
  const [justLoggedIn, setJustLoggedIn] = useState(false);

  // Verify saved user data against API
  const verifyUserDataWithAPI = useCallback(async (savedUserData) => {
    try {
      if (process.env.NODE_ENV === 'development') {
        console.log('?? [AUTH] Verifying user data with API...');
      }

      let token = savedUserData?.data?.token;

      // If no token in user data, check authService
      if (!token) {
        if (await authService.hasToken()) {
          token = await authService.getToken();
          if (token && savedUserData) {
            // Update user data with token from authService
            savedUserData.data = savedUserData.data || {};
            savedUserData.data.token = token;
            await AsyncStorage.setItem('@user', JSON.stringify(savedUserData));
            console.log('? [AUTH] Updated user data with token from authService');
          }
        }
      }

      if (!token) {
        console.log('? [AUTH] No token found in saved user data or authService');
        return false;
      }

      // Store token in auth service before making request
      await authService.setToken(token);

      // Add timeout and better error handling for production
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout

      try {
        const response = await fetch(`${API_CONFIG.AUTH.baseUrl}${API_CONFIG.AUTH.meEndpoint}`, {
          method: 'GET',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (process.env.NODE_ENV === 'development') {
          console.log('?? [AUTH] API verification response status:', response.status);
        }

        if (response.ok) {
          const apiUser = await response.json();
          if (process.env.NODE_ENV === 'development') {
            console.log('? [AUTH] API verification successful');
          }

          // Verify critical user data matches (prefer stable email)
          const savedUser = savedUserData.data?.user || savedUserData;
          const apiUserData = apiUser.data?.user || apiUser.user || apiUser;

          // Extract email from various possible locations in saved data
          const savedEmail = savedUser?.email || savedUserData?.email || savedUserData?.data?.email;
          const apiEmail = apiUserData?.email;

          const emailMatches = savedEmail && apiEmail && (savedEmail === apiEmail);
          const idsMatch = savedUser?._id === apiUserData?._id && savedUser?.googleId === apiUserData?.googleId;

          if (!emailMatches && !idsMatch) {
            console.log('? [AUTH] User data mismatch detected');
            console.log('?? [AUTH] Saved user email:', savedEmail);
            console.log('?? [AUTH] API user email:', apiEmail);
            console.log('?? [AUTH] Saved user structure:', {
              hasSavedUser: !!savedUser,
              hasEmail: !!savedEmail,
              savedUserKeys: savedUser ? Object.keys(savedUser) : [],
              savedDataKeys: savedUserData ? Object.keys(savedUserData) : []
            });
            await authService.clearToken();
            return false;
          }

          // If email matches but IDs changed (e.g., user re-provisioned), refresh local cache safely
          if (emailMatches && !idsMatch) {
            console.log('?? [AUTH] Email matches but IDs differ - refreshing local user cache');
          }

          // Update user data in AsyncStorage, PRESERVING token
          console.log('? [AUTH] User data verification passed & saved');
          try {
            const existing = await AsyncStorage.getItem('@user');
            let tokenToKeep = token;
            if (existing) {
              const existingParsed = JSON.parse(existing);
              tokenToKeep = existingParsed?.data?.token || token;
            }
            const mergedUser = {
              data: {
                user: apiUser.data?.user || apiUser.user || apiUser,
                token: tokenToKeep,
              }
            };
            await AsyncStorage.setItem('@user', JSON.stringify(mergedUser));
            // Update userType from API response
            const apiUserObj = apiUser.data?.user || apiUser.user || apiUser;
            if (apiUserObj?.user_type) {
              setUserType(apiUserObj.user_type);
            }
            if (process.env.NODE_ENV === 'development') {
              console.log('?? [AUTH] Persisted token with verified user');
            }
          } catch (e) {
            console.warn('?? [AUTH] Failed to merge token while saving user, attempting fallback');
            await AsyncStorage.setItem('@user', JSON.stringify(apiUser));
          }
          return true;
        } else if (response.status === 401 || response.status === 403) {
          console.log('? [AUTH] Token expired or invalid');

          // Check if the response indicates a user was deleted and requires re-authentication
          try {
            const errorData = await response.json();
            if (errorData.requiresReauth) {
              if (errorData.reason === 'user_deleted') {
                console.log('?? [AUTH] User deleted from database, clearing orphaned token');
                await authService.clearOrphanedToken();
              } else {
                console.log('?? [AUTH] Token invalid, clearing all auth data');
                await authService.clearToken();
              }

              // Set authentication state to false to trigger signup screen
              setIsAuthenticated(false);

              return false;
            }
          } catch (parseError) {
            console.log('? [AUTH] Could not parse error response, treating as standard auth failure');
          }

          await authService.clearToken();
          return false;
        } else if (response.status === 429) {
          console.warn('?? [AUTH] Rate limited (429) during verification, keeping user authenticated');
          return true; // Rate limiting is not an auth failure
        } else {
          console.log('? [AUTH] API verification failed with status:', response.status);
          return false;
        }
      } catch (fetchError) {
        clearTimeout(timeoutId);

        // Handle abort/timeout
        if (fetchError.name === 'AbortError') {
          console.log('?? [AUTH] Verification request timed out, assuming offline mode');
          return true; // Keep user authenticated in offline mode
        }

        throw fetchError; // Re-throw for outer catch block
      }
    } catch (error) {
      console.error('? [AUTH] Error verifying user data:', error.message);

      // Network errors should not automatically clear user data
      // Only clear on authentication errors
      const isAuthError = error.message.includes('401') || error.message.includes('403');
      const isNetworkError = error.name === 'TypeError' && error.message.includes('Failed to fetch');
      const isCorsError = error.message.includes('CORS') || error.message.includes('blocked');

      if (isAuthError) {
        console.log('?? [AUTH] Authentication error detected, clearing tokens');
        await authService.clearToken();
        return false;
      }

      if (isNetworkError || isCorsError) {
        // For network/CORS errors, assume user data is valid but log the issue
        console.log('?? [AUTH] Network/CORS error during verification, keeping user data for offline mode');
        return true;
      }

      // For other unknown errors, fail safe
      console.log('?? [AUTH] Unknown verification error. Marking as unauthenticated for safety.');
      return false;
    }
  }, [setIsAuthenticated]);

  // Clear invalid user data from AsyncStorage
  const clearInvalidUserData = useCallback(async () => {
    console.log('?? [AUTH] Clearing invalid user data from AsyncStorage');
    try {
      await AsyncStorage.removeItem('@user');
      await authService.clearToken();
      console.log('? [AUTH] Invalid user data cleared successfully');
    } catch (error) {
      console.error('? [AUTH] Error clearing user data:', error);
    }
  }, []);

  // Force the UI back into the Google sign-in flow when authentication data is missing.
  const forceGoogleReauth = useCallback(async (reason = 'token-missing') => {
    if (reauthInProgressRef.current) {
      console.log('?? [AUTH] Re-authentication already in progress, skipping duplicate request:', reason);
      return;
    }

    reauthInProgressRef.current = true;
    console.warn('?? [AUTH] Forcing Google re-authentication:', reason);

    try {
      await clearInvalidUserData();
    } catch (cleanupError) {
      console.error('? [AUTH] Failed to clear auth data before re-auth:', cleanupError);
    }

    setIsAuthenticated(false);
    setActiveScreen('signup');
    setCurrentUserEmail(null);

    if (typeof window !== 'undefined') {
      try {
        window.dispatchEvent?.(new CustomEvent('authFailure', { detail: { reason } }));
      } catch (eventError) {
        console.warn('?? [AUTH] Failed to emit authFailure event:', eventError);
      }
    }
  }, [clearInvalidUserData]);

  useEffect(() => {
    const unsubscribe = authService.onAuthRequired((reason) => {
      forceGoogleReauth(reason);
    });

    return unsubscribe;
  }, [forceGoogleReauth]);

  // Removed periodic user data verification - using reactive auth failure handling instead

  // on mount, check if user is already signed in and verify data
  useEffect(() => {
    // Skip if user just logged in to prevent interference
    if (justLoggedIn) {
      console.log('?? [AUTH] Skipping mount auth check - user just logged in');
      setAuthCheckComplete(true);
      return;
    }

    (async () => {
      try {
        const saved = await AsyncStorage.getItem('@user');
        if (saved) {
          const userData = JSON.parse(saved);
          console.log('?? [AUTH] Found saved user data, verifying...');

          // Extract user_type from saved data
          const savedUserType = userData?.data?.user?.user_type;
          if (savedUserType) {
            setUserType(savedUserType);
          }

          const isValid = await verifyUserDataWithAPI(userData);

          if (isValid) {
            console.log('? [AUTH] User data verified, authentication successful');
            setIsAuthenticated(true);
            setAuthCheckComplete(true);

            // Update storage manager with user email
            await updateStorageManagerDeviceId();

            // Note: Persona check will be handled by the useEffect monitoring isFirstTime
            // Don't immediately navigate to chat - let persona check complete first
            console.log('?? [AUTH] Auth complete, waiting for persona check...');

          } else {
            console.log('? [AUTH] User data verification failed, clearing data');
            await clearInvalidUserData();
            setIsAuthenticated(false);
            setAuthCheckComplete(true);
            if (Platform.OS === 'android') {
              // Skip signup for Android, go straight to chat as guest
              setIsAuthenticated(true);
              setActiveScreen('chat');
            } else {
              setActiveScreen('signup');
            }
          }
        } else {
          // No @user in storage � check if user is already authenticated via
          // UserProvider/MainApp (token in authService or 'auth_token' key)
          // before kicking to signup. This prevents overriding MainApp's routing.
          const hasExistingToken = await authService.hasToken();
          if (hasExistingToken) {
            console.log('?? [AUTH] No @user data but token exists (UserProvider auth), staying on current screen');
            setIsAuthenticated(true);
            setAuthCheckComplete(true);
            await updateStorageManagerDeviceId();
          } else {
            console.log('?? [AUTH] No saved user data found');
            setAuthCheckComplete(true);
            if (Platform.OS === 'android') {
              // Skip signup for Android, go straight to chat as guest
              setIsAuthenticated(true);
              setActiveScreen('chat');
            } else {
              setActiveScreen('signup');
            }
          }
        }
      } catch (error) {
        console.error('? [AUTH] Error during authentication check:', error);
        setAuthCheckComplete(true);
        // On error, default to appropriate screen based on platform
        if (Platform.OS === 'android') {
          // Skip signup for Android, go straight to chat as guest
          setIsAuthenticated(true);
          setActiveScreen('chat');
        } else {
          // Only go to signup if we're not already authenticated
          const hasToken = await authService.hasToken().catch(() => false);
          if (!hasToken) {
            setActiveScreen('signup');
          }
        }
      }
    })();
  }, [verifyUserDataWithAPI, clearInvalidUserData, justLoggedIn]);

  // Removed periodic verification interval - using reactive auth failure handling instead

  const gestureRef = useRef(null);

  // Get safe area insets for proper Android positioning
  const insets = useSafeAreaInsets();

  // Animated value that will equal keyboard height (plus safe area if needed)
  const keyboardHeight = useRef(new Animated.Value(0)).current;

  // Track if initialization has been completed to prevent re-initialization
  const initializationCompleted = useRef(false);
  const reauthInProgressRef = useRef(false);

  // Fixed model key since we're no longer using model selection
  const selectedModelKey = 'citra-ai-lite';

  // Get user email for device identification
  const getUserEmail = useCallback(async () => {
    try {
      const saved = await AsyncStorage.getItem('@user');
      if (saved) {
        const userData = JSON.parse(saved);
        // Check multiple possible email locations in the data structure
        const email = userData?.data?.user?.email
          || userData?.data?.email
          || userData?.email;
        if (email && email.includes('@')) {
          return email;
        }
      }

      // Fallback: try authService which can decode email from JWT
      try {
        const serviceEmail = await authService.getCurrentUserEmail();
        if (serviceEmail && serviceEmail.includes('@')) {
          return serviceEmail;
        }
      } catch (serviceError) {
        // authService couldn't find email either � continue to fallback
      }

      // Missing email is NOT an authentication failure � don't nuke tokens
      console.warn('?? [AUTH] Could not determine user email for device ID, using fallback');
      return 'unknown-user';
    } catch (error) {
      console.error('Error getting user email:', error);
      // Return fallback instead of triggering nuclear reauth
      return 'unknown-user';
    }
  }, []);

  // Initialize storage manager
  const [storageManager] = useState(() => new AsyncStorageManager(QUERY_URL, DOCUMENT_URL, CHAT_URL, NOTE_URL, TRANSCRIPT_URL, ''));

  // Update storage manager device ID when user authenticates
  const updateStorageManagerDeviceId = useCallback(async () => {
    try {
      const userEmail = await getUserEmail();
      if (process.env.NODE_ENV === 'development') {
        console.log('?? [AUTH] Updating device IDs to:', userEmail);
      }
      storageManager.updateDeviceId(userEmail);
      setUserDeviceId(userEmail); // Update persona device ID immediately
      if (process.env.NODE_ENV === 'development') {
        console.log('?? [AUTH] Storage manager device ID updated to:', userEmail);
        console.log('?? [AUTH] Persona device ID updated to:', userEmail);
      }
    } catch (error) {
      console.error('? [AUTH] Failed to update storage manager device ID:', error);
    }
  }, [getUserEmail, storageManager]);

  const handleSignupAuthSuccess = useCallback(async (auth = {}) => {
    console.log('Google auth success:', auth);
    reauthInProgressRef.current = false;
    setIsAuthenticated(true);
    setAuthCheckComplete(true);
    setJustLoggedIn(true);

    if (onClearPendingPlan && typeof onClearPendingPlan === 'function') {
      onClearPendingPlan();
    }

    await updateStorageManagerDeviceId();

    if (typeof window !== 'undefined') {
      window.dispatchEvent?.(new CustomEvent('authSuccess'));
    }

    try {
      const pendingPurchase = await AsyncStorage.getItem('@pending_purchase');
      const pendingPlanFromStorage = await AsyncStorage.getItem('@pending_plan_purchase');

      if (pendingPurchase || pendingPlanFromStorage) {
        console.log('?? [AUTH] Pending purchase detected after login, returning to intro to complete purchase');
        console.log('?? [AUTH] Pending purchase:', pendingPurchase);
        console.log('?? [AUTH] Pending plan:', pendingPlanFromStorage);

        if (onBackToIntro && typeof onBackToIntro === 'function') {
          console.log('?? [NAV] Calling onBackToIntro to return to intro screen for purchase completion');
          onBackToIntro();
        } else {
          console.warn('?? [NAV] onBackToIntro callback not provided - staying on chat');
          setActiveScreen('chat');
        }

        setTimeout(() => setJustLoggedIn(false), 1000);
        return;
      }
    } catch (error) {
      console.error('? [AUTH] Error checking pending purchase:', error);
    }

    const needsPersonaSetup = auth?.isFirstTimeUser || auth?.isNewUser || auth?.needsPersonaSetup || auth?.data?.isNewUser || auth?.user?.isNewUser;

    if (needsPersonaSetup) {
      console.log('?? [ONBOARDING] First-time user detected, routing directly to home panel');

      // Track whether welcome bonus was actually granted (may be skipped for returning/duplicate users)
      const bonusGranted = auth?.welcomeBonusGranted === true;
      setWelcomeBonusGranted(bonusGranted);
      if (!bonusGranted) {
        console.log('?? [ONBOARDING] Welcome bonus was NOT granted (returning user or duplicate device)');
      }

      // Extract name from Google auth for default persona
      const googleName = auth?.data?.user?.name || auth?.user?.name || 'User';

      // Auto-create default persona in background (non-blocking)
      (async () => {
        try {
          const userEmail = await getUserEmail();
          console.log('?? [PERSONA] Auto-creating default persona for:', userEmail);

          // Create default persona with "General Professional"
          const response = await authService.authenticatedFetch(`${API_CONFIG.PERSONA_URL}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              user_id: userEmail,
              persona: {
                name: googleName,
                mobile: '9999999999', // Placeholder - can be updated later
                profession: 'General Professional'
              }
            })
          });

          if (response.ok) {
            console.log('? [PERSONA] Default persona created successfully');
          } else {
            console.warn('?? [PERSONA] Failed to create default persona:', response.status);
          }
        } catch (error) {
          console.error('? [PERSONA] Error creating default persona:', error);
        }
      })();

      // Skip Quick Start Dialog — land first-time users on the home panel
      // (HomePanel for desktop web, MobileHomeScreen for mobile web — selected by isMobileWeb at render site)
      setActiveScreen('chat');
      setCurrentView('home');
      if (Platform.OS === 'web') {
        navigateToHome();
      }
    } else {
      console.log('?? [PERSONA] Returning user, going to chat');
      setActiveScreen('chat');
    }

    setTimeout(() => setJustLoggedIn(false), 1000);
  }, [onBackToIntro, onClearPendingPlan, setActiveScreen, setIsAuthenticated, setJustLoggedIn, updateStorageManagerDeviceId, getUserEmail]);

  // ===== AUTHENTICATION DEBUG FUNCTIONS =====
  const debugAuth = useCallback(async () => {
    console.log('?? [DEBUG] Starting authentication debug...');
    await AuthDebugHelper.checkAuthStatus();
  }, []);

  const setDebugAuth = useCallback(async () => {
    console.log('?? [DEBUG] Setting debug authentication...');
    const success = await AuthDebugHelper.setDebugAuth();
    if (success) {
      console.log('? [DEBUG] Debug auth set, updating app state...');
      await updateStorageManagerDeviceId();
      setIsAuthenticated(true);
      setAuthCheckComplete(true);
      setActiveScreen('chat');
    }
  }, [updateStorageManagerDeviceId]);

  const testApiCall = useCallback(async () => {
    console.log('?? [DEBUG] Testing API call...');
    await AuthDebugHelper.testApiCall();
  }, []);

  // Sync auth state - force synchronization between storage locations
  const syncAuthState = useCallback(async () => {
    try {
      const saved = await AsyncStorage.getItem('@user');
      if (saved) {
        const userData = JSON.parse(saved);
        console.log('?? [AUTH] Attempting to sync auth state...');
        const isValid = await verifyUserDataWithAPI(userData);
        if (isValid) {
          setIsAuthenticated(true);
          console.log('? [AUTH] Auth state synced successfully');
          return true;
        }
      }
      console.log('? [AUTH] Auth sync failed - no valid data found');
      return false;
    } catch (error) {
      console.error('? [AUTH] Error syncing auth state:', error);
      return false;
    }
  }, [verifyUserDataWithAPI]);

  // Expose debug functions globally for testing
  useEffect(() => {
    if (process.env.NODE_ENV === 'development') {
      // Make debug functions available in console
      global.debugAuth = debugAuth;
      global.setDebugAuth = setDebugAuth;
      global.testApiCall = testApiCall;
      global.clearAuth = AuthDebugHelper.clearAuth;
      global.syncAuth = syncAuthState;

      // Development debug functions are available in console
      // debugAuth(), setDebugAuth(), testApiCall(), clearAuth(), syncAuth()
    }
  }, [debugAuth, setDebugAuth, testApiCall, syncAuthState]);
  // =============================================

  // Load current user email on component mount
  useEffect(() => {
    let isMounted = true;

    const loadUserEmail = async () => {
      if (!isAuthenticated) {
        setCurrentUserEmail(null);
        return;
      }

      try {
        const email = await getUserEmail();
        if (!isMounted) {
          return;
        }

        setCurrentUserEmail(email);

        // If we have a valid authenticated user email, trigger folder refresh
        if (email && email.includes('@')) {
          console.log('? [AUTH] User email loaded:', email, '- triggering folder refresh');
          if (typeof window !== 'undefined') {
            window.dispatchEvent?.(new CustomEvent('authSuccess'));
          }
        }
      } catch (error) {
        console.error('Error loading user email:', error);
        if (isMounted) {
          setCurrentUserEmail(null);
        }
      }
    };

    loadUserEmail();

    return () => {
      isMounted = false;
    };
  }, [getUserEmail, isAuthenticated]);

  // Check for completed purchase flag on mount
  useEffect(() => {
    const checkPurchaseFlag = async () => {
      const wasPending = await AsyncStorage.getItem('@was_pending_purchase');
      if (wasPending === 'true') {
        console.log('?? [PURCHASE] Detected completed purchase - preventing signup loop');
        setHasCompletedPurchase(true);
        setUserType('paid'); // User made a payment, upgrade to paid
        await AsyncStorage.removeItem('@was_pending_purchase');

        // Don't reset the flag after 5 seconds - keep it for the session
        // The flag will reset when app restarts
      }
    };

    checkPurchaseFlag();
  }, []);

  // Land the user on the home panel once per session.
  //
  // This was previously the tail of a persona-monitoring effect and fired only
  // for users who had no persona yet; it is kept because it is what puts a
  // signed-in user on the home panel rather than the chat view. The ref guard
  // replaces the `!hasPersona` condition that used to make it self-limiting —
  // without it the effect re-fires and pulls the user back out of chat.
  const didLandOnHomeRef = useRef(false);
  useEffect(() => {
    if (isInitializing || !currentUserEmail || didLandOnHomeRef.current) return;
    if (activeScreen !== 'chat' && activeScreen !== 'signup') return;
    if (showQuickStartDialog) return;

    didLandOnHomeRef.current = true;
    setActiveScreen('chat');
    setCurrentView('home');
    if (Platform.OS === 'web') {
      navigateToHome();
    }
  }, [currentUserEmail, isInitializing, activeScreen, showQuickStartDialog]);

  // Move an authenticated user off a pre-auth screen. The persona test this
  // used to carry is gone; being authenticated is the whole condition.
  useEffect(() => {
    if (isAuthenticated && userDeviceId && userDeviceId.includes('@')) {
        // Only navigate to chat if we're on a pre-auth screen (signup) or not yet initialized
        // Don't override post-auth screens like folder-content, history, notes, etc.
        const preAuthScreens = ['signup'];
        if (!preAuthScreens.includes(activeScreen)) {
          // User is already on a valid post-auth screen, don't override
          return;
        }

        // Check if there's a pending purchase - if so, don't navigate to chat yet
        AsyncStorage.getItem('@pending_plan_purchase').then(pendingPlan => {
          if (pendingPlan) {
            console.log('?? [PERSONA] Pending purchase detected, staying on intro screen');
            return;
          }
          // User has a persona and no pending purchase, navigate to chat
          console.log('? [PERSONA] User has persona, navigating to chat');
          setActiveScreen('chat');
        }).catch(err => {
          console.error('? [PERSONA] Error checking pending purchase:', err);
          // On error, still navigate to chat
          setActiveScreen('chat');
        });
    }
  }, [isAuthenticated, userDeviceId, hasCompletedPurchase, activeScreen]);

  // Handle message button actions

  // Handle upload progress updates

  // REMOVED: trackUsageAfterAction function
  // This was causing automatic tracking calls to track-query-usage and track-document-upload
  // which should not be called automatically from the UI

  // Stub function to prevent errors in existing code
  const trackUsageAfterAction = async (action, data) => {
    // Tracking disabled - no action taken
    console.log('?? Usage tracking disabled:', action, data);
  };

  // REMOVED: validateSubscriptionForAction function
  // This was calling validate-subscription endpoint which is no longer needed

  // Check if initialization was already completed in a previous session (web only)
  useEffect(() => {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      const wasInitialized = localStorage.getItem('citra_ai_initialized');
      // Add a way to force re-initialization by adding ?reset=true to the URL
      const shouldReset = window.location.search.includes('reset=true');

      // Also detect stuck state: if citra_init_started_at exists and is old, treat as stuck
      const initStartedAt = localStorage.getItem('citra_init_started_at');
      const wasStuck = initStartedAt && (Date.now() - parseInt(initStartedAt, 10)) > 30000;

      if (shouldReset || wasStuck) {
        if (wasStuck) {
          console.warn('?? [INIT] Detected stuck initialization from previous session, clearing...');
        } else {
          console.log('?? [INIT] Force reset flag detected, clearing initialization state...');
        }
        localStorage.removeItem('citra_ai_initialized');
        localStorage.removeItem('citra_init_started_at');
        initializationCompleted.current = false;
      } else if (wasInitialized === 'true') {
        console.log('?? [INIT] Web platform - found previous initialization, skipping...');
        initializationCompleted.current = true;
        setIsInitializing(false);
      }
    }
  }, []);

  // Web-specific error handling to prevent UI freezing
  useEffect(() => {
    // Only add web error handlers if we're in a web environment
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      // Handle unhandled promise rejections
      const handleUnhandledRejection = (event) => {
        console.error('Unhandled promise rejection:', event.reason);
        // Prevent the default behavior (which might freeze the UI)
        if (event.preventDefault) {
          event.preventDefault();
        }
      };

      // Handle general window errors
      const handleWindowError = (event) => {
        // Benign "ResizeObserver loop" notification (fired when a resizable
        // container settles, e.g. the workflow canvas during a splitter drag) —
        // ignore it so it doesn't spam the console with "Window error: null".
        const _msg = (event && (event.message || (event.error && event.error.message))) || '';
        if (typeof _msg === 'string' && _msg.indexOf('ResizeObserver loop') !== -1) {
          if (event.preventDefault) event.preventDefault();
          return;
        }
        console.error('Window error:', event.error);
        // Prevent the default behavior
        if (event.preventDefault) {
          event.preventDefault();
        }
      };

      // Add event listeners
      window.addEventListener('unhandledrejection', handleUnhandledRejection);
      window.addEventListener('error', handleWindowError);

      // Override console.error to prevent UI freezing in development
      const originalConsoleError = console.error;
      console.error = (...args) => {
        try {
          // Still log the error but don't let it freeze the UI
          originalConsoleError.apply(console, args);
        } catch (e) {
          // If console.error itself throws, just ignore it
        }
      };

      // Cleanup
      return () => {
        window.removeEventListener('unhandledrejection', handleUnhandledRejection);
        window.removeEventListener('error', handleWindowError);
        console.error = originalConsoleError;
      };
    }
  }, []);

  // Removed aggressive global text selection override. Selection now handled via scoped CSS and component props.

  // Keyboard handling for mobile platforms
  useEffect(() => {
    const showEvent = Platform.OS === 'android' ? 'keyboardDidShow' : 'keyboardWillShow';
    const hideEvent = Platform.OS === 'android' ? 'keyboardDidHide' : 'keyboardWillHide';

    const onShow = (e) => {
      const toValue = e.endCoordinates?.height ?? 0;
      Animated.timing(keyboardHeight, {
        toValue,
        duration: Platform.OS === 'android' ? 250 : e.duration || 250,
        useNativeDriver: false
      }).start();
    };
    const onHide = (e) => {
      Animated.timing(keyboardHeight, {
        toValue: 0,
        duration: Platform.OS === 'android' ? 200 : e?.duration || 200,
        useNativeDriver: false
      }).start();
    };

    const showSub = Keyboard.addListener(showEvent, onShow);
    const hideSub = Keyboard.addListener(hideEvent, onHide);
    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, [keyboardHeight]);



  // Theme is now provided by useTheme() hook - modern theme system integrated

  const openSessionOptions = () => {
    const options = ['Share Chat', 'Share as Link', 'Delete Chat', 'Cancel'];
    const destructiveButtonIndex = 2;
    const cancelButtonIndex = 3;

    if (Platform.OS === 'ios') {
      ActionSheetIOS.showActionSheetWithOptions(
        {
          title: sessionName,
          message: 'Choose an action for this conversation',
          options,
          destructiveButtonIndex,
          cancelButtonIndex,
          // Add safe area consideration for iOS
          anchor: insets.bottom > 0 ? { x: width / 2, y: 100 } : undefined,
          userInterfaceStyle: isDarkMode ? 'dark' : 'light',
        },
        buttonIndex => {
          if (buttonIndex === 0) shareChat();
          else if (buttonIndex === 1) shareChatAsLink();
          else if (buttonIndex === 2) confirmDeleteChat();
        }
      );
    } else {
      // For Android, use action sheet with proper safe area handling
      showActionSheetWithOptions(
        {
          title: sessionName,
          options,
          destructiveButtonIndex,
          cancelButtonIndex,
          containerStyle: {
            paddingBottom: insets.bottom + 10, // Add safe area bottom padding
            backgroundColor: theme.background,
          },
          textStyle: {
            fontSize: 18,
            color: theme.text,
          },
          titleTextStyle: {
            fontSize: 16,
            fontWeight: '600',
            color: theme.text,
          },
          separatorStyle: {
            backgroundColor: theme.borderColor,
          },
          showSeparators: true,
        },
        buttonIndex => {
          if (buttonIndex === 0) shareChat();
          else if (buttonIndex === 1) shareChatAsLink();
          else if (buttonIndex === 2) confirmDeleteChat();
        }
      );
    }
  };

  // Share chat as a public link
  async function shareChatAsLink() {
    if (!activeSessionId) {
      Alert.alert('Error', 'No active chat session to share.');
      return;
    }
    try {
      const result = await ShareService.createShare('chat', activeSessionId, sessionName || 'Chat Conversation');
      if (result && result.share_token) {
        // Generate URL client-side to use correct environment (localhost vs production)
        const shareUrl = ShareService.getPublicUrl(result.share_token);
        const copied = await ShareService.copyShareUrl(shareUrl);
        if (copied) {
          Alert.alert(
            'Share Link Created',
            `Link copied to clipboard!\n\n${shareUrl}`,
            [{ text: 'OK' }]
          );
        } else {
          Alert.alert(
            'Share Link Created',
            `Share this link:\n\n${shareUrl}`,
            [{ text: 'OK' }]
          );
        }
      }
    } catch (error) {
      console.error('[App] Share chat as link error:', error);
      Alert.alert('Error', 'Could not create share link. Please try again.');
    }
  }

  async function shareChat() {
    const chatText = messages
      .map(m => (m.sender === 'user' ? `You: ${m.text}` : `Bot: ${m.text}`))
      .join('\n\n');

    try {
      // Apply the same rich text formatting as individual messages
      const formattedChatText = cleanMarkdownForCopy(chatText);

      // For web, try to use the Web Share API with better formatting
      if (Platform.OS === 'web' && navigator.share) {
        try {
          await navigator.share({
            title: 'Citra AI Conversation',
            text: formattedChatText,
          });
          return;
        } catch (webShareError) {
          console.warn('Web Share API failed, falling back to React Native Share:', webShareError);
        }
      }

      // Use React Native Share with formatted text
      await Share.share({
        message: formattedChatText,
        title: 'Citra AI Conversation'
      });
    } catch (_) {
      Alert.alert('Error', 'Could not share this conversation.');
    }
  }

  function confirmDeleteChat() {
    Alert.alert(
      'Delete Conversation?',
      'This will remove it from your history permanently.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete', style: 'destructive', onPress: async () => {
            // delete on your backend
            await storageManager.deleteChatSession(activeSessionId);
            // refresh history list
            await loadChatSessions();
            // go back to history view
            setActiveScreen('history');
          }
        }
      ]
    );
  }


  // Replace existing scroll functions with consolidated scroll management
  const executeScrollAction = useCallback((action) => {
    if (!flatListRef.current || !action) return;

    const { type, target, delay = 150 } = action;

    setTimeout(() => {
      try {
        if (type === 'bottom') {
          flatListRef.current?.scrollToEnd({ animated: true });
        } else if (type === 'message' && target) {
          const messageIndex = messages.findIndex(msg => msg.id === target);
          if (messageIndex !== -1) {
            flatListRef.current?.scrollToIndex({
              index: messageIndex,
              animated: true,
              viewPosition: 0.5,
            });
          } else {
            // Fallback to scroll to bottom if message not found
            flatListRef.current?.scrollToEnd({ animated: true });
          }
        }
      } catch (error) {
        console.log('Scroll action failed, falling back to scroll to end:', error);
        flatListRef.current?.scrollToEnd({ animated: true });
      }
    }, delay);
  }, [messages]);

  // Consolidated scroll effect
  useEffect(() => {
    if (scrollAction) {
      executeScrollAction(scrollAction);
      setScrollAction(null);
    }
  }, [scrollAction, executeScrollAction]);

  // Helper functions for setting scroll actions with proper priorities
  const scheduleScrollToBottom = useCallback((priority = 1) => {
    setScrollAction(prev => {
      if (!prev || prev.priority <= priority) {
        return { type: 'bottom', priority };
      }
      return prev;
    });
  }, []);

  const scheduleScrollToMessage = useCallback((messageId, priority = 2) => {
    setScrollAction(prev => {
      if (!prev || prev.priority <= priority) {
        return { type: 'message', target: messageId, priority };
      }
      return prev;
    });
  }, []);

  // Queue status state for progress bubbles
  const [queueStatus, setQueueStatus] = useState({
    queue: [],
    activeUploads: new Map(),
    processing: false,
    totalInQueue: 0,
    totalActive: 0
  });

  // Set up queue status listener

  // Set up duplicate upload listener (avoid undefined setters)

  // Combine enhanced progress with queue progress for progress bubbles

  // Track enhanced upload progress for Vault screen





  // Close/dismiss audio recording progress bar

  // Cancel video recording (stops recording and discards)

  // Cancel audio recording (stops recording and discards)

  // Add scroll to specific message function
  const scrollToMessage = useCallback((messageId) => {
    if (!flatListRef.current) return;

    const messageIndex = messages.findIndex(msg => msg.id === messageId);
    if (messageIndex !== -1) {
      setTimeout(() => {
        flatListRef.current?.scrollToIndex({
          index: messageIndex,
          animated: true,
          viewPosition: 0.5, // Center the message in view
        });
      }, 100); // Small delay to ensure render is complete
    }
  }, [messages]);

  // Add scroll to bottom function
  const scrollToBottom = useCallback(() => {
    if (!flatListRef.current) return;

    setTimeout(() => {
      flatListRef.current?.scrollToEnd({ animated: true });
    }, 100); // Small delay to ensure render is complete
  }, []);

  // Effect to handle scrolling when messages change
  useEffect(() => {
    if (shouldScrollToMessage) {
      scrollToMessage(shouldScrollToMessage);
      setShouldScrollToMessage(null);
    } else if (pendingScrollToBottom) {
      scrollToBottom();
      setPendingScrollToBottom(false);
    }
  }, [messages, shouldScrollToMessage, pendingScrollToBottom, scrollToMessage, scrollToBottom]);

  // Handle content size change for auto-scrolling
  // DISABLED: Don't auto-scroll during streaming - let user stay at reading position
  const handleContentSizeChange = useCallback((contentWidth, contentHeight) => {
    // if (autoScrollEnabled) {
    //   setTimeout(() => {
    //     flatListRef.current?.scrollToEnd({ animated: true });
    //   }, 100);
    // }
  }, [autoScrollEnabled]);

  // Handle layout change
  const handleLayout = useCallback((event) => {
    // Handle layout changes if needed
    const { height } = event.nativeEvent.layout;
    // You can add specific layout handling logic here if needed
  }, []);





  const loadChatSessions = useCallback(async (loadMore = false) => {
    console.log('?? [LOAD_CHAT_SESSIONS] Starting to load chat sessions');
    console.log('?? [LOAD_CHAT_SESSIONS] Load more:', loadMore);
    console.log('?? [LOAD_CHAT_SESSIONS] Current page:', chatHistoryPage);

    // If MongoDB chat history is disabled, return empty sessions
    if (browserChatManager.isBrowserOnlyMode()) {
      console.log('?? [LOAD_CHAT_SESSIONS] Browser-only mode, returning empty sessions');
      if (loadMore) {
        setIsLoadingMoreChat(false);
      } else {
        setIsLoadingHistory(false);
        setChatSessions([]);
        setHasMoreChatHistory(false);
      }
      return;
    }

    if (loadMore) {
      setIsLoadingMoreChat(true);
    } else {
      setIsLoadingHistory(true);
      setChatHistoryPage(0);
      setHasMoreChatHistory(true);
    }

    try {
      const userEmail = await getUserEmail();
      console.log('?? [LOAD_CHAT_SESSIONS] User email:', userEmail);

      const skip = loadMore ? chatHistoryPage * ITEMS_PER_PAGE : 0;
      console.log('?? [LOAD_CHAT_SESSIONS] Skip:', skip, 'Limit:', ITEMS_PER_PAGE);

      // Use API pagination with authentication
      const url = `${CHAT_URL}?operation=chat_sessions&user_id=${encodeURIComponent(userEmail)}&limit=${ITEMS_PER_PAGE}&skip=${skip}`;
      console.log('?? [LOAD_CHAT_SESSIONS] Request URL:', url);

      const response = await authService.authenticatedFetch(url);
      console.log('?? [LOAD_CHAT_SESSIONS] Response status:', response.status);

      if (response.ok) {
        const data = await response.json();
        console.log('?? [LOAD_CHAT_SESSIONS] Parsed data:', data);
        const rawSessions = data.data || [];
        console.log('?? [LOAD_CHAT_SESSIONS] Raw sessions count:', rawSessions.length);

        const sessions = rawSessions.map((session, index) => {
          console.log(`?? [LOAD_CHAT_SESSIONS] Processing session ${index + 1}:`, session);
          return {
            id: session._id, // Use _id instead of chat_session_id for consistency
            title: session.title || 'New Chat',
            summary: session.summary || '',
            timestamp: session.lastUpdatedAt || session.createdAt,
            isActive: session.isActive,
            mongoId: session._id
          };
        });

        console.log('? [LOAD_CHAT_SESSIONS] Processed sessions:', sessions);

        if (!loadMore) {
          // Initial load - reset everything
          setChatSessions(sessions);
          setChatHistoryPage(1);
          setHasMoreChatHistory(sessions.length === ITEMS_PER_PAGE);
          console.log('?? [LOAD_CHAT_SESSIONS] Initial load complete, sessions set');
        } else {
          // Load more
          if (sessions.length > 0) {
            setChatSessions(prev => {
              const updated = [...prev, ...sessions];
              console.log('?? [LOAD_CHAT_SESSIONS] Added more sessions, total count:', updated.length);
              return updated;
            });
            setChatHistoryPage(prev => prev + 1);
            setHasMoreChatHistory(sessions.length === ITEMS_PER_PAGE);
          } else {
            setHasMoreChatHistory(false);
            console.log('?? [LOAD_CHAT_SESSIONS] No more sessions to load');
          }
        }
      } else {
        console.log('? [LOAD_CHAT_SESSIONS] Non-200 response, status:', response.status);
        if (!loadMore) {
          setChatSessions([]);
          setHasMoreChatHistory(false);
        }
      }
    } catch (error) {
      console.error('? [LOAD_CHAT_SESSIONS] Error loading chat sessions:', error);
      console.error('? [LOAD_CHAT_SESSIONS] Error message:', error.message);
      console.error('? [LOAD_CHAT_SESSIONS] Error response:', error.response?.data);
      Alert.alert('Error', 'Failed to load chat history. Please try again.');
      if (!loadMore) {
        setChatSessions([]);
        setHasMoreChatHistory(false);
      }
    } finally {
      setIsLoadingHistory(false);
      setIsLoadingMoreChat(false);
      console.log('? [LOAD_CHAT_SESSIONS] Load chat sessions completed');
    }
  }, [chatHistoryPage, getUserEmail]);

  // Load messages for a specific chat session
  const loadChatMessages = useCallback(async (chatSessionId) => {
    console.log('?? [LOAD_CHAT] Starting to load messages for session:', chatSessionId);

    // Set transitioning state to prevent flicker
    setIsTransitioningChat(true);

    // Reset loading states to ensure new messages can be sent
    setIsLoading(false);
    setIsGenerating(false);
    setIsBotTyping(false);
    console.log('?? [LOAD_CHAT] Reset loading states for new session');

    try {
      console.log('?? [LOAD_CHAT] Loading fresh messages from API...');

      let userEmail = 'guest-user';
      try {
        userEmail = await getUserEmail();
      } catch (error) {
        console.log('?? [LOAD_CHAT] Could not get user email, using guest');
      }

      const url = `${CHAT_URL}?operation=message_pairs&user_id=${encodeURIComponent(userEmail)}&chat_session_id=${encodeURIComponent(chatSessionId)}&limit=100&skip=0`;
      console.log('?? [LOAD_CHAT] Request URL:', url);

      const response = await authService.authenticatedFetch(url);
      console.log('?? [LOAD_CHAT] Response status:', response.status);

      const data = await response.json();
      console.log('?? [LOAD_CHAT] Parsed data:', data);

      const messagePairs = data.data || [];
      console.log('?? [LOAD_CHAT] Raw message pairs from API:', messagePairs);

      // Build messages in memory first
      const allMessages = [];

      messagePairs.forEach((pair, pairIndex) => {
        console.log(`?? [LOAD_CHAT] Processing pair ${pairIndex}:`, pair);

        // Handle user message
        const userText = pair.userMessage?.content || pair.user_query;
        const userTimestamp = pair.userMessage?.createdAt || pair.createdAt;
        if (userText) {
          const userMessage = {
            id: `${pair._id || pairIndex}-user`,
            key: `${pair._id || pairIndex}-user-${Date.now()}`,
            text: userText,
            sender: 'user',
            timestamp: userTimestamp,
            shouldAnimate: false,
            isUpdated: false
          };
          console.log('?? [LOAD_CHAT] Adding user message:', userMessage);
          allMessages.push(userMessage);
        }

        // Handle bot message
        const botText = pair.botReply?.content || pair.bot_reply;
        const botTimestamp = pair.botReply?.createdAt || pair.createdAt;
        if (botText) {
          const botMessage = {
            id: `${pair._id || pairIndex}-bot`,
            key: `${pair._id || pairIndex}-bot-${Date.now()}`,
            text: botText,
            sender: 'bot',
            timestamp: botTimestamp,
            shouldAnimate: false,
            isUpdated: false
          };
          console.log('?? [LOAD_CHAT] Adding bot message:', botMessage);
          allMessages.push(botMessage);
        }
      });

      console.log('?? [LOAD_CHAT] Total messages collected:', allMessages.length);

      // Add welcome message if no history
      if (allMessages.length === 0) {
        allMessages.push({
          id: '1',
          key: `welcome-${Date.now()}`,
          text: 'Hello! How can I assist you today?',
          sender: 'bot',
          shouldAnimate: false,
          isUpdated: false
        });
      }

      console.log('? [LOAD_CHAT] Final messages array:', allMessages);

      // Batch state updates to prevent flicker
      setTimeout(() => {
        setMessages(allMessages);
        setTimeout(() => setIsTransitioningChat(false), 100);
      }, 0);

      return allMessages;

    } catch (error) {
      console.error('? [LOAD_CHAT] Error loading chat messages:', error);

      // Set default message on error
      const defaultMessages = [{
        id: '1',
        key: `error-${Date.now()}`,
        text: 'Hello! How can I assist you today?',
        sender: 'bot',
        shouldAnimate: false,
        isUpdated: false
      }];

      setTimeout(() => {
        setMessages(defaultMessages);
        setTimeout(() => setIsTransitioningChat(false), 100);
      }, 0);

      return defaultMessages;
    }
  }, [authService, getUserEmail]);

  // Remove AsyncStorage-based session management
  // preserveMessages: if true, keeps existing messages (used when auto-creating session for follow-up)
  const createNewSession = useCallback((preserveMessages = false) => {
    const newSessionId = uuidv4(); // Use UUID v4 instead of timestamp
    setActiveSessionId(newSessionId);

    if (!preserveMessages) {
      // Only reset messages when explicitly starting a new chat
      setMessages([
        { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' },
      ]);
    }

    // Create browser chat session if MongoDB chat history is disabled
    if (browserChatManager.isBrowserOnlyMode()) {
      browserChatManager.createSession(newSessionId, 'New Chat');
      console.log('?? Created browser chat session:', newSessionId);
    }

    // Ensure bot typing indicator is cleared after welcome message
    setIsBotTyping(false);
    return newSessionId;
  }, []);



  // Calculate active connections count
  const activeConnectionsCount = useMemo(() => {
    let count = 0;
    // Personal vault only counts as a connection when it is a source at all.
    if (PERSONAL_VAULT_ENABLED && useUploadedData) count++;
    if (enableInternetSearch) count++;
    if (useEnterprise) count++;
    return count;
  }, [useUploadedData, enableInternetSearch, useEnterprise]);



  // Theme toggle is now handled by the modern theme hook
  // const toggleTheme = ... (removed - using hook version)

  const toggleMenu = useCallback(() => {
    if (isMenuOpen) {
      Animated.timing(menuAnimation, {
        toValue: -width * 0.6,
        duration: 300,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: platformOptimization.shouldUseNativeDriver,
      }).start(() => setIsMenuOpen(false));
    } else {
      setIsMenuOpen(true);
      Animated.timing(menuAnimation, {
        toValue: 0,
        duration: 300,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: platformOptimization.shouldUseNativeDriver,
      }).start();
    }
    // Close dropdown when menu is toggled
  }, [isMenuOpen, menuAnimation]);





  const stopGenerating = useCallback(() => {

    // 2) Recording in progress or waiting for its title?
    if (isWaitingForRecordingTitle || isRecording) {
      setIsWaitingForRecordingTitle(false);
      setIsRecording(false);
      setRecordingTitle('');
      if (recordingRef.current) {
        recordingRef.current.stopAndUnloadAsync().catch(console.error);
        recordingRef.current = null;
      }

      const cancelRec = {
        id: uuidv4(),
        text: 'Recording cancelled.',
        sender: 'bot',
        hideActions: true,
      };
      setMessages(prev => [...prev, cancelRec]);
      scheduleScrollToBottom(1);
      return;
    }

    // 3) Image title prompt pending? cancel it:
    if (isWaitingForImageTitle && pendingImage) {
      setIsWaitingForImageTitle(false);
      setPendingImage(null);
      setIsLoading(false);
      setIsGenerating(false);
      setIsBotTyping(false);

      // Removed: Upload cancellation toast (not a warning or failure)
      // showUploadToast('Image upload cancelled.');

      // Skip adding cancellation message to chat - we use toast instead
      // const cancelMsg = {
      //   id: uuidv4(),
      //   text: 'Image upload cancelled.',
      //   sender: 'bot',
      //   hideActions: true,
      // };
      // setMessages(prev => [...prev, cancelMsg]);
      // scheduleScrollToBottom(1);
      return;
    }

    // 4) Document title prompt pending? cancel it:
    if (isWaitingForDocumentTitle && pendingDocument) {
      setIsWaitingForDocumentTitle(false);
      setPendingDocument(null);
      setIsLoading(false);
      setIsGenerating(false);
      setIsBotTyping(false);

      // Removed: Upload cancellation toast (not a warning or failure)
      // showUploadToast('Document upload cancelled.');

      // Skip adding cancellation message to chat - we use toast instead
      // const cancelMsg = {
      //   id: uuidv4(),
      //   text: 'Document upload cancelled.',
      //   sender: 'bot',
      //   hideActions: true,
      // };
      // setMessages(prev => [...prev, cancelMsg]);
      // scheduleScrollToBottom(1);
      return;
    }

    // 5) Photo title prompt pending? cancel it:
    if (isWaitingForPhotoTitle && pendingPhoto) {
      setIsWaitingForPhotoTitle(false);
      setPendingPhoto(null);
      setIsLoading(false);
      setIsGenerating(false);
      setIsBotTyping(false);

      // Removed: Upload cancellation toast (not a warning or failure)
      // showUploadToast('Photo upload cancelled.');

      // Skip adding cancellation message to chat - we use toast instead
      // const cancelMsg = {
      //   id: uuidv4(),
      //   text: 'Photo upload cancelled.',
      //   sender: 'bot',
      //   hideActions: true,
      // };
      // setMessages(prev => [...prev, cancelMsg]);
      // scheduleScrollToBottom(1);
      return;
    }


    // 7) In-flight AI call? cancel that:
    if (cancelTokenSource) {
      cancelTokenSource.cancel('User hit stop.');
      setCancelTokenSource(null);
      // remove any �typing�� indicator:
      setMessages(ms => ms.filter(m => m.id !== 'typing'));
      setIsLoading(false);
      setIsGenerating(false);
      setIsBotTyping(false);
      // Also abort any active SSE streams (handleSSEStreamingResponse path
      // doesn't set cancelTokenSource — its abort controllers live in
      // streamingService.activeStreams and would otherwise leak).
      try { streamingService.abortAll(); } catch (_) { /* non-fatal */ }
      return;
    }

    // 8) SSE streaming in progress (no axios token, but bot is generating)?
    //    handleSSEStreamingResponse keeps its AbortController inside
    //    streamingService.activeStreams. Abort all active SSE streams; the
    //    onAbort callback in streamingQueryHandler will clear isBotTyping etc.
    if (isBotTyping || isLoading || isGenerating) {
      try { streamingService.abortAll(); } catch (_) { /* non-fatal */ }
      setMessages(ms => ms.filter(m => m.id !== 'typing'));
      setIsLoading(false);
      setIsGenerating(false);
      setIsBotTyping(false);
      return;
    }
  }, [
    isBotTyping,
    isLoading,
    isGenerating,
    isWaitingForRecordingTitle,
    isRecording,
    isWaitingForImageTitle,
    pendingImage,
    isWaitingForDocumentTitle,
    pendingDocument,
    isWaitingForPhotoTitle,
    pendingPhoto,
    cancelTokenSource,
    mediaRecorderRef,
    scheduleScrollToBottom
  ]);

  // Deep Research Specific Streaming Handler

  const prepareChatHistory = useCallback((messages) => {
    const chats = [];
    let userMessage = null;

    for (const message of messages) {
      if (message.sender === 'user' && message.id !== '1') { // Skip default bot message
        userMessage = message.text;
      } else if (message.sender === 'bot' && userMessage && message.id !== '1') {
        chats.push({
          user: userMessage,
          bot: message.text
        });
        userMessage = null;
      }
    }

    // Return up to 20 recent chat pairs for enhanced context in query API
    // This supports the new enhanced chat history approach where the backend 
    // can maintain context across longer conversations
    return chats.slice(-20);
  }, []);

  const getSessionSummary = useCallback(async (sessionId) => {
    try {
      const sessions = await storageManager.getChatSessions();
      const currentSession = sessions.find(session => session.id === sessionId);
      return currentSession?.summary || '';
    } catch (error) {
      console.error('Error getting session summary:', error);
      return '';
    }
  }, [storageManager]);

  // Download function to handle file downloads

  // Add transcript loading function after loadChatSessions
  const loadTranscripts = useCallback(async (loadMore = false, searchQuery = '') => {
    if (loadMore) {
      setIsLoadingMoreTranscripts(true);
    } else {
      setIsLoadingTranscripts(true);
      setTranscriptsPage(0);
      setHasMoreTranscripts(true);
    }

    try {
      const userEmail = await getUserEmail();
      const skip = loadMore ? transcriptsPage * ITEMS_PER_PAGE : 0;

      // Get the selected folder ID (use first selected folder or 'general' as default)
      const folderId = selectedFolderIds.length > 0 ? selectedFolderIds[0] : 'general';

      console.log('?? [TRANSCRIPTS] Loading transcripts using unified v2 API:', {
        userEmail,
        folderId,
        skip,
        limit: ITEMS_PER_PAGE,
        loadMore
      });

      // FIXED: Use unified v2 API to prevent duplicates
      // Instead of calling both audio and video APIs separately, use the unified endpoint
      const params = new URLSearchParams({
        limit: ITEMS_PER_PAGE.toString(),
        skip: skip.toString(),
        folder_id: folderId
      });

      // Add search parameter if provided
      if (searchQuery) {
        params.append('query', searchQuery);
      }

      const response = await authService.authenticatedFetch(
        `${API_ENDPOINTS.CITRA_AI.BASE}/v2/transcripts/${encodeURIComponent(userEmail)}?${params.toString()}`
      );

      if (!response.ok) {
        console.error('?? [TRANSCRIPTS] Failed to fetch unified transcripts:', response.status);
        throw new Error(`Failed to load transcripts: ${response.status}`);
      }

      const data = await response.json();
      console.log('?? [TRANSCRIPTS] Unified API response:', {
        totalCount: data.total_count,
        transcriptsLength: data.transcripts?.length,
        hasMore: (data.transcripts?.length || 0) === ITEMS_PER_PAGE
      });

      // Transform unified API response to match expected format
      const allTranscripts = (data.transcripts || []).map(transcript => {
        // Enhanced detection: Check multiple indicators for video transcripts
        const isVideoTranscript = transcript.transcript_type === 'video' ||
          transcript.original_filename ||  // Video transcripts have original_filename
          (transcript.video_url && !transcript.audio_url) ||
          (transcript.full_transcription && !transcript.transcript);

        return {
          id: transcript.transcript_id,
          _id: transcript.transcript_id,
          transcript_id: transcript.transcript_id,
          topic: transcript.topic_or_filename,
          text: transcript.transcript || transcript.full_transcription || '',
          duration: transcript.duration || null,
          timestamp: transcript.utc_date,
          utc_date: transcript.utc_date,  // Add utc_date field for component access
          deviceId: transcript.user_id,
          audioUrl: transcript.audio_url,
          videoUrl: transcript.video_url,
          originalFilename: transcript.original_filename,  // Add original filename for videos
          type: isVideoTranscript ? 'video' : 'audio',
          source: isVideoTranscript ? 'video_transcripts' : 'audio_transcripts',
          entity_id: transcript.entity_id,  // Add entity fields
          entity_name: transcript.entity_name,
          is_enterprise: transcript.is_enterprise || false  // Add enterprise flag
        };
      })
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

      console.log('?? [TRANSCRIPTS] Processed unified transcripts:', {
        totalCount: allTranscripts.length,
        typeBreakdown: {
          audio: allTranscripts.filter(t => t.type === 'audio').length,
          video: allTranscripts.filter(t => t.type === 'video').length
        },
        sampleItems: allTranscripts.slice(0, 2).map(t => ({
          id: t.id,
          topic: t.topic,
          type: t.type
        }))
      });

      if (!loadMore) {
        // Initial load - reset everything
        setTranscripts(allTranscripts);
        setTranscriptsPage(1);
        setHasMoreTranscripts(allTranscripts.length === ITEMS_PER_PAGE);
      } else {
        // Load more
        if (allTranscripts.length > 0) {
          setTranscripts(prev => [...prev, ...allTranscripts]);
          setTranscriptsPage(prev => prev + 1);
          setHasMoreTranscripts(allTranscripts.length === ITEMS_PER_PAGE);
        } else {
          setHasMoreTranscripts(false);
        }
      }
    } catch (error) {
      console.error('Error loading transcripts:', error);
      Alert.alert('Error', 'Failed to load transcripts. Please try again.');
      if (!loadMore) {
        setTranscripts([]);
        setHasMoreTranscripts(false);
      }
    } finally {
      setIsLoadingTranscripts(false);
      setIsLoadingMoreTranscripts(false);
    }
  }, [transcriptsPage, getUserEmail, selectedFolderIds]);

  // Load notes when switching to notes screen



  const handleDocumentUpload = useCallback(async (title, documentAsset, useOCR = false) => {
    try {
      console.log('??? DATABASE ENRICHMENT FLOW - Processing document upload with title for Milvus storage', { title });

      if (!documentAsset || !documentAsset.uri) {
        console.error('Invalid document asset:', documentAsset);
        Alert.alert('Error', 'Invalid document data. Please try selecting the document again.');
        return;
      }

      // Get user email for processing
      const userEmail = await getUserEmail();
      console.log('?? Uploading document for user:', userEmail);

      // REMOVED: Document upload message from chat to prevent black bubbles
      // const newMessage = {
      //   id: uuidv4(),  
      //   document: { name: title, uri: documentAsset.uri },
      //   sender: 'user',
      // };
      // 
      // setMessages((prevMessages) => {
      //   const newMessages = [...prevMessages, newMessage];
      //   storageManager.storeMessagePairs(newMessages, activeSessionId);
      //   return newMessages;
      // });
      // scheduleScrollToBottom(1);

      // Add to upload queue instead of direct upload
      const uploadId = uuidv4();

      // Document upload folder routing using workspace context helper
      const workspaceFolder = getSelectedWorkspaceFolder();
      const documentFolderId = workspaceFolder.id;
      const documentFolderName = workspaceFolder.name;

      // ?? DETAILED LOGGING: Document upload folder selection
      console.log('?? DOCUMENT_UPLOAD_FOLDER_DEBUG:', {
        selectedFolderIds: selectedFolderIds,
        allFolders: folders.map(f => ({ id: f.id, name: f.name, type: f.type })),
        workspaceFolder: workspaceFolder,
        documentFolderId: documentFolderId,
        documentFolderName: documentFolderName
      });

      // Check if user has multiple selections (BLOCK UPLOAD)
      if (selectedFolderIds.length > 1) {
        const selectedFolderNames = selectedFolderIds.map(id => {
          if (id === 'documents') return 'Documents';
          const folder = folders.find(f => f.id === id);
          return folder ? folder.name : 'Unknown';
        });

        // BLOCK THE UPLOAD - Show error and return early
        const errorText = `? Multiple drives selected (${selectedFolderNames.join(', ')}). You must select only ONE drive for uploads. Please deselect other drives and try again.`;
        showUploadToast(errorText);

        // Also show an alert for better visibility
        Alert.alert(
          'Upload Not Allowed',
          `You have selected ${selectedFolderIds.length} drives. For document uploads, you can only select ONE drive at a time.\n\nCurrently selected: ${selectedFolderNames.join(', ')}\n\nPlease deselect other drives and try uploading again.`,
          [{ text: 'OK', style: 'default' }]
        );

        // Stop the upload process here - do not continue
        return;
      }

      // Removed: Folder routing toast (completion toast will provide sufficient feedback)
      // showFolderRoutingToast('document', 'chat', documentAsset.name || title || 'Document', documentFolderId);

      console.log('?? QUEUE_ADDITION_DEBUG:', {
        documentFolderId: documentFolderId,
        documentFolderName: documentFolderName,
        title: title,
        uploadId: uploadId
      });

      uploadQueueManager.addToQueue({
        id: uploadId,
        document: documentAsset,
        title: title,
        type: 'document',
        fileName: documentAsset.name || title,
        folderId: documentFolderId,
        folderName: documentFolderName,
        useOCR: useOCR // Pass OCR flag to upload queue
      });

      // Removed: success toast for queue addition (folder routing toast is enough)

    } catch (err) {
      console.error('handleDocumentUpload error:', err);
      Alert.alert('Error', err.message || 'Document upload failed.');
    }
  }, [scheduleScrollToBottom, setMessages, storageManager, activeSessionId, getSelectedWorkspaceFolder, getSelectedFolders, showFolderRoutingToast]);

  // Google Drive document handler

  // Download Google Drive file and convert to uploadable format

  // Open Google Drive picker


  // New asynchronous audio upload function that doesn't block UI

  // Updated audio file upload function



  // Start actual recording without prompting for topic
  const startActualRecording = useCallback(async (isForQuestion = false) => {
    // Check if multiple folders are selected and BLOCK RECORDING (unless it's for questions)
    if (!isForQuestion) {
      console.log('?? DEBUG: Audio Recording - Multiple folder validation check:', {
        selectedFolderIds: selectedFolderIds,
        selectedFolderIdsLength: selectedFolderIds.length,
        allFolders: folders,
        isForQuestion: isForQuestion
      });

      if (selectedFolderIds.length > 1) {
        const selectedFolderNames = selectedFolderIds.map(id => {
          if (id === "documents") return "Documents";
          const folder = folders.find(f => f.id === id);
          return folder ? folder.name : "Unknown";
        });

        // BLOCK THE RECORDING - Show error and return early
        const errorText = `? Multiple drives selected (${selectedFolderNames.join(', ')}). You must select only ONE drive for recording. Please deselect other drives and try again.`;
        showUploadToast(errorText);

        // Also show an alert for better visibility
        Alert.alert(
          'Recording Not Allowed',
          `You have selected ${selectedFolderIds.length} drives. For audio recording, you can only select ONE drive at a time.\n\nCurrently selected: ${selectedFolderNames.join(', ')}\n\nPlease deselect other drives and try recording again.`,
          [{ text: 'OK', style: 'default' }]
        );

        // Stop the recording process here - do not continue
        return;
      }
    }

    // Show folder routing information - recordings always go to meetings
    if (!isForQuestion) {
      showFolderRoutingToast('recording', 'recording', 'Recording');
    }

    // Start the actual recording process
    try {
      console.log('Starting recording...');
      setIsRecording(true);
      setRecordingStartTime(Date.now());

      if (Platform.OS === 'web') {
        if (isForQuestion) {
          // ORIGINAL QUESTION RECORDING PATH (leave as lightweight attachment capture)
          if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('Audio recording is not supported in this browser.');
          }
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const mediaRecorder = new MediaRecorder(stream);
          const audioChunks = [];
          mediaRecorder.ondataavailable = (event) => { audioChunks.push(event.data); };
          mediaRecorder.onstop = async () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
            const audioFile = new File([audioBlob], `question_audio_${Date.now()}.wav`, { type: 'audio/wav' });
            const attachment = {
              id: uuidv4(),
              type: 'audio',
              uri: URL.createObjectURL(audioBlob),
              name: audioFile.name,
              mimeType: 'audio/wav',
              file: audioFile,
              blob: audioBlob,
              isQuestionRecording: true,
              processed: true
            };
            addAttachmentWithLimit(attachment);
            stream.getTracks().forEach(track => track.stop());
            setIsRecordingForQuestion(false);
          };
          webAudioRecorderRef.current = mediaRecorder;
          mediaRecorder.start();
        }
      } else {
        // Mobile recording setup using Expo AV - Skip on web platform
        if (Platform.OS === 'web') {
          throw new Error('Mobile recording logic should not run on web platform');
        }

        const { Audio } = require('expo-av');

        const { status } = await Audio.requestPermissionsAsync();
        if (status !== 'granted') {
          throw new Error('Audio recording permission not granted');
        }

        await Audio.setAudioModeAsync({
          allowsRecordingIOS: true,
          playsInSilentModeIOS: true,
        });

        // Check if Audio.Recording is available before using it
        if (!Audio.Recording) {
          throw new Error('Audio.Recording not available on this platform');
        }

        const recording = new Audio.Recording();
        try {
          const recordingOptions = Audio.RECORDING_OPTIONS_PRESET_HIGH_QUALITY || Audio.RecordingOptionsPresets?.HIGH_QUALITY;
          if (!recordingOptions) {
            throw new Error('Recording options not available');
          }
          await recording.prepareToRecordAsync(recordingOptions);
          await recording.startAsync();
          recordingRef.current = recording;
        } catch (error) {
          throw new Error(`Failed to start recording: ${error.message}`);
        }
      }
    } catch (error) {
      console.error('Error starting recording:', error);
      setIsRecording(false);
      if (isForQuestion) {
        setIsRecordingForQuestion(false);
      }
      throw error;
    }
  }, [selectedFolderIds, folders, showUploadToast, showFolderRoutingToast, setIsRecording, setRecordingStartTime, setIsRecordingForQuestion, recordingTitle]);

  // Handle question recording start with topic
  const handleQuestionRecordingStart = useCallback(async (topic) => {
    try {
      console.log('??? Starting question recording with topic:', topic);

      // Set question recording mode
      setIsRecordingForQuestion(true);
      setRecordingTitle(topic); // Store the topic for later use

      // Start the actual recording directly (bypass the prompt in startRecording)
      await startActualRecording(true); // true = isForQuestion

    } catch (error) {
      console.error('Failed to start question recording:', error);
      setIsRecordingForQuestion(false);
      setRecordingTitle('');
      Alert.alert('Error', 'Failed to start question recording. Please try again.');
    }
  }, [setIsRecordingForQuestion, setRecordingTitle, startActualRecording]);

  // Removed checkNetworkConnectivity function - it was causing CORS errors
  // Network connectivity is handled naturally by the actual API calls

  // Legacy attachment handling removed - now UI processes attachments locally and includes extracted text in JSON query

  // Handle streaming response for pro and reasoning models
  const handleStreamingResponse = useCallback(async (requestData, newMessage, currentSessionId) => {
    // First show typing indicator (3-dot animation)
    const typingMessage = {
      id: 'typing',
      text: '',
      sender: 'bot',
      isTyping: true,
    };

    setMessages(prev => [...prev, typingMessage]);
    // DISABLED: Don't auto-scroll - let user stay at reading position
    // scheduleScrollToBottom(1);
    setIsBotTyping(true);

    // Wait a short time to show the typing indicator
    await new Promise(resolve => setTimeout(resolve, 1500));

    // Helper function to generate unique bot message ID
    const getBotMessageId = (messagePairId, suffix = '') => {
      return `bot-${messagePairId}${suffix ? '-' + suffix : ''}-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`;
    };

    // Then replace with streaming message
    const botMessageId = getBotMessageId(requestData.message_pair_id, 'stream');
    const streamingBotMessage = {
      id: botMessageId,
      text: '',
      sender: 'bot',
      isStreaming: true,
      shouldAnimate: false, // Don't animate streaming messages
      streamingRef: { current: '' }, // Add streaming ref for smooth updates
    };

    // Replace typing indicator with streaming message
    setMessages(prev => prev.filter(msg => msg.id !== 'typing').concat([streamingBotMessage]));
    // DISABLED: Don't auto-scroll - let user stay at reading position
    // scheduleScrollToBottom(1);

    try {
      // Use the streaming endpoint
      const streamUrl = API_CONFIG.CITRA_SERVICE_URL + '/query-stream';
      console.log('?? Starting streaming request to:', streamUrl);
      console.log('?? Request data:', JSON.stringify(requestData, null, 2));

      // Use fetch for streaming support (better than axios for streaming)
      const response = await authService.authenticatedFetch(streamUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/plain',
        },
        body: JSON.stringify(requestData),
      });

      console.log('?? Response status:', response.status);
      console.log('?? Response headers:', Object.fromEntries(response.headers.entries()));

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      // Check if the browser/environment supports streaming
      if (!response.body || !response.body.getReader) {
        console.warn('Streaming not supported, falling back to regular response');
        // Fallback to reading full response
        const text = await response.text();
        console.log('?? Fallback response text:', text);

        // Try to parse the response in case it's JSON
        let finalText = text;
        try {
          const jsonResponse = JSON.parse(text);
          console.log('?? Parsed JSON response:', jsonResponse);

          // Extract content from various possible formats
          if (jsonResponse.content) {
            finalText = jsonResponse.content;
            console.log('? Using content field:', finalText);
          } else if (jsonResponse.choices && jsonResponse.choices[0] && jsonResponse.choices[0].message && jsonResponse.choices[0].message.content) {
            finalText = jsonResponse.choices[0].message.content;
            console.log('? Using OpenAI format content:', finalText);
          } else if (jsonResponse.text) {
            finalText = jsonResponse.text;
            console.log('? Using text field:', finalText);
          } else {
            console.warn('?? Could not extract content from JSON, using raw text');
          }
        } catch (parseError) {
          console.log('?? Response is not JSON, checking if it\'s streaming data...');

          // Check if this is streaming data format
          if (text.includes('data: {"content":') || text.includes('data: {"done":')) {
            console.log('?? Detected streaming data format, parsing...');

            // Parse streaming data format
            const lines = text.split('\n');
            let extractedContent = '';

            for (const line of lines) {
              if (line.trim().startsWith('data: ')) {
                try {
                  const jsonStr = line.slice(6).trim();
                  if (jsonStr && jsonStr !== '{"done": true}') {
                    const data = JSON.parse(jsonStr);
                    if (data.content !== undefined) {
                      extractedContent += data.content;
                    }
                  }
                } catch (streamParseError) {
                  console.warn('Failed to parse streaming line:', line);
                }
              }
            }

            if (extractedContent) {
              finalText = extractedContent;
              console.log('? Extracted content from streaming data:', finalText.substring(0, 100) + '...');
            } else {
              console.warn('?? Could not extract content from streaming data');
            }
          } else {
            console.log('?? Using response as plain text');
          }
        }

        setMessages(prev => prev.map(msg =>
          msg.id === botMessageId
            ? { ...msg, text: finalText, isStreaming: false, shouldAnimate: false }
            : msg
        ));

        const finalBotMessage = {
          id: botMessageId,
          text: finalText || 'Sorry, I did not understand that.',
          sender: 'bot',
        };

        await storageManager.addMessagePair(currentSessionId, newMessage, finalBotMessage);

        // Also store in browser chat manager if MongoDB chat history is disabled
        if (browserChatManager.isBrowserOnlyMode()) {
          browserChatManager.addMessage(currentSessionId, newMessage.text, finalBotMessage.text, newMessage.id);
        }
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let streamedText = '';
      let isComplete = false;
      let buffer = ''; // Buffer to handle partial chunks

      // Get reference to the streaming message for smooth updates
      const messageRef = streamingBotMessage.streamingRef;

      while (!isComplete) {
        const { done, value } = await reader.read();

        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;

        // Process complete lines from buffer
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep the last incomplete line in buffer

        for (const line of lines) {
          if (line.trim().startsWith('data: ')) {
            try {
              const jsonStr = line.slice(6).trim(); // Remove 'data: ' prefix

              // Handle empty data lines
              if (!jsonStr) continue;

              const data = JSON.parse(jsonStr);

              // Handle DeepSeek streaming format
              if (data.content !== undefined) {
                console.log('? Adding content:', JSON.stringify(data.content));

                // Sanitize content to prevent rendering issues
                const sanitizedContent = typeof data.content === 'string'
                  ? data.content
                  : String(data.content || '');

                // Add content to streamed text (content can be empty string)
                streamedText += sanitizedContent;

                // Update the streaming reference for smooth rendering
                if (messageRef) {
                  messageRef.current = streamedText;
                }

                // No need to trigger setMessages - let StreamingMessage handle its own updates
              }
              // Handle standard OpenAI format as fallback
              else if (data.choices && data.choices[0] && data.choices[0].delta && data.choices[0].delta.content) {
                console.log('? Adding OpenAI content:', JSON.stringify(data.choices[0].delta.content));

                // Sanitize content to prevent rendering issues
                const sanitizedContent = typeof data.choices[0].delta.content === 'string'
                  ? data.choices[0].delta.content
                  : String(data.choices[0].delta.content || '');

                // Add OpenAI format content
                streamedText += sanitizedContent;

                // Update the streaming reference for smooth rendering
                if (messageRef) {
                  messageRef.current = streamedText;
                }

                // No need to trigger setMessages - let StreamingMessage handle its own updates
              }
              // Log unhandled data format (excluding deep research events which have their own handler)
              else if (!data.done && !data.error && !data.type?.startsWith('deep_research')) {
                console.warn('?? Unhandled streaming data format:', JSON.stringify(data));
                // Don't add raw JSON to the stream - skip it instead
              }

              // Check for completion
              if (data.done === true) {
                console.log('? Received completion signal {"done": true}');
                isComplete = true;
                // Final update with complete content - no animation needed since content was streamed
                try {
                  setMessages(prev => prev.map(msg =>
                    msg.id === botMessageId
                      ? { ...msg, text: streamedText, isStreaming: false, shouldAnimate: false, streamingRef: null }
                      : msg
                  ));
                  console.log('?? Streaming completed successfully');
                  console.log('?? Final streamed text length:', streamedText.length);
                } catch (updateError) {
                  console.error('Error updating message on completion:', updateError);
                }
                break;
              }

              if (data.error) {
                throw new Error(data.error);
              }
            } catch (parseError) {
              console.warn('Failed to parse streaming data:', parseError, 'Line:', line);
              // Continue processing other lines
            }
          }
        }
      }

      // Process any remaining content in buffer
      if (buffer.trim()) {
        const line = buffer.trim();
        if (line.startsWith('data: ')) {
          try {
            const jsonStr = line.slice(6).trim();
            if (jsonStr) {
              const data = JSON.parse(jsonStr);
              if (data.content !== undefined) {
                streamedText += data.content;
                try {
                  setMessages(prev => prev.map(msg =>
                    msg.id === botMessageId
                      ? { ...msg, text: streamedText, isStreaming: false, shouldAnimate: false, streamingRef: null }
                      : msg
                  ));
                } catch (updateError) {
                  console.error('Error updating message from final buffer:', updateError);
                }
              }
            }
          } catch (parseError) {
            console.warn('Failed to parse final buffer:', parseError);
          }
        }
      }

      // Ensure final update if not already completed
      if (!isComplete) {
        try {
          setMessages(prev => prev.map(msg =>
            msg.id === botMessageId
              ? { ...msg, text: streamedText, isStreaming: false, shouldAnimate: false, streamingRef: null }
              : msg
          ));
          console.log('?? Streaming ended without done signal');
        } catch (updateError) {
          console.error('Error updating message on stream end:', updateError);
        }
      }

      console.log('?? Streaming completed. Final text length:', streamedText.length);

      // Store messages in AsyncStorage after streaming is complete
      const finalBotMessage = {
        id: botMessageId,
        text: streamedText || 'Sorry, I did not understand that.',
        sender: 'bot',
        citations: [] // Add empty citations array for streaming (will be populated if streaming includes citations)
      };

      // Save the streamed message to AsyncStorage
      try {
        await storageManager.addMessagePair(currentSessionId, newMessage, finalBotMessage);
        console.log('?? Streamed message saved to AsyncStorage successfully');

        // Also store in browser chat manager if MongoDB chat history is disabled
        if (browserChatManager.isBrowserOnlyMode()) {
          browserChatManager.addMessage(currentSessionId, newMessage.text, finalBotMessage.text, newMessage.id);
        }

        // Track usage after successful response
        await trackUsageAfterAction('chat_query', { query: requestData.query });

        // Trigger scroll to ensure message is visible
        scheduleScrollToBottom(200);

      } catch (storageError) {
        console.error('? Failed to save streamed message to AsyncStorage:', storageError);
      }

    } catch (error) {
      console.error('Streaming error:', error);

      // Update the bot message with error message instead of throwing
      const errorMessage = error.message || 'Sorry, I encountered an error while processing your request.';
      const errorText = `?? ${errorMessage}\n\nPlease try again or use a different AI model.`;

      setMessages(prev => prev.map(msg =>
        msg.id === botMessageId
          ? {
            ...msg,
            text: errorText,
            isStreaming: false,
            shouldAnimate: false,
            streamingRef: null
          }
          : msg
      ));

      // CRITICAL FIX: Save error message to AsyncStorage too
      try {
        const errorBotMessage = {
          id: botMessageId,
          text: errorText,
          sender: 'bot',
        };
        await storageManager.addMessagePair(currentSessionId, newMessage, errorBotMessage);
        console.log('?? Error message saved to AsyncStorage successfully');

        // Also store in browser chat manager if MongoDB chat history is disabled
        if (browserChatManager.isBrowserOnlyMode()) {
          browserChatManager.addMessage(currentSessionId, newMessage.text, errorBotMessage.text, newMessage.id);
        }

        // // Force a final UI update for error message too
        // setMessages(prev => prev.map(msg => 
        //   msg.id === `bot-${requestData.message_pair_id}` 
        //     ? { 
        //         ...msg, 
        //         text: errorText,
        //         isStreaming: false, 
        //         shouldAnimate: true 
        //       }
        //     : msg
        // ));

        // // Trigger scroll for error message
        scheduleScrollToBottom(100);

      } catch (storageError) {
        console.error('? Failed to save error message to AsyncStorage:', storageError);
      }

      // Don't re-throw to prevent UI freezing
      console.log('?? Error handled gracefully, UI remains responsive');

      // Optional: Show a toast/alert for user feedback
      if (Platform.OS === 'web') {
        // Web-specific error handling
        console.warn('Web streaming error handled');
      }
    }

    // Ensure bot typing is always turned off and cancel token is cleared
    setIsLoading(false);
    setIsGenerating(false);
    setIsBotTyping(false);
    // CRITICAL FIX: Clear cancel token after streaming completes
    setCancelTokenSource(null);
  }, [storageManager, scheduleScrollToBottom, setMessages, setIsBotTyping, loadChatMessages]);

  // Handle regular response for models
  const handleRegularResponse = useCallback(async (requestData, newMessage, currentSessionId) => {

    // Add typing indicator
    const typingMessage = {
      id: 'typing',
      text: '',
      sender: 'bot',
      isTyping: true,
    };

    setMessages(prev => [...prev, typingMessage]);
    scheduleScrollToBottom(1);

    const source = axios.CancelToken.source();
    setCancelTokenSource(source);

    // Create AbortController for fetch API compatibility
    const abortController = new AbortController();

    // Link axios cancel token to AbortController
    source.token.promise.then(() => {
      abortController.abort();
    }).catch(() => { }); // Ignore promise rejection

    // Track if request was cancelled
    let isCancelled = false;
    const cancelPromise = new Promise((resolve) => {
      source.token.promise.then(() => {
        isCancelled = true;
        resolve();
      }).catch(() => { }); // Ignore promise rejection
    });

    try {
      logApiCall(QUERY_URL, 'POST', requestData);

      const response = await authService.authenticatedFetch(QUERY_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify(requestData),
        signal: abortController.signal
      });

      console.log('?? [API_RESPONSE] Received response:', {
        status: response.status,
        ok: response.ok,
        timestamp: new Date().toISOString()
      });

      // Check if request was cancelled before processing response
      if (source.token.reason) {
        console.log('?? [API_RESPONSE] Request was cancelled, not processing response');
        return;
      }

      if (!response.ok) {
        // Handle payment required (402) and other specific errors
        let errorData;
        try {
          errorData = await response.json();
        } catch (e) {
          // If JSON parsing fails, throw generic error
          throw new Error('Network response was not ok');
        }

        // Handle other errors with message from response if available
        throw new Error(errorData.message || errorData.error || 'Network response was not ok');
      }

      const data = await response.json();

      // Check if request was cancelled
      if (isCancelled) {
        console.log('?? [API_RESPONSE] Request was cancelled, not processing response');
        return;
      }

      setIsBotTyping(true);

      // Enhanced response processing (same as streaming) for better markdown and rich text handling
      let finalText = '';

      // Handle different response formats with enhanced processing
      if (data.response) {
        finalText = data.response;
      } else if (data.answer) {
        // Some endpoints might return 'answer' field
        finalText = data.answer;
      } else if (data.content) {
        finalText = data.content;
      } else if (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) {
        finalText = data.choices[0].message.content;
      } else if (data.text) {
        finalText = data.text;
      } else {
        finalText = 'Sorry, I did not understand that.';
        console.warn('?? Could not extract content from response data:', data);
      }

      // Sanitize content to prevent rendering issues (same as streaming)
      finalText = typeof finalText === 'string' ? finalText : String(finalText || '');

      // Log response processing for debugging
      // console.log('?? Regular response processed:', {
      //   originalDataKeys: Object.keys(data),
      //   finalTextLength: finalText.length,
      //   finalTextPreview: finalText.substring(0, 100) + (finalText.length > 100 ? '...' : ''),
      //   citationsCount: data.citations ? data.citations.length : 0,
      //   citationsPreview: data.citations ? data.citations.slice(0, 2).map(c => ({
      //     title: c.document_title,
      //     hasContentPreview: !!(c.content_preview),
      //     hasExcerpt: !!(c.excerpt)
      //   })) : []
      // });

      // ENHANCED DEBUG: Raw API response analysis
      // console.log('?? [API_DEBUG] Raw citations data:', {
      //   hasCitations: !!(data.citations),
      //   citationsType: Array.isArray(data.citations) ? 'array' : typeof data.citations,
      //   firstCitation: data.citations && data.citations[0] ? {
      //     allKeys: Object.keys(data.citations[0]),
      //     title: data.citations[0].document_title,
      //     content_preview: data.citations[0].content_preview,
      //     excerpt: data.citations[0].excerpt
      //   } : null
      // });

      // ========== ADD DEEP RESEARCH HANDLING FOR REGULAR RESPONSES ==========
      // Check if this is a deep research response that needs special handling
      if (requestData.deep_research && data) {
        console.log('?? Processing deep research regular response:', data);

        // Handle Deep Research Questions/Clarification
        if (data.stage === 'questioning' && data.is_awaiting_user_input && data.questions) {
          console.log('?? Deep research needs clarification - handling questions');

          setDeepResearchState(prev => ({
            ...prev,
            stage: 'questioning',
            questions: data.questions || [],
            isAwaitingUserInput: true,
            researchId: data.research_id || prev.researchId,
            findings: data.findings || prev.findings,
            researchMetadata: data.research_metadata || prev.researchMetadata
          }));

          // Process and format questions similar to streaming version
          if (data.questions && data.questions.length > 0) {
            console.log('?? Raw questions data:', JSON.stringify(data.questions, null, 2));

            // Handle questions that might be objects with IDs or simple strings
            const questionTexts = data.questions.map((q, i) => {
              if (typeof q === 'string') {
                return `${i + 1}. ${q}`;
              } else if (q.text || q.question) {
                return `${i + 1}. ${q.text || q.question}`;
              } else {
                return `${i + 1}. ${q}`;
              }
            });

            // Extract question IDs - try multiple possible fields
            const questionIds = data.questions.map((q, index) => {
              console.log(`?? [QUESTION_ID_DEBUG] Question ${index}:`, JSON.stringify(q, null, 2));

              if (typeof q === 'string') {
                console.log(`?? [QUESTION_ID_DEBUG] Question ${index} is string, using 'general'`);
                return 'general';
              } else if (q.id) {
                console.log(`?? [QUESTION_ID_DEBUG] Question ${index} using id:`, q.id);
                return q.id;
              } else if (q.key) {
                console.log(`?? [QUESTION_ID_DEBUG] Question ${index} using key:`, q.key);
                return q.key;
              } else if (q.question_id) {
                console.log(`?? [QUESTION_ID_DEBUG] Question ${index} using question_id:`, q.question_id);
                return q.question_id;
              } else if (q.type) {
                console.log(`?? [QUESTION_ID_DEBUG] Question ${index} using type:`, q.type);
                return q.type;
              } else {
                console.log(`?? [QUESTION_ID_DEBUG] Question ${index} fallback to 'general'`);
                return 'general';
              }
            });

            console.log('?? Extracted question IDs:', questionIds);

            const clarificationMessage = {
              id: `clarification-${Date.now()}-${Math.random()}`,
              text: `I need some clarification to continue the research:\n\n${questionTexts.join('\n\n')}\n\nPlease provide your responses in the chat below.`,
              sender: 'assistant',
              timestamp: new Date(),
              type: 'clarification',
              deepResearchId: data.research_id,
              questions: data.questions, // Store the full questions array with IDs
              questionIds: questionIds, // Store extracted question IDs
              isStreaming: false,
              shouldAnimate: false
            };

            // Remove typing indicator and add clarification message
            setMessages(prev => {
              const filtered = prev.filter(msg => msg.id !== 'typing');
              const newMessages = [...filtered, clarificationMessage];
              storageManager.storeMessagePairs(newMessages, currentSessionId);
              return newMessages;
            });

            setIsPendingClarificationResponse(true);
            setIsBotTyping(false);
            scheduleScrollToBottom(1);
            return; // Exit early for clarification case
          }
        }
        // Handle Deep Research Completion
        else if (data.stage === 'completed' || data.stage === 'complete' || data.status === 'completed' || data.final_answer) {
          console.log('?? Deep research completed - processing final answer');
          console.log('?? [COMPLETION_DEBUG] Stage:', data.stage, 'Status:', data.status, 'Has final_answer:', !!data.final_answer);
          console.log('?? [COMPLETION_DEBUG] Research ID from response:', data.research_id);
          console.log('?? [COMPLETION_DEBUG] Full completion data keys:', Object.keys(data));
          console.log('?? [COMPLETION_DEBUG] Updating deep research state to complete...');

          setDeepResearchState(prev => ({
            ...prev,
            stage: 'complete',  // ?? CRITICAL: Use 'complete' to match other flows
            isResearching: false,
            isAwaitingUserInput: false,
            findings: data.findings || prev.findings,
            researchMetadata: data.research_metadata || prev.researchMetadata,
            completed: true,  // ?? CRITICAL: Add completed flag for UI rendering
            researchId: data.research_id || prev.researchId,  // ?? CRITICAL: Update research ID from response
            progress: data.progress || 100,  // ?? CRITICAL: Set progress to 100% if not provided
            currentStep: data.currentStep || 5,  // ?? CRITICAL: Set to final step
            totalSteps: data.totalSteps || 5  // ?? CRITICAL: Ensure total steps
          }));

          console.log('?? [COMPLETION_DEBUG] Deep research state updated to complete with completed=true');
          console.log('?? [COMPLETION_DEBUG] Updated deepResearchState:', {
            stage: 'complete',
            completed: true,
            isResearching: false,
            isAwaitingUserInput: false,
            researchId: data.research_id,
            previousResearchId: deepResearchState.researchId,
            progress: data.progress || 100,
            currentStep: data.currentStep || 5,
            totalSteps: data.totalSteps || 5
          });

          // Use final_answer if available, otherwise use the processed finalText
          finalText = data.final_answer || finalText;
          setIsPendingClarificationResponse(false);

          // Track usage after successful deep research
          try {
            await trackUsageAfterAction('deep_research', { query: deepResearchState.query || requestData.query });
          } catch (trackingError) {
            console.error('Deep research usage tracking error:', trackingError);
          }
        }
        // Handle Deep Research Error
        else if (data.stage === 'error' || data.error) {
          console.log('?? Deep research error:', data.error);

          setDeepResearchState(prev => ({
            ...prev,
            stage: 'error',
            isResearching: false,
            isAwaitingUserInput: false,
            error: data.error
          }));

          finalText = data.error || 'Deep research encountered an error. Please try again.';
          setIsPendingClarificationResponse(false);
        }
      }
      // ========== END DEEP RESEARCH HANDLING ==========

      // Create final bot message - ensure unique ID
      // Note: Citations are parsed directly from SOURCES section in response text by RichMessageRenderer
      // Keep bot message ID stable per message pair so inline edit can reliably target it
      const botMessage = {
        id: `bot-${requestData.message_pair_id}`,
        messagePairId: requestData.message_pair_id,
        text: finalText,
        sender: 'bot',
        shouldAnimate: false // No animation for bot messages
      };

      console.log('?? [BOT_MESSAGE_DEBUG] About to add bot message:', {
        id: botMessage.id,
        textLength: botMessage.text.length,
        textPreview: botMessage.text.substring(0, 100),
        currentSessionId: currentSessionId
      });

      // Remove typing indicator and add actual bot message
      setMessages(prev => {
        console.log('?? [BOT_MESSAGE_DEBUG] Current messages count:', prev.length);
        console.log('?? [BOT_MESSAGE_DEBUG] Removing typing indicator and adding bot message');
        const updatedMessages = prev.filter(msg => msg.id !== 'typing').concat([botMessage]);
        console.log('?? [BOT_MESSAGE_DEBUG] Updated messages count:', updatedMessages.length);
        // Store the complete conversation including both user and bot messages
        storageManager.storeMessagePairs(updatedMessages, currentSessionId);
        return updatedMessages;
      });
      scheduleScrollToBottom(1); // Schedule scroll for bot reply

      // Track usage after successful response
      try {
        await trackUsageAfterAction('chat_query', { query: requestData.query });
      } catch (trackingError) {
        console.error('Usage tracking error:', trackingError);
        // Don't block the flow for tracking errors
      }

      // ?? FIX: Don't wait for animation to complete - it was blocking the bot typing indicator from being cleared
      // The animation will complete on its own via handleMessageAnimationComplete callback
      setIsBotTyping(false); // Clear typing indicator immediately after adding message

    } catch (error) {
      // Handle cancelled requests (both axios and fetch AbortError)
      if (axios.isCancel(error) || (error instanceof DOMException && error.name === 'AbortError')) {
        console.log('Regular response cancelled by user');
        // Remove typing indicators
        setMessages(prevMessages =>
          prevMessages.filter(msg => msg.id !== 'typing')
        );
        return; // Exit early for cancelled requests
      }

      console.error('Regular response error:', error);

      // Detect credit error and show specific message
      const errorText = `?? Failed to get a response. Please try again.\n\nError: ${error.message}`;

      setMessages(prev => prev.map(msg =>
        msg.id === 'typing'
          ? {
            id: `bot-${requestData.message_pair_id}-error-${Date.now()}`,
            text: errorText,
            sender: 'bot',
            shouldAnimate: false
          }
          : msg
      ));
    } finally {
      // Always clear states and cancel token
      setIsLoading(false);
      setIsGenerating(false);
      setIsBotTyping(false);
      setCancelTokenSource(null);
    }
  }, [storageManager, scheduleScrollToBottom, setCancelTokenSource, setMessages, setIsBotTyping, setActiveScreen, loadTranscripts, trackUsageAfterAction]);

  const sendMessage = useCallback(async (text) => {
    // Prevent duplicate rapid calls
    if (isLoading || isGenerating) {
      console.log('?? [SEND_MESSAGE] Preventing duplicate sendMessage call - already processing');
      return;
    }

    // Max characters allowed in a single chat query
    const MAX_CHAT_CHARS = 100000;

    // Check if we're waiting for a clarification response BEFORE modifying state
    const isHandlingClarification = isPendingClarificationResponse && deepResearchState.isAwaitingUserInput ||
      deepResearchState.stage === 'questioning' ||
      (deepResearchState.questions && deepResearchState.questions.length > 0) ||
      deepResearchState.stage === 'processing_clarification';
    console.log('?? [CLARIFICATION_DEBUG] State check:', {
      isPendingClarificationResponse,
      isAwaitingUserInput: deepResearchState.isAwaitingUserInput,
      researchId: deepResearchState.researchId,
      stage: deepResearchState.stage,
      isHandlingClarification
    });

    if (isHandlingClarification) {
      console.log('?? Handling deep research clarification response:', text);
      // Enforce max length for clarification responses as well
      if (typeof text === 'string' && text.length > MAX_CHAT_CHARS) {
        const errorMsg = {
          id: `error-${Date.now()}`,
          text: `Your message is ${text.length.toLocaleString()} characters and exceeds the maximum allowed length of ${MAX_CHAT_CHARS.toLocaleString()} characters. Please shorten it and try again.`,
          sender: 'bot',
          type: 'system',
          timestamp: new Date()
        };
        setMessages(prev => [...prev, errorMsg]);
        scheduleScrollToBottom(1);
        return;
      }

      // Find the most recent clarification message to get the question IDs
      const clarificationMessages = messages.filter(msg => msg.type === 'clarification' && msg.deepResearchId === deepResearchState.researchId);
      const clarificationMessage = clarificationMessages[clarificationMessages.length - 1]; // Get the last one
      let questionId = 'general'; // Default fallback

      console.log('?? [QUESTION_ID_DEBUG] Clarification message found:', !!clarificationMessage);
      console.log('?? [QUESTION_ID_DEBUG] Clarification message structure:', {
        hasQuestionIds: !!(clarificationMessage && clarificationMessage.questionIds),
        questionIdsLength: clarificationMessage?.questionIds?.length,
        questionIds: clarificationMessage?.questionIds,
        hasQuestions: !!(clarificationMessage && clarificationMessage.questions),
        questionsLength: clarificationMessage?.questions?.length,
        questions: clarificationMessage?.questions
      });

      if (clarificationMessage && clarificationMessage.questionIds && clarificationMessage.questionIds.length > 0) {
        // Use the first question ID (for now, assuming single question or general response)
        questionId = clarificationMessage.questionIds[0];
        console.log('?? Using question ID from clarification message:', questionId);
        console.log('?? Available question IDs:', clarificationMessage.questionIds);
      } else if (clarificationMessage && clarificationMessage.questions && clarificationMessage.questions.length > 0) {
        // Fallback: try to extract ID from the questions array directly
        const firstQuestion = clarificationMessage.questions[0];
        console.log('?? [QUESTION_ID_DEBUG] First question object:', firstQuestion);

        if (typeof firstQuestion === 'object' && firstQuestion !== null) {
          // Try different possible ID fields
          questionId = firstQuestion.id || firstQuestion.key || firstQuestion.question_id || firstQuestion.type || 'general';
          console.log('?? [QUESTION_ID_DEBUG] Extracted question ID from question object:', questionId);
        } else {
          console.log('?? [QUESTION_ID_DEBUG] First question is not an object:', typeof firstQuestion);
        }
      } else {
        console.log('?? No question IDs found in clarification message, using default:', questionId);
        console.log('?? Clarification message:', clarificationMessage);
      }

      // Add user's clarification response to chat
      const message_pair_id = uuidv4();
      const clarificationResponse = {
        id: message_pair_id,
        text: text,
        sender: 'user',
        timestamp: new Date(),
        type: 'clarification_response',
        deepResearchId: deepResearchState.researchId
      };

      setMessages(prev => {
        const newMessages = [...prev, clarificationResponse];
        storageManager.storeMessagePairs(newMessages, activeSessionId);
        return newMessages;
      });

      // Send clarification response to continue deep research
      try {
        // Don't reset isPendingClarificationResponse immediately - wait for successful response
        setDeepResearchState(prev => ({
          ...prev,
          isAwaitingUserInput: false,
          isResearching: true,
          stage: 'processing_clarification'
        }));

        console.log('?? Calling handleDeepResearchUserResponse with:', { questionId, text });
        const response = await handleDeepResearchUserResponse(questionId, text);
        console.log('?? Clarification response sent successfully via /deep-research-response endpoint');

        // Note: isPendingClarificationResponse will be reset in handleDeepResearchUserResponse based on the actual response
      } catch (error) {
        console.error('?? Error sending clarification response:', error);
        setIsPendingClarificationResponse(true);
        setDeepResearchState(prev => ({ ...prev, isAwaitingUserInput: true, stage: 'questioning' }));

        // Add error message to chat
        const errorMessage = {
          id: `error-${Date.now()}`,
          text: 'Sorry, there was an error processing your clarification. Please try again.',
          sender: 'assistant',
          timestamp: new Date(),
          type: 'error'
        };
        setMessages(prev => [...prev, errorMessage]);
      }
      return; // Exit early to avoid normal chat flow
    }


    // Removed health check - it was causing CORS errors and query failures
    // Network connectivity will be handled naturally by the actual API calls

    // DISABLED: Don't auto-scroll - let user stay at reading position
    // scheduleScrollToBottom(1);
    // setAutoScrollEnabled(true);
    if (isWaitingForRecordingTitle) {
      await submitRecordingTitle(text.trim());
      return;
    }

    // The image / document / photo title prompts that used to sit here are gone
    // with the personal vault: nothing ever set pendingImage, pendingDocument or
    // pendingPhoto to a real asset — the chat composer takes no file input on
    // this surface (config/featureFlags.js).

    // Create new session if none exists (for new chat scenario)
    let currentSessionId = activeSessionId;
    if (!currentSessionId) {
      console.log('?? [SESSION_DEBUG] No active session found, creating new session');
      console.log('?? [SESSION_DEBUG] Current activeSessionId:', activeSessionId);
      // Pass true to preserve existing messages (e.g., transcription messages)
      currentSessionId = createNewSession(true);
      setActiveSessionId(currentSessionId);
      console.log('?? [SESSION_DEBUG] Created new session:', currentSessionId);
    } else {
      console.log('?? [SESSION_DEBUG] Using existing session:', currentSessionId);
    }

    const message_pair_id = uuidv4();

    // Enhanced text processing: combine user text with extracted attachment content
    let finalQueryText = text;
    let multimodalAttachmentsPayload = [];

    if (questionAttachments.length > 0) {
      // Wait for all attachments to be processed if they aren't already
      if (!areAllAttachmentsProcessed()) {
        // Show processing message and wait
        const processingMessage = {
          id: `processing-${Date.now()}`,
          text: `? Processing ${questionAttachments.length} attachment${questionAttachments.length > 1 ? 's' : ''}... Please wait.`,
          sender: 'bot',
          type: 'system', // mark as system so excluded from conversation history
          hideActions: true,
        };

        setMessages(prev => {
          const newMessages = [...prev, processingMessage];
          storageManager.storeMessagePairs(newMessages, currentSessionId);
          return newMessages;
        });

        // Wait for processing to complete (max 30 seconds)
        let attempts = 0;
        const maxAttempts = 60; // 30 seconds with 500ms intervals

        while (!areAllAttachmentsProcessed() && attempts < maxAttempts) {
          await new Promise(resolve => setTimeout(resolve, 500));
          attempts++;
        }

        // Remove processing message
        setMessages(prev => prev.filter(msg => msg.id !== `processing-${Date.now()}`));
      }

      // Get combined text with attachment content
      finalQueryText = getCombinedQueryText(text);

      try {
        multimodalAttachmentsPayload = await prepareAttachmentsForQuery(questionAttachments);
      } catch (attachmentPrepError) {
        console.error('? Failed to prepare multimodal attachments for query:', attachmentPrepError);
      }

      // Clear attachments after processing
      setQuestionAttachments([]);
      setAttachmentProgress({});
    }

    // Validate final query length (including any attachment-derived text)
    if (typeof finalQueryText === 'string' && finalQueryText.length > MAX_CHAT_CHARS) {
      const tooLongMsg = {
        id: `error-${Date.now()}`,
        text: `Your message is ${finalQueryText.length.toLocaleString()} characters and exceeds the maximum allowed length of ${MAX_CHAT_CHARS.toLocaleString()} characters. Please shorten it and try again.`,
        sender: 'bot',
        type: 'system',
        timestamp: new Date()
      };
      setMessages(prev => [...prev, tooLongMsg]);
      scheduleScrollToBottom(1);
      return;
    }

    // Entity filtering is now handled via enterprise_entity_id parameter in backend
    // No need to append entity context to query text

    const newMessage = {
      id: message_pair_id,
      messagePairId: message_pair_id,
      text: text || (questionAttachments.length > 0 ? `?? Processing ${questionAttachments.length} attachment${questionAttachments.length > 1 ? 's' : ''}...` : ''),
      sender: 'user',
    };

    // Check if SSE streaming is supported for the current model
    const supportsStreaming = shouldUseSSEStreaming(selectedModelKey);
    console.log('?? [STREAMING] Model:', selectedModelKey, '| SSE Streaming:', supportsStreaming ? 'ENABLED' : 'DISABLED');

    // Add user message immediately
    setMessages(prev => {
      const newMessages = [...prev, newMessage];
      storageManager.storeMessagePairs(newMessages, currentSessionId);
      return newMessages;
    });
    scheduleScrollToBottom(1);
    setIsLoading(true);
    setIsGenerating(true);

    try {
      // Prepare data according to new API requirements - use messages from state (includes loaded history)
      let chats;
      if (browserChatManager.isBrowserOnlyMode()) {
        // Use browser chat manager for messages if MongoDB chat history is disabled
        chats = browserChatManager.getRecentMessages(currentSessionId, 20);
        console.log('?? [BROWSER_CHAT] Session:', currentSessionId, 'Browser chats for API:', chats.length);
      } else {
        // Use messages from state (includes loaded historical messages)
        // Filter out system messages and prepare chat history
        const userAndBotMessages = messages.filter(m => m.type !== 'system');
        chats = prepareChatHistory(userAndBotMessages);
        console.log('?? [CHAT_FIX] Session:', currentSessionId, 'Messages from state:', messages.length, 'User/Bot only:', userAndBotMessages.length, 'Chats for API:', chats.length);
      }

      const userEmail = await getUserEmail();
      const requestData = {
        message_pair_id: message_pair_id,
        user_id: userEmail,
        chat_session_id: currentSessionId,
        query: finalQueryText,
        chats: chats,
        user_info: "", // Personal info is now built in RAG system, no need to send
        ai_model: selectedModelKey,
        // Deep Research flag removed - LLM orchestrator now decides multi-hop automatically
        // deep_research: isModelOnlyMode ? false : isDeepResearchEnabled,
        // Add Query Enhancement flags - disable if AI Model mode is active
        // Personal-vault chat search was retired with folder_management.py.
        // Sent as an explicit false, not dropped — the backend request shape
        // is shared and an absent key means something different to it.
        use_personal_data: false,
        // Enterprise flags. The server forces enterprise sources ON for main
        // chat (they are the only sources left), so `enterprise_enabled: false`
        // alone is no longer enough to mean "search nothing" — General Query
        // mode has to say so explicitly via `model_only`.
        enterprise_enabled: isModelOnlyMode ? false : !!useEnterprise,
        use_enterprise_data: isModelOnlyMode ? false : !!useEnterprise,
        model_only: !!isModelOnlyMode,
        enterprise_entity_id: enterpriseEntityId && enterpriseEntityId.trim().length > 0 ? enterpriseEntityId.trim() : null,
        enterprise_entity_name: enterpriseEntityName && enterpriseEntityName.trim().length > 0 ? enterpriseEntityName.trim() : null,
        enterprise_entity_type: enterpriseEntityType && enterpriseEntityType.trim().length > 0 ? enterpriseEntityType.trim() : null,
        // Explicit flag to indicate entity filtering should be used (safer than checking entity_id truthy)
        enterprise_entity_enabled: !isModelOnlyMode && !!useEnterprise && enterpriseEntityId && enterpriseEntityId.trim().length > 0,
        // Persona was removed from the UI. The field is still sent as null
        // rather than dropped, because the backend request shape is shared and
        // an absent key is a different thing to it than an explicit null.
        persona_data: null,
        // Folder scoping retired with folder_management.py — always the
        // "no vault" shape now, kept explicit for the same reason as
        // use_personal_data above.
        selected_folder_ids: null,
        folder_search_enabled: false,
        vault: 'none',
        // INTERNET SEARCH: Pass internet search toggle state to backend
        enable_internet_search: enableInternetSearch,
        // LEGAL FILTERS: Pass court and law filter selections to backend for collection targeting
        legal_filters: legalFilters || undefined
      };
      if (multimodalAttachmentsPayload.length > 0) {
        requestData.multimodal_attachments = multimodalAttachmentsPayload;
      }

      // Debug session ID tracking
      console.log('?? [SESSION_TRACKING] Sending message with session ID:', currentSessionId);
      console.log('?? [SESSION_TRACKING] Original activeSessionId:', activeSessionId);
      console.log('?? [SESSION_TRACKING] Device ID:', userEmail);

      // Log API call in development
      console.log('?? Using fixed model key:', selectedModelKey);
      console.log('? Streaming support:', supportsStreaming ? 'YES' : 'NO');
      console.log('?? Environment:', API_CONFIG.ENVIRONMENT, '| Base URL:', API_CONFIG.BASE_URL);
      console.log('?? AI Model Mode:', isModelOnlyMode ? 'ENABLED' : 'DISABLED');
      console.log('?? Deep Research/Multi-hop: Auto-decided by LLM orchestrator based on query complexity');
      console.log('?? Use Uploaded Data:', (isModelOnlyMode ? false : useUploadedData) ? 'ENABLED' : 'DISABLED');
      console.log('?? Folder Filtering:', selectedFolderIds.length > 0 ? `ENABLED (${selectedFolderIds.length} folders)` : 'DISABLED');
      console.log('?? Enterprise:', (isModelOnlyMode ? false : !!useEnterprise) ? `ENABLED${enterpriseEntityId ? ` (ID: ${enterpriseEntityId})` : ''}` : 'DISABLED');
      console.log('?? Selected Folders:', selectedFolderIds.length > 0 ? selectedFolderIds : 'None');
      console.log('?? Internet Search:', enableInternetSearch ? 'ENABLED' : 'DISABLED');

      // CONVERSATION CONTEXT: Keep all user and assistant (LLM) replies; exclude only explicit system messages
      const buildFilteredConversationHistory = (all) => {
        if (!all || all.length === 0) return [];
        const scoped = all.slice(-60); // look back window
        const filtered = scoped.filter(m => m.type !== 'system');
        const finalList = filtered.slice(-10).map(msg => ({
          role: msg.sender === 'user' ? 'user' : 'assistant',
          content: msg.text,
          timestamp: msg.timestamp
        }));
        console.log('?? [HISTORY_FILTER_UI] raw:', all.length, 'scoped:', scoped.length, 'after_system_filter:', filtered.length, 'final_sent:', finalList.length);
        return finalList;
      };
      requestData.conversation_history = buildFilteredConversationHistory(messages);

      // Deep Research handling removed - LLM orchestrator now decides multi-hop automatically
      // All queries go through normal flow; LLM decides complexity-based routing

      // All attachment text has been pre-processed and combined into finalQueryText
      // Use regular text-only query flow for all requests
      if (supportsStreaming) {
        // Use NEW SSE streaming for real-time "typewriter" effect
        console.log('?? [STREAMING] Using SSE streaming for real-time response');
        await handleSSEStreamingResponse({
          requestData,
          newMessage,
          currentSessionId,
          setMessages,
          scheduleScrollToBottom,
          setIsBotTyping,
          setIsLoading,
          setIsGenerating,
          storageManager,
          browserChatManager,
        });
      } else {
        // Use regular API for non-streaming models  
        await handleRegularResponse(requestData, newMessage, currentSessionId);
      }

    } catch (error) {
      // Handle cancelled requests first (don't log these as errors)
      if (axios.isCancel(error)) {
        // Just remove any typing indicators or streaming messages, don't show error message or log
        setMessages(prevMessages =>
          prevMessages.filter(msg => msg.id !== 'typing' && !msg.isStreaming)
        );
        setCancelTokenSource(null); // Clear the cancel token
        return; // Exit early for cancelled requests
      }

      console.error('Error:', error);

      let errorMessage = 'Failed to get a response. Please try again.';
      if (error.response) {
        errorMessage = `Server error: ${error.response.status}`;
        console.error('Server error details:', error.response.data);
      } else if (error.request) {
        errorMessage = 'No response from server. Please check your internet connection.';
      } else {
        errorMessage = 'An error occurred while sending the message.';
      }

      // Show error but don't use Alert.alert which can freeze UI
      console.warn('User-friendly error:', errorMessage);

      // Remove any streaming/typing indicators
      setMessages(prevMessages =>
        prevMessages.filter(msg => msg.id !== 'typing' && !msg.isStreaming)
      );

      const errorBotResponse = {
        id: uuidv4(),
        text: `?? ${errorMessage}\n\nPlease try again or contact support if the problem persists.`,
        sender: 'bot',
        type: 'system', // mark for exclusion from future conversation_history
      };

      // Add error message safely
      try {
        setMessages(prev => [...prev, errorBotResponse]);
        scheduleScrollToBottom(1); // Schedule scroll for error message
      } catch (stateError) {
        console.error('Error updating messages state:', stateError);
      }
    } finally {
      // Always cleanup, even if there are errors
      try {
        setIsLoading(false);
        setIsBotTyping(false);
        setIsGenerating(false);
        // Don't clear cancelTokenSource here - let response handlers manage their own tokens
      } catch (cleanupError) {
        console.error('Error during cleanup:', cleanupError);
      }
    }
  }, [
    messages,
    activeSessionId,
    isWaitingForRecordingTitle,
    isWaitingForImageTitle,
    isWaitingForDocumentTitle,
    isWaitingForPhotoTitle,
    pendingImage,
    pendingDocument,
    pendingPhoto,
    storageManager,
    prepareChatHistory,
    getSessionSummary,
    scheduleScrollToBottom,
    createNewSession,
    setActiveScreen,
    handleStreamingResponse,
    handleRegularResponse,
    getUserEmail,
    trackUsageAfterAction,
    setActiveScreen,
    prepareAttachmentsForQuery
  ]);

  // Handle sending message from input - async chat allows multiple sends
  const handleSendMessage = useCallback(() => {
    console.log('?? handleSendMessage called with:', {
      inputText: inputText.trim(),
      attachmentCount: questionAttachments.length
    });

    const textToSend = inputText.trim();

    // Allow sending if there's text OR attachments
    if (textToSend || questionAttachments.length > 0) {
      console.log('?? Sending message:', textToSend || '[with attachments only]');
      setInputText('');
      // Direct call to sendMessage - no ref needed
      sendMessage(textToSend);
    } else {
      console.log('?? No text or attachments to send');
    }
  }, [inputText, questionAttachments.length, sendMessage]);

  // Create a direct send handler - async chat, no restrictions



  // Simplified direct send handler - no restrictions for async chat
  const handleSimpleSend = useCallback(() => {
    console.log('?? Simple send clicked');
    const textToSend = inputText.trim();
    console.log('?? Input text:', textToSend);
    console.log('?? Attachments:', questionAttachments.length);

    // Allow sending if there's text OR attachments
    if (!textToSend && questionAttachments.length === 0) {
      console.log('?? No text or attachments to send');
      return;
    }

    console.log('?? Sending message asynchronously');
    setInputText('');

    // Direct call to sendMessage - let it handle async processing
    sendMessage(textToSend);
  }, [inputText, sendMessage, questionAttachments.length]);

  // Deep Research State Reset Function
  const resetDeepResearchState = useCallback(() => {
    console.log('?? Resetting Deep Research state for new research');

    // Clear any pending polling timeout
    if (pollingTimeoutRef.current) {
      clearTimeout(pollingTimeoutRef.current);
      pollingTimeoutRef.current = null;
      console.log('?? Cleared pending polling timeout');
    }

    setDeepResearchState({
      isResearching: false,
      stage: 'ready',
      currentStep: 1,
      totalSteps: 5,
      substeps: [],
      progress: 0,
      query: '',
      findings: [],
      questions: [],
      isAwaitingUserInput: false,
      researchId: null,
      citations: [],
      researchMetadata: {}
    });
    setShowDeepResearchPanel(false);
  }, []);

  // Deep Research Handlers removed - LLM orchestrator now decides automatically

  // AI Model Toggle Handler
  const handleModelOnlyToggle = useCallback((forcedValue) => {
    // Don't change query sources until they're loaded from storage
    if (!querySourcesLoaded) {
      console.log('?? [MODEL_ONLY] Skipping toggle - query sources not loaded yet');
      return;
    }

    setIsModelOnlyMode(prevState => {
      const nextState = typeof forcedValue === 'boolean' ? forcedValue : !prevState;

      if (nextState === prevState) {
        return prevState;
      }

      if (nextState) {
        // AI-only mode disables enterprise/personal retrieval
        setUseUploadedData(false);
        setUseEnterprise(false);
        console.log('?? AI Model mode enabled - all retrieval features disabled');
      } else {
        console.log('?? AI Model mode disabled - retrieval features available for manual toggle');
      }

      return nextState;
    });
  }, [setUseUploadedData, setUseEnterprise, querySourcesLoaded]);

  // Internet Search Toggle Handler with Warning
  const handleInternetSearchToggle = useCallback((newValue) => {
    if (!newValue && enableInternetSearch) {
      // User is trying to turn OFF internet search - show warning
      setShowInternetWarningModal(true);
    } else {
      // User is turning ON internet search - allow directly
      setEnableInternetSearch(newValue);
    }
  }, [enableInternetSearch]);

  // Handler for confirming internet search disable
  const handleConfirmDisableInternet = useCallback(() => {
    setShowInternetWarningModal(false);
    setEnableInternetSearch(false);
  }, []);

  // Handler for canceling internet search disable
  const handleCancelDisableInternet = useCallback(() => {
    setShowInternetWarningModal(false);
    // Keep internet search enabled
  }, []);


  // Handle mindmap node click

  // Handle diagram generation
  const handleGenerateDiagram = useCallback(async (queryOrDocuments) => {
    // Support both old (query string), single document, and array of documents
    let selectedDocuments = null;
    let query = '';

    if (Array.isArray(queryOrDocuments)) {
      // Array of documents
      selectedDocuments = queryOrDocuments;
      query = diagramQuery || 'Generate comprehensive diagram';
    } else if (typeof queryOrDocuments === 'object' && queryOrDocuments !== null) {
      // Single document object
      selectedDocuments = [queryOrDocuments];
      query = diagramQuery || 'Generate comprehensive diagram';
    } else if (typeof queryOrDocuments === 'string') {
      // Query string provided - use stored selected documents
      query = queryOrDocuments;
      selectedDocuments = selectedDiagramDocuments ? (Array.isArray(selectedDiagramDocuments) ? selectedDiagramDocuments : [selectedDiagramDocuments]) : null;
    } else {
      // No query or documents provided - should not auto-generate
      console.warn('?? handleGenerateDiagram called without query or documents');
      return;
    }

    // Validate query
    if (!query || query.trim().length < 5) {
      console.warn('?? Query too short or missing');
      setDiagramError('Please enter a query (minimum 5 characters)');
      return;
    }

    console.log('?? Generate diagram requested', selectedDocuments ? `for ${selectedDocuments.length} document(s)` : `with query: ${query}`);

    // Get folder ID if available (optional now)
    const nonDefaultFolders = selectedFolderIds.filter(id => id !== 'documents');
    const folderId = nonDefaultFolders.length > 0 ? nonDefaultFolders[0] : null;
    const folder = folderId ? folders.find(f => f.id === folderId) : null;

    if (folderId) {
      console.log('?? Generating diagram for folder:', folder?.name || folderId);
    } else {
      console.log('?? Generating diagram from all user documents');
    }

    setIsDiagramLoading(true);
    setDiagramError(null);

    try {
      // Auto-enhance query with color instruction for first-time diagram generation
      let enhancedQuery = query;
      if (diagramData.length === 0) {
        enhancedQuery = `${query}. Use beautiful, vibrant colors with good contrast to make the diagram visually appealing. IMPORTANT: Ensure all text in nodes and labels uses black color (#000000) for maximum readability. Make sure all lines and connections are clearly visible with sufficient thickness and contrast.`;
        console.log('?? Auto-enhanced query with beautiful colors and black text instruction');
      }

      // Build API URL with user_id (required), query (required), and optional folder_id
      let apiUrl = `${API_CONFIG.CITRA_SERVICE_URL}/api/diagram/generate?user_id=${encodeURIComponent(currentUserEmail)}&query=${encodeURIComponent(enhancedQuery)}`;
      if (folderId) {
        apiUrl += `&folder_id=${folderId}`;
      }
      if (selectedDocuments && selectedDocuments.length > 0) {
        const documentIds = selectedDocuments.map(doc => doc.document_id).join(',');
        apiUrl += `&document_ids=${encodeURIComponent(documentIds)}`;
        console.log('?? Filtering by document_ids:', documentIds);
      }

      const response = await authService.authenticatedFetch(apiUrl, {
        method: 'POST'
      });

      if (!response.ok) {
        throw new Error(`Failed to generate diagram: ${response.status}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error(data.error || 'Failed to generate diagram');
      }

      console.log('? Diagram generation successful:', {
        diagramType: data.diagram_type,
        totalDocuments: data.total_documents,
        documentCount: data.diagram?.document_count
      });

      // New API returns single unified diagram object
      // Wrap in array for backward compatibility with DiagramPanel
      const diagramArray = data.diagram ? [data.diagram] : [];
      setDiagramData(diagramArray);
      setDiagramType(data.diagram_type);
      setDiagramError(null);

    } catch (error) {
      console.error('? Failed to generate diagram:', error);
      setDiagramError(error.message || 'Failed to generate diagram');
    } finally {
      setIsDiagramLoading(false);
    }
  }, [selectedFolderIds, folders, currentUserEmail, diagramQuery, selectedDiagramDocuments]);

  // Handle Generate Draft
  // Handle "How to use Citra AI" button click
  const handleShowHowToUse = useCallback(() => {
    console.log('?? Opening How to Use Citra AI modal');
    setShowHowToUseModal(true);
  }, []);

  // SaaS Connections handler removed � SaaS analytics moved to Citra Agent desktop


  useEffect(() => {
    // Don't auto-apply defaults until query sources are loaded from storage
    if (!querySourcesLoaded) return;
    // A folder selection persisted from a build that still had the personal
    // Data Store must NOT silently switch the vault back on.
    if (!PERSONAL_VAULT_ENABLED) return;
    const userDisabledVault = loadedQuerySourcesRef.current?.useVault === false;

    const hasWorkspaceSelection = selectedFolderIds.some(id => id !== 'documents');

    // Automatically disable AI Only mode when a personal folder is selected
    if (hasWorkspaceSelection && !hasAutoAppliedWorkspaceDefaultsRef.current) {
      if (!useUploadedData && !userDisabledVault) {
        console.log('?? [WORKSPACE] Auto-enabling Vault due to folder selection');
        setUseUploadedData(true);
      }
      if (isModelOnlyMode) {
        handleModelOnlyToggle(false);
        console.log('?? AI Only mode automatically disabled due to personal folder selection');
      }
      hasAutoAppliedWorkspaceDefaultsRef.current = true;
    } else if (!hasWorkspaceSelection) {
      hasAutoAppliedWorkspaceDefaultsRef.current = false;
    }
  }, [selectedFolderIds, useUploadedData, setUseUploadedData, isModelOnlyMode, handleModelOnlyToggle, querySourcesLoaded]);

  useEffect(() => {
    const currentEntityId = selectedEntityDetails?.entity_id ?? null;

    if (currentEntityId && lastAutoAppliedEntityIdRef.current !== currentEntityId) {
      if (isModelOnlyMode) {
        handleModelOnlyToggle(false);
      }
      if (!useEnterprise) {
        setUseEnterprise(true);
      }
      lastAutoAppliedEntityIdRef.current = currentEntityId;
    } else if (!currentEntityId) {
      lastAutoAppliedEntityIdRef.current = null;
    }
  }, [selectedEntityDetails, isModelOnlyMode, handleModelOnlyToggle, useEnterprise, setUseEnterprise]);


  const handleDeepResearchUserResponse = useCallback(async (questionId, response) => {
    console.log('?? User response to deep research question:', { questionId, response });

    try {
      // Update research state to show we're processing the response
      setDeepResearchState(prev => ({
        ...prev,
        isAwaitingUserInput: false,
        stage: 'processing_response',
        progress: Math.min(prev.progress + 10, 95)
      }));

      // Get user email with error handling
      let userEmail = 'anonymous';
      try {
        userEmail = await getUserEmail();
      } catch (error) {
        console.log('?? User not authenticated for deep research, using anonymous:', error.message);
      }

      // Use the dedicated deep-research-response endpoint with simplified payload
      const continueData = {
        research_id: deepResearchState.researchId,
        user_response: response, // The user's clarification response
        question_id: questionId,
        user_id: userEmail
      };

      console.log('?? Sending deep research clarification via dedicated endpoint:', continueData);

      // Use the dedicated deep-research-response endpoint for clarification responses
      const DEEP_RESEARCH_RESPONSE_URL = `${API_CONFIG.CITRA_SERVICE_URL}/deep-research-response`;
      const response_data = await authService.authenticatedFetch(DEEP_RESEARCH_RESPONSE_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify(continueData),
        signal: AbortSignal.timeout(parseInt(API_CONFIG.API_TIMEOUT) || 300000)
      });

      // Handle the response from the deep-research-response endpoint
      const responseData = await response_data.json();
      console.log('?? Deep research clarification response received:', responseData);

      if (responseData.status === 'completed') {
        // Research completed successfully
        setDeepResearchState(prev => ({
          ...prev,
          isResearching: false,
          stage: 'completed',
          progress: 100,
          isAwaitingUserInput: false,
          finalAnswer: responseData.final_answer,
          // No separate citations - they're included naturally in the final_answer
          researchId: responseData.research_id || prev.researchId
        }));

        // Add the final answer to chat
        const finalMessage = {
          id: `bot-${Date.now()}`,
          text: responseData.final_answer || 'Research completed.',
          sender: 'bot',
          timestamp: new Date(),
          shouldAnimate: false
        };

        setMessages(prev => {
          const newMessages = [...prev, finalMessage];
          storageManager.storeMessagePairs(newMessages, activeSessionId);
          return newMessages;
        });

        setIsPendingClarificationResponse(false);
        console.log('?? Deep research completed after clarification');
      }
      else if (responseData.stage === 'questioning' && responseData.questions) {
        // Research needs more clarification
        setDeepResearchState(prev => ({
          ...prev,
          isAwaitingUserInput: true,
          stage: 'questioning',
          progress: Math.min(prev.progress + 10, 90),
          questions: responseData.questions || [],
          isResearching: false,
          researchId: responseData.research_id || prev.researchId,
          findings: responseData.findings || prev.findings,
          researchMetadata: responseData.research_metadata || prev.researchMetadata
        }));

        // Keep the clarification response flag set for the next round
        setIsPendingClarificationResponse(true);

        // Add new clarification questions to chat
        const questionTexts = responseData.questions.map((q, i) => {
          if (typeof q === 'string') {
            return `${i + 1}. ${q}`;
          } else if (q.text || q.question) {
            return `${i + 1}. ${q.text || q.question}`;
          } else {
            return `${i + 1}. ${q}`;
          }
        });

        const clarificationMessage = {
          id: `clarification-${Date.now()}-${Math.random()}`,
          text: `I need some additional clarification:\n\n${questionTexts.join('\n\n')}\n\nPlease provide your responses in the chat below.`,
          sender: 'assistant',
          timestamp: new Date(),
          type: 'clarification',
          deepResearchId: responseData.research_id,
          questions: responseData.questions,
          questionIds: responseData.questions.map(q => q.id || q.key || 'general'),
          isStreaming: false,
          shouldAnimate: false
        };

        setMessages(prev => {
          const newMessages = [...prev, clarificationMessage];
          storageManager.storeMessagePairs(newMessages, activeSessionId);
          return newMessages;
        });

        setIsPendingClarificationResponse(true);
        console.log('?? Deep research awaiting additional clarification');
      }
      else if (responseData.stage === 'error' || responseData.error) {
        // Research encountered an error
        setDeepResearchState(prev => ({
          ...prev,
          isResearching: false,
          isAwaitingUserInput: false,
          stage: 'error',
          error: responseData.error || 'Unknown error occurred'
        }));

        const errorMessage = {
          id: `bot-${continueData.message_pair_id}-error-${Date.now()}`,
          text: responseData.error || 'Deep research encountered an error. Please try again.',
          sender: 'bot',
          timestamp: new Date(),
          shouldAnimate: false
        };

        setMessages(prev => {
          const newMessages = [...prev, errorMessage];
          storageManager.storeMessagePairs(newMessages, activeSessionId);
          return newMessages;
        });

        setIsPendingClarificationResponse(false);
        console.log('?? Deep research encountered an error');
      }
      else {
        // Default case - research might be continuing
        setDeepResearchState(prev => ({
          ...prev,
          isResearching: true,
          isAwaitingUserInput: false,
          stage: 'processing',
          progress: Math.min(prev.progress + 15, 95),
          researchId: responseData.research_id || prev.researchId
        }));
        setIsPendingClarificationResponse(false);
        console.log('?? Deep research continuing after clarification response');
      }

    } catch (error) {
      console.error('? Error in handleDeepResearchUserResponse:', error);

      setDeepResearchState(prev => ({
        ...prev,
        isResearching: false,
        isAwaitingUserInput: false,
        stage: 'error',
        error: 'Failed to process your response. Please try again.'
      }));

      // Add error message to chat
      const errorMessage = {
        id: `error-${Date.now()}`,
        text: 'Sorry, there was an error processing your response. Please try again.',
        sender: 'assistant',
        timestamp: new Date(),
        shouldAnimate: false
      };

      setMessages(prev => {
        const newMessages = [...prev, errorMessage];
        storageManager.storeMessagePairs(newMessages, activeSessionId);
        return newMessages;
      });

      setIsPendingClarificationResponse(false);
    } finally {
      setIsLoading(false);
    }
  }, [deepResearchState.researchId, activeSessionId, useUploadedData, storageManager]);


  // Deep Research Panel Management

  // Poll for deep research updates when research is continuing
  // Ref to track polling timeout for cleanup
  const pollingTimeoutRef = useRef(null);

  // Cleanup polling timeout on component unmount or research ID change
  useEffect(() => {
    return () => {
      if (pollingTimeoutRef.current) {
        clearTimeout(pollingTimeoutRef.current);
        pollingTimeoutRef.current = null;
        console.log('?? Cleaned up polling timeout on component unmount/change');
      }
    };
  }, [deepResearchState.researchId]);

  const pollForDeepResearchUpdates = useCallback(async () => {
    if (!deepResearchState.researchId || !deepResearchState.isResearching) {
      return;
    }

    try {
      console.log('?? Polling for deep research updates:', deepResearchState.researchId);

      // Get user email for the API call
      const userEmail = await getUserEmail();

      // Check authentication before making request
      const token = await authService.getToken();
      if (!token) {
        console.error('?? No auth token available for progress polling');
        // Clear any pending timeout and stop polling on auth failure
        if (pollingTimeoutRef.current) {
          clearTimeout(pollingTimeoutRef.current);
          pollingTimeoutRef.current = null;
        }
        setDeepResearchState(prev => ({
          ...prev,
          isResearching: false,
          stage: 'error',
          error: 'Authentication lost. Please refresh and try again.'
        }));
        return;
      }

      console.log('?? Auth token exists for polling:', !!token);

      // Get auth headers manually as fallback
      const authHeaders = await authService.getAuthHeaders({
        'Content-Type': 'application/json',
      });
      console.log('?? Auth headers prepared:', Object.keys(authHeaders));

      // BREAKING CHANGE: Migrated from /deep-research-status/ to /research/progress/
      // The old endpoint relied on persistent storage that was removed for performance
      const statusResponse = await authService.authenticatedFetch(
        `${API_CONFIG.CITRA_SERVICE_URL}/research/progress/${deepResearchState.researchId}`,
        {
          method: 'GET',
          headers: authHeaders
        }
      );

      if (statusResponse.ok) {
        const statusData = await statusResponse.json();
        console.log('?? [REDIS_PROGRESS] Retrieved progress data:', statusData);

        // Handle new Redis-based response structure (BREAKING CHANGE)
        if (statusData.status === 'completed') {
          // Clear any pending polling timeout since research is completed
          if (pollingTimeoutRef.current) {
            clearTimeout(pollingTimeoutRef.current);
            pollingTimeoutRef.current = null;
          }
          setDeepResearchState(prev => ({
            ...prev,
            isResearching: false,
            stage: 'complete',
            progress: 100,
            completed: true,
            // Note: final_answer comes from the main query response, not progress endpoint
            researchMetadata: statusData.metadata || {}
          }));
          console.log('?? Deep research completed (from Redis polling)');
        } else if (statusData.status === 'awaiting_input') {
          // Clear any pending polling timeout since polling stops for user input
          if (pollingTimeoutRef.current) {
            clearTimeout(pollingTimeoutRef.current);
            pollingTimeoutRef.current = null;
          }
          setDeepResearchState(prev => ({
            ...prev,
            isResearching: false,
            isAwaitingUserInput: true,
            stage: statusData.stage || 'questioning',
            currentQuestions: statusData.questions || [],
            progress: statusData.progress || prev.progress,
            researchMetadata: statusData.metadata || {}
          }));
          console.log('?? Deep research awaiting input (from Redis polling)');
        } else if (statusData.status === 'error') {
          // Clear any pending polling timeout since research has errored
          if (pollingTimeoutRef.current) {
            clearTimeout(pollingTimeoutRef.current);
            pollingTimeoutRef.current = null;
          }
          setDeepResearchState(prev => ({
            ...prev,
            isResearching: false,
            stage: 'error',
            error: statusData.message || 'Unknown error occurred'
          }));
          console.log('?? Deep research error (from Redis polling)');
        } else if (statusData.status === 'not_found') {
          console.log('?? Research not found in Redis, may have completed or expired');
          // Don't change state if research not found - might be completed
        } else {
          // Still processing, update progress with enhanced metadata
          setDeepResearchState(prev => ({
            ...prev,
            progress: statusData.progress || prev.progress,
            stage: statusData.stage || prev.stage,
            researchMetadata: {
              ...prev.researchMetadata,
              ...statusData.metadata || {},
              lastUpdate: statusData.updated_at,
              message: statusData.message
            }
          }));
          console.log('?? [REDIS_PROGRESS] Updated progress:', {
            stage: statusData.stage,
            progress: statusData.progress,
            message: statusData.message
          });

          // Continue polling after a delay
          pollingTimeoutRef.current = setTimeout(() => {
            pollForDeepResearchUpdates();
          }, 5000); // Polling interval changed to 5 seconds
        }
      } else if (statusResponse.status === 410) {
        // Handle breaking change - old endpoint removed
        const errorData = await statusResponse.json();
        console.error('?? [BREAKING_CHANGE] Old endpoint removed:', errorData);
        // Clear any pending timeout and stop polling
        if (pollingTimeoutRef.current) {
          clearTimeout(pollingTimeoutRef.current);
          pollingTimeoutRef.current = null;
        }
        setDeepResearchState(prev => ({
          ...prev,
          isResearching: false,
          stage: 'error',
          error: 'Progress tracking system updated. Please refresh the page.'
        }));
      } else if (statusResponse.status === 401) {
        // Handle authentication error
        console.error('?? [AUTH_ERROR] Unauthorized access to progress endpoint');
        console.error('?? Token exists:', !!(await authService.getToken()));
        console.error('?? User authenticated:', await authService.isAuthenticated());
        // Clear any pending timeout and stop polling on auth error
        if (pollingTimeoutRef.current) {
          clearTimeout(pollingTimeoutRef.current);
          pollingTimeoutRef.current = null;
        }
        setDeepResearchState(prev => ({
          ...prev,
          isResearching: false,
          stage: 'error',
          error: 'Authentication required. Please refresh the page and log in again.'
        }));
      } else {
        console.error('?? Progress polling failed with status:', statusResponse.status);
        const errorText = await statusResponse.text();
        console.error('?? Error response:', errorText);
        // Clear any pending timeout and stop polling on other errors
        if (pollingTimeoutRef.current) {
          clearTimeout(pollingTimeoutRef.current);
          pollingTimeoutRef.current = null;
        }
        setDeepResearchState(prev => ({
          ...prev,
          isResearching: false,
          stage: 'error',
          error: `Polling failed: ${statusResponse.status}. Please try again.`
        }));
      }
    } catch (error) {
      console.error('?? Error polling for deep research updates:', error);
      // Clear any pending timeout and stop polling on exception
      if (pollingTimeoutRef.current) {
        clearTimeout(pollingTimeoutRef.current);
        pollingTimeoutRef.current = null;
      }
      // Set error state and stop polling on exception
      setDeepResearchState(prev => ({
        ...prev,
        isResearching: false,
        stage: 'error',
        error: `Polling error: ${error.message}. Please try again.`
      }));
    }
  }, [deepResearchState.researchId, deepResearchState.isResearching, API_CONFIG.CITRA_SERVICE_URL, getUserEmail]);

  // Handle launching the current report modal

  // Handle Deep Research progress updates from server

  // --- Folder Management Functions ---

  // Load folders from backend with auto-setup for new users
  // MOVED TO WORKSPACE CONTEXT - See contexts/WorkspaceContext.js for fetchFolders implementation
  /*
  const loadFolders = useCallback(async () => {
    console.log('?? [DEBUG] loadFolders called, isAuthenticated:', isAuthenticated);
    setIsFoldersLoading(true);
    
    try {
      // Try to get user email, but don't fail if not authenticated
      let userEmail = 'guest-user';
      try {
        userEmail = await getUserEmail();
        console.log('?? [DEBUG] getUserEmail result:', userEmail);
      } catch (emailError) {
        console.log('?? [DEBUG] Could not get user email, using guest:', emailError.message);
      }
      
      // Try to fetch folders from API first
      let allFolders = [];
      let apiSuccess = false;
      
      if (isAuthenticated) {
        try {
          const response = await authService.authenticatedFetch(`${API_CONFIG.CITRA_SERVICE_URL}/api/folders/list?user_id=${encodeURIComponent(userEmail)}`);
          if (response.ok) {
            const data = await response.json();
            allFolders = data.folders || [];
            apiSuccess = true;
            console.log('?? [DEBUG] Folders from API:', allFolders);
          } else {
            console.log('?? [DEBUG] Folders API not available (404), creating local system folders');
          }
        } catch (apiError) {
          console.log('?? [DEBUG] Folders API error, creating local system folders:', apiError.message);
        }
      } else {
        console.log('?? [DEBUG] User not authenticated, creating local system folders');
      }
      
      // Only create local system folders if API was not successful
      if (!apiSuccess) {
        const generalFolder = {
          id: 'general',
          name: 'General',
          color: '#6b7280',
          description: 'General documents and files (default folder)',
          isDefault: true,
          isSystem: true,
          documentCount: 0,
          created_at: new Date().toISOString(),
          lastUpdated: new Date().toISOString()
        };
        
        const meetingsFolder = {
          id: 'meetings',
          name: 'Meetings',
          color: '#10b981',
          description: 'Audio recordings and meeting videos',
          isDefault: false,
          isSystem: true,
          documentCount: 0,
          created_at: new Date().toISOString(),
          lastUpdated: new Date().toISOString()
        };
        
        const notesFolder = {
          id: 'notes',
          name: 'Notes',
          color: '#f59e0b',
          description: 'Personal notes and quick captures',
          isDefault: false,
          isSystem: true,
          documentCount: 0,
          created_at: new Date().toISOString(),
          lastUpdated: new Date().toISOString()
        };
        
        allFolders = [generalFolder, meetingsFolder, notesFolder];
        console.log('?? [DEBUG] Created local system folders:', allFolders);
      }
      
      setFolders(allFolders);
      console.log('?? [DEBUG] Folders set in state - showing', allFolders.length, 'folders');
      
    } catch (error) {
      console.error('Error in loadFolders:', error);
      // Fallback to local system folders if everything fails
      const systemFoldersOnly = [
        {
          id: 'general',
          name: 'General',
          color: '#6b7280',
          description: 'General documents and files (default folder)',
          isDefault: true,
          isSystem: true,
          documentCount: 0,
          created_at: new Date().toISOString(),
          lastUpdated: new Date().toISOString()
        },
        {
          id: 'meetings',
          name: 'Meetings',
          color: '#10b981',
          description: 'Audio recordings and meeting videos',
          isDefault: false,
          isSystem: true,
          documentCount: 0,
          created_at: new Date().toISOString(),
          lastUpdated: new Date().toISOString()
        },
        {
          id: 'notes',
          name: 'Notes',
          color: '#f59e0b',
          description: 'Personal notes and quick captures',
          isDefault: false,
          isSystem: true,
          documentCount: 0,
          created_at: new Date().toISOString(),
          lastUpdated: new Date().toISOString()
        }
      ];
      console.log('?? [DEBUG] Error occurred, setting fallback system folders:', systemFoldersOnly);
      setFolders(systemFoldersOnly);
    } finally {
      setIsFoldersLoading(false);
    }
  }, [getUserEmail, isAuthenticated]);
  */
  // END OF COMMENTED OUT loadFolders FUNCTION

  // Create new folder



  // Handle opening folder content with pagination support - Clean Architecture

  // Handle opening the currently selected vault directly (e.g., from Home Screen)
  // Load more folder content

  // Handle closing folder content

  // Helper functions for folder content display


  const formatFileSize = useCallback((bytes) => {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }, []);

  // Toggle folder selection - NOW PROVIDED BY WORKSPACE CONTEXT
  // Folder selection functionality moved to WorkspaceContext

  // Animated folder panel toggle

  // Reusable back navigation to restore ribbon-enabled chat view
  const BackToChatButton = ({ marginBottom = 12 }) => (
    <TouchableOpacity
      onPress={() => setActiveScreen('chat')}
      style={{
        alignSelf: 'flex-start',
        backgroundColor: '#2563eb',
        paddingHorizontal: 14,
        paddingVertical: 10,
        borderRadius: 8,
        marginBottom,
        flexDirection: 'row',
        alignItems: 'center',
      }}
      accessibilityRole="button"
      accessibilityLabel="Back"
    >
      <Ionicons name="arrow-back" size={18} color="#ffffff" style={{ marginRight: 6 }} />
      <Text style={{ color: '#ffffff', fontWeight: '700', fontSize: 14 }}>
        Back
      </Text>
    </TouchableOpacity>
  );

  // Reusable Home Navigation Button



  // Upload documents to specific folder
  const uploadToFolder = useCallback(async (folderId) => {
    try {
      // Use existing document picker but pass folder ID
      const result = await DocumentPicker.getDocumentAsync({
        type: '*/*',
        copyToCacheDirectory: true,
        multiple: true
      });

      if (!result.cancelled && result.assets) {
        for (const document of result.assets) {
          // Use existing upload function but with folder metadata
          await uploadDocumentToFolder(document, folderId);
        }
      }
    } catch (error) {
      console.error('Error uploading to folder:', error);
      Alert.alert('Error', 'Failed to upload documents to folder.');
    }
  }, []);

  // Modified upload function that includes folder metadata
  const uploadDocumentToFolder = useCallback(async (document, folderId = null) => {
    // ?? DETAILED LOGGING: Function entry
    console.log('?? uploadDocumentToFolder called with:', {
      documentName: document?.name,
      documentType: typeof document,
      folderId: folderId,
      folderIdType: typeof folderId,
      hasDocument: !!document,
      hasFolderId: !!folderId
    });

    const id = uuidv4();
    const documentTitle = document.name;

    // Get folder information for display
    let folderName = 'Default';
    if (folderId) {
      const folder = folders.find(f => f.id === folderId);
      folderName = folder ? folder.name : 'Unknown Folder';

      // ?? DETAILED LOGGING: Folder lookup
      console.log('?? Folder lookup result:', {
        searchingForId: folderId,
        foundFolder: folder,
        folderName: folderName,
        totalFolders: folders.length,
        allFolderIds: folders.map(f => f.id)
      });
    }

    console.log('??? DATABASE ENRICHMENT FLOW START - Uploading document to folder', {
      title: documentTitle,
      folderId: folderId || 'documents',
      folderName: folderName,
      uploadId: id
    });

    setBackgroundUploads(prev => new Map(prev.set(id, {
      id,
      title: documentTitle,
      type: document.mimeType?.startsWith('image/') ? 'image' : 'document',
      status: 'uploading',
      folderId: folderId || 'documents',
      folderName: folderName
    })));

    try {
      const formData = new FormData();

      // Platform-specific file appending
      if (Platform.OS === 'web') {
        if (document.file) {
          formData.append('file', document.file, document.name);
        } else {
          throw new Error('Web upload error: File object not found in the document asset.');
        }
      } else {
        formData.append('file', {
          uri: document.uri,
          type: document.mimeType,
          name: document.name,
        });
      }

      const userEmail = await getUserEmail();
      formData.append('user_id', userEmail);
      formData.append('topic', documentTitle);

      // Add folder metadata
      if (folderId) {
        formData.append('folder_id', folderId);
        const folder = folders.find(f => f.id === folderId);
        if (folder) {
          formData.append('folder_id', folder.name);
        }

        // ?? DETAILED LOGGING: FormData folder information
        console.log('?? FormData folder metadata added:', {
          folderId: folderId,
          folderName: folder?.name,
          folderFound: !!folder
        });
      } else {
        console.log('?? No folderId provided - will use default folder');
      }

      // Add team_id for workspace association
      if (activeTeamId) {
        formData.append('team_id', activeTeamId);
      }

      // ?? DETAILED LOGGING: Complete FormData contents
      console.log('?? Complete FormData being sent to API:', {
        user_id: await getUserEmail(),
        topic: documentTitle,
        folder_id: folderId || 'NOT_SET',
        folder_id: folderId ? folders.find(f => f.id === folderId)?.name : 'NOT_SET',
        apiEndpoint: `${API_CONFIG.DOCUMENT_URL}s`
      });

      const response = await authService.authenticatedFetch(`${API_CONFIG.DOCUMENT_URL}s`, {
        method: 'POST',
        headers: { 'Content-Type': undefined }, // Let browser set multipart/form-data boundary
        body: formData,
        signal: AbortSignal.timeout(parseInt(API_CONFIG.DOCUMENT_UPLOAD_TIMEOUT) || 1200000) // 20 minutes timeout for document uploads
      });

      if (response.ok) {
        const folderName = folderId
          ? folders.find(f => f.id === folderId)?.name || 'Unknown Folder'
          : 'Default';

        console.log('??? DATABASE ENRICHMENT FLOW COMPLETE - Document successfully stored in Milvus database', {
          title: documentTitle,
          folderId: folderId || 'documents',
          folderName: folderName,
          uploadId: id
        });

        // Show user-friendly success message with folder information
        // Note: Success messages now handled by newer upload queue system
        const uploadSuccessText = folderId
          ? `Document "${documentTitle}" uploaded to "${folderName}" folder`
          : `Document "${documentTitle}" uploaded to General folder`;

        // Update upload status to success
        setBackgroundUploads(prev => {
          const updated = new Map(prev);
          updated.set(id, {
            id,
            title: documentTitle,
            type: document.mimeType?.startsWith('image/') ? 'image' : 'document',
            status: 'success',
            folderId: folderId || 'documents',
            folderName: folderName,
            successMessage: uploadSuccessText
          });
          return updated;
        });

        // Removed: upload completion success toast (we only show folder routing toast once)

        // Update folder document count
        if (folderId) {
          setFolders(prev => prev.map(f =>
            f.id === folderId
              ? { ...f, documentCount: (f.documentCount || 0) + 1, lastUpdated: new Date().toISOString() }
              : f
          ));
        }

        // Show success toast notification instead of chat message
        // Note: Folder routing messages are now handled by the newer upload queue system
        // Removed duplicate toast to prevent confusion

        // Remove from tracking after delay
        setTimeout(() => {
          setBackgroundUploads(prev => {
            const updated = new Map(prev);
            updated.delete(id);
            return updated;
          });
        }, 5000);

      } else {
        throw new Error(`Upload failed with status ${response.status}`);
      }
    } catch (err) {
      console.error('uploadDocumentToFolder error:', err);

      setBackgroundUploads(prev => {
        const updated = new Map(prev);
        updated.set(id, {
          id,
          title: documentTitle,
          type: document.mimeType?.startsWith('image/') ? 'image' : 'document',
          status: 'failed',
          error: err.message,
          folderId: folderId
        });
        return updated;
      });

      // Skip adding error message to chat - we handle errors in ChatUploadBubble
      // const errorMessage = {
      //   id: uuidv4(),
      //   text: `? Failed to upload "${documentTitle}": ${err.message}`,
      //   sender: 'bot',
      //   timestamp: new Date(),
      //   hideActions: true,
      // };

      // setMessages(prev => {
      //   const updatedMessages = [...prev, errorMessage];
      //   storageManager.storeMessagePairs(activeSessionId, updatedMessages).catch(err => 
      //     console.error('Failed to store upload error message:', err)
      //   );
      //   return updatedMessages;
      // });
      // scheduleScrollToBottom(1);

      // Remove from tracking after delay
      setTimeout(() => {
        setBackgroundUploads(prev => {
          const updated = new Map(prev);
          updated.delete(id);
          return updated;
        });
      }, 5000);
    }
  }, [folders, getUserEmail, setMessages, storageManager, activeSessionId, scheduleScrollToBottom, setBackgroundUploads]);

  // Handle functions for folder management (aliases to the actual functions)
  const handleUploadToFolder = uploadToFolder;
  const handleFolderSelect = toggleFolderSelection;

  // Load folders on component mount - NOW HANDLED BY WORKSPACE CONTEXT
  // Folders are automatically loaded when userEmail is available in WorkspaceProvider
  // useEffect(() => {
  //   loadFolders();
  // }, [loadFolders]);

  // Web-specific send button handler - async chat, no restrictions

  const handleSwipeGesture = useCallback((event) => {
    const { translationX, velocityX, state } = event.nativeEvent;

    if (state === State.ACTIVE) {
      // During swipe, update menu position
      if (!isMenuOpen && translationX > 0) {
        const progress = Math.min(translationX / (width * 0.3), 1);
        menuAnimation.setValue(-width * 0.8 * (1 - progress));
      } else if (isMenuOpen && translationX < 0) {
        const progress = Math.min(Math.abs(translationX) / (width * 0.3), 1);
        menuAnimation.setValue(-width * 0.8 * progress);
      }
    } else if (state === State.END) {
      // Determine if menu should open or close based on swipe distance and velocity
      const shouldOpen = !isMenuOpen && (translationX > width * 0.15 || velocityX > 500);
      const shouldClose = isMenuOpen && (translationX < -width * 0.15 || velocityX < -500);

      if (shouldOpen) {
        setIsMenuOpen(true);
        Animated.timing(menuAnimation, {
          toValue: 0,
          duration: 200,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: platformOptimization.shouldUseNativeDriver,
        }).start();
      } else if (shouldClose) {
        Animated.timing(menuAnimation, {
          toValue: -width * 0.8,
          duration: 200,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: platformOptimization.shouldUseNativeDriver,
        }).start(() => setIsMenuOpen(false));
      } else {
        // Snap back to current state
        Animated.timing(menuAnimation, {
          toValue: isMenuOpen ? 0 : -width * 0.8,
          duration: 200,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: platformOptimization.shouldUseNativeDriver,
        }).start();
      }
    }
  }, [isMenuOpen, menuAnimation]);

  const handleBackdropPress = useCallback(() => {
    if (isMenuOpen) {
      toggleMenu();
    }
  }, [isMenuOpen, toggleMenu]);

  // Initialize storage on app start
  useEffect(() => {
    // Prevent re-initialization if already completed
    if (initializationCompleted.current) {
      console.log('?? [INIT] Initialization already completed, skipping...');
      return;
    }

    const initializeApp = async () => {
      const startTime = Date.now();
      console.log('?? [INIT] Starting app initialization...');

      console.log('?? [INIT] Environment:', API_CONFIG.ENVIRONMENT);
      console.log('?? [INIT] Base URL:', API_CONFIG.BASE_URL);
      console.log('?? [INIT] Build Timestamp:', API_CONFIG.BUILD_TIMESTAMP);
      console.log('?? [INIT] Cache Version:', API_CONFIG.CACHE_VERSION);
      const userEmail = isAuthenticated
        ? await getUserEmail().catch((error) => {
          console.error('? [INIT] Unable to load authenticated user email:', error);
          return null;
        })
        : null;
      console.log('?? [INIT] User Email:', userEmail || 'N/A');
      console.log('??? [INIT] Chat URL:', CHAT_URL);

      // Check for cache version updates (web only)
      if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
        const lastCacheVersion = localStorage.getItem('app_cache_version');
        if (lastCacheVersion && lastCacheVersion !== API_CONFIG.CACHE_VERSION) {
          console.log('?? [CACHE] Cache version changed, clearing localStorage');
          // Clear app-specific cache but keep user authentication
          const authData = localStorage.getItem('user_data');
          localStorage.clear();
          if (authData) {
            localStorage.setItem('user_data', authData);
          }
        }
        localStorage.setItem('app_cache_version', API_CONFIG.CACHE_VERSION);
      }

      // Set the userDeviceId immediately during initialization
      if (userEmail) {
        setUserDeviceId(userEmail);
        console.log('?? [INIT] User Device ID set to:', userEmail);
      }

      try {
        // Validate critical configuration
        if (!CHAT_URL) {
          console.error('? [INIT] Critical configuration missing!');
          console.error('? [INIT] CHAT_URL:', CHAT_URL);

          console.warn('?? [INIT] Using fallback configuration for missing values');
          setIsInitializing(false);
          initializationCompleted.current = true;
          return;
        }

        if (!userEmail) {
          console.log('? [INIT] No authenticated user yet; deferring storage initialization until login');
          setIsInitializing(false);
          initializationCompleted.current = true;
          return;
        }

        // For web platform, check if we already have data to avoid unnecessary reinitialization
        if (Platform.OS === 'web') {
          try {
            const existingData = await storageManager.getStorageInfo();
            if (existingData && (existingData.chatSessionsCount > 0 || existingData.notesCount > 0)) {
              console.log('?? [INIT] Web platform - existing data found, skipping full initialization');
              setIsInitializing(false);
              initializationCompleted.current = true;
              return;
            }
          } catch (error) {
            console.log('?? [INIT] Web platform - no existing data found, proceeding with initialization');
          }
        }

        setIsInitializing(true);

        // Step 1: Clear storage only on first initialization (not on every app resume)
        // For web apps, we don't want to clear storage every time user switches tabs
        if (Platform.OS === 'web') {
          console.log('?? [INIT] Web platform - skipping storage clear to preserve user data');
        } else {
          console.log('?? [INIT] Clearing AsyncStorage...');
          await storageManager.clearAllStorage();
          console.log('? [INIT] AsyncStorage cleared successfully');
        }

        // Step 2: Initialize storage with timeout protection
        console.log('?? [INIT] Starting storage initialization...');

        // Set a timeout for the entire initialization process
        const initTimeout = new Promise((_, reject) =>
          setTimeout(() => reject(new Error('Initialization timeout after 15 seconds')), 15000)
        );

        const initPromise = storageManager.initializeStorage();

        await Promise.race([initPromise, initTimeout]);

        const duration = Date.now() - startTime;
        console.log(`? [INIT] App initialization completed successfully in ${duration}ms`);

        // Mark initialization as completed
        initializationCompleted.current = true;

        // Store initialization flag in localStorage for web
        if (Platform.OS === 'web' && typeof window !== 'undefined') {
          localStorage.setItem('citra_ai_initialized', 'true');
        }

      } catch (error) {
        const duration = Date.now() - startTime;
        console.error(`? [INIT] App initialization failed after ${duration}ms:`, error);
        console.error('? [INIT] Error details:', {
          message: error.message,
          stack: error.stack,
          name: error.name
        });

        // Show user-friendly error but don't block the app
        Alert.alert(
          'Initialization Warning',
          'Some features may not work properly due to initialization issues. Please check your network connection.',
          [{ text: 'Continue', style: 'default' }]
        );

        // Mark initialization as completed even if failed to prevent retries
        initializationCompleted.current = true;

        // Store initialization flag in localStorage for web even on failure
        if (Platform.OS === 'web' && typeof window !== 'undefined') {
          localStorage.setItem('citra_ai_initialized', 'true');
        }
      } finally {
        console.log('?? [INIT] Setting initialization complete');
        setIsInitializing(false);
      }
    };

    // Add a backup timeout to prevent infinite loading
    const backupTimeout = setTimeout(() => {
      console.log('? [INIT] Backup timeout triggered after 20 seconds');
      setInitializationTimeout(true);

      // If still initializing after 20 seconds, force complete
      setTimeout(() => {
        if (isInitializing) {
          console.log('?? [INIT] Force completing initialization due to backup timeout');
          setIsInitializing(false);
          initializationCompleted.current = true;

          // Store initialization flag even on timeout
          if (Platform.OS === 'web' && typeof window !== 'undefined') {
            localStorage.setItem('citra_ai_initialized', 'true');
          }
        }
      }, 5000); // Give user 5 seconds to see the timeout message
    }, 20000);

    initializeApp().finally(() => {
      clearTimeout(backupTimeout);

      // Using simplified performance hooks - no complex lazy loading needed
      if (isActive) {
        console.log('?? [PERF] App initialization complete in active state');
      }
    });

    return () => {
      clearTimeout(backupTimeout);
    };
  }, [storageManager, getUserEmail, isAuthenticated]); // Removed isActive dependency

  // Fetch user personal info from RAG system via query API
  const fetchPersonalInfoFromRAG = useCallback(async () => {
    try {
      // Create session ID for the request
      const sessionId = activeSessionId || uuidv4();
      const userEmail = await getUserEmail();

      const requestData = {
        message_pair_id: uuidv4(),
        user_id: userEmail,
        chat_session_id: sessionId,
        query: "What personal information do you know about me? Please provide a comprehensive summary of my personal details, preferences, background, and any other relevant information you have learned about me.",
        chats: [],
        user_info: "", // Empty since we're fetching it
        ai_model: "nano",
        // Add Query Enhancement flags (default to false for personal info fetch)
        use_personal_data: false
      };

      const response = await authService.authenticatedFetch(QUERY_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify(requestData),
        signal: AbortSignal.timeout(parseInt(API_CONFIG.API_TIMEOUT) || 300000) // Use configured API timeout
      });

      if (response.ok) {
        const data = await response.json();
        if (data.response) {
          const personalInfo = data.response;
          return personalInfo;
        } else {
          return "No personal information found. Start chatting to build your personal profile!";
        }
      } else {
        return "No personal information found. Start chatting to build your personal profile!";
      }
    } catch (error) {
      console.error('? [PERSONAL_INFO] Error fetching personal info from RAG system:', error);
      if (error.code === 'ECONNABORTED') {
        throw new Error('Request timed out. Please check your connection and try again.');
      } else if (error.response?.status >= 500) {
        throw new Error('Server error. Please try again later.');
      } else {
        throw new Error('Failed to fetch personal information. Please try again.');
      }
    }
  }, [activeSessionId, getUserEmail]);

  const handleMessageAnimationComplete = useCallback((messageId) => {
    setMessages(msgs =>
      msgs.map(m =>
        m.id === messageId ? { ...m, shouldAnimate: false } : m
      )
    );
    if (animationCompleteRef.current) {
      animationCompleteRef.current();
      animationCompleteRef.current = null;
    }
  }, []);


  const showAlert = (title, message, buttons = [{ text: 'OK', style: 'default' }], type = 'info') => {
    setModernAlertConfig({
      title,
      message,
      type,
      buttons: buttons.map(button => ({
        ...button,
        onPress: () => {
          setShowModernAlert(false);
          if (button.onPress) {
            button.onPress();
          }
        }
      }))
    });
    setShowModernAlert(true);
  };



  const confirmLogout = () => {
    Alert.alert(
      'Log Out?',
      'Are you sure you want to log out?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Log Out',
          style: 'destructive',
          onPress: async () => {
            // clear stored user + referral cache
            await clearInvalidUserData();
            setIsAuthenticated(false);
            // reset chat state
            setActiveSessionId(null);
            setMessages([
              { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' }
            ]);
            // show appropriate screen based on platform
            if (Platform.OS === 'android') {
              // Skip signup for Android, go straight to chat as guest
              setIsAuthenticated(true);
              setActiveScreen('chat');
            } else {
              setActiveScreen('signup');
            }
          }
        }
      ]
    );
  };

  const renderSessionItem = useCallback(
    ({ item, index }) => (
      <View>
        {index > 0 && <View style={[styles.separator, { backgroundColor: theme.borderColor }]} />}
        <TouchableOpacity
          style={styles.historyItem}
          onPress={async () => {
            console.log('?? [HISTORY_DEBUG] Loading chat from history');
            console.log('?? [HISTORY_DEBUG] Session item.id:', item.id);
            console.log('?? [HISTORY_DEBUG] Current activeSessionId before:', activeSessionId);

            // Set active session and switch to chat screen first
            setActiveSessionId(item.id);
            setActiveScreen('chat');

            // Small delay before showing transition to prevent flash on fast loads
            const transitionTimer = setTimeout(() => {
              setIsTransitioningChat(true);
            }, 50);

            // Load messages in background with smooth transition
            await loadChatMessages(item.id);

            // Clear the timer in case messages loaded before delay
            clearTimeout(transitionTimer);
          }}>
          <HistoryLogo theme={theme} />
          <View style={{ flex: 1 }}>
            <Text style={[styles.historyItemText, { color: theme.text }]} numberOfLines={1}>
              {item.title}
            </Text>
            {item.summary && (
              <Text style={[styles.historyItemSummary, { color: theme.placeholderText }]} numberOfLines={2}>
                {item.summary}
              </Text>
            )}
            <Text style={[styles.historyItemTimestamp, { color: theme.placeholderText }]}>
              {new Date(item.timestamp).toLocaleString()}
            </Text>
          </View>
        </TouchableOpacity>
      </View>
    ),
    [theme, setActiveSessionId, setActiveScreen, loadChatMessages, activeSessionId]
  );








  const renderHeader = useCallback(
    (title, leftIcon, rightIcon, leftAction, rightAction, onOptionsPress, rightComponent) => (
      <View style={[styles.header, { borderBottomWidth: 0 }]}>
        <TouchableOpacity onPress={leftAction} style={styles.headerButton}>
          <Ionicons name={leftIcon} size={24} color={theme.text} />
        </TouchableOpacity>

        <View style={styles.headerTitleContainer}>
          <Text style={[styles.headerText, { color: theme.text }]}>
            {title}
          </Text>
          {onOptionsPress && (
            <TouchableOpacity
              onPress={onOptionsPress}
              style={[styles.headerButton, { marginLeft: 8 }]}
            >
              <Ionicons name="ellipsis-vertical" size={24} color={theme.text} />
            </TouchableOpacity>
          )}
        </View>

        {/* Remove top-bar theme toggle; theme control lives in Customize UI ribbon tab */}
        {rightComponent ? (
          rightComponent
        ) : !rightIcon || rightIcon === 'theme-toggle' ? (
          <View style={{ width: 24 }} />
        ) : (
          <TouchableOpacity onPress={rightAction} style={styles.headerButton}>
            <Ionicons name={rightIcon} size={24} color={theme.text} />
          </TouchableOpacity>
        )}
      </View>
    ),
    [theme]
  );

  const renderEnterpriseSearch = useCallback(() => (
    <View style={{
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      backgroundColor: selectedEntityDetails ? (theme.isDark ? '#2a4d3a' : '#e8f5e8') : theme.inputBackground,
      borderColor: selectedEntityDetails ? '#4CAF50' : theme.borderColor,
      borderWidth: selectedEntityDetails ? 2 : 1,
      borderRadius: 8,
      paddingHorizontal: 10,
      paddingVertical: 6,
      minWidth: 320,
      position: 'relative',
      overflow: 'visible',
      zIndex: 20001
    }}>
      <Ionicons
        name={selectedEntityDetails ? "checkmark-circle" : "search"}
        size={16}
        color={selectedEntityDetails ? '#4CAF50' : theme.text}
      />
      <TextInput
        style={{ color: theme.text, flex: 1 }}
        placeholder={selectedEntityDetails ? `${UI_TEXT.entitySpecificTitle} selected` : `Link ${UI_TEXT.entitySpecificTitle}`}
        placeholderTextColor={selectedEntityDetails ? '#4CAF50' : theme.placeholderText}
        value={enterpriseSearchText}
        editable={!selectedEntityDetails}
        onChangeText={(text) => {
          if (selectedEntityDetails) return; // Don't allow editing when entity is selected

          setEnterpriseSearchText(text);
          setShowEntitySuggestions(true);
          // Debounced suggestions fetch
          if (entitySearchTimeoutRef.current) {
            clearTimeout(entitySearchTimeoutRef.current);
          }
          entitySearchTimeoutRef.current = setTimeout(async () => {
            try {
              const q = text.trim();
              if (!q || q.length < 2) {
                setEntitySuggestions([]);
                setIsEntitySearching(false);
                return;
              }
              setIsEntitySearching(true);
              const params = new URLSearchParams({ query: q, limit: '10' });
              const url = `${API_CONFIG.CITRA_SERVICE_URL}/v2/enterprise-entities/search?${params.toString()}`;
              const resp = await authService.authenticatedFetch(url, {
                headers: { 'Accept': 'application/json' }
              });
              if (resp.ok) {
                const data = await resp.json();
                setEntitySuggestions(data.entities || []);
              } else {
                setEntitySuggestions([]);
              }
            } catch (e) {
              console.warn('Entity suggestions error:', e);
              setEntitySuggestions([]);
            } finally {
              setIsEntitySearching(false);
            }
          }, 300);
        }}
        onFocus={() => !selectedEntityDetails && setShowEntitySuggestions(true)}
        onBlur={() => {
          setTimeout(() => {
            if (!isSelectingEntityRef.current) {
              setShowEntitySuggestions(false);
            }
          }, 150);
        }}
      />
      {isEntitySearching && !selectedEntityDetails && (
        <ActivityIndicator size="small" color={theme.placeholderText} />
      )}
      {enterpriseSearchText?.length > 0 && (
        <TouchableOpacity
          onPress={() => {
            setEnterpriseSearchText('');
            setEnterpriseEntityId('');
            setSelectedEntityDetails(null);
            setEntitySuggestions([]);
          }}
          style={{
            backgroundColor: selectedEntityDetails ? '#f44336' : theme.inputBackground,
            borderRadius: 12,
            padding: 2
          }}
        >
          <Ionicons
            name="close-circle"
            size={18}
            color={selectedEntityDetails ? 'white' : theme.placeholderText}
          />
        </TouchableOpacity>
      )}
      {showEntitySuggestions && entitySuggestions.length > 0 && (
        <View className="entity-suggestions-overlay" style={{
          position: 'absolute',
          top: '100%',
          left: 0,
          right: 0,
          backgroundColor: theme.background,
          borderColor: theme.borderColor,
          borderWidth: 1,
          borderTopWidth: 0,
          borderBottomLeftRadius: 8,
          borderBottomRightRadius: 8,
          maxHeight: 280,
          zIndex: 30002,
          elevation: 10,
          overflow: 'hidden',
          shadowColor: '#000',
          shadowOffset: { width: 0, height: 2 },
          shadowOpacity: 0.25,
          shadowRadius: 3.84,
        }}>
          <ScrollView keyboardShouldPersistTaps="handled">
            {entitySuggestions.map((ent, idx) => {
              return (
                <TouchableOpacity
                  key={ent.entity_id || `${idx}`}
                  onPressIn={() => {
                    isSelectingEntityRef.current = true;
                  }}
                  onPress={() => {
                    setEnterpriseEntityId(ent.entity_id);
                    if (isModelOnlyMode) {
                      handleModelOnlyToggle(false);
                    }
                    if (!useEnterprise) {
                      setUseEnterprise(true);
                    }
                    if (setEnterpriseEntityName) {
                      setEnterpriseEntityName(ent.entity_name || '');
                    }
                    if (setEnterpriseEntityType) {
                      setEnterpriseEntityType(ent.entity_type || '');
                    }
                    setEnterpriseSearchText(`${ent.entity_name} (${ent.entity_id})`);
                    setSelectedEntityDetails({
                      entity_name: ent.entity_name,
                      entity_id: ent.entity_id,
                      entity_type: ent.entity_type,
                      org: ent.org
                    });
                    setShowEntitySuggestions(false);
                    isSelectingEntityRef.current = false;
                  }}
                  onPressOut={() => {
                    setTimeout(() => {
                      isSelectingEntityRef.current = false;
                    }, 0);
                  }}
                  style={{ paddingVertical: 8, paddingHorizontal: 10, borderBottomWidth: 1, borderBottomColor: theme.borderLight, backgroundColor: theme.background }}
                >
                  <Text style={{ color: theme.text, fontWeight: '600' }}>{ent.entity_name}</Text>
                  <Text style={{ color: theme.isDark ? theme.text : theme.textSecondary, fontSize: 12 }}>{ent.entity_type} � {ent.entity_id}</Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </View>
      )}
    </View>
  ), [
    selectedEntityDetails,
    theme.isDark,
    theme.inputBackground,
    theme.borderColor,
    theme.text,
    theme.placeholderText,
    enterpriseSearchText,
    showEntitySuggestions,
    entitySuggestions,
    isEntitySearching,
    theme.borderLight,
    theme.background,
    theme.textSecondary,
    setEnterpriseSearchText,
    setShowEntitySuggestions,
    setIsEntitySearching,
    setEntitySuggestions,
    setEnterpriseEntityId,
    setSelectedEntityDetails,
    setEnterpriseEntityName,
    setEnterpriseEntityType,
    setUseEnterprise,
    useEnterprise,
    isModelOnlyMode,
    handleModelOnlyToggle,
    setTimeout,
  ]);

  // Document picker - moved before pickDocumentWithFolderCheck to avoid circular dependency

  // Enhanced document picker with folder validation

  // OCR document picker - specifically for OCR processing of scanned documents and images
  const pickDocumentOCR = useCallback(async (isAttachToQuery = false) => {
    try {
      console.log('?? OCR Document upload requested - checking folder validation first');

      // Check if multiple folders are selected and BLOCK UPLOAD (unless in attachment mode)
      if (!isAttachToQuery) {
        console.log('?? DEBUG: OCR Upload (before picker) - Multiple folder validation check:', {
          selectedFolderIds: selectedFolderIds,
          selectedFolderIdsLength: selectedFolderIds.length,
          allFolders: folders
        });

        if (selectedFolderIds.length > 1) {
          const selectedFolderNames = selectedFolderIds.map(id => {
            if (id === "documents") return "Documents";
            const folder = folders.find(f => f.id === id);
            return folder ? folder.name : "Unknown";
          });

          // BLOCK THE UPLOAD - Show error and return early
          const errorText = `? Multiple drives selected (${selectedFolderNames.join(', ')}). You must select only ONE drive for uploads. Please deselect other drives and try again.`;
          showUploadToast(errorText);

          // Also show an alert for better visibility
          Alert.alert(
            'Upload Not Allowed',
            `You have selected ${selectedFolderIds.length} drives. For OCR document uploads, you can only select ONE drive at a time.\n\nCurrently selected: ${selectedFolderNames.join(', ')}\n\nPlease deselect other drives and try uploading again.`,
            [{ text: 'OK', style: 'default' }]
          );

          // Stop the upload process here - do not continue to file picker
          return;
        }

      }

      console.log('?? OCR Document validation passed - proceeding to pickDocumentWithOCR');

      // Pass the isAttachToQuery flag to pickDocumentWithOCR
      await pickDocumentWithOCR(isAttachToQuery);
    } catch (err) {
      console.error('pickDocumentOCR error:', err);
      Alert.alert('Error', err.message || 'OCR document selection failed.');
    }
  }, [selectedFolderIds, folders]);

  // OCR-specific document picker with OCR processing flag
  const pickDocumentWithOCR = useCallback(async (isAttachToQuery = false) => {
    try {
      console.log('?? pickDocumentWithOCR called - starting file picker for OCR...');

      // If called from Attach to Query, use attachment processing
      if (isAttachToQuery) {
        // Check if max attachments reached
        if (questionAttachments.length >= MAX_ATTACHMENTS) {
          Alert.alert(
            'Maximum Attachments Reached',
            `You can attach up to ${MAX_ATTACHMENTS} files at once. Please remove some attachments before adding more.`,
            [{ text: 'OK' }]
          );
          return;
        }

        console.log('?? Opening OCR file picker for attachment...');
        let result;

        // --- WEB-SPECIFIC IMPLEMENTATION ---
        if (Platform.OS === 'web') {
          console.log('?? Using web file picker for OCR attachment');
          result = await new Promise((resolve) => {
            const input = document.createElement('input');
            input.type = 'file';
            // Accept PDF and image files for OCR processing
            input.accept = '.pdf,.jpg,.jpeg,.png,.bmp,.tiff,.webp,application/pdf,image/jpeg,image/png,image/bmp,image/tiff,image/webp';
            input.multiple = false; // Allow only one file
            input.onchange = (e) => {
              const file = e.target.files[0];
              if (file) {
                resolve({
                  canceled: false,
                  assets: [{
                    name: file.name,
                    uri: URL.createObjectURL(file), // Create a temporary URL for potential previews
                    mimeType: file.type,
                    size: file.size,
                    // Crucially, store the actual File object for the upload
                    file: file
                  }]
                });
              } else {
                resolve({ canceled: true });
              }
            };
            input.click();
          });
        } else {
          // --- MOBILE IMPLEMENTATION for OCR documents ---
          result = await DocumentPicker.getDocumentAsync({
            type: [
              'application/pdf',  // PDF
              'image/jpeg',       // JPEG images
              'image/png',        // PNG images
              'image/bmp',        // BMP images
              'image/tiff',       // TIFF images
              'image/webp'        // WebP images
            ],
            copyToCacheDirectory: true,
            multiple: false, // Allow only one file
          });
        }

        if (result.canceled) return;

        const asset = result.assets?.[0];
        if (!asset) return;

        // Create attachment object for text extraction
        const attachment = {
          id: uuidv4(),
          name: asset.name,
          type: 'document',
          mimeType: asset.mimeType,
          size: asset.size,
          uri: asset.uri,
          file: asset.file,
          blob: asset.blob,
          isOCR: true,
          processed: false
        };

        // Add to attachments with limit check - will be auto-processed by the useEffect
        const success = addAttachmentWithLimit(attachment);

        if (!success) {
          console.log(`? Failed to add OCR attachment due to limit: ${attachment.name}`);
          return;
        }

        console.log(`?? Added OCR attachment for processing: ${attachment.name} (ID: ${attachment.id})`);

        // Close modal

        return;
      }

      // Original "Build Citra Vault Database" flow
      // Check if multiple folders are selected and BLOCK UPLOAD
      console.log('?? DEBUG: OCR Upload - Multiple folder validation check:', {
        selectedFolderIds: selectedFolderIds,
        selectedFolderIdsLength: selectedFolderIds.length,
        allFolders: folders
      });

      if (selectedFolderIds.length > 1) {
        const selectedFolderNames = selectedFolderIds.map(id => {
          if (id === "documents") return "Documents";
          const folder = folders.find(f => f.id === id);
          return folder ? folder.name : "Unknown";
        });

        // BLOCK THE UPLOAD - Show error and return early
        const errorText = `? Multiple drives selected (${selectedFolderNames.join(', ')}). You must select only ONE drive for uploads. Please deselect other drives and try again.`;
        showUploadToast(errorText);

        // Also show an alert for better visibility
        Alert.alert(
          'Upload Not Allowed',
          `You have selected ${selectedFolderIds.length} drives. For OCR document uploads, you can only select ONE drive at a time.\n\nCurrently selected: ${selectedFolderNames.join(', ')}\n\nPlease deselect other drives and try uploading again.`,
          [{ text: 'OK', style: 'default' }]
        );

        // Stop the upload process here - do not continue
        return;
      }

      console.log('?? Opening file picker for OCR documents...');
      let result;
      // --- WEB-SPECIFIC IMPLEMENTATION ---
      if (Platform.OS === 'web') {
        console.log('?? Using web file picker for OCR');
        result = await new Promise((resolve) => {
          const input = document.createElement('input');
          input.type = 'file';
          // Accept PDF and image files for OCR processing
          input.accept = '.pdf,.jpg,.jpeg,.png,.bmp,.tiff,.webp,application/pdf,image/jpeg,image/png,image/bmp,image/tiff,image/webp';
          input.multiple = false; // Allow only one file
          input.onchange = (e) => {
            const file = e.target.files[0];
            if (file) {
              resolve({
                canceled: false,
                assets: [{
                  name: file.name,
                  uri: URL.createObjectURL(file), // Create a temporary URL for potential previews
                  mimeType: file.type,
                  size: file.size,
                  // Crucially, store the actual File object for the upload
                  file: file
                }]
              });
            } else {
              resolve({ canceled: true });
            }
          };
          input.click();
        });
      } else {
        // --- MOBILE IMPLEMENTATION for OCR documents ---
        result = await DocumentPicker.getDocumentAsync({
          type: [
            'application/pdf',  // PDF
            'image/jpeg',       // JPEG images
            'image/png',        // PNG images
            'image/bmp',        // BMP images
            'image/tiff',       // TIFF images
            'image/webp'        // WebP images
          ],
          copyToCacheDirectory: true,
          multiple: false, // Allow only one file
        });
      }

      if (result.canceled) return;

      const asset = result.assets?.[0];
      if (!asset) return;

      // Extract filename without extension for suggested topic
      const fileName = asset.name;

      // Set OCR flag for this document and process directly
      // Close modal now that document is selected 

      // Process document directly without topic prompt - topic will be auto-generated
      handleDocumentUpload("", { ...asset, useOCR: true }, true); // Pass empty topic, OCR flag

    } catch (err) {
      console.error('pickDocumentWithOCR error:', err.message);
      Alert.alert('Error', err.message || 'OCR document selection failed.');
    }
  }, [scheduleScrollToBottom, setPendingDocument, setIsWaitingForDocumentTitle, setMessages, storageManager, activeSessionId, selectedFolderIds, folders, handleDocumentUpload, questionAttachments, setQuestionAttachments]);

  // Web content picker (HTML/JSON files)
  const pickWebDocument = useCallback(async () => {
    try {
      console.log('?? pickWebDocument called - starting file picker for HTML/JSON...');

      // Check if multiple folders are selected and BLOCK UPLOAD
      if (selectedFolderIds.length > 1) {
        const selectedFolderNames = selectedFolderIds.map(id => {
          if (id === "documents") return "Documents";
          const folder = folders.find(f => f.id === id);
          return folder ? folder.name : "Unknown";
        });

        Alert.alert(
          'Upload Not Allowed',
          `You have selected ${selectedFolderIds.length} drives. For uploads, you can only select ONE drive at a time.\n\nCurrently selected: ${selectedFolderNames.join(', ')}\n\nPlease deselect other drives and try uploading again.`,
          [{ text: 'OK', style: 'default' }]
        );
        return;
      }

      let result;
      if (Platform.OS === 'web') {
        result = await new Promise((resolve) => {
          const input = document.createElement('input');
          input.type = 'file';
          // Accept HTML and JSON files
          input.accept = '.html,.htm,.json,text/html,application/json';
          input.multiple = true;
          input.onchange = (e) => {
            const files = Array.from(e.target.files);
            if (files.length > 0) {
              const limitedFiles = files.slice(0, 10);
              if (files.length > 10) {
                alert(`You selected ${files.length} files. Only the first 10 files will be processed.`);
              }
              const assets = limitedFiles.map(file => ({
                name: file.name,
                uri: URL.createObjectURL(file),
                mimeType: file.type,
                size: file.size,
                file: file
              }));
              resolve({ canceled: false, assets: assets });
            } else {
              resolve({ canceled: true });
            }
          };
          input.click();
        });
      } else {
        // Mobile implementation
        result = await DocumentPicker.getDocumentAsync({
          type: ['text/html', 'application/json', 'text/json'],
          copyToCacheDirectory: true,
          multiple: true,
        });
      }

      if (result.canceled) return;

      const assets = result.assets || [];
      if (assets.length === 0) return;

      // Close modal

      // Process each file
      for (const asset of assets) {
        console.log(`?? Processing web file: ${asset.name}`);
        handleDocumentUpload("", asset); // Pass empty topic - auto-generated
      }

    } catch (err) {
      console.error('pickWebDocument error:', err.message);
      Alert.alert('Error', err.message || 'Web document selection failed.');
    }
  }, [selectedFolderIds, folders, handleDocumentUpload]);

  // Open URL fetch modal

  // Fetch content from URL
  const fetchFromURL = useCallback(async (url, customTopic = '') => {
    if (!url || !url.trim()) {
      Alert.alert('Error', 'Please enter a valid URL');
      return;
    }

    // Validate URL format
    try {
      const parsedUrl = new URL(url.trim());
      if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
        Alert.alert('Error', 'Only HTTP and HTTPS URLs are supported');
        return;
      }
    } catch (e) {
      Alert.alert('Error', 'Invalid URL format. Please enter a valid URL (e.g., https://example.com)');
      return;
    }

    setUrlFetchLoading(true);

    try {
      // Determine target folder
      const selectedFolder = selectedFolderIds.length === 1 ? selectedFolderIds[0] : null;
      const targetFolder = determineUploadFolder('document', 'chat', selectedFolder);

      console.log(`?? Fetching content from URL: ${url}`);

      const response = await authService.authenticatedFetch(
        `${API_CONFIG.CITRA_SERVICE_URL}/from-url`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            url: url.trim(),
            topic: customTopic || null,
            folder_id: targetFolder !== 'documents' ? targetFolder : null,
            is_enterprise: false,
          }),
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Failed to fetch URL: ${response.status}`);
      }

      const result = await response.json();
      console.log('? URL content processed:', result);

      setShowURLFetchModal(false);
      setUrlFetchLoading(false);

      // Show success message
      Alert.alert(
        'Success',
        `Content imported successfully!\n\n� Source: ${result.source_url || url}\n� Words: ${result.word_count || 0}\n� Chunks: ${result.total_chunks || 0}`,
        [{ text: 'OK' }]
      );

      // Refresh document list
      if (typeof fetchFolders === 'function') {
        fetchFolders();
      }

    } catch (error) {
      console.error('? URL fetch error:', error);
      setUrlFetchLoading(false);
      Alert.alert('Error', error.message || 'Failed to import content from URL');
    }
  }, [selectedFolderIds, determineUploadFolder, fetchFolders]);

  // Handle paste text submit � creates a .txt file from text and uploads via existing document pipeline
  // Pure callback for UnifiedUploadModal inline view � returns result or throws
  const handlePasteTextSubmit = useCallback(async (topic, text) => {
    if (!topic || !text) return;
    try {
      const selectedFolder = selectedFolderIds.length === 1 ? selectedFolderIds[0] : null;
      const targetFolder = determineUploadFolder('document', 'chat', selectedFolder);
      const wordCount = text.split(/\s+/).filter(Boolean).length;

      console.log(`?? Embedding pasted text as .txt file: "${topic}" (${wordCount} words)`);

      // Create a .txt file from the text content
      const fileName = `${topic.replace(/[^a-zA-Z0-9_\- ]/g, '').trim()}.txt`;
      const formData = new FormData();

      if (Platform.OS === 'web') {
        const blob = new Blob([text], { type: 'text/plain' });
        const file = new File([blob], fileName, { type: 'text/plain' });
        formData.append('file', file, fileName);
      } else {
        // For React Native mobile, write text to a temp file and use URI
        const tempUri = `${FileSystem.cacheDirectory}${fileName}`;
        await FileSystem.writeAsStringAsync(tempUri, text, { encoding: FileSystem.EncodingType.UTF8 });
        formData.append('file', { uri: tempUri, type: 'text/plain', name: fileName });
      }

      const userEmail = await getUserEmail();
      formData.append('document_id', uuidv4());
      formData.append('user_id', userEmail);
      formData.append('topic', topic);

      if (targetFolder && targetFolder !== 'documents') {
        formData.append('folder_id', targetFolder);
      }
      if (activeTeamId) {
        formData.append('team_id', activeTeamId);
      }

      const response = await authService.authenticatedFetch(`${CITRA_SERVICE}/v2/documents`, {
        method: 'POST',
        headers: { 'Content-Type': undefined },
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Failed to embed text: ${response.status}`);
      }

      const result = await response.json();
      console.log('? Text content embedded:', result);

      Alert.alert(
        'Success',
        `Text embedded successfully!\n\n� Title: ${result.topic_or_filename || topic}\n� Vectors: ${result.vectors_created || 0}`,
        [{ text: 'OK' }]
      );

      if (typeof fetchFolders === 'function') fetchFolders();
      return result;
    } catch (error) {
      console.error('? Paste text error:', error);
      Alert.alert('Error', error.message || 'Failed to embed text in data store');
      throw error;
    }
  }, [selectedFolderIds, determineUploadFolder, fetchFolders, activeTeamId]);

  // Save a bot message directly to Vault � reuses the Paste Text API
  const handleSaveToVault = useCallback(async (text) => {
    if (!text || !text.trim()) throw new Error('Empty message');

    const now = new Date();
    const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    const snippet = text.trim().split(/\s+/).slice(0, 6).join(' ');
    const topic = `Chat Answer - ${dateStr} - ${snippet}`;
    const fileName = `${topic.replace(/[^a-zA-Z0-9_\- ]/g, '').trim()}.txt`;

    const formData = new FormData();
    if (Platform.OS === 'web') {
      const blob = new Blob([text], { type: 'text/plain' });
      const file = new File([blob], fileName, { type: 'text/plain' });
      formData.append('file', file, fileName);
    } else {
      const tempUri = `${FileSystem.cacheDirectory}${fileName}`;
      await FileSystem.writeAsStringAsync(tempUri, text, { encoding: FileSystem.EncodingType.UTF8 });
      formData.append('file', { uri: tempUri, type: 'text/plain', name: fileName });
    }

    const userEmail = await getUserEmail();
    formData.append('document_id', uuidv4());
    formData.append('user_id', userEmail);
    formData.append('topic', topic);

    const selectedFolder = selectedFolderIds.length === 1 ? selectedFolderIds[0] : null;
    const targetFolder = determineUploadFolder('document', 'chat', selectedFolder);
    if (targetFolder && targetFolder !== 'documents') {
      formData.append('folder_id', targetFolder);
    }
    if (activeTeamId) {
      formData.append('team_id', activeTeamId);
    }

    const response = await authService.authenticatedFetch(`${CITRA_SERVICE}/v2/documents`, {
      method: 'POST',
      headers: { 'Content-Type': undefined },
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Failed to save to data store: ${response.status}`);
    }

    const result = await response.json();
    console.log('? Bot message saved to vault:', result);
    if (typeof fetchFolders === 'function') fetchFolders();
    return result;
  }, [selectedFolderIds, determineUploadFolder, fetchFolders, activeTeamId]);

  // Handle internet ingest fetch � calls /internet-ingest to get data from the web
  // Pure callback � returns { text, query, word_count } or throws

  // Handle internet ingest embed � creates a .txt file from reviewed content and uploads via existing document pipeline
  // Pure callback � returns result or throws

  // Preload-specific document picker with enhanced progress tracking and multi-file support


  // Shared Repository Functions


  // Enterprise OCR document upload function

  // Enterprise image upload function

  // Enterprise audio file upload function

  // Enterprise Google Drive picker function
  const openGoogleDrivePickerForEnterprise = useCallback(async () => {
    console.log('?? Enterprise Google Drive picker');

    try {
      // Check internet connection
      const netInfo = await NetInfo.fetch();
      if (!netInfo.isConnected) {
        Alert.alert('No Internet Connection', 'Please check your network settings and try again.');
        return;
      }

      // Open Google Drive picker for Shared Repository intake
      console.log('?? DEBUG: Setting isEnterpriseGoogleDriveUpload to true');
      setIsEnterpriseGoogleDriveUpload(true);
      setShowGoogleDrivePicker(true);

      // Note: The actual upload will be handled by handleGoogleDriveDocument
      // which should be configured to use enterprise parameters

    } catch (err) {
      console.error('openGoogleDrivePickerForEnterprise error:', err);
      Alert.alert('Enterprise Google Drive Error', err.message || 'An error occurred opening Google Drive picker');
    }
  }, []);

  // Entity upload functions for "Upload to Entity" modal



  async function ensureCameraPermission() {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert(
        'Permission required',
        'We need camera access to take photos.'
      );
      return false;
    }
    return true;
  }


  // ================== ENHANCED ATTACHMENT PROCESSING ==================

  // Process attachment in background and extract text
  // Helper function to simulate smooth progress updates

  const processAttachmentInBackground = useCallback(async (attachment) => {
    const attachmentId = attachment.id;

    try {
      // ? NEW BEHAVIOR: Attachments are sent directly to query API
      // No need to call extract-text-only API anymore
      // Query API will handle text extraction from audio and images
      console.log(`?? Attachment ready for query: ${attachment.name} (ID: ${attachmentId})`);
      console.log(`?? Type: ${attachment.type}, MimeType: ${attachment.mimeType}`);
      console.log(`?? Will be processed by query API - no pre-extraction needed`);

      // Show brief progress indicator
      setAttachmentProgress(prev => ({
        ...prev,
        [attachmentId]: { stage: 'uploading', progress: 50, message: 'Attachment ready...' }
      }));

      // Simulate brief processing to show user the attachment is acknowledged
      await new Promise(resolve => setTimeout(resolve, 300));

      // Mark as ready - query API will handle text extraction
      setAttachmentProgress(prev => ({
        ...prev,
        [attachmentId]: {
          stage: 'completed',
          progress: 100,
          message: 'Ready to send',
          extractionSuccessful: true
        }
      }));

      // Mark attachment as processed (no extracted text needed - query API handles it)
      setQuestionAttachments(prev => prev.map(att =>
        att.id === attachmentId
          ? { ...att, processed: true }
          : att
      ));

      // Clear progress state after brief display
      setTimeout(() => {
        setAttachmentProgress(prev => {
          const newProgress = { ...prev };
          delete newProgress[attachmentId];
          console.log(`?? Cleared progress state for ${attachment.name}`);
          return newProgress;
        });
      }, 2000);

      return '';

    } catch (error) {
      console.error(`? Error preparing attachment ${attachment.name}:`, error);

      // Update progress: error
      setAttachmentProgress(prev => ({
        ...prev,
        [attachmentId]: {
          stage: 'error',
          progress: 0,
          message: `Failed to prepare: ${error.message}`,
          error: error.message
        }
      }));

      // Mark attachment as processed with error
      setQuestionAttachments(prev => prev.map(att =>
        att.id === attachmentId
          ? { ...att, processed: true, processingError: error.message }
          : att
      ));

      return '';
    }
  }, [setQuestionAttachments]);

  // Process all pending attachments
  const processAllAttachments = useCallback(async () => {
    const unprocessedAttachments = questionAttachments.filter(att => !att.processed);

    if (unprocessedAttachments.length === 0) return;

    setIsProcessingAttachments(true);

    // Process all attachments in parallel
    const processingPromises = unprocessedAttachments.map(attachment =>
      processAttachmentInBackground(attachment)
    );

    try {
      await Promise.all(processingPromises);
    } catch (error) {
      console.error('Error processing attachments:', error);
    } finally {
      setIsProcessingAttachments(false);
    }
  }, [questionAttachments, processAttachmentInBackground]);

  // Auto-process attachments when they're added
  useEffect(() => {
    const unprocessedAttachments = questionAttachments.filter(att => !att.processed);
    // console.log(`?? Auto-process check: ${unprocessedAttachments.length} unprocessed, isProcessing: ${isProcessingAttachments}`);

    if (unprocessedAttachments.length > 0 && !isProcessingAttachments) {
      console.log(`?? Auto-triggering processing for ${unprocessedAttachments.length} attachments:`,
        unprocessedAttachments.map(att => `${att.name} (${att.id})`));
      processAllAttachments();
    }
  }, [questionAttachments, isProcessingAttachments, processAllAttachments]);

  // Enhanced file upload handler for Ask Question
  const handleFileToQuestion = useCallback(async () => {
    try {
      const netInfo = await NetInfo.fetch();
      if (!netInfo.isConnected) {
        Alert.alert('Error', 'No internet connection. Please check your network settings and try again.');
        return;
      }

      if (Platform.OS === 'web') {
        // Web file upload
        const input = document.createElement('input');
        input.type = 'file';
        // Added audio file types to support audio uploads
        input.accept = '.pdf,.docx,.xlsx,.xls,.pptx,.txt,.md,.html,.htm,.json,.jpg,.jpeg,.png,.gif,.bmp,.webp,.mp3,.wav,.m4a,.aac,.ogg,.webm,.mp4,.mov,.avi';
        input.multiple = false;

        input.onchange = async (event) => {
          const file = event.target.files[0];
          if (!file) return;

          // Determine file type
          let fileType = 'document';
          if (file.type.startsWith('image/')) {
            fileType = 'image';
          } else if (file.type.startsWith('audio/') || file.type.startsWith('video/')) {
            fileType = 'audio';
          }

          // Create attachment object
          const attachment = {
            id: uuidv4(),
            type: fileType,
            file: file,
            name: file.name,
            mimeType: file.type,
            processed: false
          };

          // Add to attachments queue
          const addSuccess = addAttachmentWithLimit(attachment);
        };

        input.click();
      } else {
        // Mobile file picker
        const result = await DocumentPicker.getDocumentAsync({
          type: ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'text/plain', 'text/markdown', 'text/html', 'application/json', 'image/*', 'audio/*', 'video/*'],
          copyToCacheDirectory: true,
        });

        if (!result.canceled && result.assets && result.assets.length > 0) {
          const asset = result.assets[0];

          // Determine file type
          let fileType = 'document';
          if (asset.mimeType && asset.mimeType.startsWith('image/')) {
            fileType = 'image';
          } else if (asset.mimeType && (asset.mimeType.startsWith('audio/') || asset.mimeType.startsWith('video/'))) {
            fileType = 'audio';
          }

          // Create attachment object
          const attachment = {
            id: uuidv4(),
            type: fileType,
            uri: asset.uri,
            name: asset.name,
            mimeType: asset.mimeType,
            processed: false
          };

          // Add to attachments queue
          const addSuccess = addAttachmentWithLimit(attachment);

          if (addSuccess) {
            // Show toast notification instead of chat message
          }
        }
      }

    } catch (err) {
      console.error('handleFileToQuestion error:', err);
      Alert.alert('Error', err.message || 'File selection failed.');
    }
  }, [scheduleScrollToBottom, setMessages, storageManager, activeSessionId]);

  // Enhanced audio recording handler for Ask Question
  const handleAudioToQuestion = useCallback(async () => {
    try {
      const netInfo = await NetInfo.fetch();
      if (!netInfo.isConnected) {
        Alert.alert('Error', 'No internet connection. Please check your network settings and try again.');
        return;
      }

      if (Platform.OS === 'web') {
        // Web audio recording
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          Alert.alert('Error', 'Audio recording is not supported in this browser.');
          return;
        }

        // Start recording logic (simplified - you may want to use a more robust recording component)
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mediaRecorder = new MediaRecorder(stream);
        const chunks = [];

        mediaRecorder.ondataavailable = (event) => {
          chunks.push(event.data);
        };

        mediaRecorder.onstop = async () => {
          const blob = new Blob(chunks, { type: 'audio/wav' });
          const fileName = `audio_${Date.now()}.wav`;

          // Create attachment object
          const attachment = {
            id: uuidv4(),
            type: 'audio',
            blob: blob,
            name: fileName,
            mimeType: 'audio/wav',
            processed: false
          };

          // Add to attachments queue
          const addSuccess = addAttachmentWithLimit(attachment);

          // Stop all tracks
          stream.getTracks().forEach(track => track.stop());
        };

        // Start recording
        mediaRecorder.start();

        // Stop after 30 seconds or when user clicks stop
        setTimeout(() => {
          if (mediaRecorder.state === 'recording') {
            mediaRecorder.stop();
          }
        }, 30000);

        // You might want to show a recording UI here
        Alert.alert('Recording', 'Recording started. It will stop automatically after 30 seconds.');

      } else {
        // Mobile audio recording - use existing audio recording logic
        Alert.alert('Info', 'Mobile audio recording for questions coming soon!');
      }

    } catch (err) {
      console.error('handleAudioToQuestion error:', err);
      Alert.alert('Error', err.message || 'Audio recording failed.');
    }
  }, [scheduleScrollToBottom, setMessages, storageManager, activeSessionId]);

  // Check if all attachments are processed
  const areAllAttachmentsProcessed = useCallback(() => {
    if (questionAttachments.length === 0) return true;
    return questionAttachments.every(att => att.processed);
  }, [questionAttachments]);

  // Get combined text from user input and processed attachments
  const getCombinedQueryText = useCallback((userText) => {
    const processedAttachments = questionAttachments.filter(att => att.processed && att.extractedText);

    if (processedAttachments.length === 0) {
      return userText; // No attachments or no extracted text
    }

    // Separate attachments by type for different handling
    const questionAttachments_items = processedAttachments.filter(att =>
      att.isScreenshot === true || att.isQuestionRecording === true
    );
    const topicAttachments = processedAttachments.filter(att =>
      att.isScreenshot !== true && att.isQuestionRecording !== true
    );

    let combinedText = userText || '';

    // Handle audio questions or screenshots: Add as question form using extracted text
    if (questionAttachments_items.length > 0) {
      const questionTexts = questionAttachments_items.map(att => {
        const fileType = att.isScreenshot ? 'SCREENSHOT' : 'AUDIO QUESTION';
        return `${att.extractedText}`;
      }).join(' ');

      // Add question content to the user query
      if (combinedText) {
        combinedText = `${combinedText} ${questionTexts}`;
      } else {
        combinedText = questionTexts;
      }
    }

    // Handle documents, Google files, audio uploads, or OCR: Add as topic to query
    if (topicAttachments.length > 0) {
      const topicTexts = topicAttachments.map(att => {
        // The extractedText now contains the topic for synchronous processing
        return `topic - ${att.extractedText}`;
      }).join(' ');

      // Add topic information to the user query
      if (combinedText) {
        combinedText = `${combinedText} ${topicTexts}`;
      } else {
        combinedText = topicTexts;
      }
    }

    return combinedText;
  }, [questionAttachments]);

  // ================== END ENHANCED ATTACHMENT PROCESSING ==================

  // Enhanced photo-to-question capture (mobile)
  const handlePhotoToQuestion = useCallback(async () => {
    try {
      const netInfo = await NetInfo.fetch();
      if (!netInfo.isConnected) {
        Alert.alert('Error', 'No internet connection. Please check your network settings and try again.');
        return;
      }

      if (!(await ensureCameraPermission())) return;

      const result = await ImagePicker.launchCameraAsync({
        allowsEditing: true,
        quality: 0.8,
      });

      if (!result.canceled && result.assets && result.assets.length > 0) {
        const asset = result.assets[0];

        // Create attachment object
        const attachment = {
          id: uuidv4(),
          type: 'image',
          uri: asset.uri,
          name: asset.fileName || `photo_${Date.now()}.jpg`,
          mimeType: asset.mimeType || 'image/jpeg',
          processed: false
        };

        // Add to attachments queue for processing with limit check
        const success = addAttachmentWithLimit(attachment);

        if (!success) {
          console.log(`? Failed to add camera image attachment due to limit: ${attachment.name}`);
          return;
        }
      }
    } catch (err) {
      console.error('handlePhotoToQuestion error:', err);
      Alert.alert('Error', err.message || 'Photo capture failed.');
    }
  }, [scheduleScrollToBottom, setMessages, storageManager, activeSessionId]);

  // Shared title-submit step. Called from the chat-input branch (legacy
  // bot-prompt path) AND from the audio-meeting overlay's TextInput.
  const submitRecordingTitle = useCallback(async (rawTopic) => {
    const topic = (rawTopic || '').trim();
    if (!topic) return;
    setIsWaitingForRecordingTitle(false);
    setRecordingTitle(topic);
    if (recordingType === 'question') {
      await handleQuestionRecordingStart(topic);
    }
    setRecordingType('');
  }, [recordingType, handleQuestionRecordingStart]);

  // New function for recording audio to Citra AI database

  // Recording functions - moved here to be available for handleAudioQuestion
  // Chat microphone only. The meeting-recording path that used to share this
  // function (everything behind `!isForQuestion`) is gone, along with the
  // legacy tail below it that only that path could reach.
  const startRecording = useCallback(async () => {
    setIsRecordingForQuestion(true);
    setRecordingTitle(''); // no user-supplied title for question recording
    await startActualRecording(true);
  }, [setIsRecordingForQuestion, setRecordingTitle, startActualRecording]);

  const stopRecording = useCallback(async () => {
    setIsRecording(false);

    // Clear audio recording timer and progress
    if (audioRecordingTimerRef.current) {
      clearInterval(audioRecordingTimerRef.current);
      audioRecordingTimerRef.current = null;
    }
    setAudioRecordingDuration(0);
    setAudioRecordingProgress({
      duration: 0,
      size: 0,
      chunkCount: 0,
      isRecording: false,
      isPaused: false
    });

    try {
      if (Platform.OS === 'web') {
        // Stop web audio recording
        if (webAudioRecorderRef.current && webAudioRecorderRef.current.state === 'recording') {
          webAudioRecorderRef.current.stop();
          // The onstop handler will process the recording
        }
      } else {
        // Stop mobile recording and get its URI
        await recordingRef.current.stopAndUnloadAsync();
        const uri = recordingRef.current.getURI();
        if (!uri) {
          throw new Error("Failed to get recording URI.");
        }

        const filename = uri.split('/').pop();
        const mimeType = 'audio/mp4'; // Default for .m4a recordings from Expo AV

        // Check if this was a question recording
        if (isRecordingForQuestion) {
          // Create attachment object for question
          const attachment = {
            id: uuidv4(),
            type: 'audio',
            uri: uri,
            name: filename || `audio_${Date.now()}.m4a`,
            mimeType: mimeType
          };

          // Add to attachments
          const addSuccess = addAttachmentWithLimit(attachment);

          // Reset question recording flag
          setIsRecordingForQuestion(false);
        }
      }

      // Clean up recording state
      setRecordingTitle('');
      setRecordingStartTime(null);

    } catch (error) {
      console.error('Error stopping recording:', error);
      const errorMessage = {
        id: uuidv4(),
        text: '? Failed to stop recording. Please try again.',
        sender: 'bot',
      };
      setMessages(prev => [...prev, errorMessage]);
      scheduleScrollToBottom(1);

      // Reset question recording flag on error
      setIsRecordingForQuestion(false);
    }
  }, [scheduleScrollToBottom, recordingTitle, isRecordingForQuestion, setQuestionAttachments, setMessages, storageManager, activeSessionId]);

  // Pause audio recording

  // Resume audio recording

  // New function for audio-to-question capture
  const handleAudioQuestion = useCallback(async () => {
    try {
      const netInfo = await NetInfo.fetch();
      if (!netInfo.isConnected) {
        Alert.alert('Error', 'No internet connection. Please check your network settings and try again.');
        return;
      }

      // Show instructions message
      const instructionMessage = {
        id: uuidv4(),
        text: '?? Start recording your question now. Your audio will be attached and processed when you send your message.',
        sender: 'bot',
        hideActions: true,
      };

      setMessages(prev => {
        const newMessages = [...prev, instructionMessage];
        storageManager.storeMessagePairs(newMessages, activeSessionId);
        return newMessages;
      });
      scheduleScrollToBottom(1);

      // Start recording automatically (for questions, not memory upload)
      setIsRecordingForQuestion(true);

      setTimeout(async () => {
        try {
          await startRecording(true); // Pass true to indicate this is for questions

          // Add a follow-up message
          const recordingMessage = {
            id: uuidv4(),
            text: '?? Recording in progress... Press the microphone button again to stop and attach the audio.',
            sender: 'bot',
            hideActions: true,
          };

          setMessages(prev => {
            const newMessages = [...prev, recordingMessage];
            storageManager.storeMessagePairs(newMessages, activeSessionId);
            return newMessages;
          });
          scheduleScrollToBottom(1);
        } catch (recordingError) {
          console.error('Failed to start recording:', recordingError);
          Alert.alert('Error', 'Failed to start recording. Please try again.');
          setIsRecordingForQuestion(false);
        }
      }, 500);

    } catch (err) {
      console.error('handleAudioQuestion error:', err);
      Alert.alert('Error', err.message || 'Audio question capture failed.');
      setIsRecordingForQuestion(false);
    }
  }, [scheduleScrollToBottom, setMessages, storageManager, activeSessionId, startRecording]);

  // Web-specific screenshot and crop function
  const handleWebScreenshotQuestion = useCallback(async () => {
    try {
      if (Platform.OS !== 'web') {
        Alert.alert('Error', 'This feature is only available on web platforms.');
        return;
      }

      const netInfo = await NetInfo.fetch();
      if (!netInfo.isConnected) {
        Alert.alert('Error', 'No internet connection. Please check your network settings and try again.');
        return;
      }

      // Show instruction message
      const instructionMessage = {
        id: uuidv4(),
        text: '?? Starting screen capture... Please select the area of your screen that contains the question you want to ask about.',
        sender: 'bot',
        hideActions: true,
      };

      setMessages(prev => {
        const newMessages = [...prev, instructionMessage];
        storageManager.storeMessagePairs(newMessages, activeSessionId);
        return newMessages;
      });
      scheduleScrollToBottom(1);

      // Check if Screen Capture API is supported
      if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
        Alert.alert('Error', 'Screen capture is not supported in this browser. Please use a modern browser like Chrome, Firefox, or Edge.');
        return;
      }

      try {
        // Request screen capture
        const stream = await navigator.mediaDevices.getDisplayMedia({
          video: {
            mediaSource: 'screen'
          }
        });

        // Create a video element to capture the frame
        const video = document.createElement('video');
        video.srcObject = stream;
        video.play();

        video.addEventListener('loadedmetadata', () => {
          // Create canvas to capture frame
          const canvas = document.createElement('canvas');
          const ctx = canvas.getContext('2d');

          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;

          // Draw video frame to canvas
          ctx.drawImage(video, 0, 0);

          // Stop the stream
          stream.getTracks().forEach(track => track.stop());

          // Convert canvas to blob
          canvas.toBlob(async (blob) => {
            if (!blob) {
              Alert.alert('Error', 'Failed to capture screenshot.');
              return;
            }

            // Show cropping interface
            await handleImageCropping(blob);
          }, 'image/png');
        });

      } catch (captureError) {
        console.error('Screen capture error:', captureError);

        if (captureError.name === 'NotAllowedError') {
          Alert.alert('Permission Denied', 'Screen capture permission was denied. Please allow screen sharing to use this feature.');
        } else if (captureError.name === 'NotFoundError') {
          Alert.alert('Error', 'No screen source found. Please try again.');
        } else {
          Alert.alert('Error', 'Failed to capture screen. Please try again.');
        }

        // Remove instruction message
        setMessages(prev => prev.filter(msg => msg.id !== instructionMessage.id));
      }

    } catch (err) {
      console.error('handleWebScreenshotQuestion error:', err);
      Alert.alert('Error', err.message || 'Screenshot capture failed.');
    }
  }, [scheduleScrollToBottom, setMessages, storageManager, activeSessionId]);

  // Function to handle image cropping and text extraction
  const handleImageCropping = useCallback(async (imageBlob) => {
    try {
      // Create image cropping interface
      const cropModal = document.createElement('div');
      cropModal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.9);
        z-index: 10000;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 20px;
      `;

      const instructions = document.createElement('div');
      instructions.style.cssText = `
        color: white;
        font-size: 16px;
        margin-bottom: 20px;
        text-align: center;
        max-width: 600px;
      `;
      instructions.innerHTML = `
        <h3>?? Crop your question area</h3>
        <p>Use your mouse to select the area containing the question text. Click and drag to create a selection rectangle.</p>
      `;

      const imageContainer = document.createElement('div');
      imageContainer.style.cssText = `
        position: relative;
        max-width: 90%;
        max-height: 70%;
        overflow: auto;
        border: 2px solid #4f46e5;
        border-radius: 8px;
      `;

      const img = document.createElement('img');
      img.src = URL.createObjectURL(imageBlob);
      img.style.cssText = `
        max-width: 100%;
        height: auto;
        display: block;
        cursor: crosshair;
      `;

      const selectionOverlay = document.createElement('div');
      selectionOverlay.style.cssText = `
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
      `;

      const buttonContainer = document.createElement('div');
      buttonContainer.style.cssText = `
        margin-top: 20px;
        display: flex;
        gap: 15px;
      `;

      const cropButton = document.createElement('button');
      cropButton.textContent = '?? Crop and Attach';
      cropButton.style.cssText = `
        background: #4f46e5;
        color: white;
        border: none;
        padding: 12px 24px;
        border-radius: 8px;
        font-size: 16px;
        cursor: pointer;
        font-weight: 600;
      `;
      cropButton.disabled = true;
      cropButton.style.opacity = '0.5';

      const cancelButton = document.createElement('button');
      cancelButton.textContent = '? Cancel';
      cancelButton.style.cssText = `
        background: #ef4444;
        color: white;
        border: none;
        padding: 12px 24px;
        border-radius: 8px;
        font-size: 16px;
        cursor: pointer;
        font-weight: 600;
      `;

      const fullImageButton = document.createElement('button');
      fullImageButton.textContent = '?? Use Full Image';
      fullImageButton.style.cssText = `
        background: #10b981;
        color: white;
        border: none;
        padding: 12px 24px;
        border-radius: 8px;
        font-size: 16px;
        cursor: pointer;
        font-weight: 600;
      `;

      let isSelecting = false;
      let startX, startY, currentSelection = null;

      // Mouse events for selection
      img.addEventListener('mousedown', (e) => {
        isSelecting = true;
        const rect = img.getBoundingClientRect();
        startX = e.clientX - rect.left;
        startY = e.clientY - rect.top;

        // Clear previous selection
        selectionOverlay.innerHTML = '';
      });

      img.addEventListener('mousemove', (e) => {
        if (!isSelecting) return;

        const rect = img.getBoundingClientRect();
        const currentX = e.clientX - rect.left;
        const currentY = e.clientY - rect.top;

        // Clear previous selection rectangle
        selectionOverlay.innerHTML = '';

        // Create selection rectangle
        const selectionRect = document.createElement('div');
        selectionRect.style.cssText = `
          position: absolute;
          border: 2px dashed #4f46e5;
          background: rgba(79, 70, 229, 0.2);
          left: ${Math.min(startX, currentX)}px;
          top: ${Math.min(startY, currentY)}px;
          width: ${Math.abs(currentX - startX)}px;
          height: ${Math.abs(currentY - startY)}px;
          pointer-events: none;
        `;

        selectionOverlay.appendChild(selectionRect);

        currentSelection = {
          x: Math.min(startX, currentX),
          y: Math.min(startY, currentY),
          width: Math.abs(currentX - startX),
          height: Math.abs(currentY - startY)
        };
      });

      img.addEventListener('mouseup', () => {
        isSelecting = false;
        if (currentSelection && currentSelection.width > 10 && currentSelection.height > 10) {
          cropButton.disabled = false;
          cropButton.style.opacity = '1';
        }
      });

      // Crop button click handler
      cropButton.addEventListener('click', async () => {
        if (!currentSelection) return;

        document.body.removeChild(cropModal);
        await processScreenshotSelection(imageBlob, currentSelection, img.naturalWidth, img.naturalHeight);
      });

      // Full image button handler
      fullImageButton.addEventListener('click', async () => {
        document.body.removeChild(cropModal);
        await processScreenshotSelection(imageBlob, null, img.naturalWidth, img.naturalHeight);
      });

      // Cancel button handler
      cancelButton.addEventListener('click', () => {
        document.body.removeChild(cropModal);
        URL.revokeObjectURL(img.src);
      });

      // Assemble the modal
      imageContainer.appendChild(img);
      imageContainer.appendChild(selectionOverlay);
      buttonContainer.appendChild(fullImageButton);
      buttonContainer.appendChild(cropButton);
      buttonContainer.appendChild(cancelButton);
      cropModal.appendChild(instructions);
      cropModal.appendChild(imageContainer);
      cropModal.appendChild(buttonContainer);

      document.body.appendChild(cropModal);

    } catch (err) {
      console.error('Image cropping error:', err);
      Alert.alert('Error', 'Failed to create cropping interface.');
    }
  }, []);

  // Process the selected/cropped area and extract text
  const processScreenshotSelection = useCallback(async (originalBlob, selection, naturalWidth, naturalHeight) => {
    try {
      let finalBlob = originalBlob;

      if (selection) {
        // Create canvas for cropping
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');

        // Calculate crop dimensions relative to original image
        const scaleX = naturalWidth / naturalWidth;
        const scaleY = naturalHeight / naturalHeight;

        canvas.width = selection.width * scaleX;
        canvas.height = selection.height * scaleY;

        // Load original image - use document.createElement for web compatibility
        const img = document.createElement('img');
        img.onload = async () => {
          // Draw cropped portion
          ctx.drawImage(
            img,
            selection.x * scaleX, selection.y * scaleY,
            selection.width * scaleX, selection.height * scaleY,
            0, 0,
            canvas.width, canvas.height
          );

          // Convert to blob
          canvas.toBlob(async (croppedBlob) => {
            if (croppedBlob) {
              await extractTextFromScreenshot(croppedBlob);
            }
          }, 'image/png');
        };
        img.src = URL.createObjectURL(originalBlob);
      } else {
        // Use full image
        await extractTextFromScreenshot(finalBlob);
      }

    } catch (err) {
      console.error('Screenshot processing error:', err);
      Alert.alert('Error', 'Failed to process screenshot.');
    }
  }, []);

  // Prepare screenshot for query - no text extraction needed
  const extractTextFromScreenshot = useCallback(async (imageBlob) => {
    try {
      // Create attachment object for the screenshot
      const attachment = {
        id: uuidv4(),
        type: 'screenshot',
        blob: imageBlob,
        name: `screenshot_${Date.now()}.png`,
        mimeType: 'image/png',
        isScreenshot: true,
        processed: true
      };

      // Add to attachments - query API will handle OCR/text extraction
      const addSuccess = addAttachmentWithLimit(attachment);

    } catch (err) {
      console.error('extractTextFromScreenshot error:', err);
      Alert.alert('Error', 'Failed to attach screenshot.');
    }
  }, [scheduleScrollToBottom, setMessages, storageManager, activeSessionId]);

  // Clipboard handling functions
  const handleClipboardPaste = useCallback(async () => {
    try {
      if (Platform.OS === 'web') {
        console.log('??? Attempting to paste from clipboard on web...');

        // Web: Access clipboard via navigator.clipboard
        if (!navigator.clipboard) {
          console.error('? Clipboard API not supported');
          Alert.alert('Error', 'Clipboard access is not supported in this browser. Please use HTTPS or enable clipboard permissions.');
          return;
        }

        // Check if we're on HTTPS (required for clipboard API)
        if (location.protocol !== 'https:' && location.hostname !== 'localhost') {
          console.error('? Clipboard API requires HTTPS');
          Alert.alert('Error', 'Clipboard access requires HTTPS. Please use https:// or localhost.');
          return;
        }

        // Request clipboard permissions first
        try {
          const permission = await navigator.permissions.query({ name: 'clipboard-read' });
          console.log('?? Clipboard permission status:', permission.state);

          if (permission.state === 'denied') {
            Alert.alert('Permission Denied', 'Clipboard access is denied. Please enable clipboard permissions in your browser settings.');
            return;
          }
        } catch (permError) {
          console.log('?? Could not check clipboard permissions:', permError);
          // Continue anyway, as some browsers don't support permission queries
        }

        // Try to read clipboard items (including images)
        try {
          console.log('?? Reading clipboard items...');
          const clipboardItems = await navigator.clipboard.read();
          console.log('?? Clipboard items:', clipboardItems.length);

          for (const clipboardItem of clipboardItems) {
            console.log('?? Clipboard item types:', clipboardItem.types);

            // Check for image types
            const imageTypes = clipboardItem.types.filter(type => type.startsWith('image/'));
            console.log('??? Found image types:', imageTypes);

            if (imageTypes.length > 0) {
              // Handle image from clipboard
              const imageType = imageTypes[0];
              console.log('??? Processing image type:', imageType);

              const blob = await clipboardItem.getType(imageType);
              console.log('??? Got blob:', blob.size, 'bytes');

              // Convert blob to data URL for React Native Web
              const reader = new FileReader();
              const dataUrl = await new Promise((resolve, reject) => {
                reader.onload = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
              });

              console.log('??? Created data URL:', dataUrl.substring(0, 50) + '...');

              // Create attachment object for the clipboard image
              const attachment = {
                id: uuidv4(),
                type: 'image',
                uri: dataUrl, // Use data URL instead of blob for React Native Web
                blob: blob, // Keep blob for potential API uploads
                name: `clipboard_image_${Date.now()}.png`,
                mimeType: imageType,
                size: blob.size,
                processed: true
              };

              console.log('??? Created attachment:', attachment.id);

              // Add to attachments with limit check
              const success = addAttachmentWithLimit(attachment);

              if (!success) {
                console.log(`? Failed to add clipboard image attachment due to limit: ${attachment.name}`);
                return;
              }

              console.log('??? Updated attachments count:', questionAttachments.length + 1);

              console.log('? Image clipboard paste completed successfully');
              return; // Exit early since we handled an image
            }
          }
          console.log('?? No images found in clipboard, checking for text...');
        } catch (readError) {
          console.error('? Could not read clipboard items:', readError);
          console.log('?? Trying text fallback...');
        }

        // Fallback: Try to read as text
        try {
          console.log('?? Attempting to read clipboard text...');
          const text = await navigator.clipboard.readText();
          console.log('?? Clipboard text length:', text ? text.length : 0);

          if (text && text.trim()) {
            // Add text to input (preserving existing text)
            const currentText = inputText || '';
            const newText = currentText ? `${currentText}\n${text}` : text;
            setInputText(newText);
            console.log('?? Text pasted successfully');

            // Focus input after pasting
            if (inputRef.current) {
              inputRef.current.focus();
            }
          } else {
            console.log('?? No text found in clipboard');
          }
        } catch (textError) {
          console.error('? Could not read clipboard text:', textError);
          Alert.alert('Error', 'Could not access clipboard content. Please check browser permissions.');
        }

      } else {
        // Mobile: Use Expo Clipboard
        console.log('?? Attempting mobile clipboard paste...');

        try {
          // Check if clipboard has an image
          const hasImage = await Clipboard.hasImageAsync();
          console.log('??? Mobile clipboard has image:', hasImage);

          if (hasImage) {
            // Get image from clipboard
            const imageResult = await Clipboard.getImageAsync();
            console.log('??? Mobile image result:', imageResult ? 'success' : 'failed');

            if (imageResult && imageResult.data) {
              console.log('??? Processing mobile clipboard image...');

              // Create attachment object for the clipboard image
              const attachment = {
                id: uuidv4(),
                type: 'image',
                uri: imageResult.data,
                name: `clipboard_image_${Date.now()}.png`,
                mimeType: 'image/png'
              };

              console.log('??? Created mobile attachment:', attachment.id);

              // Add to attachments with limit check
              const success = addAttachmentWithLimit(attachment);

              if (!success) {
                console.log(`? Failed to add mobile clipboard image attachment due to limit: ${attachment.name}`);
                return;
              }

              console.log('??? Mobile attachments count:', questionAttachments.length + 1);

              console.log('? Mobile image clipboard paste completed successfully');
              return; // Exit early since we handled an image
            } else {
              console.log('? Mobile clipboard image result invalid');
            }
          } else {
            console.log('?? No image in mobile clipboard, checking for text...');
          }

          // Fallback: Try to get text from clipboard
          console.log('?? Attempting to read mobile clipboard text...');
          const text = await Clipboard.getStringAsync();
          console.log('?? Mobile clipboard text length:', text ? text.length : 0);

          if (text && text.trim()) {
            // Add text to input (preserving existing text)
            const currentText = inputText || '';
            const newText = currentText ? `${currentText}\n${text}` : text;
            setInputText(newText);
            console.log('?? Mobile text pasted successfully');
          } else {
            console.log('?? No text found in mobile clipboard');
          }

        } catch (error) {
          console.error('? Mobile clipboard error:', error);
          Alert.alert('Error', 'Could not access clipboard content. Please check app permissions.');
        }
      }
    } catch (err) {
      console.error('? handleClipboardPaste error:', err);
      Alert.alert('Error', 'Failed to paste from clipboard. Please try again.');
    }
  }, [inputText, setInputText, setQuestionAttachments, setMessages, storageManager, activeSessionId, scheduleScrollToBottom]);

  // Handle keyboard shortcuts for web

  // Global paste event listener for web (fallback)
  useEffect(() => {
    if (Platform.OS === 'web') {
      const handleGlobalPaste = (event) => {
        // Only handle if input is focused or no specific target
        const activeElement = document.activeElement;
        const isInputFocused = activeElement && (
          activeElement.tagName === 'INPUT' ||
          activeElement.tagName === 'TEXTAREA' ||
          activeElement.contentEditable === 'true'
        );

        // If we're in the input or no specific input is focused, handle clipboard
        if (isInputFocused && activeElement === inputRef.current?._node) {
          console.log('?? Global paste event detected on input');
          event.preventDefault();
          handleClipboardPaste();
        }
      };

      document.addEventListener('paste', handleGlobalPaste);

      return () => {
        document.removeEventListener('paste', handleGlobalPaste);
      };
    }
  }, [handleClipboardPaste]);

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

  // Convert inline markdown to clean HTML (remove markdown syntax, render formatting)
  const convertInlineMarkdownToHtml = useCallback((text) => {
    if (!text && text !== 0) return '';

    let html = escapeHtml(String(text));

    // Store links temporarily to protect URLs from markdown processing
    const linkPlaceholders = [];
    const LINK_PLACEHOLDER_PREFIX = '?LINKPH';
    const LINK_PLACEHOLDER_SUFFIX = 'ENDLINK?';

    // 1. Inline code first (highest priority) - remove backticks, keep content
    html = html.replace(/`([^`]+)`/g, '$1');

    // 2. Links with titles - convert to plain links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\s+"([^"]+)"\)/g, (match, text, url, title) => {
      const placeholder = `${LINK_PLACEHOLDER_PREFIX}${linkPlaceholders.length}${LINK_PLACEHOLDER_SUFFIX}`;
      linkPlaceholders.push(`<a href="${url}" style="color: #0066cc; text-decoration: underline;">${text}</a>`);
      return placeholder;
    });

    // 3. Regular markdown links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
      const placeholder = `${LINK_PLACEHOLDER_PREFIX}${linkPlaceholders.length}${LINK_PLACEHOLDER_SUFFIX}`;
      linkPlaceholders.push(`<a href="${url}" style="color: #0066cc; text-decoration: underline;">${text}</a>`);
      return placeholder;
    });

    // 4. Auto-detect URLs
    html = html.replace(/(?<!href=")(?<!href=&quot;)(https?:\/\/[^\s<>"]+)(?![^<]*<\/a>)/g, (match, url) => {
      const placeholder = `${LINK_PLACEHOLDER_PREFIX}${linkPlaceholders.length}${LINK_PLACEHOLDER_SUFFIX}`;
      linkPlaceholders.push(`<a href="${url}" style="color: #0066cc; text-decoration: underline;">${url}</a>`);
      return placeholder;
    });

    // 5. Email links
    html = html.replace(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/g, (match, email) => {
      const placeholder = `${LINK_PLACEHOLDER_PREFIX}${linkPlaceholders.length}${LINK_PLACEHOLDER_SUFFIX}`;
      linkPlaceholders.push(`<a href="mailto:${email}" style="color: #0066cc; text-decoration: underline;">${email}</a>`);
      return placeholder;
    });

    // Now apply markdown formatting (links are protected)

    // 6. Strikethrough - remove tildes, render as strikethrough
    html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    // 7. Bold (** and __) - remove markers, render as bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

    // 8. Italic (* and _) - remove markers, render as italic
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/_([^_]+)_/g, '<em>$1</em>');

    // 9. Highlight/Mark
    html = html.replace(/==([^=]+)==/g, '<mark style="background-color: #fff3cd; padding: 0.1em 0.2em;">$1</mark>');

    // 10. Restore all links from placeholders
    linkPlaceholders.forEach((link, index) => {
      const placeholder = `${LINK_PLACEHOLDER_PREFIX}${index}${LINK_PLACEHOLDER_SUFFIX}`;
      html = html.replace(placeholder, link);
    });

    return html;
  }, [escapeHtml]);

  // Enhanced HTML generation for professional legal documents - matching RichMessageRenderer
  const createRichHTML = useCallback(async (text, messageElement) => {
    if (!text || typeof text !== 'string') return '';

    // Pre-process: Collapse multiple consecutive empty lines
    const normalizedText = text.replace(/\n{3,}/g, '\n\n');

    // Extract citations for end section
    const citationsFound = new Set();
    const extractCitations = (content) => {
      // DOC:: format
      const docMatches = content.matchAll(/DOC::([^-\s]+)--([a-f0-9-]+)/gi);
      for (const match of docMatches) {
        citationsFound.add(`DOC::${match[1]}--${match[2]}`);
      }

      // CHUNK:: format
      const chunkMatches = content.matchAll(/CHUNK::([^-\s]+)--([a-f0-9-]+)/gi);
      for (const match of chunkMatches) {
        citationsFound.add(`CHUNK::${match[1]}--${match[2]}`);
      }

      // [cite: ...] format
      const citeMatches = content.matchAll(/\[cite:\s*([^\]]+)\]/gi);
      for (const match of citeMatches) {
        const citations = match[1].split(',');
        citations.forEach(c => citationsFound.add(c.trim()));
      }

      // internet citations
      const internetMatches = content.matchAll(/internet-\d+-[a-zA-Z0-9]+/gi);
      for (const match of internetMatches) {
        citationsFound.add(match[0]);
      }
    };

    // Extract all citations from the text
    extractCitations(normalizedText);

    // Helper function to capture Mermaid diagram as base64 image
    const captureMermaidDiagram = async (mermaidCode) => {
      try {
        if (Platform.OS === 'web' && typeof document !== 'undefined') {
          // Find all rendered Mermaid SVGs in the current message
          const mermaidElements = document.querySelectorAll('svg[id^="mermaid-"]');

          // Try to find matching mermaid by comparing code content
          for (const svgElement of mermaidElements) {
            // Get the SVG as string
            const svgString = new XMLSerializer().serializeToString(svgElement);

            // Convert SVG to base64 data URL
            const svgBase64 = btoa(unescape(encodeURIComponent(svgString)));
            const dataUrl = `data:image/svg+xml;base64,${svgBase64}`;

            // Return the first Mermaid diagram found (in order of appearance)
            return dataUrl;
          }
        }
      } catch (error) {
        console.warn('Failed to capture Mermaid diagram:', error);
      }
      return null;
    };

    let htmlParts = [];
    const lines = normalizedText.split('\n');
    let i = 0;
    let mermaidIndex = 0;

    // Handle citations in text - DOC::topic--id, CHUNK::topic--id, [cite: topic--id]
    const processCitations = (content) => {
      let result = content;

      // DOC:: format
      result = result.replace(/DOC::([^-\s]+)--([a-f0-9-]+)/gi, (match, topic, id) => {
        const cleanTopic = topic.trim().replace(/_/g, ' ');
        return `<span style="background-color: #e3f2fd; color: #007acc; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; white-space: nowrap; font-weight: 500;">[?? ${cleanTopic}]</span>`;
      });

      // CHUNK:: format
      result = result.replace(/CHUNK::([^-\s]+)--([a-f0-9-]+)/gi, (match, topic, id) => {
        const cleanTopic = topic.trim().replace(/_/g, ' ');
        return `<span style="background-color: #e3f2fd; color: #007acc; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; white-space: nowrap; font-weight: 500;">[?? ${cleanTopic}]</span>`;
      });

      // [cite: topic--id] format
      result = result.replace(/\[cite:\s*([^\]]+)\]/gi, (match, citations) => {
        const cites = citations.split(',').map(c => c.trim());
        const citeHtml = cites.map(cite => {
          const [topic] = cite.split('--');
          const cleanTopic = topic.trim().replace(/_/g, ' ');
          return `<span style="background-color: #e3f2fd; color: #007acc; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; white-space: nowrap; font-weight: 500;">[?? ${cleanTopic}]</span>`;
        }).join(' ');
        return citeHtml;
      });

      // Internet citations
      result = result.replace(/internet-\d+-[a-zA-Z0-9]+/gi, (match) => {
        return `<span style="background-color: #e8f4f8; color: #1976d2; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; white-space: nowrap; font-weight: 500;">[?? Internet]</span>`;
      });

      return result;
    };

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      // Code blocks - special handling for Mermaid and JSON
      if (trimmed.startsWith('```')) {
        const langMatch = trimmed.slice(3).trim();
        const language = langMatch.toLowerCase() || 'text';
        const codeLines = [];
        i++;

        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          codeLines.push(lines[i]);
          i++;
        }

        const code = codeLines.join('\n');

        // Special handling for Mermaid - capture actual rendered diagram
        if (language === 'mermaid') {
          // Try to detect diagram type for fallback
          let diagramType = 'Diagram';
          if (code.includes('flowchart') || code.includes('graph')) diagramType = 'Flowchart';
          else if (code.includes('timeline')) diagramType = 'Timeline';
          else if (code.includes('sequenceDiagram')) diagramType = 'Sequence Diagram';
          else if (code.includes('classDiagram')) diagramType = 'Class Diagram';
          else if (code.includes('stateDiagram')) diagramType = 'State Diagram';
          else if (code.includes('erDiagram')) diagramType = 'Entity Relationship Diagram';
          else if (code.includes('gantt')) diagramType = 'Gantt Chart';
          else if (code.includes('pie')) diagramType = 'Pie Chart';

          // Try to capture the rendered Mermaid diagram
          const imageDataUrl = await captureMermaidDiagram(code);

          if (imageDataUrl) {
            // Include the actual diagram image
            htmlParts.push(`
              <div style="margin: 16px 0; text-align: center; background-color: #f9f9f9; padding: 16px; border-radius: 8px; border: 1px solid #e0e0e0;">
                <img src="${imageDataUrl}" alt="${diagramType}" style="max-width: 100%; height: auto; display: inline-block;" />
                <div style="margin-top: 8px; font-size: 12px; color: #666; font-style: italic;">${diagramType}</div>
              </div>
            `);
          } else {
            // Fallback to placeholder if image capture fails
            htmlParts.push(`
              <div style="margin: 16px 0; padding: 20px; background-color: #f0f8ff; border: 2px solid #4a9eff; border-radius: 8px; text-align: center;">
                <div style="font-size: 16px; font-weight: bold; color: #0066cc; margin-bottom: 8px;">
                  ?? ${diagramType}
                </div>
                <div style="font-size: 13px; color: #555; font-style: italic;">
                  [Visual diagram rendered in the original document]
                </div>
              </div>
            `);
          }

          mermaidIndex++;
          i++;
          continue;
        }

        // Special handling for JSON - format it nicely or skip if it's structured data
        if (language === 'json') {
          try {
            const parsed = JSON.parse(code);
            // Check if it's citations/mermaid metadata - skip it
            if (parsed.mermaid_blocks || parsed.citations) {
              i++;
              continue;
            }
            // Otherwise show as formatted JSON
            htmlParts.push(`
              <div style="margin: 16px 0; background-color: #f8f9fa; border: 1px solid #e1e4e8; border-radius: 6px; overflow: hidden;">
                <div style="background-color: #f1f3f4; padding: 8px 12px; border-bottom: 1px solid #e1e4e8; font-size: 11px; font-weight: bold; text-transform: uppercase; color: #666;">
                  DATA
                </div>
                <pre style="margin: 0; padding: 12px; background-color: #f8f9fa; font-family: Consolas, Monaco, 'Courier New', monospace; font-size: 13px; line-height: 1.5; overflow-x: auto; white-space: pre;"><code>${escapeHtml(JSON.stringify(parsed, null, 2))}</code></pre>
              </div>
            `);
          } catch (e) {
            // If JSON parsing fails, show as regular code
            htmlParts.push(`
              <div style="margin: 16px 0; background-color: #f8f9fa; border: 1px solid #e1e4e8; border-radius: 6px; overflow: hidden;">
                <div style="background-color: #f1f3f4; padding: 8px 12px; border-bottom: 1px solid #e1e4e8; font-size: 11px; font-weight: bold; text-transform: uppercase; color: #666;">
                  JSON
                </div>
                <pre style="margin: 0; padding: 12px; background-color: #f8f9fa; font-family: Consolas, Monaco, 'Courier New', monospace; font-size: 13px; line-height: 1.5; overflow-x: auto; white-space: pre;"><code>${escapeHtml(code)}</code></pre>
              </div>
            `);
          }
          i++;
          continue;
        }

        // Regular code blocks
        htmlParts.push(`
          <div style="margin: 16px 0; background-color: #f8f9fa; border: 1px solid #e1e4e8; border-radius: 6px; overflow: hidden;">
            <div style="background-color: #f1f3f4; padding: 8px 12px; border-bottom: 1px solid #e1e4e8; font-size: 11px; font-weight: bold; text-transform: uppercase; color: #666;">
              ${language}
            </div>
            <pre style="margin: 0; padding: 12px; background-color: #f8f9fa; font-family: Consolas, Monaco, 'Courier New', monospace; font-size: 13px; line-height: 1.5; overflow-x: auto; white-space: pre;"><code>${escapeHtml(code)}</code></pre>
          </div>
        `);
        i++;
        continue;
      }

      // Headers
      const headerMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
      if (headerMatch) {
        const level = headerMatch[1].length;
        const headerText = headerMatch[2];
        const sizes = ['2em', '1.5em', '1.3em', '1.1em', '1em', '0.9em'];
        const margins = ['24px', '20px', '16px', '14px', '12px', '10px'];

        htmlParts.push(`
          <h${level} style="font-size: ${sizes[level - 1]}; font-weight: bold; margin-top: ${margins[level - 1]}; margin-bottom: ${parseInt(margins[level - 1]) / 2}px; ${level <= 2 ? 'border-bottom: 1px solid #e2e8f0; padding-bottom: 8px;' : ''}">
            ${processCitations(convertInlineMarkdownToHtml(headerText))}
          </h${level}>
        `);
        i++;
        continue;
      }

      // Tables
      if (trimmed.includes('|') && lines[i + 1]?.includes('|')) {
        const tableLines = [];
        while (i < lines.length && lines[i].includes('|')) {
          if (!lines[i].match(/^\s*[\|\-\s:]+\s*$/)) { // Skip separator lines
            tableLines.push(lines[i]);
          }
          i++;
        }

        if (tableLines.length > 0) {
          const headerCells = tableLines[0].split('|').map(c => c.trim()).filter(c => c);
          const dataRows = tableLines.slice(1).map(row =>
            row.split('|').map(c => c.trim()).filter(c => c)
          );

          htmlParts.push(`
            <table style="border-collapse: collapse; width: 100%; margin: 16px 0; border: 1px solid #ddd;">
              <thead>
                <tr style="background-color: #f2f2f2;">
                  ${headerCells.map(cell => `<th style="padding: 10px; text-align: left; border: 1px solid #ddd; font-weight: bold;">${processCitations(convertInlineMarkdownToHtml(cell))}</th>`).join('')}
                </tr>
              </thead>
              <tbody>
                ${dataRows.map((row, idx) => `
                  <tr style="background-color: ${idx % 2 === 0 ? '#fff' : '#f9f9f9'};">
                    ${row.map(cell => `<td style="padding: 10px; border: 1px solid #ddd;">${processCitations(convertInlineMarkdownToHtml(cell))}</td>`).join('')}
                  </tr>
                `).join('')}
              </tbody>
            </table>
          `);
        }
        continue;
      }

      // Blockquotes
      if (trimmed.startsWith('>')) {
        const quoteLines = [];
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          quoteLines.push(lines[i].trim().slice(1).trim());
          i++;
        }

        htmlParts.push(`
          <blockquote style="border-left: 4px solid #007acc; background-color: #f7fafc; margin: 12px 0; padding: 12px 16px; font-style: italic; color: #555;">
            ${processCitations(convertInlineMarkdownToHtml(quoteLines.join(' ')))}
          </blockquote>
        `);
        continue;
      }

      // Ordered lists
      if (trimmed.match(/^\d+\.\s+/)) {
        const listItems = [];
        while (i < lines.length && lines[i].trim().match(/^\d+\.\s+/)) {
          const content = lines[i].trim().replace(/^\d+\.\s+/, '');
          listItems.push(content);
          i++;
        }

        htmlParts.push(`
          <ol style="margin: 12px 0; padding-left: 24px;">
            ${listItems.map(item => `<li style="margin: 6px 0; line-height: 1.6;">${processCitations(convertInlineMarkdownToHtml(item))}</li>`).join('')}
          </ol>
        `);
        continue;
      }

      // Unordered lists
      if (trimmed.match(/^[\-\*\+]\s+/)) {
        const listItems = [];
        while (i < lines.length && lines[i].trim().match(/^[\-\*\+]\s+/)) {
          const content = lines[i].trim().replace(/^[\-\*\+]\s+/, '');
          listItems.push(content);
          i++;
        }

        htmlParts.push(`
          <ul style="margin: 12px 0; padding-left: 24px;">
            ${listItems.map(item => `<li style="margin: 6px 0; line-height: 1.6;">${processCitations(convertInlineMarkdownToHtml(item))}</li>`).join('')}
          </ul>
        `);
        continue;
      }

      // Horizontal rule
      if (trimmed.match(/^(\*{3,}|-{3,}|_{3,})$/)) {
        htmlParts.push('<hr style="border: none; border-top: 2px solid #e2e8f0; margin: 20px 0;">');
        i++;
        continue;
      }

      // Empty lines
      if (!trimmed) {
        i++;
        continue;
      }

      // Regular paragraph - with intelligent legal document handling
      const paraLines = [];
      while (i < lines.length && lines[i].trim() &&
        !lines[i].trim().startsWith('#') &&
        !lines[i].trim().startsWith('```') &&
        !lines[i].trim().startsWith('>') &&
        !lines[i].trim().match(/^[\-\*\+\d]\.\s/) &&
        !lines[i].includes('|')) {
        paraLines.push(lines[i]);
        i++;
      }

      if (paraLines.length > 0) {
        const paraText = paraLines.join('\n');

        // Detect legal document patterns
        const isLegalHeader = /\*\*(?:IN THE |COURT OF|UNDER SECTION|FIR No\.|MATTER OF|VERSUS|APPLICATION|PRAYER|SHOWETH)/i.test(paraText);
        const isSectionPara = /^\d+\.\s+That\s+/i.test(paraText.trim());
        const isClosingLine = /^(?:Place:|Date:|Petitioner|Through|Advocate|Deponent|Verified|Identified)/i.test(paraText.trim());

        if (isLegalHeader) {
          // Legal header - split lines and render each separately with spacing (not pre-line)
          const headerLines = paraText.split('\n').filter(l => l.trim());
          const headerHtml = headerLines.map(line =>
            `<div style="text-align: center; margin-bottom: 4px; font-weight: 500;">${processCitations(convertInlineMarkdownToHtml(line))}</div>`
          ).join('');

          htmlParts.push(`
            <div style="margin: 24px 0 20px 0; line-height: 1.6;">
              ${headerHtml}
            </div>
          `);
        } else if (isClosingLine) {
          // Closing/signature lines - left aligned with spacing
          htmlParts.push(`
            <div style="margin-top: 20px; margin-bottom: 12px; line-height: 1.6; font-weight: 500;">
              ${processCitations(convertInlineMarkdownToHtml(paraText))}
            </div>
          `);
        } else if (isSectionPara) {
          // Numbered legal sections - justified, proper paragraph spacing
          htmlParts.push(`
            <p style="margin: 12px 0; line-height: 1.7; text-align: justify; text-indent: 0;">
              ${processCitations(convertInlineMarkdownToHtml(paraText))}
            </p>
          `);
        } else {
          // Regular paragraph - justified text
          htmlParts.push(`
            <p style="margin: 12px 0; line-height: 1.7; text-align: justify;">
              ${processCitations(convertInlineMarkdownToHtml(paraText))}
            </p>
          `);
        }
      }
    }

    // Add Citations Section at the end if citations were found
    if (citationsFound.size > 0) {
      htmlParts.push(`
        <div style="margin-top: 40px; padding-top: 20px; border-top: 3px solid #007acc;">
          <h2 style="font-size: 1.3em; font-weight: bold; color: #007acc; margin-bottom: 16px;">
            ?? References & Citations
          </h2>
          <ol style="margin: 0; padding-left: 24px; line-height: 1.8;">
      `);

      let citationIndex = 1;
      citationsFound.forEach(citation => {
        let citationText = '';
        let citationType = '';

        if (citation.startsWith('DOC::')) {
          const match = citation.match(/DOC::([^-\s]+)--([a-f0-9-]+)/i);
          if (match) {
            const topic = match[1].trim().replace(/_/g, ' ');
            citationText = topic;
            citationType = 'Document';
          }
        } else if (citation.startsWith('CHUNK::')) {
          const match = citation.match(/CHUNK::([^-\s]+)--([a-f0-9-]+)/i);
          if (match) {
            const topic = match[1].trim().replace(/_/g, ' ');
            citationText = topic;
            citationType = 'Document Section';
          }
        } else if (citation.startsWith('internet-')) {
          citationText = 'Internet Source';
          citationType = 'Web Reference';
        } else {
          // [cite: ...] format
          const parts = citation.split('--');
          if (parts.length > 0) {
            citationText = parts[0].trim().replace(/_/g, ' ');
            citationType = 'Document';
          }
        }

        if (citationText) {
          htmlParts.push(`
            <li style="margin: 8px 0; color: #333;">
              <strong style="color: #007acc;">${citationText}</strong>
              <span style="font-size: 0.9em; color: #666; margin-left: 8px;">(${citationType})</span>
            </li>
          `);
          citationIndex++;
        }
      });

      htmlParts.push(`
          </ol>
        </div>
      `);
    }

    return `<div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; font-size: 15px; line-height: 1.6; color: #333;">${htmlParts.join('')}</div>`;
  }, [convertInlineMarkdownToHtml, escapeHtml]);

  // Helper function to create HTML table for advanced clipboard
  const createTableHTML = useCallback((text) => {
    const lines = text.split('\n').filter(line => line.trim());
    let headerCells = [];
    const dataRows = [];
    let foundHeader = false;

    for (const line of lines) {
      if (line.includes('|') && line.trim().length > 1) {
        const cells = line.split('|').map(cell => cell.trim()).filter(cell => cell.length > 0);

        if (!foundHeader && cells.length > 1) {
          headerCells = cells;
          foundHeader = true;
        } else if (foundHeader && cells.length > 1 && !line.match(/^\s*[\|\-\s:]+\s*$/)) {
          while (cells.length < headerCells.length) cells.push('');
          dataRows.push(cells.slice(0, headerCells.length));
        }
      }
    }

    if (headerCells.length === 0) return text;

    const htmlTable = `
      <table border="1" style="border-collapse: collapse; width: 100%; font-family: 'Segoe UI', Arial, sans-serif; margin: 16px 0;">
        <thead>
          <tr style="background-color: #f2f2f2;">
            ${headerCells.map(cell => `<th style="padding: 12px 8px; text-align: left; border: 1px solid #ddd; font-weight: bold;">${cell}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
          ${dataRows.map((row, index) => `
            <tr style="background-color: ${index % 2 === 0 ? '#fff' : '#f9f9f9'};">
              ${row.map(cell => `<td style="padding: 12px 8px; border: 1px solid #ddd;">${cell}</td>`).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;

    return htmlTable;
  }, []);

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
            let htmlContent;

            // If it has tables, use the table-specific HTML generator
            if (text.includes('|') && text.split('\n').filter(line => line.includes('|')).length >= 2) {
              htmlContent = createTableHTML(text);
            } else {
              // Use comprehensive rich HTML generator (now async for Mermaid capture)
              htmlContent = await createRichHTML(text);
            }

            console.log('?? Generating rich HTML for copy...');
            console.log('   - Content length:', text.length);
            console.log('   - Has tables:', text.includes('|'));
            console.log('   - Has code blocks:', text.includes('```'));
            console.log('   - Has headers:', text.includes('#'));
            console.log('   - Has Mermaid:', text.includes('```mermaid'));

            const plainContent = cleanMarkdownForCopy(text);

            console.log('? HTML generated:', htmlContent.length, 'bytes');
            console.log('?? HTML Preview (first 800 chars):', htmlContent.substring(0, 800));

            await navigator.clipboard.write([
              new ClipboardItem({
                'text/html': new Blob([htmlContent], { type: 'text/html' }),
                'text/plain': new Blob([plainContent], { type: 'text/plain' })
              })
            ]);

            console.log('? Successfully copied to clipboard with rich text format');

            // Provide more specific feedback
            if (text.includes('```mermaid')) {
              Alert.alert('Copied', 'Rich content with diagrams copied to clipboard (optimized for Word)');
            } else if (text.includes('![')) {
              Alert.alert('Copied', 'Rich content with images copied to clipboard (optimized for Word)');
            } else if (text.includes('|')) {
              Alert.alert('Copied', 'Table copied to clipboard (optimized for Word)');
            } else {
              Alert.alert('Copied', 'Rich formatted text copied to clipboard (optimized for Word)');
            }
            return;
          } catch (clipboardError) {
            console.error('? Advanced clipboard failed:', clipboardError);
            console.warn('Advanced clipboard failed, falling back:', clipboardError);
          }
        } else {
          console.log('?? Rich copy not available:', {
            hasRichContent,
            hasClipboard: !!navigator.clipboard,
            hasWrite: !!(navigator.clipboard && navigator.clipboard.write)
          });
        }
      }

      // Fallback to simple text copy
      console.log('?? Using fallback plain text copy');
      const formattedText = cleanMarkdownForCopy(text);
      await Clipboard.setString(formattedText);
      Alert.alert('Copied', 'Message copied to clipboard');
    } catch (error) {
      console.error('Error copying to clipboard:', error);
      Alert.alert('Error', 'Failed to copy message');
    }
  }, [cleanMarkdownForCopy, createTableHTML, createRichHTML]);

  const handleShareMessage = useCallback(async (text) => {
    try {
      // Use the same rich text processing as copy functionality
      const formattedText = cleanMarkdownForCopy(text);

      // For web, try to use the Web Share API with better formatting
      if (Platform.OS === 'web' && navigator.share) {
        try {
          await navigator.share({
            title: 'Citra AI',
            text: formattedText,
          });
          return;
        } catch (webShareError) {
          // Fall back to React Native Share if Web Share API fails
          console.warn('Web Share API failed, falling back to React Native Share:', webShareError);
        }
      }

      // Use React Native Share with formatted text
      await Share.share({
        message: formattedText,
        title: 'Citra AI',
      });
    } catch (error) {
      console.error('Error sharing message:', error);
      Alert.alert('Error', 'Failed to share message');
    }
  }, [cleanMarkdownForCopy]);

  const handleEditMessage = useCallback((message) => {
    setEditingMessageId(message.id);
    setEditingText(message.text);
  }, []);

  const handleCancelEdit = useCallback(() => {
    setEditingMessageId(null);
    setEditingText('');
  }, []);

  const handleOpenReader = useCallback((documentId) => {
    console.log('?? HANDLE_OPEN_READER: Opening Reader with document_id:', documentId);
    setInitialReaderDocumentId(documentId);
    // All callers are chat surfaces (upload-success message, message actions),
    // so closing the reader must return to the chat, not the home panel.
    setReaderLaunchContext('chat');
    setShowReader(true);
    if (Platform.OS === 'web') {
      navigateToReader();
    }
  }, []);

  const handleSaveEditedMessage = useCallback(async (messageId, newText) => {
    console.log('?? [SAVE_EDITED] handleSaveEditedMessage called', {
      messageId,
      newText: newText?.substring(0, 50),
      messagesCount: messages.length
    });

    // Find the message being edited
    const messageToEdit = messages.find(msg => msg.id === messageId);

    if (!messageToEdit) {
      console.error('? [SAVE_EDITED] Message not found:', messageId);
      Alert.alert('Error', 'Message not found');
      return;
    }

    console.log('? [SAVE_EDITED] Found message:', {
      id: messageToEdit.id,
      sender: messageToEdit.sender,
      hasMessagePairId: !!messageToEdit.messagePairId,
      originalText: messageToEdit.text?.substring(0, 50)
    });

    // Check if the text has actually changed
    if (messageToEdit.text === newText.trim()) {
      // No change, just reset editing state
      setEditingMessageId(null);
      setEditingText('');
      return;
    }

    // Only proceed with API call if it's a user message and text has changed
    if (messageToEdit.sender === 'user') {
      console.log('Editing user message:', messageToEdit);
      // Check if we have a valid message pair ID from Chat
      if (!messageToEdit.messagePairId) {
        Alert.alert('Error', 'Cannot edit this message - no message pair ID found. This might be a new message that hasn\'t been saved yet.');
        setEditingMessageId(null);
        setEditingText('');
        return;
      }

      try {
        setIsLoading(true);
        setIsGenerating(true);

        // Find the corresponding bot message and convert it to typing indicator
        const botMessageBaseId = `bot-${messageToEdit.messagePairId}`;
        const existingBotMessage = messages.find(msg =>
          msg.id === botMessageBaseId || msg.id.startsWith(`${botMessageBaseId}-`)
        );
        const botMessageId = existingBotMessage ? existingBotMessage.id : botMessageBaseId;

        // Update the local message and convert bot message to typing
        const updatedMessages = messages.map(msg => {
          if (msg.id === messageId) {
            return { ...msg, text: newText };
          } else if (msg.id === botMessageId || msg.id.startsWith(`${botMessageBaseId}-`)) {
            // Convert bot message to typing indicator during edit processing
            return {
              ...msg,
              text: '',
              isTyping: true,
              shouldAnimate: false,
              key: `${botMessageBaseId}_typing_${Date.now()}` // Force re-render
            };
          }
          return msg;
        });

        setMessages(updatedMessages);
        scheduleScrollToMessage(messageId, 2); // Schedule scroll to edited message

        // Update AsyncStorage
        await storageManager.updateMessagePair(activeSessionId, messageId, newText, messageToEdit.messagePairId);

        console.log('Editing message with Chat ID:', messageToEdit.messagePairId);
        console.log('New text:', newText);

        // Prepare data according to new API requirements - get messages only from current session
        let chats;
        if (browserChatManager.isBrowserOnlyMode()) {
          // Use browser chat manager for messages if MongoDB chat history is disabled
          chats = browserChatManager.getRecentMessages(activeSessionId, 20);
          console.log('?? [BROWSER_EDIT] Session:', activeSessionId, 'Browser chats for API:', chats.length);
        } else {
          // Use traditional AsyncStorage for messages if MongoDB chat history is enabled
          const currentSessionMessages = await storageManager.getMessagePairs(activeSessionId);
          chats = prepareChatHistory(currentSessionMessages);
          console.log('?? [EDIT_FIX] Session:', activeSessionId, 'Messages count:', currentSessionMessages.length, 'Chats for API:', chats.length);
        }

        const userInfo = await storageManager.getUserDetail();
        const userEmail = await getUserEmail();

        // Fixed model key since we're no longer using model selection
        const modelKey = 'citra-ai-lite';

        // Prepare filtered conversation history for edit mode (drop only system-typed messages)
        let conversationHistory = [];
        if (messages && messages.length > 0) {
          const scoped = messages.slice(-60);
          const filtered = scoped.filter(m => m.type !== 'system');
          conversationHistory = filtered.slice(-20).map(msg => ({
            role: msg.sender === 'user' ? 'user' : 'assistant',
            content: msg.text,
            timestamp: msg.timestamp
          }));
          console.log('?? [HISTORY_FILTER_UI][EDIT] raw:', messages.length, 'scoped:', scoped.length, 'after_system_filter:', filtered.length, 'final_sent:', conversationHistory.length);
        }

        console.log('Sending edit request with:', {
          user_id: userEmail,
          chat_session_id: activeSessionId,
          query: newText,
          chats: chats,
          user_info: userInfo,
          edit_mode: true,
          message_pair_id: messageToEdit.messagePairId,
          ai_model: modelKey,
          conversation_history: conversationHistory
        });

        console.log('?? Edit mode - Using fixed model key:', modelKey);

        // Call the Query API in edit mode with the correct Chat message pair ID
        const response = await authService.authenticatedFetch(QUERY_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
          },
          body: JSON.stringify({
            user_id: userEmail,
            chat_session_id: activeSessionId,
            query: newText,
            chats: chats,
            user_info: userInfo,
            edit_mode: true,
            message_pair_id: messageToEdit.messagePairId, // Use the Chat _id
            ai_model: modelKey,
            // Folder scoping retired with folder_management.py — see the
            // main query payload for the same simplification.
            use_personal_data: false,
            vault: 'none',
            // Enterprise flags
            enterprise_enabled: !!useEnterprise,
            use_enterprise_data: !!useEnterprise,
            // Add conversation history for better edit context
            conversation_history: conversationHistory
          })
        });

        if (response.ok) {
          const data = await response.json();

          console.log('Edit API response:', data);

          // Hide Stop Generating Button immediately when edit reply is received

          setIsLoading(false);
          setIsGenerating(false);

          // Update the corresponding bot message with the new response
          if (data.response) {
            console.log('Looking for bot message with ID:', botMessageId);

            const finalUpdatedMessages = updatedMessages.map(msg => {
              if (msg.id === botMessageId || msg.id.startsWith(`${botMessageBaseId}-`)) {
                console.log('Updating bot message:', msg.id);
                return {
                  ...msg,
                  text: data.response,
                  isTyping: false, // Remove typing indicator
                  isUpdated: false, // Treat as fresh response
                  shouldAnimate: false, // No animation for bot messages
                  key: `${botMessageId}_updated_${Date.now()}` // Force re-render by changing key
                };
              }
              return msg;
            });

            setMessages(finalUpdatedMessages);
            scheduleScrollToMessage(botMessageId, 2); // Schedule scroll to updated bot reply

            // Update AsyncStorage with both the specific bot message and the complete message list
            await storageManager.updateMessagePair(activeSessionId, botMessageId, data.response, messageToEdit.messagePairId);
            await storageManager.storeMessagePairs(activeSessionId, finalUpdatedMessages);
          }

          console.log('Message pair updated successfully');
        } else {
          throw new Error(`API returned status ${response.status}`);
        }
      } catch (error) {
        console.error('Error updating message:', error);

        // Revert the local change on error - restore original bot message too
        setMessages(prevMessages =>
          prevMessages.map(msg => {
            if (msg.id === messageId) {
              return { ...msg, text: messageToEdit.text };
            } else if (msg.id === botMessageId || msg.id.startsWith(`${botMessageBaseId}-`)) {
              // Restore original bot message and remove typing indicator
              const originalBotMessage = messages.find(m =>
                m.id === botMessageId || m.id.startsWith(`${botMessageBaseId}-`)
              );
              return {
                ...originalBotMessage,
                isTyping: false,
                key: `${originalBotMessage.id}_restored_${Date.now()}`
              };
            }
            return msg;
          })
        );
        scheduleScrollToMessage(messageId, 2); // Scroll to reverted message

        let errorMessage = 'Failed to update message. Please try again.';
        if (error.response) {
          errorMessage = `Server error: ${error.response.status} - ${error.response.data?.error || 'Unknown error'}`;
        } else if (error.request) {
          errorMessage = 'No response from server. Please check your internet connection.';
        } else if (error.message) {
          errorMessage = error.message;
        }
        Alert.alert('Error', errorMessage);
      } finally {
        setIsLoading(false);
        setIsGenerating(false);
      }
    } else {
      // For bot messages, just update locally (no API call needed)
      const updatedMessages = messages.map(msg =>
        msg.id === messageId ? { ...msg, text: newText, isUpdated: true } : msg
      );
      setMessages(updatedMessages);
      scheduleScrollToMessage(messageId, 2); // Schedule scroll to edited bot message

      // Update AsyncStorage
      await storageManager.updateMessagePair(activeSessionId, messageId, newText);
    }

    setEditingMessageId(null);
    setEditingText('');
  }, [messages, activeSessionId, setMessages, storageManager, prepareChatHistory, getSessionSummary, scheduleScrollToMessage, loadTranscripts, getUserEmail]);

  const handleSaveInlineEdit = useCallback(() => {
    console.log('?? [INLINE_EDIT] handleSaveInlineEdit called', {
      editingMessageId,
      editingText: editingText?.substring(0, 50),
      textLength: editingText?.length,
      trimmedLength: editingText?.trim().length
    });

    if (editingMessageId && editingText.trim()) {
      console.log('?? [INLINE_EDIT] Calling handleSaveEditedMessage');
      handleSaveEditedMessage(editingMessageId, editingText.trim());
    } else {
      console.warn('?? [INLINE_EDIT] Condition not met:', {
        hasEditingMessageId: !!editingMessageId,
        hasText: !!editingText?.trim()
      });
    }
  }, [editingMessageId, editingText, handleSaveEditedMessage]);


  // Update the deleteTranscript function to use showAlert
  const deleteTranscript = useCallback((transcriptIdOrObject) => {
    // Extract ID if full transcript object is passed (from folder view) or use as-is if it's just the ID
    const transcriptId = typeof transcriptIdOrObject === 'object'
      ? (transcriptIdOrObject.transcript_id || transcriptIdOrObject.id || transcriptIdOrObject._id)
      : transcriptIdOrObject;

    console.log('deleteTranscript called with:', transcriptIdOrObject);
    console.log('deleteTranscript extracted ID:', transcriptId);

    if (!transcriptId) {
      console.error('? DELETE: No valid transcript ID found');
      showAlert('Error', 'Invalid transcript ID');
      return;
    }

    showAlert(
      'Delete Transcript',
      'Are you sure you want to delete this transcript?',
      [
        {
          text: 'Cancel',
          style: 'cancel'
        },
        {
          text: 'Delete',
          onPress: async () => {
            try {
              const response = await authService.authenticatedFetch(`${TRANSCRIPT_URL}/${transcriptId}`, {
                method: 'DELETE'
              });

              if (response) {
                // Update transcript list state (for menu -> history -> transcript view)
                setTranscripts(prev => prev.filter(transcript => transcript.id !== transcriptId));

                // Update folder content state (for folder view)
                setFolderContent(prevContent => {
                  if (prevContent && prevContent.data) {
                    const newData = prevContent.data.filter(item =>
                      item.id !== transcriptId &&
                      item.document_id !== transcriptId
                    );
                    setDocuments(newData); // Update documents state too since folder view uses documents
                    return {
                      ...prevContent,
                      data: newData
                    };
                  }
                  return prevContent;
                });

                // Update open folder content state (for currently open folder view)
                setOpenFolderContent(prev => {
                  if (prev && prev.documents) {
                    const newDocuments = prev.documents.filter(item =>
                      item.id !== transcriptId &&
                      item.document_id !== transcriptId
                    );
                    return {
                      ...prev,
                      documents: newDocuments,
                      total: newDocuments.length
                    };
                  }
                  return prev;
                });

                // Update documents list state (for documents view)
                setDocuments(prev => prev.filter(item => item.id !== transcriptId && item.document_id !== transcriptId));

                if (response.message) {
                  showAlert('Success', response.data.message);
                }
              } else {
                throw new Error('Failed to delete transcript');
              }
            } catch (error) {
              console.error('Error deleting transcript:', error);

              let errorMessage = 'Failed to delete transcript. Please try again.';
              if (error.response?.data?.message) {
                errorMessage = error.response.data.message;
              } else if (error.response?.status) {
                errorMessage = `Server error: ${error.response.status}`;
              }

              showAlert('Error', errorMessage);
            }
          },
          style: 'destructive'
        }
      ]
    );
  }, []);



  const viewTranscript = useCallback(async (transcript) => {
    console.log('??? [VIEW_TRANSCRIPT] Opening transcript in reader:', {
      id: transcript.id,
      transcript_id: transcript.transcript_id,
      _id: transcript._id,
      type: transcript.type,
      source: transcript.source,
      topic: transcript.topic,
      videoUrl: transcript.videoUrl,
      audioUrl: transcript.audioUrl
    });

    // Validate transcript ID before opening reader
    const transcriptId = transcript.id || transcript.transcript_id || transcript._id;
    if (!transcriptId) {
      console.error('??? [VIEW_TRANSCRIPT] ERROR: No valid transcript ID found:', transcript);
      Alert.alert('Error', 'Cannot view transcript: Invalid transcript ID');
      return;
    }

    // Open transcript in reader panel instead of modal
    console.log('?? [VIEW_TRANSCRIPT] Opening Reader with transcript ID:', transcriptId);
    setInitialReaderDocumentId(transcriptId);
    setShowReader(true);
  }, []);

  const editTranscript = useCallback(async (transcript) => {
    setIsLoadingTranscriptContent(true);
    console.log('?? [EDIT_TRANSCRIPT] Loading transcript for editing:', {
      id: transcript.id,
      transcript_id: transcript.transcript_id,
      _id: transcript._id,
      type: transcript.type,
      source: transcript.source,
      topic: transcript.topic,
      videoUrl: transcript.videoUrl,
      audioUrl: transcript.audioUrl
    });

    // Validate transcript ID before making API call
    const transcriptId = transcript.id || transcript.transcript_id || transcript._id;
    if (!transcriptId) {
      console.error('?? [EDIT_TRANSCRIPT] ERROR: No valid transcript ID found:', transcript);
      Alert.alert('Error', 'Cannot edit transcript: Invalid transcript ID');
      setIsLoadingTranscriptContent(false);
      return;
    }

    try {
      let response;

      // Enhanced detection: Check multiple indicators for video transcripts
      const isVideoTranscript = transcript.type === 'video' ||
        transcript.source === 'video_transcripts' ||
        (transcript.videoUrl && !transcript.audioUrl);

      // Use different API endpoints based on transcript type/source
      if (isVideoTranscript) {
        // Use video transcript API
        console.log('?? [EDIT_TRANSCRIPT] Using video API endpoint for ID:', transcriptId);
        response = await authService.authenticatedFetch(`${API_ENDPOINTS.CITRA_AI.BASE}/transcripts/${transcriptId}`);
      } else {
        // Use audio transcript API (default)
        console.log('?? [EDIT_TRANSCRIPT] Using audio API endpoint for ID:', transcriptId);
        response = await authService.authenticatedFetch(`${TRANSCRIPT_URL}/${transcriptId}`);
      }

      if (response.ok) {
        const data = await response.json();
        console.log('?? [EDIT_TRANSCRIPT] API response received for editing:', {
          hasFullTranscription: !!data.full_transcription,
          hasTranscript: !!data.transcript,
          topic: data.topic
        });

        // Handle different response formats for video vs audio transcripts
        if (isVideoTranscript) {
          setEditingTranscript({
            ...transcript,
            text: data.full_transcription || transcript.text, // Use full_transcription for video
            topic: data.topic || transcript.topic,
            duration: null, // Video transcripts don't have duration
            videoUrl: data.video_url || transcript.videoUrl,
            originalFilename: data.original_filename || transcript.originalFilename
          });
        } else {
          setEditingTranscript({
            ...transcript,
            text: data.transcript || transcript.text, // Use transcript for audio
            topic: data.topic || transcript.topic,
            duration: data.duration || transcript.duration
          });
        }
      } else {
        // Fallback to partial data if API call fails
        console.warn('?? [EDIT_TRANSCRIPT] API call failed, using cached transcript data for editing');
        setEditingTranscript(transcript);
      }
    } catch (error) {
      console.error('Error loading full transcript content for editing:', error);
      // Fallback to partial data
      setEditingTranscript(transcript);
    } finally {
      setIsLoadingTranscriptContent(false);
    }
    setIsTranscriptEditModalVisible(true);
  }, []);

  const downloadTranscript = useCallback(async (transcript) => {
    console.log('?? DOWNLOAD_TRANSCRIPT: Starting download process for transcript:', {
      id: transcript.id,
      topic: transcript.topic,
      type: transcript.type
    });

    try {
      // Use environment-aware service URL from API_CONFIG
      const serviceBaseUrl = API_CONFIG.CITRA_SERVICE_URL;

      const transcriptId = transcript.id || transcript.transcript_id || transcript._id;
      const downloadEndpoint = `${serviceBaseUrl}/api/transcripts/${transcriptId}/download`;

      console.log('?? DOWNLOAD_TRANSCRIPT: Requesting presigned URL from:', downloadEndpoint);

      const response = await authService.authenticatedFetchJson(downloadEndpoint);

      if (!response || !response.download_url) {
        throw new Error('Download URL not available from service');
      }

      const presignedUrl = response.download_url;
      const filename = response.filename || transcript.topic || 'transcript';

      console.log('? DOWNLOAD_TRANSCRIPT: Received presigned URL, opening download...', {
        filename,
        type: response.type
      });

      // Open presigned URL directly (avoids CORS issues)
      if (Platform.OS === 'web') {
        const a = document.createElement('a');
        a.href = presignedUrl;
        a.download = filename;
        a.target = '_blank';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        console.log('? DOWNLOAD_TRANSCRIPT: Download initiated successfully');
      }
    } catch (error) {
      console.error('? DOWNLOAD_TRANSCRIPT: Error downloading transcript:', {
        transcriptId: transcript.id,
        type: transcript.type,
        error: error.message,
        stack: error.stack
      });
      Alert.alert('Error', `Failed to download transcript file: ${error.message}`);
    }
  }, []);

  const handleSaveEditedTranscript = useCallback(async (transcriptId, newTopic, newText) => {
    try {
      console.log('Saving transcript with:', {
        transcript_id: transcriptId,  // V2 API expects 'transcript_id'
        topic: newTopic,
        transcript: newText
      });

      // Call the V2 transcript update API with authentication
      const response = await authService.authenticatedFetch(`${TRANSCRIPT_URL}/${transcriptId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          topic: newTopic || '',
          transcript: newText || ''
        })
      });

      console.log('Transcript edit response:', response);

      if (response) {
        // Update local state
        setTranscripts(prev => prev.map(transcript =>
          transcript.id === transcriptId
            ? { ...transcript, topic: newTopic, text: newText }
            : transcript
        ));

        // Also update the selected transcript if it's being viewed
        if (selectedTranscript && selectedTranscript.id === transcriptId) {
          setSelectedTranscript(prev => ({
            ...prev,
            topic: newTopic,
            text: newText
          }));
        }

        Alert.alert('Success', 'Transcript updated successfully');
      } else {
        throw new Error(`API returned status ${response.status}: ${response.data?.message || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Error updating transcript:', error);

      let errorMessage = 'Failed to update transcript. Please try again.';

      if (error.response) {
        // Server responded with error status
        errorMessage = `Server error (${error.response.status}): ${error.response.data?.message || error.response.data?.error || 'Unknown server error'}`;
      } else if (error.request) {
        // Request was made but no response received
        errorMessage = 'No response from server. Please check your internet connection and try again.';
      } else if (error.code === 'ECONNABORTED') {
        // Request timeout
        errorMessage = 'Request timed out. Please try again.';
      } else {
        // Something else happened
        errorMessage = error.message || 'An unexpected error occurred';
      }

      Alert.alert('Error', errorMessage);
      throw error; // Re-throw to be handled by the modal
    }
  }, [selectedTranscript]);

  // Add TranscriptViewModal component
  const TranscriptViewModal = ({ isVisible, onClose, transcript, theme }) => {
    const formatDuration = (seconds) => {
      const mins = Math.floor(seconds / 60);
      const secs = seconds % 60;
      return `${mins}:${secs.toString().padStart(2, '0')}`;
    };

    const handleCopyTranscript = useCallback(async () => {
      try {
        const textToCopy = transcript?.text || '';
        if (!textToCopy || !textToCopy.trim()) {
          Alert.alert('Nothing to copy', 'This transcript has no text content available to copy.');
          return;
        }

        await Clipboard.setStringAsync(textToCopy);
        Alert.alert('Copied', 'Transcript content copied to your clipboard.');
      } catch (error) {
        console.error('Error copying transcript content:', error);
        Alert.alert('Error', 'Unable to copy the transcript content. Please try again.');
      }
    }, [transcript]);

    const headerIconBackground = theme?.isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.06)';

    return (
      <Modal
        animationType="slide"
        transparent={true}
        visible={isVisible}
        onRequestClose={onClose}
      >
        <View style={[styles.modalContainer, { backgroundColor: 'rgba(0, 0, 0, 0.5)' }]}>
          <View style={[styles.modalContent, { backgroundColor: theme.background }]}
          >
            <View style={styles.modalHeader}>
              <Text style={[styles.modalTitle, { color: theme.text }]}>Transcript Details</Text>
              <View style={styles.modalHeaderActions}>
                <TouchableOpacity
                  onPress={handleCopyTranscript}
                  style={[styles.modalHeaderIconButton, { backgroundColor: headerIconBackground }]}
                  accessibilityRole="button"
                  accessibilityLabel="Copy transcript content"
                >
                  <Ionicons name="copy-outline" size={20} color={theme.text} />
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={onClose}
                  style={[styles.modalHeaderIconButton, { backgroundColor: headerIconBackground }]}
                  accessibilityRole="button"
                  accessibilityLabel="Close transcript viewer"
                >
                  <Ionicons name="close" size={22} color={theme.text} />
                </TouchableOpacity>
              </View>
            </View>

            <Text style={[styles.transcriptViewTopic, { color: theme.text }]}
            >
              {transcript?.topic}
            </Text>

            <View style={styles.transcriptViewMeta}>
              <Text style={[styles.noteTimestamp, { color: theme.placeholderText }]}
              >
                {transcript ? new Date(transcript.timestamp).toLocaleString() : ''}
              </Text>
              <Text style={[styles.transcriptDuration, { color: theme.placeholderText }]}
              >
                {transcript ? formatDuration(transcript.duration) : ''}
              </Text>
            </View>

            <ScrollView style={styles.noteViewScrollContainer}>
              <Text style={[styles.noteViewText, { color: theme.text }]}
              >
                {transcript?.text}
              </Text>
            </ScrollView>
          </View>
        </View>
      </Modal>
    );
  };

  // Update renderTranscriptItem to include onEdit
  const renderTranscriptItem = useCallback(
    ({ item }) => (
      <TranscriptItem
        item={item}
        theme={theme}
        onDelete={deleteTranscript}
        onView={viewTranscript}
        onEdit={editTranscript}
        onDownload={downloadTranscript}
      />
    ),
    [theme, deleteTranscript, viewTranscript, editTranscript, downloadTranscript]
  );

  // Add ONLY the missing document functions after existing functions




  // Replace document function - deletes old document and uploads new one



  // Wrapper function for PreloadScreen that routes to appropriate delete function based on document type

  // Helper function to detect if a document is an audio file (transcript)




  const handleSaveEditedDocument = useCallback(async (documentId, newTitle, newText) => {
    try {
      // Use V2 API endpoint for document updates with authentication
      const response = await authService.authenticatedFetch(`${DOCUMENT_URL}/${documentId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          topic: newTitle || '',
          extracted_text: newText || ''
        })
      });

      if (response) {
        setDocuments(prev => prev.map(document =>
          document.id === documentId
            ? { ...document, title: newTitle, text: newText }
            : document
        ));

        if (selectedDocument && selectedDocument.id === documentId) {
          setSelectedDocument(prev => ({
            ...prev,
            title: newTitle,
            text: newText
          }));
        }

        Alert.alert('Success', 'Document updated successfully');
      } else {
        throw new Error(`API returned status ${response.status}: ${response.data?.message || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Error updating document:', error);

      let errorMessage = 'Failed to update document. Please try again.';

      if (error.response) {
        if (error.response.status === 404) {
          errorMessage = 'Document update endpoint not found. This feature may not be implemented yet.';
        } else {
          errorMessage = `Server error (${error.response.status}): ${error.response.data?.message || error.response.data?.error || 'Unknown server error'}`;
        }
      } else if (error.request) {
        errorMessage = 'No response from server. Please check your internet connection and try again.';
      } else if (error.code === 'ECONNABORTED') {
        errorMessage = 'Request timed out. Please try again.';
      } else {
        errorMessage = error.message || 'An unexpected error occurred';
      }

      Alert.alert('Error', errorMessage);
      throw error;
    }
  }, [selectedDocument]);


  // Debounced search for transcripts
  const transcriptsSearchTimeoutRef = useRef(null);
  useEffect(() => {
    if (transcriptsSearchTimeoutRef.current) {
      clearTimeout(transcriptsSearchTimeoutRef.current);
    }

    transcriptsSearchTimeoutRef.current = setTimeout(() => {
      if (false) {
        loadTranscripts(false, transcriptsSearchQuery);
      }
    }, 500); // 500ms debounce

    return () => {
      if (transcriptsSearchTimeoutRef.current) {
        clearTimeout(transcriptsSearchTimeoutRef.current);
      }
    };
  }, [transcriptsSearchQuery, activeScreen]);

  // Debounced search for folder content
  const folderContentSearchTimeoutRef = useRef(null);
  useEffect(() => {
    if (folderContentSearchTimeoutRef.current) {
      clearTimeout(folderContentSearchTimeoutRef.current);
    }

    folderContentSearchTimeoutRef.current = setTimeout(() => {
    }, 500);

    return () => {
      if (folderContentSearchTimeoutRef.current) {
        clearTimeout(folderContentSearchTimeoutRef.current);
      }
    };
  }, [folderContentSearchQuery, activeScreen]); // Removed openFolderContent from dependencies
  if (activeScreen === 'signup' && initialScreen !== 'chat') {
    return (
      <>
        <StatusBar style={isDarkMode ? 'light' : 'dark'} />
        <SafeAreaView style={[styles.container, { backgroundColor: theme.background }]}>
          <SignUpScreen
            theme={theme}
            pendingAction={pendingPlanId ? { type: 'buy_credits', data: { planId: pendingPlanId } } : undefined}
            onCancel={() => {
              console.log('?? [AUTH] User cancelled sign-up, clearing auth data');
              setIsAuthenticated(false);
              setCurrentUserEmail(null);
              authService.clearToken();
              // Navigate back to IntroScreen via MainApp callback
              if (onBackToIntro && typeof onBackToIntro === 'function') {
                console.log('?? [NAV] Calling onBackToIntro to return to welcome screen');
                onBackToIntro();
              } else {
                console.warn('?? [NAV] onBackToIntro callback not provided!');
              }
            }}
            onAuthSuccess={handleSignupAuthSuccess}
          />
        </SafeAreaView>
      </>
    );
  }
  const INPUT_BAR_HEIGHT = 60; // keep in sync with styles (minHeight)

  // === MOBILE VIEWPORT HEIGHT FIX ===
  // react-native-web's atomic CSS system ignores inline height overrides.
  // className prop isn't rendered to DOM, so CSS @media rules are dead.
  // Solution: use ref to set height directly on the DOM element.
  const webContainerRef = React.useRef(null);

  React.useEffect(() => {
    if (Platform.OS !== 'web') return;
    // Only apply JS-based height fix on mobile web (for address bar handling)
    // On desktop, CSS 100% height handles zoom correctly without JS intervention
    if (!isMobileWeb) return;

    const applyHeight = () => {
      const el = webContainerRef.current;
      if (!el) return;
      const vh = window.visualViewport?.height || window.innerHeight;
      el.style.height = vh + 'px';
      el.style.maxHeight = vh + 'px';

      // === DEBUG: Log DOM ancestor chain ===
      if (isMobileWeb) {
        let ancestor = el;
        let depth = 0;
        while (ancestor && depth < 6) {
          const cs = window.getComputedStyle(ancestor);
          const rect = ancestor.getBoundingClientRect();
          // console.log(`?? [DOM-CHAIN] depth=${depth} <${ancestor.tagName}> class="${(ancestor.className || '').substring(0, 80)}" | rect=${Math.round(rect.width)}x${Math.round(rect.height)} | computed: height=${cs.height}, display=${cs.display}, flex=${cs.flex}, overflow=${cs.overflow}, position=${cs.position}`);
          ancestor = ancestor.parentElement;
          depth++;
        }
        // console.log(`?? [DOM-FIX] Set webContainer height to ${vh}px via DOM ref`);
      }
    };

    // Apply immediately and on resize
    applyHeight();
    // Re-apply after a short delay (React may re-render and reset)
    const timer = setTimeout(applyHeight, 500);
    const timer2 = setTimeout(applyHeight, 2000);

    window.addEventListener('resize', applyHeight);
    window.visualViewport?.addEventListener('resize', applyHeight);

    return () => {
      clearTimeout(timer);
      clearTimeout(timer2);
      window.removeEventListener('resize', applyHeight);
      window.visualViewport?.removeEventListener('resize', applyHeight);
    };
  }, [isMobileWeb]);

  return (
    <>
      <StatusBar
        style={isDarkMode ? 'light' : 'dark'}
        translucent={Platform.OS === 'android'}
      />
      {Platform.OS === 'web' ? (
        // Web Layout with Sidebar
        <View ref={webContainerRef} style={[styles.webContainer, isMobileWeb && { flexDirection: 'column' }]} className="web-container" onLayout={(e) => { if (isMobileWeb) { const { x, y, width, height } = e.nativeEvent.layout; /* console.log(`?? [LAYOUT-DEBUG] webContainer: ${width}x${height} (x:${x}, y:${y}) | window.innerHeight=${window.innerHeight}`); */ } }}>{/*@ts-ignore*/}
          {/* Modern Web Sidebar - hidden on mobile web */}
          {!isMobileWeb && (
            <ModernSidebar
              className="modern-sidebar"
              theme={theme}
              personaText={personaText}
              activeScreen={activeScreen}
              activeSessionId={activeSessionId}
              currentView={currentView}
              setCurrentView={setCurrentView}
              onNavigate={(screen) => {
                console.log('?? [SIDEBAR_NAV] Navigating to screen:', screen);
                console.log('?? [SIDEBAR_NAV] Current activeScreen:', activeScreen);
                console.log('?? [SIDEBAR_NAV] Current currentView:', currentView);

                // Set activeScreen
                setActiveScreen(screen);

                // When navigating to 'chat', set appropriate currentView
                if (screen === 'chat') {
                  console.log('?? [SIDEBAR_NAV] Setting currentView to chat');
                  setCurrentView('chat');
                }
                // When navigating away from chat (to credits, support, etc.), 
                // we don't change currentView since those screens don't use it

                setShowHistoryDropdown(false);
                setShowPersonalInfoDropdown(false);
                console.log('?? [SIDEBAR_NAV] After navigation - activeScreen:', screen, 'currentView:', screen === 'chat' ? 'chat' : currentView);
              }}
              showDropdowns={{
                history: showHistoryDropdown,
                personalInfo: showPersonalInfoDropdown,
                customization: showCustomizationDropdown,
              }}
              onToggleDropdown={(dropdownName) => {
                switch (dropdownName) {
                  case 'history':
                    setShowHistoryDropdown(!showHistoryDropdown);
                    setShowPersonalInfoDropdown(false);
                    setShowCustomizationDropdown(false);
                    break;
                  case 'personalInfo':
                    setShowPersonalInfoDropdown(!showPersonalInfoDropdown);
                    setShowHistoryDropdown(false);
                    setShowCustomizationDropdown(false);
                    break;
                  case 'customization':
                    setShowCustomizationDropdown(!showCustomizationDropdown);
                    setShowHistoryDropdown(false);
                    setShowPersonalInfoDropdown(false);
                    break;
                }
              }}
              onNewChat={() => {
                // Reduced verbosity - only log key state changes
                // console.log('?? [NEW_CHAT] New Chat menu item clicked');
                // console.log('?? [NEW_CHAT] Current active session:', activeSessionId);
                // console.log('?? [NEW_CHAT] Deep research active:', deepResearchState.isResearching);
                // console.log('?? [NEW_CHAT] Pending clarification:', isPendingClarificationResponse);

                // Check if deep research is active and warn user
                if (deepResearchState.isResearching || isPendingClarificationResponse) {
                  Alert.alert(
                    'Deep Research In Progress',
                    'You have an active deep research session. Starting a new chat will stop the current research. Are you sure you want to continue?',
                    [
                      {
                        text: 'Cancel',
                        style: 'cancel'
                      },
                      {
                        text: 'Continue',
                        style: 'destructive',
                        onPress: () => {
                          // Reset deep research state
                          setDeepResearchState(prev => ({
                            ...prev,
                            isResearching: false,
                            stage: 'ready',
                            isAwaitingUserInput: false
                          }));
                          setIsPendingClarificationResponse(false);
                          setShowDeepResearchPanel(false);

                          // Start new chat
                          // Reduced verbosity
                          // console.log('?? [NEW_CHAT] Starting new chat after deep research warning');
                          setActiveSessionId(null);
                          setMessages([
                            { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot', timestamp: new Date() },
                          ]);
                          setNoteText('');
                          setActiveScreen('chat');
                          setShowAiModelDropdown(false);
                          // Preserve toggle states - do not reset them
                          // setSelectedFolderIds([]); // REMOVED - preserve folder selection on new chat
                          // console.log('?? [NEW_CHAT] New chat setup completed - toggle states preserved');
                        }
                      }
                    ]
                  );
                } else {
                  // Reduced verbosity
                  // console.log('?? [NEW_CHAT] No deep research active, starting new chat directly');
                  setActiveSessionId(null);
                  // console.log('?? [NEW_CHAT] Setting activeSessionId to null');
                  setMessages([
                    { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot', timestamp: new Date() },
                  ]);
                  // console.log('?? [NEW_CHAT] Set messages to initial greeting');

                  // Add a small delay to verify the state change
                  // setTimeout(() => {
                  //   console.log('?? [NEW_CHAT] Verifying state after 100ms - activeSessionId:', activeSessionId);
                  //   console.log('?? [NEW_CHAT] Verifying state after 100ms - messages length:', messages.length);
                  // }, 100);
                  setNoteText('');
                  setActiveScreen('chat');
                  // console.log('?? [NEW_CHAT] Set active screen to chat');
                  setShowAiModelDropdown(false);
                  // Preserve toggle states - do not reset them
                  // setSelectedFolderIds([]); // REMOVED - preserve folder selection on new chat
                  // console.log('?? [NEW_CHAT] New chat setup completed directly - toggle states preserved');
                }
              }}
              onManageDeptSources={() => setShowDeptSources(true)}
              isAdmin={isAdmin}
              onLoadChatHistory={() => {
                loadChatSessions(); // Load data first
                setActiveScreen('history'); // Then switch screen
                setShowHistoryDropdown(false);
                setShowAiModelDropdown(false);
                setShowPersonalInfoDropdown(false);
              }}
              isCollapsed={isSidebarCollapsed}
              onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
            />
          )}

          {/* Mobile Web Header */}
          {isMobileWeb && (
            <MobileWebHeader
              title={currentView === 'home' ? 'Citra AI' : activeScreen === 'credits' ? 'Tokens' : activeScreen === 'support' ? 'Support' : activeScreen === 'history' ? 'Chat History' : 'Chat'}
              showBack={currentView === 'chat'}
              onBack={() => {
                if (activeScreen === 'credits' || activeScreen === 'support') {
                  setActiveScreen('chat');
                  setCurrentView('home');
                } else {
                  setCurrentView('home');
                }
              }}
              onHome={() => {
                setCurrentView('home');
                setActiveScreen('chat');
              }}
              showChatMenu={currentView === 'chat' && activeScreen === 'chat'}
              onNewChat={() => {
                if (deepResearchState.isResearching || isPendingClarificationResponse) {
                  Alert.alert(
                    'Deep Research In Progress',
                    'You have an active deep research session. Starting a new chat will stop the current research. Are you sure you want to continue?',
                    [
                      { text: 'Cancel', style: 'cancel' },
                      {
                        text: 'Continue',
                        style: 'destructive',
                        onPress: () => {
                          setDeepResearchState(prev => ({ ...prev, isResearching: false, stage: 'ready', isAwaitingUserInput: false }));
                          setIsPendingClarificationResponse(false);
                          setShowDeepResearchPanel(false);
                          setActiveSessionId(null);
                          setMessages([{ id: '1', text: 'Hello! How can I assist you today?', sender: 'bot', timestamp: new Date() }]);
                          setNoteText('');
                          setActiveScreen('chat');
                          setShowAiModelDropdown(false);
                        }
                      }
                    ]
                  );
                } else {
                  setActiveSessionId(null);
                  setMessages([{ id: '1', text: 'Hello! How can I assist you today?', sender: 'bot', timestamp: new Date() }]);
                  setNoteText('');
                  setActiveScreen('chat');
                  setShowAiModelDropdown(false);
                }
              }}
              onChatHistory={() => {
                loadChatSessions();
                setActiveScreen('history');
              }}
              onCurrentChat={() => {
                setActiveScreen('chat');
              }}
              theme={theme}
              // Vault selector props
              selectedFolders={folders.filter(f => selectedFolderIds.includes(f.id))}
              folders={folders}
              onSelectFolder={selectSingleFolder}
              // Personal vault picker in the mobile-web header. Off with the
              // rest of the personal Data Store — mobile chat searches the same
              // enterprise MCP + SOP sources as desktop.
            />
          )}

          {/* Deep Research Panel - Removed - now works as simple toggle with normal query flow */}

          {/* Web Main Content */}
          <View style={[
            styles.webMainContent,
            {
              backgroundColor: theme.inputBackground,
              flex: 1,
              marginLeft: isSidebarCollapsed ? 0 : 0, // No additional margin needed as flex handles it
              flexDirection: isMobileWeb ? 'column' : 'row', // Column on mobile, row on desktop for mindmap panel
              ...(isMobileWeb && { height: 0 }), // Override 100vh on mobile - height:0 + flex:1 fills remaining space
            }
          ]} className="web-main-content" onLayout={(e) => { if (isMobileWeb) { const { x, y, width, height } = e.nativeEvent.layout; /* console.log(`?? [LAYOUT-DEBUG] webMainContent: ${width}x${height} (x:${x}, y:${y})`); */ } }}>

            {/* Mindmap Modal - REPLACED BY MINDMAP MODAL */}
            {/* Old tree view panel removed - now using MindmapModal directly */}

            {/* Diagram Panel removed - merged into DiagramChatInterface */}

            {/* Document viewer — every uploaded PDF/Word/text/transcript opens here. */}
            <ReaderPanel
              isVisible={showReader}
              onClose={() => {
                setShowReader(false);
                setInitialReaderDocumentId(null); // Reset so same doc can be reopened
                if (Platform.OS === 'web') {
                  if (readerLaunchContext === 'chat') {
                    navigateToChat();
                  } else {
                    navigateToHome();
                  }
                }
              }}
              folderId={selectedFolderIds.length === 1 && selectedFolderIds[0] !== 'documents' ? selectedFolderIds[0] : null}
              folderName={
                selectedFolderIds.length === 1 && selectedFolderIds[0] !== 'documents'
                  ? folders.find(f => f.id === selectedFolderIds[0])?.name
                  : null
              }
              theme={theme}
              userId={currentUserEmail}
              initialDocumentId={initialReaderDocumentId}
              onEmbedToVault={handlePasteTextSubmit}
            />




            {/* Profession Suggestions Box - Floating draggable helper (hidden on mobile) */}
            {!isMobileWeb && showProfessionSuggestions && activeScreen === 'chat' && currentView === 'chat' && (
              <ProfessionSuggestionsBox
                profession={userProfession}
                onQuestionSelect={(question) => {
                  console.log('?? [SUGGESTIONS] Question selected:', question);
                  // Insert the question into chat input
                  setInputText(question);
                  // Optionally auto-send or let user edit
                  // sendMessage(question);
                }}
                isDarkMode={theme.isDark}
                isVisible={showProfessionSuggestions}
                isMinimized={isProfessionSuggestionsMinimized}
                onClose={handleProfessionSuggestionsClose}
                onMinimizeToggle={handleProfessionSuggestionsMinimizeToggle}
              />
            )}

            <View style={[
              styles.webChatContainer,
              {
                backgroundColor: theme.inputBackground,
                maxWidth: isMobileWeb
                  ? '100%'
                  : '100%', // Let flex layout handle width naturally instead of viewport units
                paddingHorizontal: isMobileWeb ? 0 : 20,
                ...(isMobileWeb && { height: 0, minHeight: 0 }), // On mobile: height:0 + flex:1 fills parent
              }
            ]} className="web-chat-container modern-chat-container" onLayout={(e) => { if (isMobileWeb) { const { x, y, width, height } = e.nativeEvent.layout; /* console.log(`?? [LAYOUT-DEBUG] webChatContainer: ${width}x${height} (x:${x}, y:${y})`); */ } }}>
              {/* Web Header (hidden on chat, credits, support, quick-chat to remove empty bar) */}
              {!(Platform.OS === 'web' && (activeScreen === 'chat' || activeScreen === 'credits' || activeScreen === 'support')) && (
                <View style={[styles.webHeader, { backgroundColor: theme.inputBackground, borderBottomColor: theme.borderColor, overflow: 'visible', zIndex: 1 }]} className="web-header">
                  <Text style={[styles.webHeaderTitle, { color: theme.text }]}>
                    {activeScreen === 'history' ? 'Chat History' :
                                    activeScreen === 'usage' ? 'Usage' :
                                      activeScreen === 'settings' ? 'Settings' :
                                        activeScreen === 'upcoming' ? 'Up Coming' :
                                          activeScreen === 'credits' ? 'Credits' :
                                            activeScreen === 'support' ? 'Support' : 'Citra AI'}
                  </Text>

                  {/* Header Controls Container */}
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, overflow: 'visible', position: 'relative', zIndex: 20000 }}>
                    {/* Enterprise Entity Status Indicator */}
                    {Platform.OS === 'web' && activeScreen === 'chat' && selectedEntityDetails && useEnterprise && !isModelOnlyMode && (
                      <View style={{
                        backgroundColor: theme.isDark ? '#1a3d2e' : '#f0f8f0',
                        borderColor: '#4CAF50',
                        borderWidth: 1,
                        borderRadius: 6,
                        paddingHorizontal: 12,
                        paddingVertical: 6,
                        marginTop: 4,
                        alignSelf: 'flex-start'
                      }}>
                        <Text style={{
                          color: '#4CAF50',
                          fontSize: 12,
                          fontWeight: '600'
                        }}>
                          ?? Enterprise Mode: {selectedEntityDetails.entity_name} ({selectedEntityDetails.entity_type})
                        </Text>
                      </View>
                    )}
                  </View>
                </View>
              )}

              {/* ========== RIBBON MENU - Show only in Home mode ========== */}
              {/* TODO(future-fix): Ribbon menu hidden for now. Its content (Wikis / Tour, etc.)
                  is outdated and needs to be reworked before re-enabling. Flip `false` back to
                  the real condition below once the tabs are refreshed. */}
              {false && Platform.OS === 'web' && activeScreen === 'chat' && currentView === 'home' && !isMobileWeb && (
                <RibbonMenu
                  theme={theme}
                  personaText={personaText}
                  userProfession={userProfession}
                  // Research Tools
                  // Productivity Tools
                  isAudioRecording={isRecording}
                  // Page Builder
                  // Workflow Builder
                  onShowIntegrations={() => {
                    setShowIntegrations(true);
                  }}
                  // renderPagesList removed 2026-08-08 — the Pages surface left
                  // the product; RibbonMenu no-ops when the prop is absent.
                  // Enterprise
                  useEnterprise={useEnterprise}
                  onUseEnterpriseChange={setUseEnterprise}
                  renderEnterpriseSearch={renderEnterpriseSearch}
                  // History
                  onOpenHistory={() => {
                    loadChatSessions();
                    setActiveScreen('history');
                  }}
                  isHistoryActive={activeScreen === 'history'}
                  // Internet
                  enableInternetSearch={enableInternetSearch}
                  onEnableInternetSearchChange={handleInternetSearchToggle}
                  // Help
                  onShowHowToUse={handleShowHowToUse}
                  // Customize UI
                  isFolderPanelVisible={isFolderPanelVisible}
                  isDarkMode={isDarkMode}
                  onToggleTheme={toggleTheme}
                  // Vault
                  useUploadedData={useUploadedData}
                  onUseUploadedDataChange={setUseUploadedData}
                  selectedFolders={folders.filter(f => selectedFolderIds.includes(f.id))}
                  folders={folders}
                  onSelectFolder={selectSingleFolder}
                  // Additional props
                  disabled={false}
                  isModelOnlyMode={isModelOnlyMode}
                  onModelOnlyModeChange={handleModelOnlyToggle}
                  // Data Connections
                  // SaaS Data Connections removed � Citra Agent desktop handles SaaS analytics
                />
              )}

              {/* ========== HOME BUTTON BAR - Show only in Chat mode ========== */}
              {Platform.OS === 'web' && !isMobileWeb && activeScreen === 'chat' && currentView === 'chat' && (
                <View style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  paddingHorizontal: 16,
                  paddingVertical: 10,
                  backgroundColor: theme.surface,
                  borderBottomWidth: 1,
                  borderBottomColor: theme.border,
                }}>
                  <TouchableOpacity
                    onPress={() => {
                      setCurrentView('home');
                      navigateToHome();
                    }}
                    style={{
                      flexDirection: 'row',
                      alignItems: 'center',
                      paddingHorizontal: 12,
                      paddingVertical: 8,
                      backgroundColor: theme.primary + '15',
                      borderRadius: 8,
                      borderWidth: 1,
                      borderColor: theme.primary + '30',
                    }}
                  >
                    <Ionicons name="home-outline" size={18} color={theme.primary} />
                    <Text style={{ marginLeft: 8, color: theme.primary, fontWeight: '600', fontSize: 14 }}>
                      Home
                    </Text>
                  </TouchableOpacity>
                  {/* "Personal Store" picker + its tip — main chat searches
                      enterprise MCP sources and the enterprise SOP library
                      only, so there is no personal scope to pick. */}
                  <View style={{ flex: 1 }} />
                  <Text style={{ color: theme.textSecondary, fontSize: 14 }}>
                    Chat Mode
                  </Text>
                </View>
              )}

              {/* Web Content Area */}
              <View style={[styles.webChatContent, { backgroundColor: theme.inputBackground }]} onLayout={(e) => { if (isMobileWeb) { const { x, y, width, height } = e.nativeEvent.layout; /* console.log(`?? [LAYOUT-DEBUG] webChatContent: ${width}x${height} (x:${x}, y:${y})`); */ } }}>
                {(isInitializing && !initializationCompleted.current) ? (
                  <View style={styles.loadingContainer}>
                    <Image
                      source={require('./assets/citra-logo.png')}
                      style={{ width: 100, height: 100, opacity: 1 }}
                    />
                    <ActivityIndicator
                      size="large"
                      color={theme.sendButton}
                      style={{ marginTop: 20 }}
                    />
                    <Text style={[styles.loadingText, { color: theme.text }]}>
                      Loading Citra AI...
                    </Text>
                  </View>
                ) : (
                  <>
                    {/* Render different screens based on activeScreen */}
                    {activeScreen === 'chat' && currentView === 'home' && isMobileWeb && (
                      <MobileHomeScreen
                        theme={theme}
                        userEmail={currentUserEmail}
                        onOpenChat={() => {
                          setCurrentView('chat');
                          if (Platform.OS === 'web') navigateToChat();
                        }}
                        onOpenSupport={() => {
                          setActiveScreen('support');
                          setCurrentView('chat');
                        }}
                        onOpenPowerApps={openPowerAppsBuilder}
                      />
                    )}

                    {/* Mobile Vault/Folder Panel Overlay */}
                    {/* Mobile "Select Data Store" overlay — personal folders,
                        so it never opens while the personal vault is off. */}

                    {activeScreen === 'chat' && currentView === 'home' && !isMobileWeb && (
                      <HomePanel
                        theme={theme}
                        onOpenChat={() => {
                          setCurrentView('chat');
                          if (Platform.OS === 'web') {
                            navigateToChat();
                          }
                        }}
                        useUploadedData={useUploadedData}
                        onUseUploadedDataChange={setUseUploadedData}
                        onOpenSupport={() => {
                          setActiveScreen('support');
                          setCurrentView('chat');
                        }}
                        onOpenDeptSources={() => setShowDeptSources(true)}
                        onOpenPowerApps={openPowerAppsBuilder}
                        onOpenDecisionApps={openDecisionAppsList}
                        onOpenDashboards={openDashboardsList}
                        canBuildApps={canBuildApps}
                        onOpenMemory={() => { setMemorySlug(null); setShowMemoryScreen(true); }}
                        onOpenDeptLibrary={() => setShowDeptLibrary(true)}
                        onOpenAdminUsers={() => setShowAdminUsers(true)}
                        onOpenDepartures={() => setShowDepartures(true)}
                        onOpenAdminResources={() => setShowAdminResources(true)}
                        onOpenImpersonateUser={() => setShowImpersonateUser(true)}
                        userType={userType}
                        isAdmin={isAdmin}
                        isSuperAdmin={isSuperAdmin}
                      />
                    )}
                    {/* Project view removed 2026-08-08 (Phase 0 OSS split) —
                        project management left the product. */}
                    {activeScreen === 'chat' && currentView === 'chat' && (
                      <>
                        {/* Center watermark removed on web as well */}

                        {/* Add transition overlay */}
                        {isTransitioningChat && (
                          <View style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            right: 0,
                            bottom: 0,
                            backgroundColor: theme.background,
                            zIndex: 1000,
                            justifyContent: 'center',
                            alignItems: 'center',
                          }}>
                            <ActivityIndicator size="large" color={theme.sendButton} />
                            <Text style={{
                              color: theme.text,
                              marginTop: 10,
                              fontSize: 14,
                            }}>
                              Loading conversation...
                            </Text>
                          </View>
                        )}



                        <View style={[styles.webScrollContainer, { flex: 1 }]}>
                          <FlatList
                            ref={flatListRef}
                            data={isTransitioningChat ? [] : messages}
                            renderItem={({ item }) => {
                              // Handle special message types
                              if (item.type === 'clarification') {
                                return (
                                  <View key={item.id} style={[styles.messageContainer, { backgroundColor: theme.background }]}>
                                    <View style={[styles.botMessage, { backgroundColor: theme.botMessageBackground }]}>
                                      <Text style={[styles.messageText, { color: theme.text }]}>
                                        {item.text}
                                      </Text>
                                      <Text style={[styles.messageText, { color: theme.secondaryText, fontSize: 12, marginTop: 8, fontStyle: 'italic' }]}>
                                        ?? Please respond in the chat input below to continue your deep research.
                                      </Text>
                                    </View>
                                  </View>
                                );
                              }

                              if (item.type === 'research_complete') {
                                return (
                                  <View key={item.id} style={[styles.messageContainer, { backgroundColor: theme.background }]}>
                                    <View style={[styles.botMessage, { backgroundColor: theme.botMessageBackground }]}>
                                      <Text style={[styles.messageText, { color: theme.text }]}>
                                        {item.text}
                                      </Text>
                                      <TouchableOpacity
                                        style={{
                                          backgroundColor: '#0078d4',
                                          padding: 12,
                                          borderRadius: 8,
                                          marginTop: 12,
                                          alignItems: 'center'
                                        }}
                                        onPress={() => {
                                          setShowCurrentReportModal(true);
                                        }}
                                      >
                                        <Text style={{ color: 'white', fontWeight: 'bold' }}>
                                          ?? Launch Report
                                        </Text>
                                      </TouchableOpacity>
                                    </View>
                                  </View>
                                );
                              }

                              if (item.type === 'warning') {
                                return (
                                  <View key={item.id} style={[styles.messageContainer, { backgroundColor: theme.background }]}>
                                    <View style={[styles.botMessage, {
                                      backgroundColor: theme.messageBackground
                                    }]}>
                                      <Text style={[styles.messageText, {
                                        color: theme.text
                                      }]}>
                                        {item.text}
                                      </Text>
                                      <View style={{ flexDirection: 'row', marginTop: 12, gap: 10 }}>
                                        <TouchableOpacity
                                          style={{
                                            backgroundColor: theme.borderColor,
                                            paddingHorizontal: 16,
                                            paddingVertical: 8,
                                            borderRadius: 6,
                                            flex: 1
                                          }}
                                          onPress={() => {
                                            // Remove the warning message
                                            setMessages(prev => prev.filter(msg => msg.id !== item.id));
                                          }}
                                        >
                                          <Text style={{ color: theme.text, textAlign: 'center', fontWeight: 'bold' }}>
                                            Cancel
                                          </Text>
                                        </TouchableOpacity>
                                        <TouchableOpacity
                                          style={{
                                            backgroundColor: '#dc3545',
                                            paddingHorizontal: 16,
                                            paddingVertical: 8,
                                            borderRadius: 6,
                                            flex: 1
                                          }}
                                          onPress={() => {
                                            // Continue with new research
                                            if (item.pendingMessage) {
                                              resetDeepResearchState();
                                              setTimeout(() => {
                                                sendMessage(item.pendingMessage);
                                              }, 100);
                                            }
                                            // Remove the warning message
                                            setMessages(prev => prev.filter(msg => msg.id !== item.id));
                                          }}
                                        >
                                          <Text style={{ color: 'white', textAlign: 'center', fontWeight: 'bold' }}>
                                            Continue New Research
                                          </Text>
                                        </TouchableOpacity>
                                      </View>
                                    </View>
                                  </View>
                                );
                              }

                              // Handle document upload success messages
                              if (item.type === 'upload-success') {
                                return (
                                  <View key={item.id} style={[styles.messageContainer, { backgroundColor: theme.background }]}>
                                    <View style={[styles.botMessage, { backgroundColor: theme.botMessageBackground, paddingBottom: 16 }]}>
                                      <Text style={[styles.messageText, { color: theme.text }]}>
                                        {item.text}
                                      </Text>
                                      <TouchableOpacity
                                        style={{
                                          backgroundColor: theme.primary || '#2F56E9',
                                          padding: 12,
                                          borderRadius: 8,
                                          marginTop: 12,
                                          alignItems: 'center',
                                          flexDirection: 'row',
                                          justifyContent: 'center',
                                          gap: 8
                                        }}
                                        onPress={() => {
                                          if (item.documentId) {
                                            handleOpenReader(item.documentId, 'personal');
                                          }
                                        }}
                                      >
                                        <FontAwesome5 name="book-open" size={16} color="white" />
                                        <Text style={{ color: 'white', fontWeight: '600', fontSize: 15 }}>
                                          Open in Reader
                                        </Text>
                                      </TouchableOpacity>
                                      <Text style={[styles.messageText, { color: theme.secondaryText, fontSize: 12, marginTop: 8, fontStyle: 'italic' }]}>
                                        ?? Click to research and chat with this document in the reader.
                                      </Text>
                                    </View>
                                  </View>
                                );
                              }

                              // Use StreamingMessage for streaming messages, regular Message for static ones
                              const MessageComponent = item.isStreaming ? StreamingMessage : Message;

                              return (
                                <MessageComponent
                                  key={item.key || item.id}
                                  message={item}
                                  theme={theme}
                                  onEdit={handleEditMessage}
                                  onCopy={handleCopyMessage}
                                  onShare={handleShareMessage}
                                  onSaveToVault={handleSaveToVault}
                                  onAnimationProgress={() => {
                                    // if (autoScrollEnabled) scheduleScrollToBottom(1);
                                  }}
                                  onAnimationComplete={msgId => {
                                    handleMessageAnimationComplete(msgId);
                                    setAutoScrollEnabled(false);
                                  }}
                                  hideFirstGreeting={item.id === '1'}
                                  getUserEmail={getUserEmail}
                                  formatTitle={formatTitle}
                                  isEditing={editingMessageId === item.id}
                                  editingText={editingText}
                                  onEditTextChange={setEditingText}
                                  onSaveEdit={handleSaveInlineEdit}
                                  onCancelEdit={handleCancelEdit}
                                  sessionId={activeSessionId}
                                  sessionName={sessionName}
                                  onSuggestionPick={(text, option, suggestion, srcMessage) => {
                                    // Mark this message's suggestions as consumed so chips
                                    // disable after a pick, and submit the chosen query.
                                    setMessages(prev => prev.map(m => (
                                      m.id === srcMessage?.id
                                        ? { ...m, suggestionsConsumed: true }
                                        : m
                                    )));
                                    if (typeof text === 'string' && text.trim()) {
                                      sendMessage(text.trim());
                                    }
                                  }}
                                />
                              );
                            }}
                            keyExtractor={(item, index) => item.key || item.id || index.toString()}
                            contentContainerStyle={[
                              styles.webMessageContainer,
                              isMobileWeb && { paddingHorizontal: 4, paddingVertical: 10 },
                              { paddingBottom: 100 }
                            ]}
                            onContentSizeChange={handleContentSizeChange}
                            onLayout={handleLayout}
                            showsVerticalScrollIndicator={false}
                            keyboardShouldPersistTaps="handled"
                            // Performance optimizations
                            windowSize={10}
                            maxToRenderPerBatch={5}
                            updateCellsBatchingPeriod={50}
                            initialNumToRender={10}
                            removeClippedSubviews={true}
                            getItemLayout={null} // Disable for dynamic content
                          />
                        </View>



                        {/* Web Input Container */}
                        <View style={[
                          styles.webInputContainer,
                          {
                            backgroundColor: theme.inputBackground,
                            borderTopColor: theme.borderColor,
                            paddingTop: 0,
                            paddingBottom: isMobileWeb ? 4 : 2,
                            paddingHorizontal: isMobileWeb ? 6 : 20,
                            marginBottom: isMobileWeb ? 0 : 10,
                          }
                        ]}>
                          {/* Stop Generating Button for Web - positioned above send button.
                              Include isBotTyping/isLoading/isGenerating so the button shows
                              during SSE streaming (handleSSEStreamingResponse doesn't set
                              cancelTokenSource — its abort controller lives in
                              streamingService.activeStreams). */}
                          {(cancelTokenSource || isBotTyping || isLoading || isGenerating ||
                            isWaitingForRecordingTitle ||
                            isWaitingForImageTitle ||
                            isWaitingForDocumentTitle || isWaitingForPhotoTitle) && (
                              <div
                                className="stopGeneratingButtonWeb"
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  stopGenerating();
                                  // Let stopGenerating() handle all the cleanup - no manual state resets
                                }}
                                style={{
                                  position: 'absolute',
                                  right: '12px',
                                  bottom: '70px',
                                  width: '40px',
                                  height: '40px',
                                  borderRadius: '50%',
                                  backgroundColor: '#FF4444',
                                  border: 'none',
                                  outline: 'none',
                                  display: 'flex',
                                  justifyContent: 'center',
                                  alignItems: 'center',
                                  cursor: 'pointer',
                                  boxShadow: '0 2px 8px rgba(255, 68, 68, 0.3)',
                                  transition: 'all 0.2s ease',
                                  zIndex: 10,
                                  animation: 'pulse 2s infinite'
                                }}
                                onMouseEnter={(e) => {
                                  e.target.style.backgroundColor = '#FF6666';
                                  e.target.style.transform = 'scale(1.05)';
                                }}
                                onMouseLeave={(e) => {
                                  e.target.style.backgroundColor = '#FF4444';
                                  e.target.style.transform = 'scale(1)';
                                }}
                              >
                                <Ionicons
                                  name="stop"
                                  size={20}
                                  color="#FFFFFF"
                                />
                              </div>
                            )}

                          {/* Question Attachments Display - Mobile Only */}
                          {/* {console.log('?? MOBILE ATTACHMENT DEBUG - Platform:', Platform.OS, 'HasAttachments:', !!questionAttachments, 'AttachmentCount:', questionAttachments?.length, 'FullArray:', questionAttachments)} */}
                          {Platform.OS !== 'web' && questionAttachments.length > 0 && (
                            <View style={{
                              marginBottom: 10,
                              paddingHorizontal: 10,
                            }}>
                              {/* {console.log('?? MOBILE ATTACHMENT SECTION RENDERING - Platform:', Platform.OS, 'Attachments:', questionAttachments.length)} */}
                              {console.log('?? Mobile attachments array:', questionAttachments)}
                              <Text style={{
                                fontSize: 14,
                                color: theme.textColor,
                                marginBottom: 8,
                                fontWeight: '500',
                              }}>Attachments for your question ({questionAttachments.length}/{MAX_ATTACHMENTS}):</Text>
                              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{
                                flexDirection: 'row',
                              }}>
                                {questionAttachments.map((attachment) => {
                                  const fileIcon = getFileIcon(attachment.name, attachment.type);
                                  const displayName = truncateFileName(attachment.name, 12);

                                  return (
                                    <View key={attachment.id} style={{
                                      // Enhanced mobile attachment item styling with better layout
                                      marginRight: 12,
                                      position: 'relative',
                                      width: 80,
                                      height: 100,
                                      borderRadius: 12,
                                      backgroundColor: theme.isDark ? '#2a2a2a' : '#f8f9fa',
                                      borderWidth: 1,
                                      borderColor: theme.isDark ? '#3a3a3a' : '#e9ecef',
                                      justifyContent: 'center',
                                      alignItems: 'center',
                                      overflow: 'hidden',
                                      shadowColor: '#000',
                                      shadowOffset: { width: 0, height: 2 },
                                      shadowOpacity: 0.1,
                                      shadowRadius: 4,
                                      elevation: 2,
                                    }}>
                                      {/* Progress Circle Overlay for Mobile */}
                                      {attachmentProgress[attachment.id] &&
                                        attachmentProgress[attachment.id].stage !== 'error' && (
                                          <View style={{
                                            position: 'absolute',
                                            top: 0,
                                            left: 0,
                                            right: 0,
                                            bottom: 0,
                                            justifyContent: 'center',
                                            alignItems: 'center',
                                            zIndex: 999,
                                            backgroundColor: 'rgba(0,0,0,0.75)',
                                            borderRadius: 12,
                                          }}>
                                            <View style={{
                                              width: 50,
                                              height: 50,
                                              borderRadius: 25,
                                              backgroundColor: attachmentProgress[attachment.id]?.stage === 'completed' ? '#28a745' :
                                                attachmentProgress[attachment.id]?.stage === 'warning' ? '#FFA500' :
                                                  attachmentProgress[attachment.id]?.stage === 'processing' ? '#FF9500' : '#007AFF',
                                              justifyContent: 'center',
                                              alignItems: 'center',
                                              borderWidth: 2,
                                              borderColor: 'white',
                                            }}>
                                              <Text style={{
                                                color: 'white',
                                                fontSize: 12,
                                                fontWeight: 'bold',
                                                textAlign: 'center',
                                              }}>
                                                {attachmentProgress[attachment.id]?.stage === 'completed' ? '?' :
                                                  attachmentProgress[attachment.id]?.stage === 'warning' ? '?' :
                                                    `${Math.round(attachmentProgress[attachment.id]?.progress || 0)}%`}
                                              </Text>
                                            </View>
                                          </View>
                                        )}

                                      {/* Success checkmark for processed attachments */}
                                      {attachment.processed && !attachment.processingError && !attachmentProgress[attachment.id] && (
                                        <View style={{
                                          position: 'absolute',
                                          top: 4,
                                          right: 4,
                                          backgroundColor: '#00C851',
                                          borderRadius: 10,
                                          width: 16,
                                          height: 16,
                                          justifyContent: 'center',
                                          alignItems: 'center',
                                          zIndex: 10,
                                        }}>
                                          <Text style={{ color: 'white', fontSize: 10, fontWeight: 'bold' }}>?</Text>
                                        </View>
                                      )}

                                      {/* Error indicator for failed processing */}
                                      {attachment.processingError && (
                                        <View style={{
                                          position: 'absolute',
                                          top: 4,
                                          right: 4,
                                          backgroundColor: '#FF6B6B',
                                          borderRadius: 10,
                                          width: 16,
                                          height: 16,
                                          justifyContent: 'center',
                                          alignItems: 'center',
                                          zIndex: 10,
                                        }}>
                                          <Text style={{ color: 'white', fontSize: 10, fontWeight: 'bold' }}>!</Text>
                                        </View>
                                      )}

                                      {/* Error state for attachment progress */}
                                      {attachmentProgress[attachment.id]?.stage === 'error' && (
                                        <View style={{
                                          position: 'absolute',
                                          top: 4,
                                          right: 4,
                                          backgroundColor: '#FF6B6B',
                                          borderRadius: 10,
                                          width: 16,
                                          height: 16,
                                          justifyContent: 'center',
                                          alignItems: 'center',
                                          zIndex: 10,
                                        }}>
                                          <Text style={{ color: 'white', fontSize: 10, fontWeight: 'bold' }}>?</Text>
                                        </View>
                                      )}

                                      {/* Content based on attachment type */}
                                      {attachment.type === 'image' ? (
                                        <View style={{ alignItems: 'center', justifyContent: 'center', flex: 1, paddingBottom: 8 }}>
                                          {attachment.blob ? (
                                            <Image
                                              source={{ uri: URL.createObjectURL(attachment.blob) }}
                                              style={{
                                                width: 50,
                                                height: 50,
                                                borderRadius: 8,
                                                opacity: attachmentProgress[attachment.id] && !attachment.processed ? 0.6 : 1
                                              }}
                                              resizeMode="cover"
                                            />
                                          ) : (
                                            <Image
                                              source={{ uri: attachment.uri }}
                                              style={{
                                                width: 50,
                                                height: 50,
                                                borderRadius: 8,
                                                opacity: attachmentProgress[attachment.id] && !attachment.processed ? 0.6 : 1
                                              }}
                                              resizeMode="cover"
                                            />
                                          )}
                                          <Text style={{
                                            fontSize: 8,
                                            color: theme.textSecondary,
                                            textAlign: 'center',
                                            marginTop: 4,
                                            paddingHorizontal: 2,
                                          }} numberOfLines={2}>
                                            {displayName}
                                          </Text>
                                        </View>
                                      ) : (
                                        <View style={{
                                          alignItems: 'center',
                                          justifyContent: 'center',
                                          flex: 1,
                                          paddingHorizontal: 4,
                                          paddingBottom: 8,
                                        }}>
                                          <Ionicons
                                            name={fileIcon.name}
                                            size={28}
                                            color={fileIcon.color}
                                            style={{
                                              opacity: attachmentProgress[attachment.id] && !attachment.processed ? 0.6 : 1
                                            }}
                                          />
                                          <Text style={{
                                            fontSize: 8,
                                            color: theme.textSecondary,
                                            textAlign: 'center',
                                            marginTop: 6,
                                            fontWeight: '500',
                                          }} numberOfLines={2}>
                                            {displayName}
                                          </Text>
                                        </View>
                                      )}

                                      <TouchableOpacity
                                        style={{
                                          position: 'absolute',
                                          top: -4,
                                          right: -4,
                                          width: 20,
                                          height: 20,
                                          borderRadius: 10,
                                          backgroundColor: theme.isDark ? '#ff4444' : '#ff0000',
                                          justifyContent: 'center',
                                          alignItems: 'center',
                                          zIndex: 9999,
                                          elevation: 10,
                                        }}
                                        onPress={() => {
                                          console.log('?? MOBILE remove button clicked for attachment:', attachment.id);
                                          console.log('?? Current questionAttachments before removal:', questionAttachments);
                                          alert(`Removing attachment: ${attachment.name}`); // Better feedback with filename
                                          setQuestionAttachments(prev => {
                                            const filtered = prev.filter(a => a.id !== attachment.id);
                                            console.log('?? Attachments after removal:', filtered);
                                            return filtered;
                                          });
                                          // Clear progress circle
                                          setAttachmentProgress(prev => {
                                            const newProgress = { ...prev };
                                            delete newProgress[attachment.id];
                                            return newProgress;
                                          });
                                        }}
                                        hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }} // Larger touch area
                                        activeOpacity={0.7}
                                      >
                                        <Text style={{
                                          color: 'white',
                                          fontSize: 14,
                                          fontWeight: 'bold',
                                          lineHeight: 14,
                                          textAlign: 'center'
                                        }}>�</Text>
                                      </TouchableOpacity>
                                    </View>
                                  );
                                })}
                              </ScrollView>
                            </View>
                          )}

                          {/* ========== WEB QUERY ENHANCEMENT TOGGLES ========== */}
                          {/* Uses ModernQueryToggles component from components/ui/ModernInteractionComponents.js */}
                          {/* Includes: How to use, Knowledge Graph, General Query, Mindmap (Green active w/ black border), Diagram, Vault, Reader, Link Enterprise Data */}
                          <ModernQueryToggles
                            theme={theme}
                            personaText={personaText}
                            useUploadedData={useUploadedData}
                            onUseUploadedDataChange={setUseUploadedData}
                            useEnterprise={useEnterprise}
                            onUseEnterpriseChange={setUseEnterprise}
                            showIndiaKanoon={true}
                            isModelOnlyMode={isModelOnlyMode}
                            onModelOnlyModeChange={handleModelOnlyToggle}
                            disabled={false}
                            canShowDiagram={true}
                            onShowReader={() => {
                              // Show reader with selected folder or all documents if none selected
                              setReaderLaunchContext('folder');
                              setShowReader(true);
                              if (Platform.OS === 'web') {
                                navigateToReader();
                              }
                            }}
                            onShowHowToUse={handleShowHowToUse}
                          />

                          {/* Web Attachment Rendering - Moved from ChatInput */}
                          {/* Horizontal Attachments Container - Web Only */}
                          {Platform.OS === 'web' && questionAttachments.length > 0 && (
                            <View style={{
                              width: '100%',
                              padding: 0,
                              margin: 0,
                              backgroundColor: 'transparent',
                              marginBottom: 6
                            }}>
                              <ScrollView
                                horizontal
                                showsHorizontalScrollIndicator={false}
                                style={{
                                  paddingHorizontal: 10,
                                  paddingVertical: 8
                                }}
                              >
                                <View style={{ flexDirection: 'row' }}>
                                  {questionAttachments.map((attachment, index) => {
                                    const fileInfo = getFileIconForWeb(attachment);
                                    const fileType = detectFileType(attachment);
                                    const fileExtension = attachment.name?.split('.').pop()?.toUpperCase() || fileType.toUpperCase();
                                    const progress = attachmentProgress[attachment.id];
                                    const isProcessing = progress && progress.stage !== 'completed';

                                    return (
                                      <View
                                        key={attachment.id || index}
                                        style={{
                                          marginRight: 12,
                                          alignItems: 'center'
                                        }}
                                      >
                                        {/* Icon container */}
                                        <View
                                          style={{
                                            width: 64,
                                            height: 64,
                                            borderRadius: 16,
                                            borderWidth: 2,
                                            borderColor: fileInfo.color,
                                            backgroundColor: theme.surface,
                                            justifyContent: 'center',
                                            alignItems: 'center',
                                            position: 'relative',
                                            shadowColor: '#000',
                                            shadowOffset: { width: 0, height: 2 },
                                            shadowOpacity: 0.1,
                                            shadowRadius: 4,
                                            elevation: 3,
                                          }}
                                        >
                                          {/* Background circle for better visibility */}
                                          <View style={{
                                            position: 'absolute',
                                            width: 48,
                                            height: 48,
                                            borderRadius: 24,
                                            backgroundColor: fileInfo.color + '20',
                                            opacity: isProcessing ? 0.5 : 1
                                          }} />

                                          {/* Main icon — show a real thumbnail for image
                                              attachments (e.g. pasted screenshots) instead of a
                                              generic file glyph; fall back to the emoji otherwise. */}
                                          {fileType === 'image' && (attachment.uri || attachment.blob) ? (
                                            <Image
                                              source={{ uri: attachment.uri || (attachment.blob ? URL.createObjectURL(attachment.blob) : undefined) }}
                                              resizeMode="cover"
                                              style={{
                                                position: 'absolute',
                                                width: 60,
                                                height: 60,
                                                borderRadius: 14,
                                                opacity: isProcessing ? 0.5 : 1,
                                              }}
                                            />
                                          ) : (
                                            <Text style={{
                                              fontSize: 28,
                                              opacity: isProcessing ? 0.5 : 1
                                            }}>
                                              {fileInfo.icon}
                                            </Text>
                                          )}

                                          {/* File type badge */}
                                          <View style={{
                                            position: 'absolute',
                                            bottom: -4,
                                            right: -4,
                                            backgroundColor: fileInfo.color,
                                            borderRadius: 6,
                                            paddingHorizontal: 6,
                                            paddingVertical: 2,
                                            borderWidth: 2,
                                            borderColor: theme.surface,
                                            minWidth: 32,
                                            height: 18,
                                            justifyContent: 'center',
                                            alignItems: 'center'
                                          }}>
                                            <Text style={{
                                              fontSize: 10,
                                              color: '#FFFFFF',
                                              fontWeight: 'bold',
                                              textAlign: 'center',
                                              fontFamily: Platform.select({
                                                ios: 'System',
                                                android: 'Roboto',
                                                web: 'system-ui, -apple-system, sans-serif'
                                              })
                                            }}>
                                              {fileExtension}
                                            </Text>
                                          </View>

                                          {/* Delete button */}
                                          <TouchableOpacity
                                            onPress={() => {
                                              console.log('?? Web attachment remove button clicked for:', attachment.id);
                                              setQuestionAttachments(prev => prev.filter(a => a.id !== attachment.id));
                                              setAttachmentProgress(prev => {
                                                const newProgress = { ...prev };
                                                delete newProgress[attachment.id];
                                                return newProgress;
                                              });
                                            }}
                                            style={{
                                              position: 'absolute',
                                              top: -6,
                                              right: -6,
                                              width: 20,
                                              height: 20,
                                              borderRadius: 10,
                                              backgroundColor: theme.error,
                                              justifyContent: 'center',
                                              alignItems: 'center',
                                              borderWidth: 2,
                                              borderColor: theme.surface
                                            }}
                                            accessibilityRole="button"
                                            accessibilityLabel={`Remove ${attachment.name}`}
                                          >
                                            <Text style={{
                                              fontSize: 16,
                                              color: theme.surface,
                                              fontWeight: 'bold',
                                              lineHeight: 20,
                                              fontFamily: Platform.select({
                                                ios: 'System',
                                                android: 'Roboto',
                                                web: 'system-ui, -apple-system, sans-serif'
                                              })
                                            }}>
                                              �
                                            </Text>
                                          </TouchableOpacity>

                                          {/* Progress indicator */}
                                          {isProcessing && (
                                            <View style={{
                                              position: 'absolute',
                                              bottom: -8,
                                              left: 0,
                                              right: 0,
                                              height: 3,
                                              backgroundColor: theme.surface,
                                              borderRadius: 2,
                                              overflow: 'hidden'
                                            }}>
                                              <View style={{
                                                height: '100%',
                                                backgroundColor: fileInfo.color,
                                                width: `${progress?.progress || 0}%`
                                              }} />
                                            </View>
                                          )}
                                        </View>

                                        {/* Filename display below icon */}
                                        <Text
                                          numberOfLines={2}
                                          ellipsizeMode="middle"
                                          style={{
                                            fontSize: 11,
                                            color: theme.text,
                                            marginTop: 4,
                                            textAlign: 'center',
                                            width: 64,
                                            fontFamily: Platform.select({
                                              ios: 'System',
                                              android: 'Roboto',
                                              web: 'system-ui, -apple-system, sans-serif'
                                            })
                                          }}
                                        >
                                          {attachment.name}
                                        </Text>

                                        {/* File size */}
                                        {attachment.size && (
                                          <Text style={{
                                            fontSize: 9,
                                            color: theme.textSecondary,
                                            textAlign: 'center',
                                            fontFamily: Platform.select({
                                              ios: 'System',
                                              android: 'Roboto',
                                              web: 'system-ui, -apple-system, sans-serif'
                                            })
                                          }}>
                                            {formatFileSize(attachment.size)}
                                          </Text>
                                        )}
                                      </View>
                                    );
                                  })}
                                </View>
                              </ScrollView>
                            </View>
                          )}

                          {/* Chat Input - Integrated with controls */}
                          {/* No "+" on main chat: onAttachmentPress opens the
                              unified upload modal, which writes into a personal
                              vault folder. This surface only searches enterprise
                              MCP sources and the enterprise SOP library, so the
                              handler is withheld and ModernChatInput renders no
                              upload button. */}
                          <ModernChatInput
                            ref={inputRef}
                            theme={theme}
                            inputText={inputText}
                            onChangeText={setInputText}
                            onSendMessage={handleSendMessage}
                            onMicPress={isRecording ? stopRecording : () => startRecording(true)}
                            onClipboardPaste={handleClipboardPaste}
                            onConnectionScopePress={() => setShowConnectionScope(true)}
                            activeConnectionsCount={activeConnectionsCount}
                            isRecording={isRecording}
                            isLoading={isLoading || isGenerating || isBotTyping}
                            questionAttachments={questionAttachments}
                            onRemoveAttachment={(attachmentId) => {
                              console.log('?? ModernChatInput remove button clicked for attachment:', attachmentId);
                              console.log('?? Current questionAttachments before removal:', questionAttachments);
                              setQuestionAttachments(prev => {
                                const filtered = prev.filter(a => a.id !== attachmentId);
                                console.log('?? Attachments after removal:', filtered);
                                return filtered;
                              });
                              // Clear progress circle
                              setAttachmentProgress(prev => {
                                const newProgress = { ...prev };
                                delete newProgress[attachmentId];
                                return newProgress;
                              });
                            }}
                            placeholder={Platform.OS === 'web' ? "Type your message... (Ctrl+V to paste)" : "Type your message... (Long press to paste)"}
                            multiline={true}
                            autoFocus={Platform.OS !== 'web'}
                            onKeyPress={({ nativeEvent }) => {
                              if (
                                (nativeEvent.key === 'Enter' || nativeEvent.key === 'NumpadEnter')
                                && !nativeEvent.shiftKey
                              ) {
                                nativeEvent.preventDefault?.();
                                handleSendMessage();
                              }
                            }}
                          />
                        </View>
                      </>
                    )}

                    {/* Other screens content */}
                    {activeScreen === 'history' && (
                      <View style={styles.webScrollContainer}>
                        <BackToChatButton />
                        {isLoadingHistory ? (
                          <View style={styles.loadingContainer}>
                            <ActivityIndicator size="large" color={theme.sendButton} />
                            <Text style={[styles.loadingText, { color: theme.text }]}>Loading chat history...</Text>
                          </View>
                        ) : (
                          <FlatList
                            data={chatSessions}
                            renderItem={renderSessionItem}
                            keyExtractor={(item) => item.id}
                            contentContainerStyle={styles.historyList}
                            onRefresh={loadChatSessions}
                            refreshing={isLoadingHistory}
                            ListHeaderComponent={
                              <View style={styles.historyHeader}>
                                <Text style={[styles.historyHeaderText, { color: theme.text }]}>
                                  Chat History
                                </Text>
                              </View>
                            }
                            ListFooterComponent={
                              <LoadMoreButton
                                onPress={() => loadChatSessions(true)}
                                isLoading={isLoadingMoreChat}
                                theme={theme}
                                hasMore={hasMoreChatHistory}
                                hasInitialData={chatSessions.length > 0}
                              />
                            }
                            ListEmptyComponent={
                              <View style={styles.emptyContainer}>
                                <Text style={[styles.emptyText, { color: theme.placeholderText }]}>
                                  No chat history available. Start a conversation to see your chat sessions here.
                                </Text>
                              </View>
                            }
                          />
                        )}
                      </View>
                    )}




                    {/* "Case Vault Drive" — the bulk personal-upload screen.
                        Its sidebar entry is already commented out; keep the
                        screen itself off the flag too so nothing can route in. */}



                    {activeScreen === 'support' && <SupportScreen theme={theme} onNavigateHome={() => { setActiveScreen('chat'); setCurrentView('home'); navigateToHome(); }} />}

                    {activeScreen === 'upcoming' && <UpcomingFeaturesScreen theme={theme} />}
                    {/* quick-chat and action-chat screens removed 2026-08-08
                        (Phase 0 OSS split) — Quick Chat and Operations Analytics
                        left the product. */}
                    {/* Personal Data Store — moved out of the right-side rail into its
                        own screen, opened from the left sidebar's "Data Store" item.
                        Not mounted while PERSONAL_VAULT_ENABLED is false: the
                        sidebar entry that routed here is gone too, so this screen
                        is unreachable rather than half-wired. */}
                  </>
                )}
              </View>
            </View>

            {/* Enhanced Upload Progress as Chat Bubbles - WEB LAYOUT - Outside chat container for extreme right positioning */}
            {/* Only show chat bubbles when NOT in preload screen AND preload modal is not open */}
            {activeScreen !== 'preload' && (() => {
              return (
                <>
                  {/* Recording Progress Bar - Shows during active video recording */}

                  {/* Audio Recording Progress Bar - Shows during active audio recording.
                      When the HomePanel "Audio Meeting" card opened the overlay, this
                      same panel stays mounted across the title→recording→uploading→done
                      phases so the transcript is shown inline with a Copy button. */}
                </>
              );
            })()}
          </View>

          {/* Folder Management Panel - Right Column (hidden in Quick Chat and Project Management).
              The whole right rail is the PERSONAL data store: folder chips, the
              Personal Store toggle and the folder tree. Main chat searches
              enterprise MCP sources and the enterprise SOP library only, so the
              rail is not mounted while PERSONAL_VAULT_ENABLED is false. */}
        </View>
      ) : (
        // Mobile Layout (existing layout)
        <GestureHandlerRootView style={{ flex: 1 }}>
          <PanGestureHandler
            ref={gestureRef}
            onHandlerStateChange={handleSwipeGesture}
            onGestureEvent={handleSwipeGesture}
            activeOffsetX={[-10, 10]}
            failOffsetY={[-50, 50]}
          >
            <SafeAreaView style={[
              styles.container,
              { backgroundColor: theme.background },
              Platform.OS === 'ios' && styles.iosContainer,
              Platform.OS === 'android' && styles.androidContainer
            ]}>
              {(isInitializing && !initializationCompleted.current) ? (
                <View style={styles.loadingContainer}>
                  <Image
                    source={require('./assets/citra-logo.png')}
                    style={{ width: 100, height: 100, opacity: 1 }}
                  />
                  <ActivityIndicator
                    size="large"
                    color={theme.sendButton}
                    style={{ marginTop: 20 }}
                  />
                  <Text style={[styles.loadingText, { color: theme.text, marginTop: 15 }]}>
                    {initializationTimeout
                      ? 'Initialization taking longer than expected...'
                      : 'Initializing Citra AI...'
                    }
                  </Text>
                  {initializationTimeout && (
                    <Text style={[styles.loadingText, { color: theme.placeholderText, marginTop: 10, fontSize: 14 }]}>
                      Please check your network connection
                    </Text>
                  )}
                  {/* Debug button for development - only show after timeout or in debug mode */}
                  {(API_CONFIG.IS_DEVELOPMENT || initializationTimeout) && (
                    <TouchableOpacity
                      style={{
                        marginTop: 30,
                        paddingHorizontal: 20,
                        paddingVertical: 10,
                        backgroundColor: theme.sendButton,
                        borderRadius: 5,
                      }}
                      onPress={() => {
                        console.log('?? [DEBUG] Manual initialization skip triggered');
                        Alert.alert(
                          initializationTimeout ? 'Continue Anyway?' : 'Debug Mode',
                          initializationTimeout
                            ? 'Initialization is taking too long. Continue anyway? Some features may not work properly.'
                            : 'Skip initialization? This might cause some features to not work properly.',
                          [
                            { text: 'Cancel', style: 'cancel' },
                            {
                              text: initializationTimeout ? 'Continue' : 'Skip',
                              style: initializationTimeout ? 'default' : 'destructive',
                              onPress: () => {
                                console.log('?? [DEBUG] Forcing initialization complete');
                                setIsInitializing(false);
                                setInitializationTimeout(false);
                              }
                            }
                          ]
                        );
                      }}
                    >
                      <Text style={{ color: 'white', fontSize: 14, fontWeight: 'bold' }}>
                        {initializationTimeout ? 'Continue Anyway' : 'Skip (Debug)'}
                      </Text>
                    </TouchableOpacity>
                  )}
                </View>
              ) : (
                <>
                  {/* Center watermark removed per latest UI request */}

                  <View style={{ flex: 1 }}>
                    {/* Optional: Wrap only the list if you want iOS padding push */}
                    <KeyboardAvoidingView
                      style={{ flex: 1 }}
                      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
                      keyboardVerticalOffset={0}
                    >
                      <View style={[styles.headerContainer, Platform.OS === 'ios' && styles.iosHeader]}>
                        {renderHeader(
                          'Citra AI',
                          'menu',
                          null,
                          toggleMenu,
                          null,
                          messages.some(m => m.sender === 'user') ? openSessionOptions : null,
                          messages.some(m => m.sender === 'user') && activeSessionId ? (
                            <View style={styles.headerButton}>
                              <ShareButton
                                contentType="chat"
                                sourceId={activeSessionId}
                                title={sessionName || 'Chat Conversation'}
                                theme={theme}
                                showLabel={false}
                                size="small"
                                userType={userType}
                              />
                            </View>
                          ) : null
                        )}
                      </View>

                      {/* Main Content Area - Remove duplicate sidebar layout */}
                      <View style={{ flex: 1 }}>

                        {/* Main Chat Area */}
                        <View style={{ flex: 1 }}>
                          {/* Main Chat Content */}
                          {activeScreen === 'chat' && (
                            <>
                              <FlatList
                                ref={flatListRef}
                                onTouchStart={() => setAutoScrollEnabled(false)}
                                data={messages.filter(msg =>
                                  // Filter out messages with empty text, except for typing indicators and streaming messages
                                  msg.text?.trim() ||
                                  msg.isTyping ||
                                  msg.isStreaming ||
                                  msg.type === 'clarification' ||
                                  msg.type === 'research_complete' ||
                                  msg.type === 'deep_research_warning'
                                )}
                                renderItem={({ item }) => {
                                  // Handle special message types
                                  if (item.type === 'clarification') {
                                    return (
                                      <View key={item.id} style={[styles.messageContainer, { backgroundColor: theme.background }]}>
                                        <View style={[styles.botMessage, { backgroundColor: theme.botMessageBackground }]}>
                                          <Text style={[styles.messageText, { color: theme.text }]}>
                                            {item.text}
                                          </Text>
                                          <Text style={[styles.messageText, { color: theme.secondaryText, fontSize: 12, marginTop: 8, fontStyle: 'italic' }]}>
                                            ?? Please respond in the chat input below to continue your deep research.
                                          </Text>
                                        </View>
                                      </View>
                                    );
                                  }

                                  if (item.type === 'research_complete') {
                                    return (
                                      <View key={item.id} style={[styles.messageContainer, { backgroundColor: theme.background }]}>
                                        <View style={[styles.botMessage, { backgroundColor: theme.botMessageBackground }]}>
                                          <Text style={[styles.messageText, { color: theme.text }]}>
                                            {item.text}
                                          </Text>
                                          <TouchableOpacity
                                            style={{
                                              backgroundColor: '#0078d4',
                                              padding: 12,
                                              borderRadius: 8,
                                              marginTop: 12,
                                              alignItems: 'center'
                                            }}
                                            onPress={() => {
                                              setShowCurrentReportModal(true);
                                            }}
                                          >
                                            <Text style={{ color: 'white', fontWeight: 'bold' }}>
                                              ?? Launch Report
                                            </Text>
                                          </TouchableOpacity>
                                        </View>
                                      </View>
                                    );
                                  }

                                  if (item.type === 'warning') {
                                    return (
                                      <View key={item.id} style={[styles.messageContainer, { backgroundColor: theme.background }]}>
                                        <View style={[styles.botMessage, {
                                          backgroundColor: theme.messageBackground
                                        }]}>
                                          <Text style={[styles.messageText, {
                                            color: theme.text
                                          }]}>
                                            {item.text}
                                          </Text>
                                          <View style={{ flexDirection: 'row', marginTop: 12, gap: 10 }}>
                                            <TouchableOpacity
                                              style={{
                                                backgroundColor: theme.borderColor,
                                                paddingHorizontal: 16,
                                                paddingVertical: 8,
                                                borderRadius: 6,
                                                flex: 1
                                              }}
                                              onPress={() => {
                                                // Remove the warning message
                                                setMessages(prev => prev.filter(msg => msg.id !== item.id));
                                              }}
                                            >
                                              <Text style={{ color: theme.text, textAlign: 'center', fontWeight: 'bold' }}>
                                                Cancel
                                              </Text>
                                            </TouchableOpacity>
                                            <TouchableOpacity
                                              style={{
                                                backgroundColor: '#dc3545',
                                                paddingHorizontal: 16,
                                                paddingVertical: 8,
                                                borderRadius: 6,
                                                flex: 1
                                              }}
                                              onPress={() => {
                                                // Continue with new research
                                                if (item.pendingMessage) {
                                                  resetDeepResearchState();
                                                  setTimeout(() => {
                                                    sendMessage(item.pendingMessage);
                                                  }, 100);
                                                }
                                                // Remove the warning message
                                                setMessages(prev => prev.filter(msg => msg.id !== item.id));
                                              }}
                                            >
                                              <Text style={{ color: 'white', textAlign: 'center', fontWeight: 'bold' }}>
                                                Continue New Research
                                              </Text>
                                            </TouchableOpacity>
                                          </View>
                                        </View>
                                      </View>
                                    );
                                  }

                                  // Use EnhancedMessage for better markdown formatting and performance
                                  const MessageComponent = EnhancedMessage;

                                  // Debug: Check if handleOpenReader exists at render time
                                  console.log('?? APP_RENDER_ITEM: Rendering message', {
                                    messageId: item?.id,
                                    hasHandleOpenReader: !!handleOpenReader,
                                    handleOpenReaderType: typeof handleOpenReader
                                  });

                                  return (
                                    <MessageComponent
                                      key={item.key || item.id} // Use key prop for force re-render
                                      message={item}
                                      theme={theme}
                                      onEdit={handleEditMessage}
                                      onCopy={handleCopyMessage}
                                      onShare={handleShareMessage}
                                      onSaveToVault={handleSaveToVault}
                                      onAnimationProgress={() => {
                                        if (autoScrollEnabled) scheduleScrollToBottom(1);
                                      }}
                                      onAnimationComplete={msgId => {
                                        handleMessageAnimationComplete(msgId);
                                        setAutoScrollEnabled(false);
                                      }}
                                      hideFirstGreeting={item.id === '1'}
                                      formatTitle={formatTitle}
                                      getUserEmail={getUserEmail}
                                      isEditing={editingMessageId === item.id}
                                      editingText={editingText}
                                      onEditTextChange={setEditingText}
                                      onSaveEdit={handleSaveInlineEdit}
                                      onCancelEdit={handleCancelEdit}
                                      sessionId={activeSessionId}
                                      sessionName={sessionName}
                                      onSuggestionPick={(text, option, suggestion, srcMessage) => {
                                        setMessages(prev => prev.map(m => (
                                          m.id === srcMessage?.id
                                            ? { ...m, suggestionsConsumed: true }
                                            : m
                                        )));
                                        if (typeof text === 'string' && text.trim()) {
                                          sendMessage(text.trim());
                                        }
                                      }}
                                    />
                                  );
                                }}
                                keyExtractor={(item) => item.key || item.id} // Use key for extraction too
                                style={[
                                  styles.chatContainer,
                                  {
                                    borderTopWidth: StyleSheet.hairlineWidth,
                                    borderTopColor: theme.borderColor
                                  }]}
                                contentContainerStyle={[
                                  styles.chatContentContainer,
                                  {
                                    paddingTop: 8,
                                    paddingBottom: INPUT_BAR_HEIGHT + insets.bottom + 20
                                  }
                                ]}
                                keyboardShouldPersistTaps="handled"
                                keyboardDismissMode="on-drag"
                                // Performance optimizations for streaming
                                windowSize={10}
                                maxToRenderPerBatch={3}
                                updateCellsBatchingPeriod={100}
                                removeClippedSubviews={true}
                                getItemLayout={null} // Disable for dynamic content
                                onScrollToIndexFailed={(info) => {
                                  // Handle scroll to index failures gracefully
                                  console.log('Scroll to index failed:', info);
                                  // Fallback to scrolling to end if index is out of bounds

                                  if (info.index >= messages.length - 3) {
                                    setTimeout(() => {
                                      flatListRef.current?.scrollToEnd({ animated: true });
                                    }, 100);
                                  }
                                }}
                              />

                            </>
                          )}

                          {activeScreen === 'history' && (
                            <View style={styles.historyContainer}>
                              {isLoadingHistory ? (
                                <View style={styles.loadingContainer}>
                                  <ActivityIndicator size="large" color={theme.sendButton} />
                                  <Text style={[styles.loadingText, { color: theme.text }]}>Loading chat history...</Text>
                                </View>
                              ) : (
                                <FlatList
                                  data={chatSessions}
                                  renderItem={renderSessionItem}
                                  keyExtractor={(item) => item.id.toString()}
                                  contentContainerStyle={styles.historyList}
                                  onRefresh={loadChatSessions}
                                  refreshing={isLoadingHistory}
                                  ListHeaderComponent={
                                    <View style={styles.historyHeader}>
                                      <Text style={[styles.historyHeaderText, { color: theme.text }]}>
                                        Chat History
                                      </Text>
                                    </View>
                                  }
                                  ListFooterComponent={
                                    <LoadMoreButton
                                      onPress={() => loadChatSessions(true)}
                                      isLoading={isLoadingMoreChat}
                                      theme={theme}
                                      hasMore={hasMoreChatHistory}
                                      hasInitialData={chatSessions.length > 0}
                                    />
                                  }
                                  ListEmptyComponent={
                                    <View style={styles.emptyContainer}>
                                      <Text style={[styles.emptyText, { color: theme.placeholderText }]}>
                                        No chat history yet. Start a conversation to see it here.
                                      </Text>
                                    </View>
                                  }
                                />
                              )}
                            </View>
                          )}
                          {isMenuOpen && (
                            <View style={styles.menuOverlay}>
                              <TouchableOpacity
                                style={{ flex: 1 }}
                                activeOpacity={1}
                                onPress={handleBackdropPress}
                              />
                              <Animated.View
                                style={[
                                  styles.menuCurtain,
                                  {
                                    backgroundColor: theme.background,
                                    transform: [{ translateX: menuAnimation }]
                                  },
                                ]}>
                                <TouchableOpacity onPress={() => {
                                  setActiveScreen('chat');
                                  setShowAiModelDropdown(false);
                                  toggleMenu();
                                }} style={{ marginRight: 15 }}>
                                  <View style={styles.menuHeader}>
                                    <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                                      <Ionicons name="arrow-back" size={24} color={theme.text} />
                                      <Text style={[styles.menuHeaderText, { color: theme.text }]}>
                                        Menu
                                      </Text>
                                    </View>
                                  </View>
                                </TouchableOpacity>

                                <View style={styles.menuContent}>


                                  <TouchableOpacity
                                    style={[
                                      styles.menuItem,
                                      activeScreen === 'chat' && styles.activeMenuItem,
                                    ]}
                                    onPress={() => {
                                      setActiveScreen('chat');
                                      setShowAiModelDropdown(false);
                                      toggleMenu();
                                    }}>
                                    <Ionicons name="chatbubbles" size={24} color={theme.text} />
                                    <Text style={[styles.menuItemText, { color: theme.text }]}>
                                      Current Chat
                                    </Text>
                                  </TouchableOpacity>

                                  {/* Only show History menu when MongoDB chat history is enabled */}
                                  {API_CONFIG.features.mongodbChatHistory && (
                                    <TouchableOpacity
                                      style={[
                                        styles.menuItem,
                                        activeScreen === 'history' && styles.activeMenuItem,
                                      ]}
                                      onPress={() => {
                                        setActiveScreen('history');
                                        loadChatSessions();
                                        setShowAiModelDropdown(false);
                                        toggleMenu();
                                      }}>
                                      <Ionicons name="time" size={24} color={theme.text} />
                                      <Text style={[styles.menuItemText, { color: theme.text }]}>
                                        History
                                      </Text>
                                    </TouchableOpacity>
                                  )}

                                  <TouchableOpacity
                                    style={styles.menuItem}
                                    onPress={() => {
                                      setShowAiModelDropdown(false);
                                      toggleMenu();
                                      setIsUserDetailModalVisible(true);
                                    }}>
                                    <Ionicons name="person" size={24} color={theme.text} />
                                    <Text style={[styles.menuItemText, { color: theme.text }]}>
                                      Your Personal Info
                                    </Text>
                                  </TouchableOpacity>

                                  {isAuthenticated
                                    ? (
                                      <TouchableOpacity
                                        style={styles.menuItem}
                                        onPress={confirmLogout}
                                      >
                                        <Ionicons name="log-out-outline" size={24} color={theme.text} />
                                        <Text style={[styles.menuItemText, { color: theme.text }]}>
                                          Log Out
                                        </Text>
                                      </TouchableOpacity>
                                    ) : (
                                      <TouchableOpacity
                                        style={styles.menuItem}
                                        onPress={() => {
                                          if (Platform.OS === 'android') {
                                            // Skip signup for Android, set authenticated state directly
                                            setIsAuthenticated(true);
                                            setActiveScreen('chat');
                                            toggleMenu();
                                          } else {
                                            setActiveScreen('signup');
                                          }
                                        }}
                                      >
                                        <Ionicons name="person-add-outline" size={24} color={theme.text} />
                                        <Text style={[styles.menuItemText, { color: theme.text }]}>
                                          {Platform.OS === 'android' ? 'Continue as Guest' : 'Sign Up'}
                                        </Text>
                                      </TouchableOpacity>
                                    )
                                  }
                                  <TouchableOpacity
                                    style={styles.menuItem}
                                    onPress={() => {
                                      // Check if deep research is active and warn user
                                      if (deepResearchState.isResearching || isPendingClarificationResponse) {
                                        Alert.alert(
                                          'Deep Research In Progress',
                                          'You have an active deep research session. Starting a new chat will stop the current research. Are you sure you want to continue?',
                                          [
                                            {
                                              text: 'Cancel',
                                              style: 'cancel'
                                            },
                                            {
                                              text: 'Continue',
                                              style: 'destructive',
                                              onPress: () => {
                                                // Reset deep research state
                                                setDeepResearchState(prev => ({
                                                  ...prev,
                                                  isResearching: false,
                                                  stage: 'ready',
                                                  isAwaitingUserInput: false
                                                }));
                                                setIsPendingClarificationResponse(false);
                                                setShowDeepResearchPanel(false);

                                                // Start new chat
                                                setActiveSessionId(null);
                                                setMessages([
                                                  { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' },
                                                ]);
                                                setNoteText('');
                                                setActiveScreen('chat');
                                                setShowAiModelDropdown(false);
                                                // Preserve toggle states - do not reset them
                                                // setSelectedFolderIds([]); // REMOVED - preserve folder selection on new chat
                                                toggleMenu();
                                              }
                                            }
                                          ]
                                        );
                                      } else {
                                        setActiveSessionId(null);
                                        setMessages([
                                          { id: '1', text: 'Hello! How can I assist you today?', sender: 'bot' },
                                        ]);
                                        setNoteText('');
                                        setActiveScreen('chat');
                                        setShowAiModelDropdown(false);
                                        // Preserve toggle states - do not reset them
                                        // setSelectedFolderIds([]); // REMOVED - preserve folder selection on new chat
                                        toggleMenu();
                                      }
                                    }}>
                                    <Ionicons name="add-circle" size={24} color={theme.text} />
                                    <Text style={[styles.menuItemText, { color: theme.text }]}
                                    >
                                      New Chat
                                    </Text>
                                  </TouchableOpacity>
                                </View>
                              </Animated.View>
                            </View>
                          )}
                        </View> {/* Close Main Chat Area */}
                      </View> {/* Close Horizontal Layout Container */}
                    </KeyboardAvoidingView>

                    {/* Animated Footer */}
                    <Animated.View
                      style={[
                        styles.inputContainer,
                        {
                          // Lift with keyboard
                          bottom: Animated.add(keyboardHeight, new Animated.Value(0)),
                          paddingBottom: 10 + insets.bottom
                        }
                      ]}
                      pointerEvents="box-none"
                    >
                      <ChatInput
                        cancelTokenSource={cancelTokenSource}
                        theme={theme}
                        isActiveScreen={activeScreen === 'chat'}
                        sendMessage={sendMessage}
                        startRecording={startRecording}
                        stopRecording={stopRecording}
                        isRecording={isRecording}
                        isLoading={isLoading}
                        isBotTyping={isBotTyping}
                        inputText={inputText}
                        setInputText={setInputText}
                        handleSimpleSend={handleSimpleSend}
                        stopGenerating={stopGenerating}
                        isWaitingForRecordingTitle={isWaitingForRecordingTitle}
                        isWaitingForImageTitle={isWaitingForImageTitle}
                        isWaitingForDocumentTitle={isWaitingForDocumentTitle}
                        isWaitingForPhotoTitle={isWaitingForPhotoTitle}
                        questionAttachments={questionAttachments}
                        setQuestionAttachments={setQuestionAttachments}
                        handleClipboardPaste={handleClipboardPaste}
                        getFileIconForWeb={getFileIconForWeb}
                        detectFileType={detectFileType}
                        attachmentProgress={attachmentProgress}
                        removeAttachment={removeAttachment}
                        formatFileSize={formatFileSize}
                        areAllAttachmentsProcessed={areAllAttachmentsProcessed}
                        // Toggle states for AI Model functionality - Deep research removed (auto-decided by LLM)
                        // isDeepResearchEnabled={isDeepResearchEnabled}
                        // handleDeepResearchToggle={handleDeepResearchToggle}
                        // setIsDeepResearchEnabled={setIsDeepResearchEnabled}
                        useUploadedData={useUploadedData}
                        setUseUploadedData={setUseUploadedData}
                        isModelOnlyMode={isModelOnlyMode}
                        setIsModelOnlyMode={setIsModelOnlyMode}
                        handleModelOnlyToggle={handleModelOnlyToggle}
                        useEnterprise={useEnterprise}
                        setUseEnterprise={setUseEnterprise}
                        hasOrgId={hasOrgId}
                        personaText={personaText}
                      />
                    </Animated.View>
                  </View>
                </>
              )}
            </SafeAreaView>
          </PanGestureHandler>
        </GestureHandlerRootView>
      )}

      {/* Only show modals if not initializing */}
      {!isInitializing && (
        <>

          <UserDetailModal
            isVisible={isUserDetailModalVisible}
            onClose={() => setIsUserDetailModalVisible(false)}
            theme={theme}
            fetchPersonalInfo={fetchPersonalInfoFromRAG}
          />
          {/* URL Fetch Modal */}
          <URLFetchModal
            isVisible={showURLFetchModal}
            onClose={() => setShowURLFetchModal(false)}
            onSubmit={fetchFromURL}
            isLoading={urlFetchLoading}
            theme={theme}
          />

          <TranscriptViewModal
            isVisible={isTranscriptViewModalVisible}
            onClose={() => setIsTranscriptViewModalVisible(false)}
            transcript={selectedTranscript}
            theme={theme}
          />
          <TranscriptEditModal
            isVisible={isTranscriptEditModalVisible}
            onClose={() => setIsTranscriptEditModalVisible(false)}
            transcript={editingTranscript}
            onSave={handleSaveEditedTranscript}
            theme={theme}
          />
          <DocumentViewModal
            isVisible={isDocumentViewModalVisible}
            onClose={() => setIsDocumentViewModalVisible(false)}
            document={selectedDocument}
            theme={theme}
          />
          <DocumentEditModal
            isVisible={isDocumentEditModalVisible}
            onClose={() => setIsDocumentEditModalVisible(false)}
            document={editingDocument}
            onSave={handleSaveEditedDocument}
            theme={theme}
          />

          {/* Content Loading Modals */}
          <ContentLoadingModal
            isVisible={isLoadingNoteContent}
            theme={theme}
            loadingText="Loading note content..."
          />
          <ContentLoadingModal
            isVisible={isLoadingTranscriptContent}
            theme={theme}
            loadingText="Loading transcript content..."
          />
          <ContentLoadingModal
            isVisible={isLoadingDocumentContent}
            theme={theme}
            loadingText="Loading document content..."
          />

          {/* First Time User Tutorial — REMOVED. It was already unreachable
              (nothing ever set isFirstTimePersonaSetup, and HowToUseModal does
              not accept the onLaunchTutorial prop App.js was passing it), and
              its content taught the personal Data Store upload flow plus the
              presentation composer — both gone. See components/
              FirstTimeUserTutorial.js, now unused. */}

          {/* Quick Start Dialog - Streamlined first-time user onboarding */}
          <QuickStartDialog
            visible={showQuickStartDialog}
            onClose={() => setShowQuickStartDialog(false)}
            onOpenChat={() => {
              setShowQuickStartDialog(false);
              setActiveScreen('chat');
              setCurrentView('chat');
              if (Platform.OS === 'web') navigateToChat();
            }}
            theme={theme}
            workspaceName="My Content"
            welcomeBonusGranted={welcomeBonusGranted}
          />

          {/* Vault Required Modal - Shows when user tries to upload without vault selected */}


          {/* Current Report Modal */}
          <CurrentReportModal
            visible={showCurrentReportModal}
            onClose={() => setShowCurrentReportModal(false)}
            reportId={deepResearchState.researchId || deepResearchState.researchMetadata?.research_id}
            deviceId={userDeviceId}
            theme={theme}
          />

          {/* How to Use Modal. It takes only (visible, onClose, initialSection)
              — the onLaunchTutorial / onLaunchTour props that used to be passed
              here were silently ignored by the component, so neither the
              tutorial nor the 'startProductTour' event ever fired from it. The
              tour is still reachable from the ribbon's Help tab (TourButton). */}
          <HowToUseModal
            visible={showHowToUseModal}
            onClose={() => setShowHowToUseModal(false)}
          />

          {/* SaaS Connections Modal removed � SaaS analytics moved to Citra Agent desktop */}

          {/* ModernAlert Component */}
          <ModernAlert
            visible={showModernAlert}
            title={modernAlertConfig.title}
            message={modernAlertConfig.message}
            type={modernAlertConfig.type}
            buttons={modernAlertConfig.buttons}
            onDismiss={() => setShowModernAlert(false)}
          />

          {/* Connection Scope Modal */}
          <ConnectionScopeModal
            visible={showConnectionScope}
            onClose={() => setShowConnectionScope(false)}
            theme={theme}
            userProfession={userProfession}
            useVault={useUploadedData}
            onUseVaultChange={setUseUploadedData}
            useInternet={enableInternetSearch}
            onUseInternetChange={handleInternetSearchToggle}
            useEnterprise={useEnterprise}
            onUseEnterpriseChange={setUseEnterprise}
            renderEnterpriseSearch={renderEnterpriseSearch}
            showInternetWarningModal={showInternetWarningModal}
            onConfirmDisableInternet={handleConfirmDisableInternet}
            onCancelDisableInternet={handleCancelDisableInternet}
          />

          {/* Internet Search Warning Modal - Standalone (can be triggered from anywhere) */}
          <Modal
            visible={showInternetWarningModal}
            animationType="fade"
            transparent={true}
            onRequestClose={handleCancelDisableInternet}
          >
            <View style={{
              flex: 1,
              backgroundColor: 'rgba(0, 0, 0, 0.7)',
              justifyContent: 'center',
              alignItems: 'center',
            }}>
              <View style={{
                width: '90%',
                maxWidth: 500,
                borderRadius: 16,
                padding: 24,
                backgroundColor: theme.background,
                shadowColor: '#000',
                shadowOffset: { width: 0, height: 4 },
                shadowOpacity: 0.3,
                shadowRadius: 8,
                elevation: 10,
              }}>
                {/* Warning Icon */}
                <View style={{ alignItems: 'center', marginBottom: 16 }}>
                  <Ionicons name="warning" size={48} color="#f59e0b" />
                </View>

                {/* Warning Title */}
                <Text style={{
                  fontSize: 22,
                  fontWeight: '700',
                  textAlign: 'center',
                  marginBottom: 12,
                  color: theme.text,
                }}>
                  Disable Internet Research?
                </Text>

                {/* Warning Message */}
                <Text style={{
                  fontSize: 15,
                  lineHeight: 22,
                  textAlign: 'center',
                  marginBottom: 20,
                  color: theme.secondaryText,
                }}>
                  Turning off internet search will significantly impact your results and research capabilities.
                  You will lose access to the latest information, recent developments, and current events.
                </Text>

                {/* Warning Details */}
                <View style={{
                  borderRadius: 12,
                  padding: 16,
                  marginBottom: 24,
                  backgroundColor: theme.card,
                }}>
                  <Text style={{
                    fontSize: 14,
                    fontWeight: '600',
                    marginBottom: 12,
                    color: theme.text,
                  }}>
                    You will lose access to:
                  </Text>
                  <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 8 }}>
                    <Ionicons name="close-circle" size={16} color="#ef4444" />
                    <Text style={{
                      fontSize: 14,
                      marginLeft: 8,
                      flex: 1,
                      color: theme.secondaryText,
                    }}>
                      Latest news and current events
                    </Text>
                  </View>
                  <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 8 }}>
                    <Ionicons name="close-circle" size={16} color="#ef4444" />
                    <Text style={{
                      fontSize: 14,
                      marginLeft: 8,
                      flex: 1,
                      color: theme.secondaryText,
                    }}>
                      Recent industry updates and developments
                    </Text>
                  </View>
                  <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 8 }}>
                    <Ionicons name="close-circle" size={16} color="#ef4444" />
                    <Text style={{
                      fontSize: 14,
                      marginLeft: 8,
                      flex: 1,
                      color: theme.secondaryText,
                    }}>
                      Up-to-date research and publications
                    </Text>
                  </View>
                  <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 8 }}>
                    <Ionicons name="close-circle" size={16} color="#ef4444" />
                    <Text style={{
                      fontSize: 14,
                      marginLeft: 8,
                      flex: 1,
                      color: theme.secondaryText,
                    }}>
                      Real-time information verification
                    </Text>
                  </View>
                </View>

                {/* Action Buttons */}
                <View style={{ flexDirection: 'row', gap: 12 }}>
                  <TouchableOpacity
                    style={{
                      flex: 1,
                      paddingVertical: 14,
                      borderRadius: 10,
                      borderWidth: 2,
                      borderColor: theme.border,
                      alignItems: 'center',
                    }}
                    onPress={handleCancelDisableInternet}
                  >
                    <Text style={{
                      fontSize: 16,
                      fontWeight: '600',
                      color: theme.text,
                    }}>
                      Keep Enabled
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={{
                      flex: 1,
                      paddingVertical: 14,
                      borderRadius: 10,
                      backgroundColor: '#ef4444',
                      alignItems: 'center',
                    }}
                    onPress={handleConfirmDisableInternet}
                  >
                    <Text style={{
                      fontSize: 16,
                      fontWeight: '600',
                      color: 'white',
                    }}>
                      Disable Anyway
                    </Text>
                  </TouchableOpacity>
                </View>
              </View>
            </View>
          </Modal>

          {/* Folder Setup Modal */}

          {/* LEFT SIDEBAR PROGRESS OVERLAY - REMOVED */}


          {/* Dept Data Sources admin (modal overlay) */}
          {showDeptSources && (
            <Modal
              transparent={true}
              animationType="fade"
              visible={showDeptSources}
              onRequestClose={() => setShowDeptSources(false)}
            >
              <View style={{
                flex: 1,
                backgroundColor: 'rgba(0, 0, 0, 0.5)',
                justifyContent: 'center',
                alignItems: 'center',
              }}>
                <View style={{
                  width: Platform.OS === 'web' ? 880 : '95%',
                  maxWidth: 1000,
                  height: '85%',
                  backgroundColor: theme.background || '#FFFFFF',
                  borderRadius: 16,
                  overflow: 'hidden',
                  shadowColor: '#000',
                  shadowOffset: { width: 0, height: 10 },
                  shadowOpacity: 0.25,
                  shadowRadius: 20,
                  elevation: 10,
                }}>
                  <DeptSourcesScreen
                    visible={showDeptSources}
                    onClose={() => setShowDeptSources(false)}
                    theme={theme}
                  />
                </View>
              </View>
            </Modal>
          )}

          {/* Admin → Manage Users (modal overlay) */}
          {showAdminUsers && (
            <Modal
              transparent={true}
              animationType="fade"
              visible={showAdminUsers}
              onRequestClose={() => setShowAdminUsers(false)}
            >
              <View style={{
                flex: 1,
                backgroundColor: 'rgba(0, 0, 0, 0.5)',
                justifyContent: 'center',
                alignItems: 'center',
              }}>
                <View style={{
                  width: Platform.OS === 'web' ? 880 : '95%',
                  maxWidth: 1000,
                  height: '85%',
                  backgroundColor: theme.background || '#FFFFFF',
                  borderRadius: 16,
                  overflow: 'hidden',
                  shadowColor: '#000',
                  shadowOffset: { width: 0, height: 10 },
                  shadowOpacity: 0.25,
                  shadowRadius: 20,
                  elevation: 10,
                }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderBottomWidth: 1, borderColor: '#eee' }}>
                    <Text style={{ fontSize: 16, fontWeight: '600' }}>Manage Users</Text>
                    <TouchableOpacity onPress={() => setShowAdminUsers(false)}>
                      <Ionicons name="close" size={22} color="#666" />
                    </TouchableOpacity>
                  </View>
                  <AdminUsersScreen
                    navigation={{
                      navigate: (screen, params) => {
                        if (screen === 'DeleteUserScreen' && params?.userId) {
                          setAdminDeleteTargetUserId(params.userId);
                          setShowAdminUsers(false);
                        }
                      },
                    }}
                  />
                </View>
              </View>
            </Modal>
          )}

          {/* Admin → Login as User (super_admin impersonation picker) */}
          {showImpersonateUser && isSuperAdmin && (
            <Modal
              transparent={true}
              animationType="fade"
              visible={showImpersonateUser}
              onRequestClose={() => setShowImpersonateUser(false)}
            >
              <View style={{
                flex: 1,
                backgroundColor: 'rgba(0, 0, 0, 0.5)',
                justifyContent: 'center',
                alignItems: 'center',
              }}>
                <View style={{
                  width: Platform.OS === 'web' ? 880 : '95%',
                  maxWidth: 1000,
                  height: '85%',
                  backgroundColor: theme.background || '#FFFFFF',
                  borderRadius: 16,
                  overflow: 'hidden',
                  shadowColor: '#000',
                  shadowOffset: { width: 0, height: 10 },
                  shadowOpacity: 0.25,
                  shadowRadius: 20,
                  elevation: 10,
                }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderBottomWidth: 1, borderColor: '#eee' }}>
                    <Text style={{ fontSize: 16, fontWeight: '600' }}>Login as User</Text>
                    <TouchableOpacity onPress={() => setShowImpersonateUser(false)}>
                      <Ionicons name="close" size={22} color="#666" />
                    </TouchableOpacity>
                  </View>
                  <ImpersonateUserScreen
                    onClose={() => setShowImpersonateUser(false)}
                  />
                </View>
              </View>
            </Modal>
          )}

          {/* Admin → Delete User per-resource picker (modal overlay) */}
          {!!adminDeleteTargetUserId && (
            <Modal
              transparent={true}
              animationType="fade"
              visible={!!adminDeleteTargetUserId}
              onRequestClose={() => setAdminDeleteTargetUserId(null)}
            >
              <View style={{
                flex: 1,
                backgroundColor: 'rgba(0, 0, 0, 0.5)',
                justifyContent: 'center',
                alignItems: 'center',
              }}>
                <View style={{
                  width: Platform.OS === 'web' ? 880 : '95%',
                  maxWidth: 1000,
                  height: '90%',
                  backgroundColor: theme.background || '#FFFFFF',
                  borderRadius: 16,
                  overflow: 'hidden',
                  shadowColor: '#000',
                  shadowOffset: { width: 0, height: 10 },
                  shadowOpacity: 0.25,
                  shadowRadius: 20,
                  elevation: 10,
                }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderBottomWidth: 1, borderColor: '#eee' }}>
                    <Text style={{ fontSize: 16, fontWeight: '600' }}>Delete user — resource picker</Text>
                    <TouchableOpacity onPress={() => setAdminDeleteTargetUserId(null)}>
                      <Ionicons name="close" size={22} color="#666" />
                    </TouchableOpacity>
                  </View>
                  <DeleteUserScreen
                    route={{ params: { userId: adminDeleteTargetUserId } }}
                    navigation={{
                      navigate: (screen) => {
                        setAdminDeleteTargetUserId(null);
                        if (screen === 'DeparturesScreen') {
                          setShowDepartures(true);
                        }
                      },
                    }}
                  />
                </View>
              </View>
            </Modal>
          )}

          {/* Admin → Departures (modal overlay) */}
          {showDepartures && (
            <Modal
              transparent={true}
              animationType="fade"
              visible={showDepartures}
              onRequestClose={() => setShowDepartures(false)}
            >
              <View style={{
                flex: 1,
                backgroundColor: 'rgba(0, 0, 0, 0.5)',
                justifyContent: 'center',
                alignItems: 'center',
              }}>
                <View style={{
                  width: Platform.OS === 'web' ? 880 : '95%',
                  maxWidth: 1000,
                  height: '85%',
                  backgroundColor: theme.background || '#FFFFFF',
                  borderRadius: 16,
                  overflow: 'hidden',
                  shadowColor: '#000',
                  shadowOffset: { width: 0, height: 10 },
                  shadowOpacity: 0.25,
                  shadowRadius: 20,
                  elevation: 10,
                }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderBottomWidth: 1, borderColor: '#eee' }}>
                    <Text style={{ fontSize: 16, fontWeight: '600' }}>Departures</Text>
                    <TouchableOpacity onPress={() => setShowDepartures(false)}>
                      <Ionicons name="close" size={22} color="#666" />
                    </TouchableOpacity>
                  </View>
                  <DeparturesScreen />
                </View>
              </View>
            </Modal>
          )}

          {/* Admin: managed resources (Workflow + SmartApp + Skill) across the org */}
          {showAdminResources && (
            <Modal
              transparent={false}
              animationType={Platform.OS === 'web' ? 'fade' : 'slide'}
              visible={showAdminResources}
              onRequestClose={() => setShowAdminResources(false)}
            >
              <View style={{ flex: 1, backgroundColor: theme.background || '#FFFFFF' }}>
                <AdminManagedResourcesScreen
                  visible={showAdminResources}
                  onClose={() => setShowAdminResources(false)}
                  theme={theme}
                  // onOpenResource removed 2026-08-08: the only wired kind was
                  // 'workflow', and the workflow engine left the Decision System.
                />
              </View>
            </Modal>
          )}

          {/* Smart Apps (full-screen) */}
          {showPowerApps && (
            <Modal
              transparent={false}
              animationType={Platform.OS === 'web' ? 'fade' : 'slide'}
              visible={showPowerApps}
              onRequestClose={() => setShowPowerApps(false)}
            >
              <View style={{ flex: 1, backgroundColor: theme.background || '#FFFFFF' }}>
                <PowerAppsScreen
                  visible={showPowerApps}
                  onClose={() => setShowPowerApps(false)}
                  theme={theme}
                  mode={powerAppsView.mode}
                  initialKind={powerAppsView.kind}
                  onOpenMemory={(slug) => { setMemorySlug(slug || null); setShowMemoryScreen(true); }}
                />
              </View>
            </Modal>
          )}

          {/* App Memory — one platform screen (HomePanel Admin entry, or
              deep-linked from a Decision App card's Learning modal). */}
          {showMemoryScreen && (
            <Modal
              transparent={false}
              animationType={Platform.OS === 'web' ? 'fade' : 'slide'}
              visible={showMemoryScreen}
              onRequestClose={() => setShowMemoryScreen(false)}
            >
              <View style={{ flex: 1, backgroundColor: theme.background || '#FFFFFF' }}>
                <MemoryScreen
                  visible={showMemoryScreen}
                  onClose={() => setShowMemoryScreen(false)}
                  theme={theme}
                  initialSlug={memorySlug}
                  canCurate={isAdmin}
                />
              </View>
            </Modal>
          )}

          {/* Department SOP Library — admin surface (HomePanel Admin entry). */}
          {showDeptLibrary && (
            <Modal
              transparent={false}
              animationType={Platform.OS === 'web' ? 'fade' : 'slide'}
              visible={showDeptLibrary}
              onRequestClose={() => setShowDeptLibrary(false)}
            >
              <View style={{ flex: 1, backgroundColor: theme.background || '#FFFFFF' }}>
                <DeptLibraryPanel
                  visible={showDeptLibrary}
                  onClose={() => setShowDeptLibrary(false)}
                  theme={theme}
                  canManage={isAdmin}
                  deptIds={userDeptIds}
                />
              </View>
            </Modal>
          )}

        </>
      )}
    </>
  );
}


