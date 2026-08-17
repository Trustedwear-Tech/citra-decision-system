// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Authentication Debug Helper for Citra AI UI
 * Use this to check and fix authentication issues
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { authService } from '../services/authService';

export class AuthDebugHelper {
  
  /**
   * Check current authentication status
   */
  static async checkAuthStatus() {
    console.log('🔍 [AUTH_DEBUG] Checking authentication status...');
    
    try {
      // Check if user data exists
      const userData = await AsyncStorage.getItem('@user');
      const directToken = await AsyncStorage.getItem('@auth_token');
      
      console.log('📱 [AUTH_DEBUG] Storage check:');
      console.log('   - User data exists:', !!userData);
      console.log('   - Direct token exists:', !!directToken);
      
      if (userData) {
        const parsed = JSON.parse(userData);
        console.log('   - User data has token:', !!parsed?.data?.token);
        console.log('   - User email:', parsed?.data?.user?.email);
      }
      
      // Check authService token
      const serviceToken = await authService.getToken();
      console.log('   - AuthService token exists:', !!serviceToken);
      
      // Check if user is authenticated according to authService
      const isAuth = await authService.isAuthenticated();
      console.log('   - AuthService isAuthenticated:', isAuth);
      
      return {
        hasUserData: !!userData,
        hasDirectToken: !!directToken,
        hasServiceToken: !!serviceToken,
        isAuthenticated: isAuth,
        userEmail: userData ? JSON.parse(userData)?.data?.user?.email : null
      };
      
    } catch (error) {
      console.error('❌ [AUTH_DEBUG] Error checking auth status:', error);
      return null;
    }
  }
  
  /**
   * Set debug authentication with valid token
   */
  static async setDebugAuth() {
    console.log('🔧 [AUTH_DEBUG] Setting debug authentication...');
    
    // NEVER hard-code a token here. This is a debug helper that ships in a
    // PUBLIC repo: a literal JWT is a credential in source control, and it
    // teaches the pattern besides. Supply one at run time instead.
    const validToken = process.env.EXPO_PUBLIC_DEBUG_JWT || '';
    if (!validToken) {
      console.warn('[AUTH_DEBUG] set EXPO_PUBLIC_DEBUG_JWT to use setDebugAuth()');
      return false;
    }
    
    // User data structure expected by your app
    const userData = {
      data: {
        token: validToken,
        user: {
          email: process.env.EXPO_PUBLIC_DEBUG_EMAIL || 'debug@example.com',
          googleId: 'test-google-id'
        }
      }
    };
    
    try {
      // Store user data
      await AsyncStorage.setItem('@user', JSON.stringify(userData));
      
      // Store token in authService
      await authService.setToken(validToken);
      
      console.log('✅ [AUTH_DEBUG] Debug authentication set successfully!');
      console.log('✅ [AUTH_DEBUG] User email:', userData.data.user.email);
      console.log('🔐 [AUTH_DEBUG] Token expires in 24 hours');
      
      // Verify storage
      const status = await this.checkAuthStatus();
      console.log('🔍 [AUTH_DEBUG] Verification result:', status);
      
      return true;
      
    } catch (error) {
      console.error('❌ [AUTH_DEBUG] Error setting debug auth:', error);
      return false;
    }
  }
  
  /**
   * Clear all authentication data
   */
  static async clearAuth() {
    console.log('🗑️ [AUTH_DEBUG] Clearing authentication data...');
    
    try {
      await AsyncStorage.removeItem('@user');
      await AsyncStorage.removeItem('@auth_token');
      await authService.clearToken();
      
      console.log('✅ [AUTH_DEBUG] Authentication data cleared');
      return true;
      
    } catch (error) {
      console.error('❌ [AUTH_DEBUG] Error clearing auth:', error);
      return false;
    }
  }
  
  /**
   * Test authenticated API call
   */
  static async testApiCall() {
    console.log('🧪 [AUTH_DEBUG] Testing authenticated API call...');
    
    try {
      const response = await authService.authenticatedFetch('http://localhost:8085/citra-ai/v2/documents?limit=1', {
        method: 'GET'
      });
      
      console.log('📡 [AUTH_DEBUG] API test response status:', response.status);
      
      if (response.ok) {
        console.log('✅ [AUTH_DEBUG] API call successful - authentication working!');
        return true;
      } else {
        console.log('❌ [AUTH_DEBUG] API call failed - authentication not working');
        return false;
      }
      
    } catch (error) {
      console.error('❌ [AUTH_DEBUG] API test error:', error);
      return false;
    }
  }
}

// Export for global access
export default AuthDebugHelper;
