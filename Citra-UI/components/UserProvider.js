import React, { createContext, useContext, useState, useEffect, useMemo } from 'react';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import authService from '../services/authService';
import { API_CONFIG } from '../config/config';

// User Context for managing authentication and onboarding state
const UserContext = createContext();

export const useUser = () => {
  const context = useContext(UserContext);
  if (!context) {
    throw new Error('useUser must be used within a UserProvider');
  }
  return context;
};

// Storage keys
const STORAGE_KEYS = {
  AUTH_TOKEN: 'auth_token',
  USER_PROFILE: 'user_profile',
};


function _decodeJwt(token) {
  try {
    if (!token || typeof token !== 'string') return {};
    const parts = token.split('.');
    if (parts.length !== 3) return {};
    const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const decoded = JSON.parse(
      typeof atob === 'function'
        ? atob(payload)
        : Buffer.from(payload, 'base64').toString('utf-8')
    );
    return decoded || {};
  } catch {
    return {};
  }
}

export const UserProvider = ({ children }) => {
  // Authentication state
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [authToken, setAuthToken] = useState(null);
  const [user, setUser] = useState(null);



  // Loading states
  const [isLoading, setIsLoading] = useState(true);
  const [isInitialized, setIsInitialized] = useState(false);

  // Initialize user state on app load
  useEffect(() => {
    // Web stuck-state recovery: detect if previous init got stuck
    if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
      const initStartedAt = localStorage.getItem('citra_init_started_at');
      if (initStartedAt) {
        const elapsed = Date.now() - parseInt(initStartedAt, 10);
        if (elapsed > 30000) {
          // Previous init was stuck for > 30s — clear bad auth state and start fresh
          console.warn('🚨 [USER_PROVIDER] Detected stuck init from previous session, clearing auth state...');
          localStorage.removeItem('citra_init_started_at');
          AsyncStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN).catch((err) =>
            console.error('🚨 [USER_PROVIDER] Failed to clear stale AUTH_TOKEN:', err));
          AsyncStorage.removeItem(STORAGE_KEYS.USER_PROFILE).catch((err) =>
            console.error('🚨 [USER_PROVIDER] Failed to clear stale USER_PROFILE:', err));
          localStorage.removeItem('citra_ai_initialized');
        }
      }
      // Mark that we're starting init
      localStorage.setItem('citra_init_started_at', Date.now().toString());
    }

    // Overall safety timeout — guarantees app becomes interactive
    const safetyTimeout = setTimeout(() => {
      console.warn('🚨 [USER_PROVIDER] Safety timeout: initializeUserState took >5s, force completing');
      setIsLoading(false);
      setIsInitialized(true);
      // Clean up stuck marker
      if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
        localStorage.removeItem('citra_init_started_at');
      }
    }, 5000);

    initializeUserState().finally(() => {
      clearTimeout(safetyTimeout);
      // Clean up stuck marker on successful completion
      if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
        localStorage.removeItem('citra_init_started_at');
      }
    });

    return () => clearTimeout(safetyTimeout);
  }, []);

  const initializeUserState = async () => {
    try {
      setIsLoading(true);

      // Bypass for localhost development - Auto-login
      if (API_CONFIG.isAuthDisabled) {
        console.log('🔓 [USER_PROVIDER] Auth disabled for localhost - auto-logging in');

        const mockUser = {
          email: 'test@citra-ai.app',
          name: 'Test User',
          persona: 'professional',
          credits: { balance: 1000 },
          onboarding_complete: true
        };

        const mockToken = 'mock-token-localhost-bypass';

        setAuthToken(mockToken);
        setIsAuthenticated(true);
        setUser(mockUser);

        setIsLoading(false);
        setIsInitialized(true);
        return;
      }

      // Load stored data
      const [
        storedToken,
        storedProfile,
      ] = await Promise.all([
        AsyncStorage.getItem(STORAGE_KEYS.AUTH_TOKEN),
        AsyncStorage.getItem(STORAGE_KEYS.USER_PROFILE)
      ]);

      console.log('🔍 [USER_PROVIDER] AsyncStorage state on load:', {
        hasToken: !!storedToken,
        tokenPreview: storedToken ? `${storedToken.substring(0, 20)}...` : null,
        hasProfile: !!storedProfile,
        profileEmail: storedProfile ? JSON.parse(storedProfile)?.email : null
      });

      let isUserAuthenticated = false;

      // FAST PATH: a cached profile + token lets us render the authenticated UI
      // immediately for returning users, then validate in the background. The
      // cached profile is only a render-speed optimization — the background
      // validate below refetches the canonical profile from /auth/me and
      // overwrites it (and storage), so any stale or legacy-shaped cache
      // self-heals within one round-trip.
      if (storedToken && storedProfile) {
        const profile = JSON.parse(storedProfile);
        setAuthToken(storedToken);
        setIsAuthenticated(true);
        setUser(profile);
        isUserAuthenticated = true;

        // Sync token to authService so authenticatedFetch and other consumers can find it
        try {
          await authService.setToken(storedToken);
          console.log('✅ [USER_PROVIDER] Token synced to authService on fast-path init');
        } catch (syncError) {
          console.warn('⚠️ [USER_PROVIDER] Failed to sync token to authService:', syncError);
        }

        // Release UI immediately with cached data
        setIsLoading(false);
        setIsInitialized(true);

        // Validate in the background (non-blocking). On success it refetches and
        // overwrites with the canonical /auth/me profile; on failure we keep the
        // session (could be a transient network issue).
        validateTokenAndFetchProfile(storedToken).then((freshProfile) => {
          if (!freshProfile) {
            console.warn('⚠️ [USER_PROVIDER] Background token validation failed');
          }
        }).catch(() => {});

        return;
      }

      // SLOW PATH: token but no cached profile — must validate before we render.
      if (storedToken) {
        setAuthToken(storedToken);

        // Sync token to authService so authenticatedFetch and other consumers can find it
        try {
          await authService.setToken(storedToken);
          console.log('✅ [USER_PROVIDER] Token synced to authService on slow-path init');
        } catch (syncError) {
          console.warn('⚠️ [USER_PROVIDER] Failed to sync token to authService:', syncError);
        }

        // Returns the unwrapped canonical profile on success, falsy on failure.
        const profile = await validateTokenAndFetchProfile(storedToken);
        if (profile) {
          setIsAuthenticated(true);
          isUserAuthenticated = true;
          } else {
          console.warn('⚠️ [USER_PROVIDER] Token validation failed — marking as unauthenticated');
        }
      }


    } catch (error) {
      console.error('Error initializing user state:', error);
    } finally {
      setIsLoading(false);
      setIsInitialized(true);
    }
  };

  const validateTokenAndFetchProfile = async (token) => {
    try {
      // Bypass for localhost development
      if (API_CONFIG.isAuthDisabled) {
        const mockUser = {
          email: 'test@citra-ai.app',
          name: 'Test User',
          persona: 'professional',
          credits: { balance: 1000 },
          onboarding_complete: true
        };
        setUser(mockUser);
        return mockUser;
      }

      const baseUrl = API_CONFIG.USER_SERVICE_URL;

      // Use AbortController with timeout to prevent hanging requests on mobile web
      const controller = new AbortController();
      const timeoutId = setTimeout(() => {
        console.warn('⚠️ [USER_PROVIDER] Token validation timed out after 5s, aborting...');
        controller.abort();
      }, 5000); // 5 second timeout

      try {
        const response = await fetch(`${baseUrl}/api/auth/me`, {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          },
          signal: controller.signal,
        });

        clearTimeout(timeoutId);

        if (response.ok) {
          const body = await response.json();
          // /api/auth/me has ONE contract: { success, data: { user } }. Every
          // consumer (HomePanel hero, derivedUserEmail, the impersonation
          // stored profile) expects the UNWRAPPED user object, so we
          // unwrap to exactly data.user — no shape-guessing fallbacks. Storing
          // the wrapped envelope was the bug behind the impersonation hero's
          // "Welcome to (unknown org)" (user.org_id was undefined). If the
          // contract is ever violated, fail loud here so it gets fixed at the
          // source instead of silently dropping fields downstream.
          const profile = body?.data?.user;
          if (!profile || !profile.email) {
            console.error(
              '❌ [USER_PROVIDER] /api/auth/me violated its contract — expected { data: { user: { email, ... } } }. Got:',
              JSON.stringify(body)
            );
            return false;
          }
          setUser(profile);

          // Store updated profile
          await AsyncStorage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify(profile));

          // Clear any stuck-detection flag on success
          if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
            localStorage.removeItem('citra_init_started_at');
          }

          return profile;
        } else if (response.status === 429) {
          // Transient — preserve the current session if we already have one.
          // On a cold start with no user yet this is null, so we never
          // fabricate an authenticated session from a rate-limit response.
          console.warn('⚠️ [USER_PROVIDER] Rate limited (429), keeping any existing session');
          return user;
        } else {
          console.warn('⚠️ [USER_PROVIDER] Token validation returned non-OK status:', response.status);
          // Don't clear auth - could be temporary backend issue
          return false;
        }
      } catch (fetchError) {
        clearTimeout(timeoutId);
        if (fetchError.name === 'AbortError') {
          console.error('❌ [USER_PROVIDER] Token validation aborted (timeout)');
        } else {
          console.error('❌ [USER_PROVIDER] Token validation fetch error:', fetchError);
        }
        return false;
      }
    } catch (error) {
      console.error('❌ [USER_PROVIDER] Token validation error:', error);
      // Don't clear auth on network errors
      return false;
    }
  };

  const handleAuthentication = async (authData) => {
    try {
      const { token, user: userData } = authData;

      console.log('🔐 [USER_PROVIDER] handleAuthentication called');
      console.log('📦 [USER_PROVIDER] Full userData object:', JSON.stringify(userData, null, 2));
      // Update state
      setAuthToken(token);
      setIsAuthenticated(true);
      setUser(userData);

      // Store authentication data
      await AsyncStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, token);
      await AsyncStorage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify(userData));

      // Sync token to authService so authenticatedFetch and other consumers can find it
      // This ensures '@auth_token' and '@user' are also updated alongside 'auth_token'
      try {
        await authService.setToken(token);
        console.log('✅ [USER_PROVIDER] Token synced to authService after handleAuthentication');
      } catch (syncError) {
        console.warn('⚠️ [USER_PROVIDER] Failed to sync token to authService:', syncError);
      }

      // Trigger folder refresh after successful authentication
      if (typeof window !== 'undefined') {
        window.dispatchEvent?.(new CustomEvent('authSuccess'));
      }

      return { success: true };
    } catch (error) {
      console.error('Error handling authentication:', error);
      return { success: false, error };
    }
  };
  const clearAuthenticationState = async () => {
    try {
      // Clear state
      setIsAuthenticated(false);
      setAuthToken(null);
      setUser(null);

      // Clear authService token
      await authService.clearToken();

      // Clear storage.
      await Promise.all([
        AsyncStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN),
        AsyncStorage.removeItem(STORAGE_KEYS.USER_PROFILE),
        AsyncStorage.removeItem('@user') // Clear the @user key used by authService
      ]);

      return { success: true };
    } catch (error) {
      console.error('Error clearing authentication state:', error);
      return { success: false, error };
    }
  };


  // ──────────────────────────────────────────────────────────────────────
  // Impersonation
  // ──────────────────────────────────────────────────────────────────────
  //
  // Impersonation is modelled as a clean re-login as the target user.
  // The minted JWT carries `act` (actor email) and `impersonation_id`
  // claims that are forwarded to every backend call, so audit rows
  // record both who did the action and on whose behalf.
  //
  // The browser session does NOT preserve the original super_admin
  // token. To return, the admin must log in again. This avoids the
  // dual-source-of-truth class of bugs (cached super_admin profile vs
  // impersonated profile vs JWT identity) and removes a local
  // privilege-escalation surface (anyone with the device while
  // impersonating could otherwise call `endImpersonation` to re-elevate
  // without re-authenticating).

  /**
   * Swap the current session for the target user's. Wipes the existing
   * auth state, plants the new minted token + profile, and reloads.
   * After reload there is no path back without re-authentication.
   *
   * @param {string} targetUserEmail
   * @param {string} reason — free-text for the audit row
   * @returns {Promise<{success: boolean, error?: string, impersonation_id?: string}>}
   */
  const impersonate = async (targetUserEmail, reason = '') => {
    try {
      if (!authToken || !user) {
        return { success: false, error: 'Not authenticated' };
      }
      // Refuse if already impersonating — the picker requires
      // super_admin, which the impersonated user does NOT have, so this
      // is mostly belt-and-braces. The picker UI also gates this.
      if (_decodeJwt(authToken).act) {
        return { success: false, error: 'Already impersonating — logout first' };
      }

      // Defer the import to avoid a circular dep at module-load time
      // (services may import contexts/UserProvider).
      const AdminUserService = require('../services/AdminUserService').default;
      const minted = await AdminUserService.mintImpersonationToken(targetUserEmail, reason);
      if (!minted || !minted.token) {
        return { success: false, error: 'Mint endpoint returned no token' };
      }
      // Fail loud if the shape ever drifts. Falling back to defaults
      // ([] dept_ids, ['user'] roles) would silently impersonate with
      // wrong context — better to surface the error than to debug a
      // half-broken impersonation hours later.
      if (!minted.target_user || !minted.target_user.email) {
        return { success: false, error: 'Mint endpoint returned malformed payload (no target_user.email)' };
      }

      // Build a minimal user profile from the target_user payload — the
      // mint endpoint returns email + org_id + dept_ids + roles. The
      // app will re-fetch a fuller profile from /auth/me after reload.
      // We deliberately do NOT carry an `is_impersonating` flag on the
      // user object — the truth is in the JWT's `act` claim, read by
      // `impersonationContext`.
      const targetUser = {
        email: minted.target_user.email,
        org_id: minted.target_user.org_id || null,
        dept_ids: minted.target_user.dept_ids || [],
        roles: minted.target_user.roles || ['user'],
      };

      // Overwrite the auth keys in place — do NOT call
      // clearAuthenticationState here. It clears the token and profile
      // outright, which logs the operator out instead of switching
      // identity. After reload every component bootstraps from storage
      // with the new identity.
      await AsyncStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, minted.token);
      await AsyncStorage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify(targetUser));
      // Mirror the legacy wrapped @user blob so App.js's fast-path
      // verifier sees a consistent profile on reload.
      await AsyncStorage.setItem('@user', JSON.stringify({
        data: { user: targetUser, token: minted.token },
      }));
      try {
        await authService.setToken(minted.token);
      } catch (err) {
        console.warn('[USER_PROVIDER] impersonate: authService.setToken failed:', err.message);
      }

      // Force a full reload on web so every component re-bootstraps
      // from the new token (folders, admin check, smart-app
      // permissions, discovery cache). Some children of UserProvider
      // hold their own local authToken state that doesn't observe
      // context changes; reloading is the simplest correctness
      // guarantee.
      //
      // 30ms is enough for the caller's await to resolve and any
      // toast/log to settle. Shorter than the original 100ms because we
      // no longer need to give clearAuthenticationState's React state
      // setters time to flush — there's nothing to flush.
      if (typeof window !== 'undefined' && window.location && window.location.reload) {
        setTimeout(() => window.location.reload(), 30);
      }

      return {
        success: true,
        impersonation_id: minted.impersonation_id,
        expires_at: minted.expires_at,
      };
    } catch (error) {
      console.error('[USER_PROVIDER] impersonate failed:', error);
      return { success: false, error: error.message || String(error) };
    }
  };

  // Derived: is the current session an impersonation? Looks at the live
  // authToken's `act` claim — single source of truth (a stale flag on
  // user state would drift). Returns the actor email when true, null
  // otherwise.
  const impersonationContext = useMemo(() => {
    const decoded = _decodeJwt(authToken);
    if (!decoded.act) return null;
    return {
      actor: decoded.act,
      target: decoded.email || decoded.user_id || null,
      impersonation_id: decoded.impersonation_id || null,
      expires_at: decoded.exp ? new Date(decoded.exp * 1000).toISOString() : null,
    };
  }, [authToken]);
  const revalidateToken = async () => {
    try {
      console.log('🔄 [USER_PROVIDER] Revalidating auth token...');

      if (!authToken) {
        console.log('⚠️ [USER_PROVIDER] No token to validate');
        return false;
      }

      const isValid = await validateTokenAndFetchProfile(authToken);

      if (isValid) {
        console.log('✅ [USER_PROVIDER] Token is valid');
        setIsAuthenticated(true);
        return true;
      } else {
        console.log('❌ [USER_PROVIDER] Token is invalid, clearing auth state');
        await clearAuthenticationState();
        return false;
      }
    } catch (error) {
      console.error('❌ [USER_PROVIDER] Error revalidating token:', error);
      return false;
    }
  };

  const refreshUserData = async () => {
    try {
      console.log('🔄 [USER_PROVIDER] Refreshing user data...');

      if (!authToken) {
        console.log('⚠️ [USER_PROVIDER] No token available for refresh');
        return false;
      }

      const isValid = await validateTokenAndFetchProfile(authToken);

      if (isValid) {
        console.log('✅ [USER_PROVIDER] User data refreshed successfully');
        return true;
      } else {
        console.log('❌ [USER_PROVIDER] Failed to refresh user data');
        return false;
      }
    } catch (error) {
      console.error('❌ [USER_PROVIDER] Error refreshing user data:', error);
      return false;
    }
  };

  // `user` is always the canonical UNWRAPPED profile (every setUser path
  // stores data.user, never the { data: { user } } envelope). Read email
  // directly — no wrapped-shape fallbacks. Drift is caught + logged loudly at
  // the source in validateTokenAndFetchProfile; we don't log here because this
  // runs in the render phase and would spam on every re-render.
  const derivedUserEmail = user?.email || null;
  const derivedUserCredits = (() => {
    if (!user) return null;
    // /api/auth/me emits both `credits_balance` (number) and `credits.balance`;
    // the login payload emits `credits` (number). These are the only three
    // shapes the backend actually returns — read them directly, no
    // wrapped-envelope ({ data: { user } }) fallbacks.
    if (typeof user.credits === 'number') {
      return user.credits;
    }
    if (typeof user.credits?.balance === 'number') {
      return user.credits.balance;
    }
    if (typeof user.credits_balance === 'number') {
      return user.credits_balance;
    }
    return null;
  })();


  const contextValue = {
    // Authentication state
    isAuthenticated,
    authToken,
    user,
    userEmail: derivedUserEmail,
    userCredits: derivedUserCredits,



    // Loading states
    isLoading,
    isInitialized,

    // Actions
    handleAuthentication,
    clearAuthenticationState,
    revalidateToken,
    refreshUserData,

    // Impersonation — super_admin can act as any user. The `act` claim
    // in the JWT is the source of truth; `impersonationContext` is null
    // when not impersonating, otherwise carries actor + target metadata.
    // To return to the admin session, the user must logout + re-login;
    // there is intentionally no `endImpersonation` shortcut (it kept a
    // stash of the original token + profile in browser storage which
    // proved both bug-prone and a re-elevation surface).
    impersonate,
    impersonationContext,
    isImpersonating: !!impersonationContext,

    // Utilities
  };

  return (
    <UserContext.Provider value={contextValue}>
      {children}
    </UserContext.Provider>
  );
};

export default UserProvider;
