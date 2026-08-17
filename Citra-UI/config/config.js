// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Consolidated Configuration for Citra AI
 * 
 * This file provides all configuration based on environment variables.
 * All URLs and settings come from .env or .env.production files.
 * 
 * For Docker deployments, window.__CITRA_ENV__ is injected at container
 * startup by docker-entrypoint.sh and overrides build-time defaults.
 * For local development (npx expo start), this is not used.
 */

import Constants from 'expo-constants';
import { Platform } from 'react-native';

// Runtime config injected by Docker entrypoint (empty object for non-Docker usage)
const runtimeEnv = (typeof window !== 'undefined' && window.__CITRA_ENV__) ? window.__CITRA_ENV__ : {};

// Environment resolution strategy:
// 1. Start with EXPO_PUBLIC_ENVIRONMENT if provided (explicit developer intent)
// 2. If running on production domain citra-ai.com always force production (security)
// 3. If running on test domain test.citra-ai.com always force test
// 4. If localhost or private IP always development
// 5. Else fall back to NODE_ENV or default 'production' (safer default for deployed hosts)
const hostname = (typeof window !== 'undefined' && window.location) ? window.location.hostname : '';
const isLocalhost = hostname === 'localhost' || hostname === '127.0.0.1' || /^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(hostname);
const isProductionDomain = hostname.includes('citra-ai.com');
const isTestDomain = hostname.includes('test.citra-ai.com');
const explicitEnv = process.env.EXPO_PUBLIC_ENVIRONMENT; // 'development' | 'test' | 'production' | 'self-hosted' | undefined
let environment = explicitEnv || (process.env.NODE_ENV === 'development' ? 'development' : 'production');
if (isLocalhost) {
  environment = 'development';
}
if (isTestDomain) {
  environment = 'test';
}
if (isProductionDomain) {
  environment = 'production';
}

// Helper booleans (exported later via CONFIG)
const isDevelopment = environment === 'development';
const isTest = environment === 'test';
const isProduction = environment === 'production';
const isSelfHosted = environment === 'self-hosted';

