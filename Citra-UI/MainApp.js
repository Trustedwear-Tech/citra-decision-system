// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useState, useEffect, useRef } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ActionSheetProvider } from '@expo/react-native-action-sheet';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { Platform, View, ActivityIndicator, Text } from 'react-native';
import * as Font from 'expo-font';
import { Ionicons, FontAwesome5 } from '@expo/vector-icons';
import { ModernThemeProvider } from './hooks/useModernTheme';
import ChatScreen from './App';
import IntroScreen from './IntroScreen';
import MobileIntroScreen from './MobileIntroScreen';
import ErrorBoundary from './ErrorBoundary';
import UserProvider, { useUser } from './components/UserProvider';
import { API_CONFIG } from './config/config';
import SignUpScreen from './components/SignUpScreen';
import ResetPasswordScreen from './components/auth/ResetPasswordScreen';
// URL Routing for web (browser back button, deep links)
import { initializeRouting, parseCurrentUrl, onUrlChange, ROUTES } from './utils/urlRouter';

// Routing States
const ROUTE_STATE = {
  INTRO: 'intro',
  LOGIN: 'login',
  CHAT: 'chat',
  LOADING: 'loading',
  VERIFY_EMAIL: 'verify_email',
  RESET_PASSWORD: 'reset_password',
};

// Pending Action Types
const ACTION_TYPE = {
  DIRECT_LOGIN: 'direct_login'
};

const PENDING_SOURCE = {
  USER: 'user',
  SYSTEM: 'system'
};

