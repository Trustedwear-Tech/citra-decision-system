// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Simplified API System for Citra AI UI
 * Removed caching for better stability and data consistency
 * Professional users (doctors/lawyers) prioritize reliability over speed
 */

import axios from 'axios';

// Simple axios instance without caching - direct API calls for reliability
const simpleAxios = axios.create({
  timeout: 30000, // 30 second timeout
  headers: {
    'Content-Type': 'application/json',
  }
});

// Add request interceptor for logging (optional)
simpleAxios.interceptors.request.use(
  (config) => {
    console.log(`🌐 API Request: ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    console.error('❌ API Request Error:', error);
    return Promise.reject(error);
  }
);

// Add response interceptor for logging (optional)
simpleAxios.interceptors.response.use(
  (response) => {
    console.log(`✅ API Response: ${response.status} ${response.config.url}`);
    return response;
  },
  (error) => {
    console.error('❌ API Response Error:', error?.response?.status, error?.config?.url);
    return Promise.reject(error);
  }
);

// Dummy cache object for backward compatibility with existing code
const apiCache = {
  clear: () => {
    console.log('📝 Cache clear called (no-op - caching disabled for stability)');
  },
  invalidate: (pattern) => {
    console.log(`📝 Cache invalidate called for ${pattern} (no-op - caching disabled for stability)`);
  },
  set: () => {
    // No-op - caching disabled for better data consistency
  },
  get: () => {
    return null; // Always return null - no caching for reliability
  },
  getStats: () => {
    return {
      size: 0,
      hitRate: 0,
      totalRequests: 0,
      cacheHits: 0,
      cacheMisses: 0
    };
  }
};

// Helper functions for backward compatibility
export const clearAPICache = () => {
  console.log('📝 Clear API cache called (no-op - caching disabled for stability)');
};

export const invalidateCache = (pattern) => {
  console.log(`📝 Invalidate cache called for ${pattern} (no-op - caching disabled for stability)`);
};

export const getCacheStats = () => {
  return {
    size: 0,
    hitRate: 0,
    totalRequests: 0,
    cacheHits: 0,
    cacheMisses: 0
  };
};

// Export simple axios without caching for better reliability and data consistency
export { simpleAxios as cachedAxios, apiCache };