// Get URLs from environment variables
const getApiUrls = () => {
  if (isSelfHosted) {
    // Self-hosted: single CITRA_API_URL points to reverse proxy / API gateway
    // Docker runtime env (window.__CITRA_ENV__) takes priority over build-time defaults
    const baseUrl = runtimeEnv.CITRA_API_URL || process.env.EXPO_PUBLIC_CITRA_API_URL || 'http://localhost:7001';
    return {
      baseUrl: baseUrl,
      transcribeUrl: `${baseUrl}/whisper/transcribe`,
      citraService: baseUrl,
      userServiceUrl: runtimeEnv.USER_SERVICE_URL || process.env.EXPO_PUBLIC_USER_SERVICE_URL || 'http://localhost:7004',
      authBaseUrl: runtimeEnv.AUTH_BASE_URL || process.env.EXPO_PUBLIC_AUTH_BASE_URL || 'http://localhost:7004/api/auth',
      smartAppService: runtimeEnv.SMART_APP_SERVICE_URL || process.env.EXPO_PUBLIC_SMART_APP_SERVICE_URL || `${baseUrl.replace(/\/$/, '')}/smart-apps`,
      // citra-workflow is path-routed by the gateway in self-hosted; same base URL.
      workflowService: runtimeEnv.WORKFLOW_SERVICE_URL || process.env.EXPO_PUBLIC_WORKFLOW_SERVICE_URL || baseUrl,
      appsBaseUrl: runtimeEnv.APPS_BASE_URL || process.env.EXPO_PUBLIC_APPS_BASE_URL || `${baseUrl.replace(/\/$/, '')}/apps`
    };
  } else if (isDevelopment) {
    // Development URLs from .env - force localhost for all services
    return {
      baseUrl: 'http://localhost:8085',
      transcribeUrl: 'http://localhost:8085/whisper/transcribe',
      citraService: 'http://localhost:8085',
      userServiceUrl: 'http://localhost:7004', // Force localhost for development
      authBaseUrl: 'http://localhost:7004/api/auth', // Force localhost for development
      smartAppService: process.env.EXPO_PUBLIC_SMART_APP_SERVICE_URL || 'http://localhost:9100',
      // citra-workflow runs as its own service on 9200 in local dev (no gateway).
      workflowService: process.env.EXPO_PUBLIC_WORKFLOW_SERVICE_URL || 'http://localhost:9200',
      appsBaseUrl: process.env.EXPO_PUBLIC_APPS_BASE_URL || 'http://localhost:3100'
    };
  } else if (isTest) {
    // Test URLs — point to test server (testapi.citra-ai.com)
    return {
      baseUrl: process.env.EXPO_PUBLIC_TEST_BASE_URL || 'https://testapi.citra-ai.com',
      transcribeUrl: process.env.EXPO_PUBLIC_TEST_TRANSCRIBE_URL || 'https://testapi.citra-ai.com/whisper/transcribe',
      citraService: process.env.EXPO_PUBLIC_TEST_CITRA_SERVICE || 'https://testapi.citra-ai.com/citra-ai',
      userServiceUrl: process.env.EXPO_PUBLIC_TEST_USER_SERVICE_URL || 'https://testapi.citra-ai.com/user-service',
      authBaseUrl: process.env.EXPO_PUBLIC_TEST_AUTH_BASE_URL || 'https://testapi.citra-ai.com/user-service/api/auth',
      smartAppService: process.env.EXPO_PUBLIC_TEST_SMART_APP_SERVICE || 'https://testapi.citra-ai.com/smart-apps',
      // citra-workflow is path-routed by nginx/ALB on the same host as citra-service.
      workflowService: process.env.EXPO_PUBLIC_TEST_WORKFLOW_SERVICE || 'https://testapi.citra-ai.com/citra-ai',
      appsBaseUrl: process.env.EXPO_PUBLIC_TEST_APPS_BASE_URL || 'https://testapps.citra-ai.com'
    };
  } else {
    // Production URLs from .env.production
    return {
      baseUrl: process.env.EXPO_PUBLIC_CLOUD_BASE_URL || 'https://api.citra-ai.com',
      transcribeUrl: process.env.EXPO_PUBLIC_CLOUD_TRANSCRIBE_URL || 'https://api.citra-ai.com/whisper/transcribe',
      citraService: process.env.EXPO_PUBLIC_CLOUD_CITRA_SERVICE || 'https://api.citra-ai.com/citra-ai',
      userServiceUrl: process.env.EXPO_PUBLIC_USER_SERVICE_URL || 'https://api.citra-ai.com/user-service',
      authBaseUrl: process.env.EXPO_PUBLIC_AUTH_BASE_URL || 'https://api.citra-ai.com/user-service/api/auth',
      smartAppService: process.env.EXPO_PUBLIC_CLOUD_SMART_APP_SERVICE || 'https://api.citra-ai.com/smart-apps',
      // citra-workflow is path-routed by nginx/ALB on the same host as citra-service.
      workflowService: process.env.EXPO_PUBLIC_CLOUD_WORKFLOW_SERVICE || 'https://api.citra-ai.com/citra-ai',
      appsBaseUrl: process.env.EXPO_PUBLIC_CLOUD_APPS_BASE_URL || 'https://apps.citra-ai.com'
    };
  }
};

const apiUrls = getApiUrls();

// Debug environment detection (development only — never expose URLs in production console)
if (environment !== 'production') {
  console.log('🔧 Environment Detection Debug:');
  console.log('  - isLocalhost:', isLocalhost, 'hostname:', hostname);
  console.log('  - isProductionDomain:', isProductionDomain);
  console.log('  - process.env.NODE_ENV:', process.env.NODE_ENV);
  console.log('  - process.env.EXPO_PUBLIC_ENVIRONMENT:', process.env.EXPO_PUBLIC_ENVIRONMENT);
  console.log('  - Final environment:', environment);
  console.log('  - Citra AI Service URL:', apiUrls.citraService);
  console.log('  - Video Upload URL:', `${apiUrls.citraService}/upload-video`);
}
if (environment === 'production' && apiUrls.citraService.startsWith('http://')) {
  console.warn('⚠️ [CONFIG] Production citraService is using http scheme; forcing https upgrade may be required.');
}