const AppContent = () => {
  // Core routing state
  const [currentRoute, setCurrentRoute] = useState(ROUTE_STATE.LOADING);
  const [pendingAction, setPendingAction] = useState(null); // { type, data }
  const pendingActionRef = useRef(null);
  const isInitialLoad = useRef(true); // Track if this is the first load
  
  // User context
  const { 
    isAuthenticated, 
    isInitialized,
    isLoading,
    userEmail,
    handleAuthentication,
    revalidateToken
  } = useUser();

  // Keep pending action in sync with ref to avoid stale closures
  useEffect(() => {
    pendingActionRef.current = pendingAction;
  }, [pendingAction]);

  // Initialize routing based on user state
  useEffect(() => {
    if (!isInitialized || isLoading) {
      setCurrentRoute(ROUTE_STATE.LOADING);
      return;
    }

    console.log('🎯 [ROUTING] Initializing route state', {
      isAuthenticated,
      userEmail
    });

    // If there's a USER-initiated pending action, honour it.
    // SYSTEM-initiated gates (from server requiresAction) are cleared so users
    // aren't stuck in a purchase/coupon loop on refresh.
    if (pendingActionRef.current) {
      const action = pendingActionRef.current;
      const isUserInitiated = action.source === PENDING_SOURCE.USER;
      
      // System-initiated pending action — clear it so the user isn't gated
      if (!isUserInitiated) {
        console.log('🧹 [ROUTING] Clearing system-initiated pending action on refresh');
        setPendingAction(null);
      }
    }

    // Check if there's a deep link URL that should bypass intro
    const hasDeepLink = Platform.OS === 'web' && (() => {
      const urlRoute = parseCurrentUrl();
      // If URL is not home (i.e., has a specific route), it's a deep link
      return urlRoute.route !== ROUTES.HOME;
    })();

    // Handle auth deep links (verify-email, reset-password) — these are public routes
    if (Platform.OS === 'web') {
      const urlRoute = parseCurrentUrl();
      if (urlRoute.route === ROUTES.VERIFY_EMAIL) {
        console.log('📧 [ROUTING] Verify-email deep link detected');
        setCurrentRoute(ROUTE_STATE.VERIFY_EMAIL);
        isInitialLoad.current = false;
        return;
      }
      if (urlRoute.route === ROUTES.RESET_PASSWORD) {
        console.log('🔑 [ROUTING] Reset-password deep link detected');
        setCurrentRoute(ROUTE_STATE.RESET_PASSWORD);
        isInitialLoad.current = false;
        return;
      }
    }

    // Decision tree for initial route
    if (!isAuthenticated) {
      // Not authenticated → show intro
      console.log('🔓 [ROUTING] User not authenticated, showing INTRO');
      setCurrentRoute(ROUTE_STATE.INTRO);
      isInitialLoad.current = false; // Mark initial load as complete
    } else {
      // User IS authenticated
      if (isInitialLoad.current) {
        isInitialLoad.current = false; // Mark initial load as complete
        
        // If there's a deep link URL (like /presentation/new), go directly to CHAT
        // so the URL routing can handle showing the correct editor
        if (hasDeepLink) {
          console.log('🔗 [ROUTING] Deep link detected with auth token, routing directly to CHAT');
          setCurrentRoute(ROUTE_STATE.CHAT);
        } else {
          // No deep link → show INTRO (landing page) as default
          console.log('🏠 [ROUTING] Initial page load with auth token, showing INTRO');
          setCurrentRoute(ROUTE_STATE.INTRO);
        }
      } else {
        // Not initial load (user navigated within app) → go to CHAT
        // This handles navigation after login, after purchase, etc.
        console.log('✅ [ROUTING] User authenticated (not initial load), routing to CHAT');
        setCurrentRoute(ROUTE_STATE.CHAT);
      }
    }
  }, [isInitialized, isLoading, isAuthenticated]);

  // Event Handlers - All routing logic centralized here

  // Handle action from IntroScreen (Buy Credits / Free Trial / Login)
  const handleIntroAction = ({ type, data }) => {
    console.log('🎬 [ROUTING] IntroScreen action:', type, data);
    
    if (type === ACTION_TYPE.DIRECT_LOGIN) {
      // User wants to login - check if already authenticated
      if (isAuthenticated) {
        // Already authenticated → validate token before routing
        console.log('🔐 [ROUTING] User appears authenticated, validating token...');
        
        // Validate token asynchronously
        revalidateToken().then(isValid => {
          if (isValid) {
            console.log('✅ [ROUTING] Token validated, routing to CHAT');
            isInitialLoad.current = false;
            setPendingAction(null);
            setCurrentRoute(ROUTE_STATE.CHAT);
          } else {
            // Token is invalid → show login screen
            console.log('❌ [ROUTING] Token invalid, showing LOGIN');
            setPendingAction(null);
            setCurrentRoute(ROUTE_STATE.LOGIN);
          }
        }).catch(error => {
          console.error('❌ [ROUTING] Token validation error, showing LOGIN:', error);
          setPendingAction(null);
          setCurrentRoute(ROUTE_STATE.LOGIN);
        });
      } else {
        // Not authenticated → show login screen
        console.log('🔓 [ROUTING] User not authenticated, showing LOGIN');
        setPendingAction(null);
        setCurrentRoute(ROUTE_STATE.LOGIN);
      }
    }
  };

  // Handle successful authentication from SignUpScreen
  const handleAuthSuccess = async (authData) => {
    console.log('✅ [ROUTING] Authentication successful', {
      authData,
      email: authData?.user?.email,
      credits: authData?.user?.credits,
      pendingAction
    });

    // Sync auth state with UserProvider
    if (authData?.token && authData?.user && typeof handleAuthentication === 'function') {
      await handleAuthentication({ token: authData.token, user: authData.user })
        .catch(error => console.error('❌ [ROUTING] Failed to sync auth state:', error));
    }

    // Dispatch auth success event for IntroScreen to handle
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('authSuccess'));
      console.log('📢 [ROUTING] Dispatched authSuccess event');
    }

    // Unknown pending action - clear and continue
    if (pendingAction) {
      setPendingAction(null);
    }

    // Server hints (requiresAction) are ignored for routing - users are
    // always let into the app.
    // Log the hint for diagnostics but don't gate on it.
    const requiresAction = (authData?.requiresAction || authData?.accessStatus?.requiresAction || '').toLowerCase();
    if (requiresAction) {
      console.log('ℹ️ [ROUTING] Server hint requiresAction:', requiresAction, '(ignored for routing)');
    }

    // Clear any leftover pending action and route to chat
    setPendingAction(null);
    console.log('✅ [ROUTING] Authentication complete, routing to CHAT');
    isInitialLoad.current = false;
    setCurrentRoute(ROUTE_STATE.CHAT);
  };  // Handle successful coupon application  // Handle coupon cancellation  // Handle onboarding completion  // Handle back to intro from chat
  const handleBackToIntro = () => {
    console.log('🔙 [ROUTING] Navigating back to intro');
    setPendingAction(null);
    
    // Mark as initial load again so next navigation will work correctly
    isInitialLoad.current = true;
    
    setCurrentRoute(ROUTE_STATE.INTRO);
  };

  // Render based on current route state
  const renderCurrentRoute = () => {
    // Verbose logging removed to reduce console noise

    switch (currentRoute) {
      case ROUTE_STATE.LOADING:
        return (
          <View style={{ 
            flex: 1, 
            justifyContent: 'center', 
            alignItems: 'center', 
            backgroundColor: '#0a0b14',
            width: '100%',
            height: '100%'
          }}>
            <View style={{
              alignItems: 'center',
              justifyContent: 'center',
              padding: 20
            }}>
              <ActivityIndicator size={Platform.OS === 'web' ? 60 : 'large'} color="#6366f1" />
              <Text style={{ 
                color: '#ffffff', 
                marginTop: 24, 
                fontSize: Platform.OS === 'web' ? 18 : 16,
                fontWeight: '500',
                textAlign: 'center'
              }}>
                Loading Citra AI...
              </Text>
            </View>
          </View>
        );

      case ROUTE_STATE.INTRO:
        return Platform.OS === 'web' ? (
          <IntroScreen 
            onAction={handleIntroAction}
            userEmail={userEmail}
            isAuthenticated={isAuthenticated}
          />
        ) : (
          <MobileIntroScreen 
            onAction={handleIntroAction}
            userEmail={userEmail}
            isAuthenticated={isAuthenticated}
          />
        );

      case ROUTE_STATE.LOGIN:
        return (
          <SignUpScreen 
            onAuthSuccess={handleAuthSuccess}
            onCancel={() => setCurrentRoute(ROUTE_STATE.INTRO)}
            pendingAction={pendingAction}
          />
        );

      case ROUTE_STATE.CHAT:
        // Parse URL to determine initial view/editor to show
        const urlRoute = Platform.OS === 'web' ? parseCurrentUrl() : { route: ROUTES.HOME, id: null };
        return (
          <ChatScreen 
            initialScreen="chat"
            onBackToIntro={handleBackToIntro}
            initialUrlRoute={urlRoute}
          />
        );

      case ROUTE_STATE.VERIFY_EMAIL: {
        // Handle /verify-email?token=... deep link
        const verifyParams = Platform.OS === 'web' ? parseCurrentUrl() : { params: {} };
        const verifyToken = verifyParams.params?.token;
        const authBase = API_CONFIG?.AUTH?.baseUrl || 'http://localhost:7004/api/auth';

        // Auto-verify on mount via a simple component
        return (
          <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#1a1a2e' }}>
            <EmailVerifyHandler token={verifyToken} authBase={authBase} onDone={() => {
              if (Platform.OS === 'web') window.history.replaceState(null, '', '/');
              setCurrentRoute(ROUTE_STATE.INTRO);
            }} />
          </View>
        );
      }

      case ROUTE_STATE.RESET_PASSWORD: {
        const resetParams = Platform.OS === 'web' ? parseCurrentUrl() : { params: {} };
        const resetToken = resetParams.params?.token;
        return (
          <ResetPasswordScreen
            token={resetToken}
            onComplete={() => {
              if (Platform.OS === 'web') window.history.replaceState(null, '', '/');
              setCurrentRoute(ROUTE_STATE.INTRO);
            }}
          />
        );
      }

      default:
        console.error('❌ [ROUTING] Unknown route state:', currentRoute);
        return (
          <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#0a0b14' }}>
            <Text style={{ color: '#ff0000', fontSize: 18 }}>Error: Unknown route state</Text>
          </View>
        );
    }
  };

  return renderCurrentRoute();
};

