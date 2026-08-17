// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// URL Router Utilities for Web
// Handles URL-based navigation without changing the UI/UX

import { Platform } from 'react-native';

// Route definitions
export const ROUTES = {
  HOME: '/home',
  CHAT: '/chat',
  READER: '/reader',
  CREDITS: '/credits',
  VERIFY_EMAIL: '/verify-email',
  RESET_PASSWORD: '/reset-password',
};

/**
 * Parse current URL and return route info
 * @returns {{ route: string, id: string|null, params: object }}
 */
export const parseCurrentUrl = () => {
  if (Platform.OS !== 'web' || typeof window === 'undefined') {
    return { route: ROUTES.HOME, id: null, params: {} };
  }

  const path = window.location.pathname;
  const searchParams = new URLSearchParams(window.location.search);
  const params = Object.fromEntries(searchParams.entries());

  // Parse route patterns
  // /presentation/abc123 -> { route: '/presentation', id: 'abc123' }
  // /home -> { route: '/home', id: null }
  // /chat -> { route: '/chat', id: null }

  if (path === '/' || path === '' || path === '/home') {
    return { route: ROUTES.HOME, id: null, params };
  }

  // Check for exact route matches (no ID)
  const exactRoutes = [
    { path: '/chat', route: ROUTES.CHAT },
    { path: '/credits', route: ROUTES.CREDITS },
    { path: '/verify-email', route: ROUTES.VERIFY_EMAIL },
    { path: '/reset-password', route: ROUTES.RESET_PASSWORD },
  ];

  for (const { path: routePath, route } of exactRoutes) {
    if (path === routePath) {
      return { route, id: null, params };
    }
  }

  // Check for item routes with ID (id can be a tab name like 'internet' or 'personal')
  const itemRoutes = [
    { prefix: '/chat/', route: ROUTES.CHAT },
  ];

  for (const { prefix, route } of itemRoutes) {
    if (path.startsWith(prefix)) {
      const id = path.slice(prefix.length) || null;
      return { route, id, params };
    }
  }

  // Default to home for unknown routes
  return { route: ROUTES.HOME, id: null, params };
};

/**
 * Navigate to a route (updates URL without page reload)
 * @param {string} route - Route path
 * @param {string|null} id - Optional item ID
 * @param {object} params - Optional query parameters
 */
export const navigateTo = (route, id = null, params = {}) => {
  if (Platform.OS !== 'web' || typeof window === 'undefined') {
    return;
  }

  let path = route;
  if (id) {
    path = `${route}/${id}`;
  }

  // Add query params if any
  const queryString = new URLSearchParams(params).toString();
  if (queryString) {
    path = `${path}?${queryString}`;
  }

  // Skip navigation if we're already on the same URL (prevents duplicate history entries)
  if (window.location.pathname === path) {
    console.log('🔗 [URL_ROUTER] Already on', path, '- skipping navigation');
    return;
  }

  // Use history.pushState to change URL without reload
  window.history.pushState({ route, id, params }, '', path);

  // Dispatch custom event for components to listen to
  window.dispatchEvent(new CustomEvent('urlchange', { 
    detail: { route, id, params } 
  }));
};

/**
 * Replace current URL without adding to history (for updates like save)
 * @param {string} route - Route path
 * @param {string|null} id - Optional item ID
 * @param {object} params - Optional query parameters
 */
export const replaceUrl = (route, id = null, params = {}) => {
  if (Platform.OS !== 'web' || typeof window === 'undefined') {
    return;
  }

  let path = route;
  if (id) {
    path = `${route}/${id}`;
  }

  // Add query params if any
  const queryString = new URLSearchParams(params).toString();
  if (queryString) {
    path = `${path}?${queryString}`;
  }

  // Use replaceState to update URL without adding history entry
  window.history.replaceState({ route, id, params }, '', path);
  console.log('🔗 [URL_ROUTER] Replaced URL to', path);
};

/**
 * Navigate to home
 */
export const navigateToHome = () => {
  navigateTo(ROUTES.HOME);
};

/**
 * Navigate to chat interface
 */
export const navigateToReader = () => {
  navigateTo(ROUTES.READER);
};

export const navigateToChat = () => {
  navigateTo(ROUTES.CHAT);
};

/**
 * Go back in browser history
 */
export const goBack = () => {
  if (Platform.OS !== 'web' || typeof window === 'undefined') {
    return;
  }
  window.history.back();
};

/**
 * Hook to listen for URL changes (popstate events)
 * @param {function} callback - Called with route info when URL changes
 * @returns {function} Cleanup function
 */
export const onUrlChange = (callback) => {
  if (Platform.OS !== 'web' || typeof window === 'undefined') {
    return () => {};
  }

  const handlePopState = () => {
    const routeInfo = parseCurrentUrl();
    callback(routeInfo);
  };

  const handleUrlChange = (event) => {
    callback(event.detail);
  };

  // Listen for browser back/forward
  window.addEventListener('popstate', handlePopState);
  // Listen for programmatic navigation
  window.addEventListener('urlchange', handleUrlChange);

  return () => {
    window.removeEventListener('popstate', handlePopState);
    window.removeEventListener('urlchange', handleUrlChange);
  };
};

/**
 * Update document title based on route
 * @param {string} route - Current route
 * @param {string|null} itemName - Optional item name
 */
export const updateDocumentTitle = (route, itemName = null) => {
  if (Platform.OS !== 'web' || typeof document === 'undefined') {
    return;
  }

  const titles = {
    [ROUTES.HOME]: 'Citra AI - Create Intelligent Content',
    [ROUTES.CHAT]: 'Chat | Citra AI',
  };

  document.title = titles[route] || 'Citra AI';
};

/**
 * Initialize URL routing on app start
 * Redirects root (/) to /home
 */
export const initializeRouting = () => {
  if (Platform.OS !== 'web' || typeof window === 'undefined') {
    return { route: ROUTES.HOME, id: null, params: {} };
  }

  const path = window.location.pathname;
  
  // If at root, redirect to /home
  if (path === '/' || path === '') {
    window.history.replaceState({}, '', '/home');
  }

  return parseCurrentUrl();
};