// Feature flags from environment variables
const featureFlags = {
  enhancedRag: process.env.EXPO_PUBLIC_ENABLE_ENHANCED_RAG === 'true',
  smartSuggestions: process.env.EXPO_PUBLIC_ENABLE_SMART_SUGGESTIONS === 'true',
  intentAnalysis: process.env.EXPO_PUBLIC_ENABLE_INTENT_ANALYSIS === 'true',
  debugLogging: process.env.EXPO_PUBLIC_ENABLE_DEBUG_LOGGING === 'true',

  // Document storage configuration flags
  storeDocumentContentInMongoDB: process.env.EXPO_PUBLIC_STORE_DOCUMENT_CONTENT_IN_MONGODB === 'true',
  storeInAzureContainer: process.env.EXPO_PUBLIC_STORE_IN_AZURE_CONTAINER === 'true',

  // Chat history configuration flags
  mongodbChatHistory: process.env.EXPO_PUBLIC_ENABLE_MONGODB_CHAT_HISTORY !== 'false', // Default to true for backwards compatibility
};

// Detect if running in our custom Native WebViews
const userAgent = (typeof window !== 'undefined' && window.navigator) ? window.navigator.userAgent : '';
const isAndroidWebView = userAgent.includes('CitraAndroid');
const isIOSWebView = userAgent.includes('CitraIOS');

// OAuth configuration from environment variables (runtime override for Docker)
const oauthConfig = {
  // If in Android WebView, use Android Client ID as requested. Otherwise use Web Client ID.
  googleWebClientId: isAndroidWebView && process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID
    ? process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID
    : (runtimeEnv.GOOGLE_WEB_CLIENT_ID || process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID),
  googleIosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
  googleExpoClientId: process.env.EXPO_PUBLIC_GOOGLE_EXPO_CLIENT_ID,
  redirectScheme: process.env.EXPO_PUBLIC_OAUTH_REDIRECT_SCHEME,
  redirectPath: process.env.EXPO_PUBLIC_OAUTH_REDIRECT_PATH,
};

// Authentication configuration
const authConfig = {
  baseUrl: apiUrls.authBaseUrl,
  googleEndpoint: process.env.EXPO_PUBLIC_AUTH_GOOGLE_ENDPOINT || '/google',
  meEndpoint: process.env.EXPO_PUBLIC_AUTH_ME_ENDPOINT || '/me',
};

// API configuration
const apiConfig = {
  timeout: parseInt(process.env.EXPO_PUBLIC_API_TIMEOUT) || 300000, // 5 minutes default for query timeout
  documentUploadTimeout: parseInt(process.env.EXPO_PUBLIC_DOCUMENT_UPLOAD_TIMEOUT) || 600000, // 10 minutes for document uploads
  maxRetryAttempts: parseInt(process.env.EXPO_PUBLIC_MAX_RETRY_ATTEMPTS) || 3,
};

// Build configuration
const buildConfig = {
  timestamp: process.env.EXPO_PUBLIC_BUILD_TIMESTAMP || new Date().toISOString(),
  cacheVersion: process.env.EXPO_PUBLIC_CACHE_VERSION || new Date().toISOString(),
  appVersion: process.env.EXPO_PUBLIC_APP_VERSION || '1.0.0',
  buildNumber: process.env.EXPO_PUBLIC_BUILD_NUMBER || '1',
  releaseVersion: process.env.EXPO_PUBLIC_RELEASE_VERSION || 'v1.0.0-dev',
  gitCommit: process.env.EXPO_PUBLIC_GIT_COMMIT || 'unknown',
  gitBranch: process.env.EXPO_PUBLIC_GIT_BRANCH || 'unknown',
  releaseDate: process.env.EXPO_PUBLIC_RELEASE_DATE || new Date().toISOString(),
  releaseNotes: process.env.EXPO_PUBLIC_RELEASE_NOTES || 'Development build',
};

