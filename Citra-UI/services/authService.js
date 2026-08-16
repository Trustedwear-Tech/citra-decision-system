/**
 * Authentication Service
 * Handles JWT token management and authenticated API requests
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { CONFIG, API_CONFIG } from '../config/config';

class AuthService {
  constructor() {
    this.token = null;
    this.refreshPromise = null;
    this.initializationPromise = null;
    this.authRequiredCallbacks = new Set();
    this.creditRequiredCallbacks = new Set();
    this.lastCreditNotifyAt = 0; // deduplication timestamp
    // Start token initialization but don't block constructor
    this.initializationPromise = this.initializeToken();
  }

  /**
   * Subscribe to auth-required events so the UI can trigger a re-login flow.
   * Returns an unsubscribe function for cleanup.
   */
  onAuthRequired(callback) {
    if (typeof callback !== 'function') {
      return () => { };
    }

    this.authRequiredCallbacks.add(callback);
    return () => {
      this.authRequiredCallbacks.delete(callback);
    };
  }

  /**
   * License model: credit notifications are disabled.
   * These methods are stubs kept for call-site compatibility.
   */
  onCreditRequired(callback) {
    // No-op: credit warnings removed in license model
    return () => { };
  }

  /**
   * Notify subscribers that authentication is required.
   */
  notifyAuthRequired(reason = 'unknown') {
    if (!this.authRequiredCallbacks?.size) {
      return;
    }

    this.authRequiredCallbacks.forEach((callback) => {
      try {
        callback(reason);
      } catch (notifyError) {
        console.warn('⚠️ [AUTH_SERVICE] Auth-required callback failed:', notifyError);
      }
    });
  }

  /** License model: credit notifications are disabled. */
  notifyCreditRequired(message = 'Insufficient credits') {
    // No-op: credit warnings removed in license model
  }

  /**
   * Initialize token from storage on startup
   */
  async initializeToken() {
    try {
      // Try to get token from stored user data first (primary method)
      const userDataString = await AsyncStorage.getItem('@user');
      if (userDataString) {
        const userData = JSON.parse(userDataString);
        const token = userData?.data?.token;
        if (token) {
          // Check if token is expired before caching
          if (this._isTokenExpired(token)) {
            console.log('⚠️ [AUTH_SERVICE] Stored token is expired — clearing');
            this.token = null;
            return;
          }
          this.token = token;
          console.log('✅ [AUTH_SERVICE] Token pre-loaded from user data');
          return;
        }
      }

      // Check direct @auth_token storage
      if (!userDataString) {
        const directToken = await AsyncStorage.getItem('@auth_token');
        if (directToken) {
          if (this._isTokenExpired(directToken)) {
            console.log('⚠️ [AUTH_SERVICE] Direct stored token is expired — clearing');
            this.token = null;
            return;
          }
          console.log('⚠️ [AUTH_SERVICE] Found orphaned token in direct storage - will validate on first use');
          this.token = directToken;
          console.log('✅ [AUTH_SERVICE] Token pre-loaded from direct storage (requires validation)');
          return;
        }
      }

      // Fallback: check UserProvider storage key ('auth_token' without @ prefix)
      // UserProvider uses this key which differs from authService's '@auth_token'
      const userProviderToken = await AsyncStorage.getItem('auth_token');
      if (userProviderToken) {
        if (this._isTokenExpired(userProviderToken)) {
          console.log('⚠️ [AUTH_SERVICE] UserProvider stored token is expired — clearing');
          this.token = null;
          return;
        }
        this.token = userProviderToken;
        console.log('✅ [AUTH_SERVICE] Token pre-loaded from UserProvider storage');
        // Sync to @auth_token for future lookups
        try {
          await AsyncStorage.setItem('@auth_token', userProviderToken);
          if (userDataString) {
            const ud = JSON.parse(userDataString);
            ud.data = ud.data || {};
            ud.data.token = userProviderToken;
            await AsyncStorage.setItem('@user', JSON.stringify(ud));
          }
        } catch (_syncErr) { /* best-effort sync */ }
        return;
      }

      if (userDataString) {
        console.log('ℹ️ [AUTH_SERVICE] User data exists but no token found in any storage');
      }
    } catch (error) {
      console.log('ℹ️ [AUTH_SERVICE] No token to pre-load:', error.message);
    }
  }

  /**
   * Check if a JWT token is expired (client-side check).
   * Returns true if expired or unparseable, false if still valid.
   * Includes a 60-second buffer to avoid edge-case failures.
   */
  _isTokenExpired(token) {
    try {
      if (!token || typeof token !== 'string') return true;
      const parts = token.split('.');
      if (parts.length !== 3) return false; // Not a JWT — let server validate
      // Base64url decode the payload
      const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      const decoded = JSON.parse(
        typeof atob === 'function'
          ? atob(payload)
          : Buffer.from(payload, 'base64').toString('utf-8')
      );
      if (!decoded.exp) return false; // No exp claim — let server validate
      // Expired if current time is past exp (with 60s buffer)
      const nowSeconds = Math.floor(Date.now() / 1000);
      return decoded.exp < nowSeconds - 60;
    } catch {
      // If we can't parse it, let the server decide
      return false;
    }
  }

  /**
   * Extract email from the cached JWT token payload.
   * Returns email string or null if not available.
   */
  _getEmailFromToken() {
    try {
      const token = this.token;
      if (!token || typeof token !== 'string') return null;
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      const decoded = JSON.parse(
        typeof atob === 'function'
          ? atob(payload)
          : Buffer.from(payload, 'base64').toString('utf-8')
      );
      // JWT payload may have email in various claims
      const email = decoded.email || decoded.user_id || decoded.sub;
      if (email && typeof email === 'string' && email.includes('@')) {
        return email;
      }
      return null;
    } catch {
      return null;
    }
  }

  /**
   * Decode the cached JWT and return its payload (user identity claims).
   * Sync — backed by the in-memory token, no AsyncStorage roundtrip.
   * Returns null if there's no valid token.
   */
  getCurrentUser() {
    try {
      const token = this.token;
      if (!token || typeof token !== 'string') return null;
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(
        typeof atob === 'function'
          ? atob(payload)
          : Buffer.from(payload, 'base64').toString('utf-8')
      );
    } catch {
      return null;
    }
  }

  /**
   * Ensure initialization is complete before operations
   */
  async ensureInitialized() {
    if (this.initializationPromise) {
      await this.initializationPromise;
      this.initializationPromise = null; // Clear after first use
    }
  }

  /**
   * Get current JWT token from storage
   */
  async getToken() {
    // Ensure initialization is complete
    await this.ensureInitialized();

    // Bypass for localhost development
    if (CONFIG.isAuthDisabled) {
      console.log('🔓 [AUTH_SERVICE] Auth disabled for localhost - returning mock token');
      return 'mock-token-localhost-bypass';
    }

    if (this.token) {
      // Check cached token for expiry before returning
      if (this._isTokenExpired(this.token)) {
        console.log('⚠️ [AUTH_SERVICE] Cached token is expired — clearing and re-authenticating');
        this.token = null;
        await this.clearToken();
        this.notifyAuthRequired('token-expired');
        throw new Error('Token expired. Please sign in again.');
      }
      return this.token;
    }

    try {
      // Try to get token from stored user data (primary method)
      const userDataString = await AsyncStorage.getItem('@user');
      if (userDataString) {
        const userData = JSON.parse(userDataString);
        const token = userData?.data?.token;
        if (token) {
          console.log('✅ [AUTH_SERVICE] Found token in user data');
          this.token = token;
          return this.token;
        }

        // User data exists but has no token — check if UserProvider stored it
        // under its own key ('auth_token' without @ prefix) before giving up
        const userProviderToken = await AsyncStorage.getItem('auth_token');
        if (userProviderToken) {
          console.log('✅ [AUTH_SERVICE] Found token in UserProvider storage, syncing');
          this.token = userProviderToken;
          // Sync it back into @user and @auth_token so future lookups are fast
          try {
            const ud = JSON.parse(userDataString);
            ud.data = ud.data || {};
            ud.data.token = userProviderToken;
            await AsyncStorage.setItem('@user', JSON.stringify(ud));
            await AsyncStorage.setItem('@auth_token', userProviderToken);
          } catch (_syncErr) { /* best-effort sync */ }
          return this.token;
        }

        // Genuinely no token anywhere — user was likely deleted
        console.log('⚠️ [AUTH_SERVICE] User data exists but no token - authentication required');
        this.notifyAuthRequired('token-missing');
        throw new Error('Authentication required. Please sign in.');
      }

      // Only use direct token storage if NO user data exists at all
      // This covers the case where storage was partially cleared
      const directToken = await AsyncStorage.getItem('@auth_token');
      if (directToken) {
        console.log('⚠️ [AUTH_SERVICE] Using direct token storage - token will be validated by API');
        this.token = directToken;
        return this.token;
      }

      // Final fallback: check UserProvider storage key ('auth_token' without @ prefix)
      const userProviderFallback = await AsyncStorage.getItem('auth_token');
      if (userProviderFallback) {
        console.log('✅ [AUTH_SERVICE] Found token in UserProvider storage (fallback)');
        this.token = userProviderFallback;
        // Sync for future lookups
        try {
          await AsyncStorage.setItem('@auth_token', userProviderFallback);
        } catch (_syncErr) { /* best-effort */ }
        return this.token;
      }

      // No token found - authentication required
      console.log('❌ [AUTH_SERVICE] No token found - authentication required');
      this.notifyAuthRequired('token-missing');
      throw new Error('Authentication required. Please sign in.');
    } catch (error) {
      console.error('❌ Error getting auth token:', error);
      // Don't re-notify — the throw sites above already called notifyAuthRequired
      throw error;
    }
  }

  /**
   * Backward-compatible wrapper used by consumers expecting getAccessToken()
   */
  async getAccessToken() {
    return this.getToken();
  }

  /**
   * Check if token exists without throwing errors
   */
  async hasToken() {
    try {
      // Ensure initialization is complete
      await this.ensureInitialized();

      // Bypass for localhost development
      if (CONFIG.isAuthDisabled) {
        return true;
      }

      // Return true if cached token exists
      if (this.token) {
        return true;
      }

      // Try to get token from user data
      const userDataString = await AsyncStorage.getItem('@user');
      if (userDataString) {
        const userData = JSON.parse(userDataString);
        const token = userData?.data?.token;
        if (token) {
          // Cache the token for next time
          this.token = token;
          return true;
        }

        // User data exists but no token — check UserProvider storage before giving up
        const userProviderToken = await AsyncStorage.getItem('auth_token');
        if (userProviderToken) {
          this.token = userProviderToken;
          return true;
        }

        return false;
      }

      // Only check direct token storage if NO user data exists
      const directToken = await AsyncStorage.getItem('@auth_token');
      if (directToken) {
        // Cache the token for next time but mark it as needing validation
        this.token = directToken;
        console.log('⚠️ [AUTH_SERVICE] Found orphaned token - will be validated on API call');
        return true;
      }

      // Final fallback: check UserProvider storage key
      const userProviderFallback = await AsyncStorage.getItem('auth_token');
      if (userProviderFallback) {
        this.token = userProviderFallback;
        return true;
      }

      return false;
    } catch (error) {
      console.error('❌ Error checking for token:', error);
      return false;
    }
  }

  /**
   * Set authentication token
   */
  async setToken(token) {
    if (!token) {
      console.warn('❌ [AUTH_SERVICE] Attempted to set null/undefined token');
      return;
    }

    try {
      this.token = token;

      // Store token directly as backup
      await AsyncStorage.setItem('@auth_token', token);

      // Also update the user data if it exists
      const userDataString = await AsyncStorage.getItem('@user');
      if (userDataString) {
        const userData = JSON.parse(userDataString);
        if (userData.data) {
          userData.data.token = token;
          await AsyncStorage.setItem('@user', JSON.stringify(userData));
          console.log('✅ [AUTH_SERVICE] Token updated in user data');
        }
      }

      console.log('✅ [AUTH_SERVICE] Token stored successfully');
    } catch (error) {
      console.error('❌ Error storing auth token:', error);
    }
  }

  /**
   * Clear authentication token
   */
  async clearToken() {
    console.log('🧹 [AUTH_SERVICE] Clearing authentication token');
    this.token = null;
    try {
      await AsyncStorage.removeItem('@auth_token');
      await AsyncStorage.removeItem('@user');
      console.log('✅ [AUTH_SERVICE] All authentication data cleared');
    } catch (error) {
      console.error('❌ Error clearing auth token:', error);
    }
  }

  /**
   * Clear orphaned tokens (when user data exists but user was deleted from database)
   * This is more targeted than clearToken() and preserves any non-auth user data
   */
  async clearOrphanedToken() {
    console.log('🧹 [AUTH_SERVICE] Clearing orphaned authentication token');
    this.token = null;
    try {
      // Remove direct token storage
      await AsyncStorage.removeItem('@auth_token');

      // Remove token from user data but preserve other user info for re-authentication
      const userDataString = await AsyncStorage.getItem('@user');
      if (userDataString) {
        const userData = JSON.parse(userDataString);
        if (userData.data && userData.data.token) {
          delete userData.data.token;
          await AsyncStorage.setItem('@user', JSON.stringify(userData));
          console.log('✅ [AUTH_SERVICE] Token removed from user data, user info preserved');
        }
      }

      console.log('✅ [AUTH_SERVICE] Orphaned token cleared');
    } catch (error) {
      console.error('❌ Error clearing orphaned token:', error);
    }
  }

  /**
   * Get authentication headers with JWT token and device ID
   */
  async getAuthHeaders(additionalHeaders = {}) {
    const token = await this.getToken();
    const deviceId = await this.getCurrentUserEmail(); // Using email as device ID

    const headers = {
      ...additionalHeaders
    };

    // Only add Content-Type if not explicitly set to undefined (for FormData requests)
    if ('Content-Type' in headers && headers['Content-Type'] === undefined) {
      // Remove Content-Type for FormData requests (explicitly passed as undefined)
      delete headers['Content-Type'];
    } else if (!headers['Content-Type'] && !headers['content-type']) {
      // Set default Content-Type for JSON requests
      headers['Content-Type'] = 'application/json';
    }

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    // Add device ID header for endpoints that require it
    if (deviceId) {
      headers['X-Device-ID'] = deviceId;
    }

    return headers;
  }

  /**
   * Make authenticated fetch request and return parsed JSON
   */
  async authenticatedFetchJson(url, options = {}) {
    const response = await this.authenticatedFetch(url, options);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Make authenticated fetch request
   */
  async authenticatedFetch(url, options = {}) {
    // Get fresh token before making request
    const token = await this.getToken();
    if (!token) {
      console.warn('❌ [AUTH_SERVICE] No token available for authenticated request');
      throw new Error('Authentication required');
    }

    // Auto-detect FormData: ensure Content-Type is NOT set so browser adds multipart boundary
    const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;
    const headerOverrides = isFormData
      ? { ...options.headers, 'Content-Type': undefined }
      : options.headers;

    const authHeaders = await this.getAuthHeaders(headerOverrides);

    const requestOptions = {
      ...options,
      headers: authHeaders
    };

    // --- Defensive production hardening: conditional https enforcement ---
    try {
      const isProductionEnv = CONFIG?.isProduction || process?.env?.EXPO_PUBLIC_ENVIRONMENT === 'production';
      const inBrowser = typeof window !== 'undefined';
      const onProdSite = inBrowser && window.location.hostname.includes('citra-ai.com');
      const prodHost = 'api.citra-ai.com';
      const targetIsProdHost = typeof url === 'string' && url.includes(prodHost);

      // Only enforce if we are in a production environment (explicit or inferred) AND
      // either running on prod site OR the request targets the prod host.
      if (isProductionEnv && (onProdSite || targetIsProdHost) && typeof url === 'string') {
        // Specific upgrade for known production host
        if (url.startsWith(`http://${prodHost}`)) {
          console.warn('⚠️ [AUTH_SERVICE] Upgrading insecure production host request to https:', url);
          url = url.replace(`http://${prodHost}`, `https://${prodHost}`);
        }
        // Generic safety net ONLY when on prod site to avoid rewriting localhost dev calls
        else if (onProdSite && /^http:\/\//.test(url) && !url.startsWith('http://localhost') && !url.startsWith('http://127.0.0.1')) {
          console.warn('⚠️ [AUTH_SERVICE] Upgrading generic http request to https (prod site context):', url);
          url = url.replace(/^http:\/\//, 'https://');
        }
      }
      // In development: no automatic rewriting (intentional)
    } catch (enforceErr) {
      console.warn('⚠️ [AUTH_SERVICE] HTTPS enforcement error (non-fatal):', enforceErr.message);
    }
    // -----------------------------------------------------------------------

    // Only log if there are authentication issues
    if (!authHeaders.Authorization) {
      console.log(`❌ [AUTH_SERVICE] No Authorization header found`);
    }

    if (!authHeaders['X-Device-ID']) {
      console.log(`❌ [AUTH_SERVICE] No X-Device-ID header found`);
    }

    try {
      const response = await fetch(url, requestOptions);

      // Handle authentication errors more carefully
      if (response.status === 401) {
        console.warn('🔒 Authentication failed - token may be invalid');

        // Try to refresh the token before clearing everything
        const freshToken = await this.getToken();
        const isValid = freshToken ? await this.validateToken() : false;
        if (isValid) {
          console.log('🔄 [AUTH_SERVICE] Retrying with fresh token');
          // Retry the request once with fresh token
          const freshHeaders = {
            ...authHeaders,
            'Authorization': `Bearer ${freshToken}`
          };
          const retryResponse = await fetch(url, {
            ...requestOptions,
            headers: freshHeaders
          });

          if (retryResponse.status !== 401) {
            return retryResponse;
          }
        }

        // Don't wipe stored token — notify re-auth so the user can sign in again.
        // Clearing here would destroy a valid token that may work with other services.
        this.notifyAuthRequired('token-invalid');
        throw new Error('Authentication required');
      }

      return response;
    } catch (error) {
      // Don't log AbortError as it's expected when requests are cancelled
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        console.error('❌ Authenticated fetch error:', error);
      }
      throw error;
    }
  }

  /**
   * Make authenticated axios request (for backward compatibility)
   */
  async getAxiosConfig(additionalConfig = {}) {
    const authHeaders = await this.getAuthHeaders(additionalConfig.headers);

    return {
      ...additionalConfig,
      headers: authHeaders
    };
  }

  /**
   * Check if user is authenticated
   */
  async isAuthenticated() {
    if (CONFIG.isAuthDisabled) {
      return true;
    }
    const token = await this.getToken();
    return !!token;
  }

  /**
   * Get current user email from token or storage
   */
  async getCurrentUserEmail() {
    try {
      // Bypass for localhost development
      if (CONFIG.isAuthDisabled) {
        return 'test@citra-ai.app';
      }

      // First try to get from stored user data
      const userDataString = await AsyncStorage.getItem('@user');
      if (userDataString) {
        const userData = JSON.parse(userDataString);
        // Support both flat structure (from 403 response) and nested structure (from full auth)
        const email = userData?.email || userData?.data?.user?.email || userData?.data?.email;
        if (email && email.includes('@')) {
          return email;
        }
      }

      // Fallback: try direct email storage
      const directEmail = await AsyncStorage.getItem('@user_email');
      if (directEmail && directEmail.includes('@')) {
        return directEmail;
      }

      // Fallback: try to extract email from the cached JWT token
      const emailFromToken = this._getEmailFromToken();
      if (emailFromToken) {
        console.log('ℹ️ [AUTH_SERVICE] Extracted email from JWT token as fallback');
        return emailFromToken;
      }

      // Authentication required - fail fast instead of masking with guest
      throw new Error('No authenticated user found. Please sign in.');
    } catch (error) {
      console.error('❌ Error getting user email:', error);
      // Re-throw to force proper authentication instead of masking with guest
      throw error;
    }
  }

  /**
   * Validate current token with server
   */
  async validateToken() {
    try {
      // Bypass for localhost development
      if (CONFIG.isAuthDisabled) {
        return true;
      }

      const token = await this.getToken();
      if (!token) {
        return false;
      }

      // Use the environment-configured auth URL so dev/prod tokens are validated against the right server
      const authBase = API_CONFIG?.AUTH?.baseUrl || API_CONFIG?.auth?.baseUrl || 'http://localhost:7004/api/auth';
      const meEndpoint = API_CONFIG?.AUTH?.meEndpoint || '/me';
      const testUrl = `${authBase}${meEndpoint}`;
      const response = await fetch(testUrl, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });

      return response.ok;
    } catch (error) {
      console.error('❌ Token validation error:', error);
      return false;
    }
  }
}

// Export singleton instance
export const authService = new AuthService();
export default authService;
