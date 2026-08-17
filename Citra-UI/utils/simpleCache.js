// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Simple API Cache - Safe for Metro bundler
 * Lightweight caching system without complex dependencies
 */

// Simple in-memory cache
const cache = new Map();
const ttlMap = new Map();

// Cache configuration
const CACHE_TTL = {
  default: 5 * 60 * 1000, // 5 minutes
  userDetails: 10 * 60 * 1000, // 10 minutes
  documents: 5 * 60 * 1000, // 5 minutes
  notes: 2 * 60 * 1000, // 2 minutes
};

// Generate cache key
const generateKey = (url, method = 'GET') => {
  return `${method}:${url.replace(/[?&]timestamp=\d+/, '')}`;
};

// Get from cache
export const getCachedData = (url, method = 'GET') => {
  const key = generateKey(url, method);
  
  if (!cache.has(key)) {
    return null;
  }

  const ttl = ttlMap.get(key);
  if (ttl && Date.now() > ttl) {
    cache.delete(key);
    ttlMap.delete(key);
    return null;
  }

  console.log(`📋 [CACHE] Hit for ${method} ${url}`);
  return cache.get(key);
};

// Set cache
export const setCachedData = (url, method = 'GET', response, ttl = CACHE_TTL.default) => {
  const key = generateKey(url, method);
  const expiryTime = Date.now() + ttl;
  
  cache.set(key, response);
  ttlMap.set(key, expiryTime);
  
  console.log(`💾 [CACHE] Set for ${method} ${url}`);
};

// Clear cache
export const clearCache = () => {
  cache.clear();
  ttlMap.clear();
  console.log('🧹 [CACHE] Cleared');
};

// Export cache TTL for use in components
export { CACHE_TTL };