// Log configuration for debugging
if (featureFlags.debugLogging) {
  console.log('=== Citra AI Configuration ===');
  console.log('Environment:', environment);
  console.log('Platform:', Platform.OS);
  console.log('User Service URL:', apiUrls.userServiceUrl);
  console.log('Citra AI Service:', apiUrls.citraService);
  console.log('Citra AI Service:', apiUrls.citraService);
  console.log('WebView Context:', {
    isAndroid: isAndroidWebView,
    isIOS: isIOSWebView,
    ua: userAgent,
    activeClientId: oauthConfig.googleWebClientId
  });
  console.log('Features:', featureFlags);
  console.log('================================');
}

// 🚀 RELEASE VERSION TRACKING - Only log in development or when debug logging is enabled
if (featureFlags.debugLogging || environment === 'development') {
  // console.log('🚀 ===== Citra AI RELEASE INFO =====');
  // console.log('📱 App Version:', buildConfig.appVersion);
  // console.log('🔢 Build Number:', buildConfig.buildNumber);
  // console.log('🏷️ Release Version:', buildConfig.releaseVersion);
  // console.log('🌿 Git Branch:', buildConfig.gitBranch);
  // console.log('🔗 Git Commit:', buildConfig.gitCommit);
  // console.log('📅 Release Date:', buildConfig.releaseDate);
  // console.log('📝 Release Notes:', buildConfig.releaseNotes);
  // console.log('⏰ Build Timestamp:', buildConfig.timestamp);
  // console.log('🔄 Cache Version:', buildConfig.cacheVersion);
  // console.log('🌍 Environment:', environment);
  // console.log('📱 Platform:', Platform.OS);
  // console.log('🚀 ======================================');
} else {
  // Minimal logging for production
  console.log(`🚀 Citra AI ${buildConfig.releaseVersion} (${environment})`);
}

// Filter out excessive debug logging in production builds
if (!featureFlags.debugLogging && environment === 'production') {
  // Store original console.log
  const originalConsoleLog = console.log;

  // Override console.log to filter debug messages
  console.log = (...args) => {
    const message = args.join(' ');

    // Filter out FlatList debug messages and other excessive logging
    if (
      message.includes('FLATLIST_DEBUG') ||
      message.includes('RENDER_ITEM_DEBUG') ||
      message.includes('INDIAKANOON_DEBUG') ||
      message.includes('🎯') ||
      message.includes('About to render FlatList') ||
      (message.includes('🔍') && message.includes('DEBUG'))
    ) {
      return; // Skip these debug messages
    }

    // Allow other console.log messages
    originalConsoleLog.apply(console, args);
  };
}