// Simple inline component for email verification deep link
const EmailVerifyHandler = ({ token, authBase, onDone }) => {
  const [status, setStatus] = React.useState('loading');
  const [message, setMessage] = React.useState('Verifying your email...');

  React.useEffect(() => {
    if (!token) {
      setStatus('error');
      setMessage('Invalid verification link. No token provided.');
      return;
    }
    fetch(`${authBase}/local/verify-email?token=${token}`)
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          setStatus('success');
          setMessage('Email verified successfully! You can now sign in.');
        } else {
          setStatus('error');
          setMessage(data.error || 'Verification failed.');
        }
      })
      .catch(() => {
        setStatus('error');
        setMessage('Verification failed. Please try again.');
      });
  }, [token]);

  return (
    <View style={{ alignItems: 'center', padding: 32 }}>
      {status === 'loading' && <ActivityIndicator size="large" color="#2563eb" />}
      <Text style={{ color: status === 'error' ? '#ef4444' : '#22c55e', fontSize: 18, marginTop: 16, textAlign: 'center' }}>
        {message}
      </Text>
      {status !== 'loading' && (
        <Text
          onPress={onDone}
          style={{ color: '#2563eb', fontSize: 16, marginTop: 20, textDecorationLine: 'underline' }}
        >
          Go to Sign In
        </Text>
      )}
    </View>
  );
};