// Main configuration export
export const CONFIG = {
  // Environment info
  environment,
  isDevelopment: environment === 'development',
  isTest: environment === 'test',
  isProduction: environment === 'production',

  // Disable authentication for localhost environment ONLY if explicitly enabled via flag
  // This allows normal localhost development by default, but enables bypass for automation
  // SECURITY: never allow in production, even if env var is accidentally set
  isAuthDisabled: environment !== 'production' && isLocalhost && process.env.EXPO_PUBLIC_ENABLE_TEST_AUTH_BYPASS === 'true',

  // Build info for cache busting (legacy format for backwards compatibility)
  BUILD_TIMESTAMP: buildConfig.timestamp,
  CACHE_VERSION: buildConfig.cacheVersion,

  // Build info (new format)
  build: buildConfig,

  // Release tracking information
  release: {
    version: buildConfig.releaseVersion,
    appVersion: buildConfig.appVersion,
    buildNumber: buildConfig.buildNumber,
    gitCommit: buildConfig.gitCommit,
    gitBranch: buildConfig.gitBranch,
    releaseDate: buildConfig.releaseDate,
    releaseNotes: buildConfig.releaseNotes,
    timestamp: buildConfig.timestamp,
    cacheVersion: buildConfig.cacheVersion,
    platform: Platform.OS,
    environment: environment
  },

  // API URLs (legacy format for backwards compatibility)
  BASE_URL: apiUrls.baseUrl,
  TRANSCRIBE_URL: apiUrls.transcribeUrl,
  CITRA_SERVICE_URL: apiUrls.citraService,
  USER_SERVICE_URL: apiUrls.userServiceUrl,
  WORKFLOW_SERVICE_URL: apiUrls.workflowService,

  // API URLs (new format)
  urls: apiUrls,

  // Specific API Endpoints (legacy format for backwards compatibility)
  AUDIO_UPLOAD_URL: apiUrls.citraService + '/v2/transcripts',
  AUDIO_DATA_URL: apiUrls.citraService + '/audio_data',
  DOCUMENT_URL: apiUrls.citraService + '/api/v2/document',
  CHAT_URL: apiUrls.citraService + '/chat',
  NOTE_URL: apiUrls.citraService + '/note',
  QUERY_URL: apiUrls.citraService + '/query',
  COMPOSER_QUERY_URL: apiUrls.citraService + '/composer/query',
  TRANSCRIPT_URL: apiUrls.citraService + '/v2/transcript',
  PERSONA_URL: apiUrls.citraService + '/api/v2/persona',

  // API configuration (legacy format for backwards compatibility)
  TIMEOUT: apiConfig.timeout,
  DOCUMENT_UPLOAD_TIMEOUT: apiConfig.documentUploadTimeout,
  MAX_RETRY_ATTEMPTS: apiConfig.maxRetryAttempts,
  ENVIRONMENT: environment,

  // API configuration (new format)
  api: apiConfig,

  // Feature flags (legacy format for backwards compatibility)
  FEATURES: featureFlags,

  // Feature flags (new format)
  features: featureFlags,

  // OAuth configuration (legacy format for backwards compatibility)
  OAUTH: oauthConfig,

  // OAuth configuration (new format)
  oauth: oauthConfig,

  // Authentication configuration (legacy format for backwards compatibility)
  AUTH: authConfig,

  // Authentication configuration (new format)
  auth: authConfig,
};

// API Endpoints - consolidated from endpoints.js
export const API_ENDPOINTS = {
  // Citra AI Service endpoints
  CITRA_AI: {
    BASE: `${apiUrls.citraService}`,
    CHAT: `${apiUrls.citraService}/chat`,
    UPLOAD_DOCUMENT: `${apiUrls.citraService}/api/v2/document`,
    UPLOAD_VIDEO: `${apiUrls.citraService}/upload-video`,
    AUDIO_UPLOAD: `${apiUrls.citraService}/v2/transcripts`,
    QUERY: `${apiUrls.citraService}/query`,
    TRANSCRIPT: `${apiUrls.citraService}/v2/transcript`,
    PERSONA: `${apiUrls.citraService}/api/v2/persona`,
    NOTE: `${apiUrls.citraService}/note`,
    RELEASE_TRACKING: `${apiUrls.citraService}/api/release-tracking`
  },
  // Page Builder endpoints  
  PAGES: {
    INTEGRATIONS: `${apiUrls.citraService}/api/v2/pages/integrations/connected`
  }
};

// Export API_BASE_URL for backward compatibility
export const API_BASE_URL = apiUrls.citraService;

// Workflow service base — separate FastAPI service after the Phase J split.
// In prod/test/self-hosted it sits behind the same gateway as citra-service
// (same host, different path-routed backend). In local dev it's a different
// process on port 9200 (no gateway).
export const WORKFLOW_API_BASE = apiUrls.workflowService;

// Utility functions
export const logApiCall = (endpoint, method = 'GET', data = null) => {
  if (featureFlags.debugLogging) {
    console.log(`[${method}] ${endpoint}`, data ? data : '');
  }
};