// Font Loading Component - handles slow network timeouts gracefully
const FontLoadingWrapper = ({ children }) => {
  const [fontsLoaded, setFontsLoaded] = useState(false);
  const [fontError, setFontError] = useState(null);

  useEffect(() => {
    let isMounted = true;
    
    const loadFonts = async () => {
      try {
        // Load icon fonts with extended timeout for slow networks
        // Default Expo timeout is 6000ms which can fail on slow connections
        await Promise.race([
          Font.loadAsync({
            ...Ionicons.font,
            ...FontAwesome5.font,
          }),
          new Promise((_, reject) => 
            setTimeout(() => reject(new Error('Font loading timeout')), 5000) // 5s timeout - proceed with fallback fonts
          )
        ]);
        
        if (isMounted) {
          setFontsLoaded(true);
        }
      } catch (error) {
        console.warn('⚠️ Font loading error (continuing with fallback):', error.message);
        // Don't block app - continue even if fonts fail
        // Icons may show as boxes but app will be functional
        if (isMounted) {
          setFontError(error);
          setFontsLoaded(true); // Allow app to proceed despite font failure
        }
      }
    };

    loadFonts();
    
    return () => {
      isMounted = false;
    };
  }, []);

  if (!fontsLoaded) {
    return (
      <View style={{ 
        flex: 1, 
        justifyContent: 'center', 
        alignItems: 'center', 
        backgroundColor: '#0a0b14',
        width: '100%',
        height: '100%'
      }}>
        <View style={{
          alignItems: 'center',
          justifyContent: 'center',
          padding: 20
        }}>
          <ActivityIndicator size={Platform.OS === 'web' ? 60 : 'large'} color="#6366f1" />
          <Text style={{ 
            color: '#888', 
            marginTop: 24, 
            fontSize: Platform.OS === 'web' ? 16 : 14,
            fontWeight: '400',
            textAlign: 'center'
          }}>
            Loading...
          </Text>
        </View>
      </View>
    );
  }

  return children;
};

export default function App() {
  return (
    <ErrorBoundary>
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <FontLoadingWrapper>
            <ModernThemeProvider>
              <ActionSheetProvider>
                <UserProvider>
                  <AppContent />
                </UserProvider>
              </ActionSheetProvider>
            </ModernThemeProvider>
          </FontLoadingWrapper>
        </SafeAreaProvider>
      </GestureHandlerRootView>
    </ErrorBoundary>
  );
}