// Release tracking utility for development team
export const reportReleaseInfo = async () => {
  try {
    const releaseInfo = {
      appVersion: buildConfig.appVersion,
      buildNumber: buildConfig.buildNumber,
      releaseVersion: buildConfig.releaseVersion,
      gitCommit: buildConfig.gitCommit,
      gitBranch: buildConfig.gitBranch,
      releaseDate: buildConfig.releaseDate,
      releaseNotes: buildConfig.releaseNotes,
      timestamp: buildConfig.timestamp,
      cacheVersion: buildConfig.cacheVersion,
      platform: Platform.OS,
      environment: environment,
      deviceInfo: {
        platform: Platform.OS,
        version: Platform.Version,
        constants: Constants.systemVersion
      }
    };

    console.log('📊 Reporting release info to backend for tracking...');

    // Send to backend for development team tracking
    const response = await fetch(`${apiUrls.citraService}/api/release-tracking`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(releaseInfo)
    });

    if (response.ok) {
      console.log('✅ Release info successfully reported to development team');
    } else {
      console.log('⚠️ Failed to report release info (non-critical)');
    }
  } catch (error) {
    console.log('⚠️ Release tracking error (non-critical):', error.message);
  }
};

// Get release summary for display in app
export const getReleaseInfo = () => {
  return {
    version: buildConfig.releaseVersion,
    build: buildConfig.buildNumber,
    date: buildConfig.releaseDate,
    environment: environment,
    platform: Platform.OS,
    notes: buildConfig.releaseNotes
  };
};

// Legacy exports for backwards compatibility (to be removed gradually)
export const API_CONFIG = {
  // Cache busting properties (critical for App.js)
  BUILD_TIMESTAMP: CONFIG.BUILD_TIMESTAMP,
  CACHE_VERSION: CONFIG.CACHE_VERSION,

  // Environment properties
  IS_DEVELOPMENT: CONFIG.isDevelopment,
  IS_TEST: CONFIG.isTest,
  IS_PRODUCTION: CONFIG.isProduction,
  ENVIRONMENT: CONFIG.ENVIRONMENT,

  // Environment helper properties (legacy compatibility)
  isDevelopment: CONFIG.isDevelopment,
  isTest: CONFIG.isTest,
  isProduction: CONFIG.isProduction,
  isAuthDisabled: CONFIG.isAuthDisabled,

  // Direct property access for legacy compatibility
  BASE_URL: CONFIG.BASE_URL,
  USER_SERVICE_URL: CONFIG.USER_SERVICE_URL,
  CITRA_SERVICE_URL: CONFIG.CITRA_SERVICE_URL,
  TRANSCRIBE_URL: CONFIG.TRANSCRIBE_URL,
  API_URL: CONFIG.CITRA_SERVICE_URL,

  // API endpoints
  AUDIO_UPLOAD_URL: CONFIG.AUDIO_UPLOAD_URL,
  AUDIO_DATA_URL: CONFIG.AUDIO_DATA_URL,
  DOCUMENT_URL: CONFIG.DOCUMENT_URL,
  CHAT_URL: CONFIG.CHAT_URL,
  NOTE_URL: CONFIG.NOTE_URL,
  QUERY_URL: CONFIG.QUERY_URL,
  COMPOSER_QUERY_URL: CONFIG.COMPOSER_QUERY_URL,
  TRANSCRIPT_URL: CONFIG.TRANSCRIPT_URL,
  PERSONA_URL: CONFIG.PERSONA_URL,

  // Configuration values
  TIMEOUT: CONFIG.TIMEOUT,
  API_TIMEOUT: CONFIG.TIMEOUT, // Alias for legacy compatibility
  DOCUMENT_UPLOAD_TIMEOUT: CONFIG.DOCUMENT_UPLOAD_TIMEOUT,
  MAX_RETRY_ATTEMPTS: CONFIG.MAX_RETRY_ATTEMPTS,

  // Features and auth
  FEATURES: CONFIG.FEATURES,
  features: CONFIG.features,
  OAUTH: CONFIG.OAUTH,
  AUTH: CONFIG.AUTH,

  // Additional legacy properties that might be referenced
  ...CONFIG.api,
};
export const ENVIRONMENT = environment;

export default CONFIG;
