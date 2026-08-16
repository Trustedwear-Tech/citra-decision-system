// SignUpScreen.js
import React, { useState, useEffect, useRef } from 'react';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import {
  View,
  Text,
  TouchableOpacity,
  Image,
  StyleSheet,
  Animated,
  Dimensions,
  StatusBar,
  KeyboardAvoidingView,
  Platform,
  Alert,
  TextInput,
  ActivityIndicator,
  Modal,
  SafeAreaView,
  Linking,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import authService from '../services/authService';  // Default export
import { API_CONFIG } from '../config/config';
import lightLogo from '../assets/citra-logo.png';
import darkLogo from '../assets/citra-logo.png';
import ContactScreen from './ContactScreen';
import PrivacyPolicyScreen from './PrivacyPolicyScreen';
import TermsOfServiceScreen from './TermsOfServiceScreen';
import EmailAuthScreen from './auth/EmailAuthScreen';

// Conditional imports for expo modules - only for web platform
let WebBrowser = null;
let AuthSession = null;
let Google = null;

// Only load Google auth modules for web platform
if (Platform.OS === 'web') {
  try {
    WebBrowser = require('expo-web-browser');
    AuthSession = require('expo-auth-session');
    Google = require('expo-auth-session/providers/google');

    // Ensure any in-flight AuthSession is completed only if WebBrowser is available
    if (WebBrowser && WebBrowser.maybeCompleteAuthSession) {
      WebBrowser.maybeCompleteAuthSession();
    }
  } catch (error) {
    console.warn('Expo modules not available for web:', error.message);
    // Fallback: These will remain null and we'll handle it in the component
  }
} else {
  console.log('Google auth disabled for mobile platform - will use native implementation later');
}

const { width, height } = Dimensions.get('window');

const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;
const isDevClient = Constants.executionEnvironment === ExecutionEnvironment.DevelopmentClient;

export default function SignUpScreen({ theme, pendingAction, onAuthSuccess, onCancel }) {
  // Persona management hook - will be null until email device ID is available

  // --- State & refs ---
  const [step, setStep] = useState(0); // 0=welcome, 1=googleAuth
  const [error, setError] = useState('');
  const [mode, setMode] = useState('signin'); // Default to signin since we only have one option
  const [isLoading, setIsLoading] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);

  // Authentication error states
  const [authError, setAuthError] = useState(null);
  const [authErrorData, setAuthErrorData] = useState(null);
  const [showAccessDeniedModal, setShowAccessDeniedModal] = useState(false);
  const [showContact, setShowContact] = useState(false);
  const [privacyVisible, setPrivacyVisible] = useState(false);
  const [termsVisible, setTermsVisible] = useState(false);
  const lastProcessedIdTokenRef = useRef(null); // Prevents double-processing the same OAuth response

  // Auth provider discovery
  const [availableProviders, setAvailableProviders] = useState(null); // null = loading, [] = error fallback
  const [authView, setAuthView] = useState('google'); // 'google' | 'email'

  // Animations
  const logoScale = useRef(new Animated.Value(0)).current;
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const textOpacity = useRef(new Animated.Value(0)).current;
  const slideUp = useRef(new Animated.Value(50)).current;
  const fadeIn = useRef(new Animated.Value(0)).current;

  // Modern IntroScreen theme colors - Light theme
  const isDark = false; // Light theme matching IntroScreen
  const logo = lightLogo;
  const colors = {
    // Light theme matching IntroScreen
    background: '#FFFFFF',
    cardBackground: '#FFFFFF',
    text: '#0F172A',
    secondaryText: '#475569',
    accent: '#7C3AED',
    primary: '#7C3AED',
    border: '#E2E8F0',
    error: '#FF453A',
    success: '#10b981',
    gradient: ['#FFFFFF', '#F5F3FF'],
    overlay: 'rgba(0, 0, 0, 0.5)', // For popup overlay
    glowPrimary: 'rgba(124, 58, 237, 0.3)', // Purple glow effect
    cardGlow: 'rgba(124, 58, 237, 0.1)', // Subtle card glow
  };

  const useProxy = isExpoGo || Platform.OS === 'android';

  // Safe redirect URI setup - WEB ONLY
  let redirectUri = '';
  if (Platform.OS === 'web') {
    try {
      redirectUri = useProxy
        ? ''
        : AuthSession && AuthSession.makeRedirectUri ? AuthSession.makeRedirectUri({
          scheme: API_CONFIG.OAUTH.redirectScheme,
          path: API_CONFIG.OAUTH.redirectPath
        }) : `${API_CONFIG.OAUTH.redirectScheme}://${API_CONFIG.OAUTH.redirectPath}`;
    } catch (error) {
      console.warn('Failed to create redirect URI:', error);
      redirectUri = `${API_CONFIG.OAUTH.redirectScheme}://${API_CONFIG.OAUTH.redirectPath}`;
    }
  }

  // Configure the Google auth request - WEB ONLY
  // Mobile will use @react-native-google-signin/google-signin later
  let request = null;
  let response = null;
  let promptAsync = null;

  if (Platform.OS === 'web') {
    try {
      if (Google && Google.useAuthRequest) {
        const [req, resp, prompt] = Google.useAuthRequest(
          {
            // Web: Use ID token flow  
            expoClientId: API_CONFIG.OAUTH.googleExpoClientId,
            webClientId: API_CONFIG.OAUTH.googleWebClientId,
            redirectUri,
            scopes: ['openid', 'profile', 'email'],
            responseType: AuthSession?.ResponseType?.IdToken || 'id_token',
            additionalParameters: {},
            selectAccount: true, // Force account selection
          },
          {
            useProxy
          }
        );
        request = req;
        response = resp;
        promptAsync = prompt;
      }
    } catch (error) {
      console.warn('Google auth setup failed for web:', error);
      // Google auth will be disabled, but app will still work
    }
  } else {
    // Skip Google auth setup for mobile platforms
  }

  // Handle the OAuth response - WEB ONLY
  useEffect(() => {
    console.log('🔍 [AUTH_DEBUG] useEffect triggered, response:', { type: response?.type, hasIdToken: !!(response?.params?.id_token || response?.authentication?.idToken) });

    if (Platform.OS === 'web' && response?.type === 'success') {
      setIsLoading(true);
      console.log('✅ [AUTH_DEBUG] OAuth success detected, processing...');

      // Web: Handle ID token flow
      const idToken = response?.params?.id_token ||
        response?.authentication?.idToken;

      if (idToken) {
        if (lastProcessedIdTokenRef.current === idToken) {
          console.log('⏭️ [AUTH_DEBUG] ID token already processed, skipping duplicate handling');
          setIsLoading(false);
          return;
        }
        lastProcessedIdTokenRef.current = idToken;
        console.log('✅ [AUTH_DEBUG] ID token found, calling handleInitialAuth');
        // For web, immediately send to backend to check if user is new
        handleInitialAuth({
          idToken: idToken,
          platform: 'web'
        }).catch(err => {
          console.error('❌ [AUTH_DEBUG] handleInitialAuth failed:', err);
          setError(err.message || 'Authentication failed');
          setIsLoading(false);
          lastProcessedIdTokenRef.current = null; // Allow retry if the same token is reissued after an error
        });
      } else {
        console.error('❌ No ID token received from Google');
        setError('No ID token received from Google');
        setIsLoading(false);
      }
    } else if (Platform.OS === 'web' && response?.type === 'error') {
      console.error('❌ OAuth Error:', response.error);
      setError(response.error?.message || 'Authentication failed');
      setIsLoading(false);
    }
  }, [response]);

  // Welcome animation & step logic
  // Skip step 0 (welcome animation) and go directly to Google auth (step 1)
  useEffect(() => {
    if (step === 0) {
      // Skip the 3-second welcome animation - go directly to Google auth
      console.log('🚀 [AUTH] Skipping welcome animation, going directly to Google auth');
      setStep(1);
    } else {
      Animated.parallel([
        Animated.timing(slideUp, { toValue: 0, duration: 600, useNativeDriver: Platform.OS !== 'web' }),
        Animated.timing(fadeIn, { toValue: 1, duration: 600, useNativeDriver: Platform.OS !== 'web' }),
      ]).start();
    }
  }, [step]);

  // Fetch available auth providers from backend
  useEffect(() => {
    const authBase = API_CONFIG?.AUTH?.baseUrl || 'http://localhost:7004/api/auth';
    fetch(`${authBase}/providers`)
      .then(r => r.json())
      .then(data => {
        const providers = data?.providers || ['google']; // default fallback
        setAvailableProviders(providers);
        // If only local is available (no google), default to email view
        if (providers.includes('local') && !providers.includes('google')) {
          setAuthView('email');
        }
      })
      .catch(() => {
        // Fallback: assume Google only (backward compatible)
        setAvailableProviders(['google']);
      });
  }, []);

  // Handle native Google Auth callback
  useEffect(() => {
    // Define the global callback for native bridge
    window.handleNativeGoogleAuth = (idToken) => {
      console.log('📱 [NATIVE_AUTH] Received token from native layer');
      if (idToken) {
        setIsLoading(true);
        handleInitialAuth({
          idToken: idToken,
          platform: 'android'
        });
      } else {
        console.error('❌ [NATIVE_AUTH] Native Sign-In failed or cancelled');
        setError('Sign-In cancelled or failed');
        setIsLoading(false);
      }
    };

    return () => {
      // Cleanup
      delete window.handleNativeGoogleAuth;
    };
  }, []);

  // Auto-trigger Google auth when step changes to 1
  useEffect(() => {
    // Detect native environment
    const userAgent = (typeof window !== 'undefined' && window.navigator) ? window.navigator.userAgent : '';
    const isNativeAndroid = userAgent.includes('CitraAndroid') && window.Android;
    const isNativeIOS = userAgent.includes('CitraIOS'); // Placeholder for iOS

    if (step === 1 && !isLoading) {
      if (isNativeAndroid) {
        console.log('📱 [AUTH] Triggering Native Android Auth');
        // Small delay to ensure UI is ready
        setTimeout(() => {
          try {
            window.Android.triggerGoogleSignin();
          } catch (e) {
            console.error('❌ [AUTH] Failed to trigger native auth:', e);
            setError('Native authentication failed');
          }
        }, 100);
      } else if (isNativeIOS && window.webkit?.messageHandlers?.IOSNative) {
        console.log('📱 [AUTH] Triggering Native iOS Auth');
        setTimeout(() => {
          try {
            window.webkit.messageHandlers.IOSNative.postMessage("triggerGoogleSignin");
          } catch (e) {
            console.error('❌ [AUTH] Failed to trigger iOS native auth:', e);
            setError('Native authentication failed');
          }
        }, 100);
      } else if (Platform.OS === 'web' && promptAsync && request) {
        // Don't auto-trigger promptAsync() here — calling window.open()
        // outside a direct user-gesture (click) causes browsers to block
        // the popup (ERR_WEB_BROWSER_BLOCKED).  The "Continue with Google"
        // button already calls promptAsync() from its onPress handler,
        // which IS a valid user gesture and won't be blocked.
        console.log('🔐 [AUTH] Google auth ready — waiting for user to click the sign-in button');
      }
    }
  }, [step, promptAsync, request, isLoading]);

  // Handle initial authentication to check if user is new - WEB ONLY
  const handleInitialAuth = async (authData) => {
    try {
      console.log('🚀 [AUTH_DEBUG] Sending initial auth to backend');
      console.log('🚀 [AUTH_DEBUG] Auth endpoint:', `${API_CONFIG.AUTH.baseUrl}${API_CONFIG.AUTH.googleEndpoint}`);

      // Generate device fingerprint for abuse prevention
      let deviceFingerprint = null;
      try {
        const { getDeviceFingerprint } = require('../services/DeviceFingerprintService');
        deviceFingerprint = await getDeviceFingerprint();
        console.log('🔑 [AUTH_DEBUG] Device fingerprint generated');
      } catch (fpErr) {
        console.warn('⚠️ [AUTH_DEBUG] Failed to generate device fingerprint:', fpErr.message);
      }

      // Create payload for initial authentication - only ID token for web
      const payload = {
        authMode: mode,
        platform: authData.platform || 'web',
        idToken: authData.idToken,
        deviceFingerprint,
        termsAcceptedAt: new Date().toISOString(),
      };

      console.log('🚀 [AUTH_DEBUG] Payload prepared:', { authMode: mode, platform: authData.platform || 'web', hasIdToken: !!authData.idToken });

      const headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      };

      // Add origin header for web platforms to help with CORS
      if (Platform.OS === 'web' && typeof window !== 'undefined') {
        headers['Origin'] = window.location.origin;
      }

      console.log('🚀 [AUTH_DEBUG] Sending fetch request...');
      const response = await fetch(`${API_CONFIG.AUTH.baseUrl}${API_CONFIG.AUTH.googleEndpoint}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });

      console.log('📥 [AUTH_DEBUG] Response received, status:', response.status);
      const result = await response.json();
      console.log('📥 [AUTH_DEBUG] Response parsed:', result);

      console.log('📥 Initial auth response:', {
        status: response.status,
        ok: response.ok,
        success: result.success,
        isNewUser: result.data?.isNewUser || result.isNewUser,
        hasUserData: !!result.data || !!result.user
      });

      // Handle 403 - User authenticated but has no access
      if (response.status === 403 && !result.success) {
        console.log('⚠️ [AUTH] User has no access (403 from backend)');

        // IMPORTANT: Store the auth token AND user data even though access is denied
        // This allows user to make authenticated calls to purchase subscription/apply coupon
        if (result.token) {
          console.log('💾 [AUTH] Storing auth token for subscription/coupon flow');
          await authService.setToken(result.token);
        }

        // Store user email in AsyncStorage so authenticatedFetch can get it
        if (result.user && result.user.email) {
          console.log('💾 [AUTH] Storing user email for authenticated API calls');
          await AsyncStorage.setItem('@user_email', result.user.email);

          // Store user data in PROPER nested format to match expected structure
          // CRITICAL: getUserEmail() expects data.user.email, not flat email
          const properUserData = {
            data: {
              user: {
                email: result.user.email,
                name: result.user.name,
                googleId: result.user.googleId,
                profilePicture: result.user.profilePicture,
                _id: result.user._id,
                credits: result.user.credits,
                hasAccess: false // 403 means no access
              },
              token: result.token
            }
          };
          await AsyncStorage.setItem('@user', JSON.stringify(properUserData));
          console.log('✅ [AUTH] User data stored in proper nested format for 403 flow');
        }

        // 🔧 CRITICAL FIX: Check for pending coupon code BEFORE showing Access Denied Modal
        // If user entered a coupon in IntroScreen, apply it automatically here
        const pendingCouponCode = await AsyncStorage.getItem('pendingCouponCode');
        if (pendingCouponCode && result.user?.email && result.token) {
          console.log('🎫 [AUTH] Found pending coupon code, applying automatically:', pendingCouponCode);

          try {
            // Apply the pending coupon
            const couponResponse = await fetch(`${API_CONFIG.USER_SERVICE_URL}/api/coupons/apply`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({
                coupon_code: pendingCouponCode,
                email: result.user.email
              })
            });

            console.log('🔍 [AUTH] Coupon application response status:', couponResponse.status);

            if (couponResponse.ok) {
              const couponResult = await couponResponse.json();
              console.log('🔍 [AUTH] Coupon application result:', couponResult);

              if (couponResult.success) {
                console.log('✅ [AUTH] Pending coupon applied successfully, bypassing Access Denied Modal');

                // Store trial info
                await AsyncStorage.setItem('trialInfo', JSON.stringify(couponResult.trial));
                await AsyncStorage.setItem('userType', 'free_trial');

                // Clear the pending coupon
                await AsyncStorage.removeItem('pendingCouponCode');

                // Re-verify access with the backend
                console.log('🔄 [AUTH] Re-verifying access after auto-applied coupon...');

                const verifyResponse = await fetch(`${API_CONFIG.AUTH.baseUrl}${API_CONFIG.AUTH.meEndpoint}`, {
                  method: 'GET',
                  headers: {
                    'Authorization': `Bearer ${result.token}`,
                    'Content-Type': 'application/json',
                  },
                });

                if (verifyResponse.ok) {
                  const verifyResult = await verifyResponse.json();
                  const updatedUser = verifyResult.data.user;

                  // Check if user now has access
                  const hasAccess = (
                    (updatedUser.user_type === 'paid' && updatedUser.has_active_subscription === true) ||
                    (updatedUser.user_type === 'free_trial' && updatedUser.trial_expiry_date && new Date(updatedUser.trial_expiry_date) > new Date())
                  );

                  if (hasAccess) {
                    console.log('🎉 [AUTH] User now has access after auto-applied coupon! Proceeding to chat...');

                    // Complete authentication with updated user data
                    await completeAuthentication({
                      success: true,
                      data: {
                        token: result.token,
                        user: updatedUser,
                        isNewUser: result.isNewUser
                      }
                    });

                    // Exit early - no need to show Access Denied Modal
                    return;
                  } else {
                    console.log('⚠️ [AUTH] User does not have access after coupon application. Updated user:', updatedUser);
                  }
                } else {
                  console.log('⚠️ [AUTH] Re-verification failed with status:', verifyResponse.status);
                }
              } else {
                console.log('⚠️ [AUTH] Coupon result success is false:', couponResult);
              }
            } else {
              const errorText = await couponResponse.text();
              console.log('⚠️ [AUTH] Coupon application HTTP error:', couponResponse.status, errorText);
            }

            // If coupon application failed, continue to show Access Denied Modal
            console.log('⚠️ [AUTH] Pending coupon application failed, showing Access Denied Modal');

          } catch (couponError) {
            console.error('❌ [AUTH] Error auto-applying pending coupon:', couponError);
            // Continue to show Access Denied Modal on error
          }
        }

        // 🔧 CRITICAL FIX: Check for pending actions (like credit purchase)
        // If user has a pending action, call onAuthSuccess to let MainApp handle routing
        if (pendingAction) {
          console.log('🎯 [AUTH] Pending action detected after 403, calling onAuthSuccess:', pendingAction);

          setIsLoading(false);

          // Call onAuthSuccess with the auth result and let MainApp handle routing
          if (onAuthSuccess) {
            onAuthSuccess({
              user: result.user,
              token: result.token,
              isNewUser: result.isNewUser,
              accessStatus: result.accessStatus,
              requiresAction: result.requiresAction
            });
          }

          return;
        }

        // 🚦 NEW: Even without a pending action, push routing responsibility up so the app can
        // automatically navigate to the correct flow (credits, coupon, etc.) after login.
        const derivedRequiresAction =
          result.requiresAction ||
          result.accessStatus?.requiresAction ||
          'purchase_credits';

        const fallbackCredits =
          typeof result.accessStatus?.credits === 'number'
            ? result.accessStatus.credits
            : result.user?.credits ?? 0;

        setIsLoading(false);

        if (onAuthSuccess) {
          console.log('🎯 [AUTH] No pending action, delegating requiresAction to MainApp:', derivedRequiresAction);

          onAuthSuccess({
            user: {
              ...(result.user || {}),
              credits: fallbackCredits,
              hasAccess: false
            },
            token: result.token,
            isNewUser: result.isNewUser,
            accessStatus: result.accessStatus,
            requiresAction: derivedRequiresAction
          });

          return;
        }

        // Fallback: keep legacy access denied modal if we don't have a routing callback
        // Set auth error data for display
        setAuthErrorData({
          message: result.error || 'Access denied',
          accessStatus: result.accessStatus,
          requiresAction: result.requiresAction,
          user: result.user,
          token: result.token,
          isNewUser: result.isNewUser
        });

        // Set error message to display in UI
        setError(result.error || 'You need an active subscription or trial to use Citra AI.');

        // Show custom access denied modal
        console.log('👤 [AUTH] No pending action, showing access denied modal');
        setShowAccessDeniedModal(true);

        return;
      }

      if (response.ok && result.success) {
        if (result.data.isNewUser) {
          // New user - complete authentication directly (phone number disabled)
          console.log('✅ New user detected, completing authentication directly (no phone required)');
          await completeAuthentication(result);
        } else {
          // Existing user - complete authentication and go to chat
          console.log('✅ Existing user detected, completing authentication');
          await completeAuthentication(result);
        }
      } else {
        throw new Error(result.error || result.message || 'Initial authentication failed');
      }
    } catch (err) {
      console.error('❌ [AUTH_DEBUG] Initial authentication failed:', err);
      console.error('❌ [AUTH_DEBUG] Error name:', err.name);
      console.error('❌ [AUTH_DEBUG] Error message:', err.message);
      console.error('❌ [AUTH_DEBUG] Error stack:', err.stack);

      if (err.name === 'TypeError' && err.message.includes('NetworkError')) {
        setError('Network error: Unable to connect to authentication server. Please check your internet connection.');
      } else if (err.message.includes('CORS')) {
        setError('Configuration error: Please contact support to resolve authentication issues.');
      } else {
        setError(`Authentication failed: ${err.message}`);
      }
    } finally {
      console.log('🏁 [AUTH_DEBUG] handleInitialAuth finally block, setting isLoading to false');
      setIsLoading(false);
    }
  };

  // Handler for opening contact screen
  const handleRequestContact = () => {
    console.log('📞 [SIGNUP] handleRequestContact called');
    setShowContact(true);
  };

  // Complete authentication for existing users or after phone input
  const completeAuthentication = async (authResult) => {
    try {
      console.log('✅ Authentication successful, storing user data...');

      // Store the complete user data
      await AsyncStorage.setItem('@user', JSON.stringify(authResult));
      console.log('✅ User data stored successfully');

      // Also store the token separately for authService
      if (authResult?.data?.token) {
        await authService.setToken(authResult.data.token);
        console.log('✅ Token stored in authService');
      }

      // Verify the stored user data immediately after storing
      try {
        console.log('🔍 [AUTH] Verifying newly stored user data...');

        if (!authResult?.data?.token) {
          const missingTokenError = new Error('missing-token');
          missingTokenError.code = 'auth-verification';
          throw missingTokenError;
        }

        const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
        const timeoutId = controller ? setTimeout(() => controller.abort(), 10000) : null;

        let verificationResponse;
        try {
          verificationResponse = await fetch(`${API_CONFIG.AUTH.baseUrl}${API_CONFIG.AUTH.meEndpoint}`, {
            method: 'GET',
            headers: {
              'Authorization': `Bearer ${authResult.data.token}`,
              'Content-Type': 'application/json',
            },
            ...(controller ? { signal: controller.signal } : {})
          });
        } finally {
          if (timeoutId) {
            clearTimeout(timeoutId);
          }
        }

        if (verificationResponse.status === 401 || verificationResponse.status === 403) {
          const authError = new Error('auth-invalid');
          authError.code = 'auth-verification';
          authError.status = verificationResponse.status;
          throw authError;
        }

        if (!verificationResponse.ok) {
          const transientError = new Error(`verification-${verificationResponse.status}`);
          transientError.code = 'transient-verification';
          transientError.status = verificationResponse.status;
          throw transientError;
        }

        const apiUser = await verificationResponse.json();
        const savedUser = authResult.data.user;
        const apiUserData = apiUser?.data?.user || apiUser?.user;

        if (!apiUserData) {
          const payloadError = new Error('Verification response missing user payload');
          payloadError.code = 'auth-verification';
          throw payloadError;
        }

        const savedEmail = typeof savedUser?.email === 'string' ? savedUser.email.toLowerCase() : null;
        const apiEmail = typeof apiUserData?.email === 'string' ? apiUserData.email.toLowerCase() : null;
        const emailMatches = savedEmail && apiEmail && savedEmail === apiEmail;
        const googleMatches = savedUser?.googleId && apiUserData?.googleId && savedUser.googleId === apiUserData.googleId;
        const isDataValid = Boolean(emailMatches || googleMatches);

        if (!isDataValid) {
          const mismatchError = new Error('User data mismatch detected');
          mismatchError.code = 'auth-verification';
          throw mismatchError;
        }

        console.log('✅ [AUTH] User data verification passed after authentication');

        // Merge latest backend fields (credits, access flags, etc.) into authResult
        authResult = {
          ...authResult,
          data: {
            ...authResult.data,
            user: {
              ...authResult.data.user,
              ...apiUserData
            }
          }
        };
        await AsyncStorage.setItem('@user', JSON.stringify(authResult));
        console.log('💾 [AUTH] Merged /me payload into auth result:', {
          credits: authResult.data.user?.credits,
          credits_balance: authResult.data.user?.credits_balance,
          hasAccess: authResult.data.user?.hasAccess
        });
      } catch (verificationError) {
        const isAuthVerificationFailure = verificationError?.code === 'auth-verification';
        const isAbortError = verificationError?.name === 'AbortError';
        const isNetworkError = verificationError?.name === 'TypeError' || verificationError?.message?.includes('Failed to fetch');

        if (isAuthVerificationFailure) {
          console.error('❌ [AUTH] User data verification failed after authentication (fatal):', verificationError.message);
          await AsyncStorage.removeItem('@user');
          await authService.clearToken();
          throw new Error('Authentication verification failed. Please try signing in again.');
        }

        console.warn('⚠️ [AUTH] Skipping user verification due to transient error:', {
          message: verificationError.message,
          status: verificationError?.status,
          isAbortError,
          isNetworkError
        });
      }

      // Check for pending coupon code and apply it
      try {
        const pendingCouponCode = await AsyncStorage.getItem('pendingCouponCode');
        if (pendingCouponCode && authResult?.data?.user?.email) {
          console.log('🎫 Found pending coupon code, applying:', pendingCouponCode);

          const couponResponse = await fetch(`${API_CONFIG.USER_SERVICE_URL}/api/coupons/apply`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              coupon_code: pendingCouponCode,
              email: authResult.data.user.email
            })
          });

          if (couponResponse.ok) {
            const couponResult = await couponResponse.json();
            if (couponResult.success) {
              console.log('✅ Pending coupon applied successfully:', couponResult);
              // Store trial info
              await AsyncStorage.setItem('trialInfo', JSON.stringify(couponResult.trial));
              await AsyncStorage.setItem('userType', 'free_trial');
              // Remove pending coupon
              await AsyncStorage.removeItem('pendingCouponCode');

              // CRITICAL FIX: Re-authenticate user to get updated access status from backend
              console.log('🔄 [AUTH] Re-authenticating user to get updated access status after coupon application');

              const token = authResult?.data?.token;
              if (!token) {
                console.error('❌ [AUTH] No token available for re-authentication');
                throw new Error('Authentication token missing');
              }

              // Call /me endpoint to get updated user data with new access status
              const meResponse = await fetch(`${API_CONFIG.AUTH.baseUrl}${API_CONFIG.AUTH.meEndpoint}`, {
                method: 'GET',
                headers: {
                  'Authorization': `Bearer ${token}`,
                  'Content-Type': 'application/json',
                },
              });

              if (meResponse.ok) {
                const updatedUserData = await meResponse.json();
                console.log('✅ [AUTH] Got updated user data after coupon:', {
                  hasAccess: updatedUserData.data?.user?.hasAccess,
                  userType: updatedUserData.data?.user?.userType
                });

                // Update authResult with new user data
                authResult = {
                  ...authResult,
                  data: {
                    ...authResult.data,
                    user: {
                      ...authResult.data.user,
                      ...updatedUserData.data.user
                    }
                  }
                };

                // Re-store the updated user data
                await AsyncStorage.setItem('@user', JSON.stringify(authResult));
                console.log('✅ [AUTH] Updated user data stored successfully');
              } else {
                console.warn('⚠️ [AUTH] Failed to re-authenticate after coupon, continuing with existing data');
              }
            }
          } else {
            console.warn('⚠️ Failed to apply pending coupon:', await couponResponse.text());
          }
        }
      } catch (couponError) {
        console.error('❌ Error applying pending coupon:', couponError);
        // Don't fail authentication if coupon application fails
      }

      // Access check is now done on backend - if we reach here, user has access
      console.log('✅ [AUTH] User authenticated successfully with access');

      // Dispatch authSuccess event for IntroScreen to handle pending purchases
      if (Platform.OS === 'web' && typeof window !== 'undefined') {
        console.log('🎉 [AUTH] Dispatching authSuccess event');
        const event = new Event('authSuccess');
        window.dispatchEvent(event);
      }

      // Call onAuthSuccess callback with structured user data
      if (onAuthSuccess && typeof onAuthSuccess === 'function') {
        console.log('✅ [SIGNUP] Calling onAuthSuccess callback...');
        console.log('📦 [SIGNUP] authResult.data.user:', JSON.stringify(authResult.data.user, null, 2));
        try {
          onAuthSuccess({
            user: authResult.data.user,
            token: authResult.data.token,
            email: authResult.data.user.email,
            credits: authResult.data.user.credits?.balance || 0,
            hasAccess: authResult.data.user.hasAccess,
            isNewUser: authResult.data.isNewUser || authResult.data.user.isNewUser,
            isFirstTimeUser: authResult.data.isNewUser || authResult.data.user.isNewUser,
            welcomeBonusGranted: authResult.data.welcomeBonusGranted !== false
          });
          console.log('✅ [SIGNUP] onAuthSuccess callback executed successfully');
        } catch (error) {
          console.error('❌ [SIGNUP] Error in onAuthSuccess callback:', error);
        }
      } else {
        console.warn('⚠️ [SIGNUP] onAuthSuccess callback not available or not a function');
      }

      // Show success alert
      console.log('✅ Showing success alert...');
      Alert.alert(
        'Welcome to Citra AI!',
        'You have successfully signed in and your account is ready.',
        [{
          text: 'OK',
          onPress: () => {
            console.log('✅ Alert dismissed');
          }
        }]
      );
    } catch (err) {
      console.error('❌ Complete authentication failed:', err);
      throw err;
    }
  };

  // --- Render ---
  if (step === 0) {
    return (
      <View style={[styles.container, { backgroundColor: colors.background }]}>
        <StatusBar barStyle="light-content" />
        <View style={styles.welcomeContainer}>
          <Animated.View style={{ transform: [{ scale: logoScale }], opacity: logoOpacity }}>
            <View style={[styles.logoGlow, { shadowColor: colors.accent }]}>
              <Image source={logo} style={styles.welcomeLogo} resizeMode="contain" />
            </View>
          </Animated.View>
          <Animated.View style={{ opacity: textOpacity, alignItems: 'center' }}>
            <Text style={[styles.welcomeTitle, { color: colors.text }]}>
              🧠 Welcome to Citra AI
            </Text>
            <Text style={[styles.welcomeSubtitle, { color: colors.secondaryText }]}>
              your intelligent partner for content creation.
            </Text>
          </Animated.View>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.popupOverlay}>
      <StatusBar barStyle="light-content" />

      {/* Ultra Modern Backdrop Blur Effect */}
      <View style={[styles.backdrop, { backgroundColor: colors.overlay }]} />

      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.popupContainer}
      >
        <Animated.View
          style={[
            styles.modernPopup,
            {
              backgroundColor: colors.cardBackground,
              transform: [{ translateY: slideUp }, { scale: fadeIn }],
              opacity: fadeIn,
              shadowColor: colors.accent,
            },
          ]}
        >
          {/* Ultra Modern Header with Glow Effect */}
          <View style={[styles.popupHeader, { borderBottomColor: colors.border }]}>
            <View style={[styles.logoContainer, { shadowColor: colors.accent }]}>
              <Image source={logo} style={styles.popupLogo} resizeMode="contain" />
            </View>
            <Text style={[styles.popupTitle, { color: colors.text }]}>🧠 Citra AI</Text>
            <Text style={[styles.popupSubtitle, { color: colors.secondaryText }]}>
              Citra is your intelligent partner for content creation
            </Text>
          </View>
          {/* Ultra Modern Popup Content */}
          <View style={styles.popupContent}>
            {step === 1 && (
              <>
                <View style={styles.modernCardHeader}>
                  <Text style={[styles.modernSubtitle, { color: colors.secondaryText }]}>
                    Citra AI to enhance productivity. Just upload and manage vault data and let AI take care of rest.
                  </Text>
                </View>

                {/* Email/password auth */}
                {authView === 'email' && availableProviders?.includes('local') && (
                  <EmailAuthScreen
                    theme={isDark ? 'dark' : 'light'}
                    onAuthSuccess={async (authData) => {
                      try {
                        await authService.setToken(authData.token);
                        const userData = { success: true, data: authData };
                        await AsyncStorage.setItem('@user', JSON.stringify(userData));
                        if (authData.user?.email) {
                          await AsyncStorage.setItem('@user_email', authData.user.email);
                        }
                        onAuthSuccess(userData);
                      } catch (e) {
                        setError(e.message || 'Authentication failed');
                      }
                    }}
                    onSwitchToGoogle={availableProviders?.includes('google') ? () => setAuthView('google') : null}
                  />
                )}

                {/* Google OAuth sign-in */}
                {authView === 'google' && (
                <View style={styles.modernAuthSection}>
                  {/* GDPR: Terms & Privacy consent checkbox */}
                  <TouchableOpacity
                    onPress={() => setTermsAccepted((v) => !v)}
                    style={{
                      flexDirection: 'row',
                      alignItems: 'flex-start',
                      marginBottom: 16,
                      paddingHorizontal: 4,
                    }}
                    activeOpacity={0.7}
                  >
                    <View
                      style={{
                        width: 22,
                        height: 22,
                        borderRadius: 4,
                        borderWidth: 2,
                        borderColor: termsAccepted ? colors.accent : colors.border,
                        backgroundColor: termsAccepted ? colors.accent : 'transparent',
                        alignItems: 'center',
                        justifyContent: 'center',
                        marginRight: 10,
                        marginTop: 2,
                      }}
                    >
                      {termsAccepted && (
                        <Text style={{ color: '#fff', fontSize: 14, fontWeight: '700' }}>✓</Text>
                      )}
                    </View>
                    <Text style={{ color: colors.secondaryText, fontSize: 13, lineHeight: 20, flex: 1 }}>
                      I agree to the{' '}
                      <Text
                        style={{ color: colors.accent, textDecorationLine: 'underline' }}
                        onPress={() => setTermsVisible(true)}
                      >
                        Terms of Service
                      </Text>
                      {' '}and{' '}
                      <Text
                        style={{ color: colors.accent, textDecorationLine: 'underline' }}
                        onPress={() => setPrivacyVisible(true)}
                      >
                        Privacy Policy
                      </Text>
                    </Text>
                  </TouchableOpacity>

                  {Platform.OS === 'web' ? (
                    // Ultra Modern Google Button
                    promptAsync ? (
                      <TouchableOpacity
                        disabled={!request || isLoading || !termsAccepted}
                        onPress={() => promptAsync()}
                        style={[
                          styles.ultraModernGoogleButton,
                          {
                            backgroundColor: colors.background,
                            borderColor: colors.accent,
                            shadowColor: colors.accent,
                            opacity: (request && !isLoading && termsAccepted) ? 1 : 0.5,
                          },
                        ]}
                        activeOpacity={0.8}
                      >
                        <View style={styles.modernGoogleContent}>
                          {isLoading ? (
                            <View style={styles.modernLoadingContainer}>
                              <ActivityIndicator size="small" color={colors.accent} />
                              <View style={[styles.modernLoadingDots, { backgroundColor: colors.accent }]} />
                            </View>
                          ) : (
                            <View style={styles.modernGoogleIconContainer}>
                              <View style={[styles.modernGoogleIcon, { backgroundColor: colors.cardBackground }]}>
                                <Image
                                  source={{ uri: 'https://developers.google.com/identity/images/g-logo.png' }}
                                  style={styles.modernGoogleIconImage}
                                  resizeMode="contain"
                                />
                              </View>
                            </View>
                          )}
                          <View style={styles.modernGoogleTextContainer}>
                            <Text style={[styles.modernGoogleText, { color: colors.text }]}>
                              {isLoading
                                ? 'Authenticating with AI...'
                                : 'Continue with Google'
                              }
                            </Text>
                            <Text style={[styles.modernGoogleSubtext, { color: colors.secondaryText }]}>
                              🛡️ Secure • ⚡ Fast • 🤖 AI-Powered
                            </Text>
                          </View>
                        </View>
                        <View style={[styles.modernGoogleGlow, { backgroundColor: colors.glowPrimary }]} />
                      </TouchableOpacity>
                    ) : (
                      <View style={[styles.modernUnavailableContainer, { backgroundColor: colors.cardBackground, borderColor: colors.border }]}>
                        <View style={styles.modernUnavailableIcon}>
                          <Text style={{ color: colors.accent, fontSize: 24 }}>⚠️</Text>
                        </View>
                        <Text style={[styles.modernUnavailableTitle, { color: colors.text }]}>
                          Google Sign-in Unavailable
                        </Text>
                        <Text style={[styles.modernUnavailableText, { color: colors.secondaryText }]}>
                          Please contact our support team for assistance with authentication
                        </Text>
                      </View>
                    )
                  ) : (
                    // Ultra Modern Mobile Message
                    <View style={[styles.modernMobileMessage, { backgroundColor: colors.cardBackground, borderColor: colors.accent }]}>
                      <View style={styles.modernMobileIcon}>
                        <Text style={{ color: colors.accent, fontSize: 32 }}>📱</Text>
                      </View>
                      <Text style={[styles.modernMobileTitle, { color: colors.text }]}>
                        Mobile Authentication
                      </Text>
                      <Text style={[styles.modernMobileText, { color: colors.secondaryText }]}>
                        We're implementing native Google Sign-In for the best mobile experience.
                      </Text>
                      <Text style={[styles.modernMobileSubtext, { color: colors.secondaryText }]}>
                        For now, please use the web version or contact support for assistance.
                      </Text>
                    </View>
                  )}
                  {/* Switch to email auth link */}
                  {availableProviders?.includes('local') && (
                    <TouchableOpacity onPress={() => setAuthView('email')} style={{ alignItems: 'center', marginTop: 12 }}>
                      <Text style={{ color: colors.accent, fontSize: 14 }}>Or sign in with email</Text>
                    </TouchableOpacity>
                  )}
                </View>
                )}

                {!!error && (
                  <View style={[styles.modernErrorContainer, { borderColor: colors.error }]}>
                    <Text style={[styles.modernErrorText, { color: colors.error }]}>
                      ⚠️ {error}
                    </Text>
                  </View>
                )}
              </>
            )}

          </View>

          {/* Ultra Modern Footer */}
          <View style={[styles.modernFooter, { borderTopColor: colors.border }]}>
            <Text style={[styles.modernFooterText, { color: colors.secondaryText }]}>
              🔒 By continuing, you agree to our Terms of Service & Privacy Policy
            </Text>
          </View>
        </Animated.View>
      </KeyboardAvoidingView>

      {/* Access Denied Modal - Shows FIRST before PricingPlan */}
      <Modal
        animationType="fade"
        transparent={true}
        visible={showAccessDeniedModal}
        onRequestClose={() => {
          console.log('🚫 [AUTH] User closed access denied modal, returning to IntroScreen');
          setShowAccessDeniedModal(false);
          setStep(0);
          setError('');
          setAuthError(null);
          setAuthErrorData(null);
        }}
      >
        <View style={{
          flex: 1,
          backgroundColor: 'rgba(0, 0, 0, 0.8)',
          justifyContent: 'center',
          alignItems: 'center',
          padding: 20
        }}>
          <View style={[
            styles.modernPopup,
            {
              backgroundColor: colors.cardBackground,
              maxWidth: 440,
              width: '100%',
              shadowColor: colors.accent,
            }
          ]}>
            {/* Header */}
            <View style={[styles.popupHeader, { borderBottomColor: colors.border }]}>
              <View style={styles.modernUnavailableIcon}>
                <Text style={{ fontSize: 48 }}>⚠️</Text>
              </View>
              <Text style={[styles.popupTitle, { color: colors.text }]}>
                Access Required
              </Text>
              <Text style={[styles.popupSubtitle, { color: colors.secondaryText }]}>
                You need an active subscription or trial to use Citra AI
              </Text>
            </View>

            {/* Content */}
            <View style={styles.popupContent}>
              <Text style={[styles.modernSubtitle, { color: colors.secondaryText, marginBottom: 24 }]}>
                {authErrorData?.message || 'You don\'t have access to Citra AI yet.'}
              </Text>

              <Text style={[styles.modernSubtitle, { color: colors.text, marginBottom: 16, fontWeight: '600' }]}>
                You can get access by:
              </Text>

              <View style={{ gap: 12, marginBottom: 32 }}>
                <Text style={[styles.modernHelperText, { color: colors.secondaryText, fontSize: 14 }]}>
                  🎫 Using a coupon code for free trial
                </Text>
                <Text style={[styles.modernHelperText, { color: colors.secondaryText, fontSize: 14 }]}>
                  💳 Subscribing to Individual plan
                </Text>
                <Text style={[styles.modernHelperText, { color: colors.secondaryText, fontSize: 14 }]}>
                  🏢 Contacting us for Enterprise plan
                </Text>
              </View>

              {/* Buttons */}
              <View style={styles.modernButtonContainer}>
                <TouchableOpacity
                  style={[
                    styles.ultraModernPrimaryButton,
                    {
                      backgroundColor: colors.accent,
                      shadowColor: colors.accent,
                    }
                  ]}
                  onPress={() => {
                    console.log('👤 [AUTH] User clicked Contact Admin');
                    setShowAccessDeniedModal(false);
                    setShowContact(true);
                  }}
                  activeOpacity={0.8}
                >
                  <View style={styles.modernButtonContent}>
                    <Text style={styles.modernButtonText}>
                      📧 Contact Administrator
                    </Text>
                    <Text style={styles.modernButtonSubtext}>
                      Request Access
                    </Text>
                  </View>
                  <View style={[styles.modernButtonGlow, { backgroundColor: colors.glowPrimary }]} />
                </TouchableOpacity>

                <TouchableOpacity
                  style={styles.modernBackButton}
                  onPress={() => {
                    console.log('👤 [AUTH] User cancelled, returning to IntroScreen');
                    setShowAccessDeniedModal(false);
                    setError('');
                    setAuthError(null);
                    setAuthErrorData(null);
                    setStep(0); // Reset to welcome screen

                    // If onCancel callback is provided, call it to clear auth data
                    if (onCancel && typeof onCancel === 'function') {
                      console.log('👤 [AUTH] Calling onCancel callback to clear auth');
                      onCancel();
                    }
                  }}
                >
                  <Text style={[styles.modernBackButtonText, { color: colors.secondaryText }]}>
                    ← Back to Welcome
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </View>
      </Modal>

      {/* Contact Screen Modal */}
      <Modal
        animationType="slide"
        transparent={false}
        visible={showContact}
        onRequestClose={() => setShowContact(false)}
      >
        <ContactScreen
          onBack={() => {
            console.log('📞 [SIGNUP] Closing ContactScreen');
            setShowContact(false);
          }}
          theme={{ isDark: isDark }}
        />
      </Modal>

      {/* Access Denied Modal - No Credits */}
      <Modal
        animationType="fade"
        transparent={true}
        visible={showAccessDeniedModal}
        onRequestClose={() => setShowAccessDeniedModal(false)}
      >
        <View style={styles.popupOverlay}>
          <View style={[styles.backdrop, { backgroundColor: colors.overlay }]} />

          <View style={styles.popupContainer}>
            <View style={[
              styles.modernPopup,
              {
                backgroundColor: colors.cardBackground,
                shadowColor: colors.accent,
              }
            ]}>
              {/* Header */}
              <View style={[styles.popupHeader, { borderBottomColor: colors.border }]}>
                <View style={[styles.logoContainer, { shadowColor: colors.accent }]}>
                  <Image source={logo} style={styles.popupLogo} resizeMode="contain" />
                </View>
                <Text style={[styles.popupTitle, { color: colors.text }]}>⚠️ Credits Required</Text>
                <Text style={[styles.popupSubtitle, { color: colors.secondaryText }]}>
                  Purchase credits to access Citra AI
                </Text>
              </View>

              {/* Content */}
              <View style={styles.popupContent}>
                <View style={styles.modernCardHeader}>
                  <Text style={[styles.modernSubtitle, { color: colors.secondaryText, textAlign: 'center' }]}>
                    {authErrorData?.message || 'You need to purchase credits to use Citra AI. Click Continue to view our affordable credit packages.'}
                  </Text>
                </View>

                {/* Credit Info */}
                {authErrorData?.accessStatus?.credits !== undefined && (
                  <View style={[styles.modernErrorContainer, { borderColor: colors.accent, backgroundColor: colors.cardGlow }]}>
                    <Text style={[styles.modernErrorText, { color: colors.text }]}>
                      💳 Token Balance: {typeof authErrorData.accessStatus.credits === 'number' ? Math.round(authErrorData.accessStatus.credits).toLocaleString('en-US') : '0'} tokens
                    </Text>
                  </View>
                )}

                {/* Continue Button */}
                <View style={styles.modernButtonContainer}>
                  <TouchableOpacity
                    style={[
                      styles.ultraModernPrimaryButton,
                      {
                        backgroundColor: colors.accent,
                        shadowColor: colors.accent,
                      }
                    ]}
                    onPress={() => {
                      console.log('� [AUTH] User clicked Contact Admin for credits');
                      setShowAccessDeniedModal(false);
                      setShowContact(true);
                    }}
                    activeOpacity={0.8}
                  >
                    <View style={styles.modernButtonContent}>
                      <Text style={styles.modernButtonText}>
                        📧 Contact Administrator
                      </Text>
                      <Text style={styles.modernButtonSubtext}>
                        Request License Access
                      </Text>
                    </View>
                    <View style={[styles.modernButtonGlow, { backgroundColor: colors.glowPrimary }]} />
                  </TouchableOpacity>

                  {/* Cancel Button */}
                  <TouchableOpacity
                    style={styles.modernBackButton}
                    onPress={() => {
                      console.log('❌ [AUTH] User cancelled credit purchase');
                      setShowAccessDeniedModal(false);
                      setAuthErrorData(null);
                      setError('');

                      // Call onCancel to return to IntroScreen
                      if (onCancel && typeof onCancel === 'function') {
                        onCancel();
                      } else {
                        setStep(0);
                      }
                    }}
                  >
                    <Text style={[styles.modernBackButtonText, { color: colors.secondaryText }]}>
                      ← Cancel
                    </Text>
                  </TouchableOpacity>
                </View>
              </View>
            </View>
          </View>
        </View>
      </Modal>

      <Modal
        visible={privacyVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setPrivacyVisible(false)}
      >
        <PrivacyPolicyScreen onBack={() => setPrivacyVisible(false)} theme={{ isDark: true }} />
      </Modal>

      <Modal
        visible={termsVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setTermsVisible(false)}
      >
        <TermsOfServiceScreen onBack={() => setTermsVisible(false)} theme={{ isDark: true }} />
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  // Welcome Screen Styles
  container: { flex: 1 },
  welcomeContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 40 },
  logoGlow: {
    ...Platform.select({
      web: {
        filter: 'drop-shadow(0 0 20px rgba(124, 58, 237, 0.5))',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 0 },
        shadowOpacity: 0.5,
        shadowRadius: 20,
        elevation: 20,
      },
    }),
  },
  welcomeLogo: { width: 120, height: 120, marginBottom: 20 },
  welcomeTitle: { fontSize: 28, fontWeight: '700', textAlign: 'center' },
  welcomeSubtitle: { fontSize: 16, textAlign: 'center', marginTop: 12 },

  // Ultra Modern Popup Styles
  popupOverlay: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
    backgroundColor: 'rgba(0, 0, 0, 0.5)', // Ensure consistent background
  },
  backdrop: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    ...Platform.select({
      web: {
        backdropFilter: 'blur(20px)',
      },
    }),
  },
  popupContainer: {
    maxWidth: 480,
    width: '100%',
    maxHeight: '90%',
  },
  modernPopup: {
    borderRadius: 24,
    padding: 0,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(124, 58, 237, 0.3)',
    ...Platform.select({
      web: {
        boxShadow: '0 25px 50px rgba(124, 58, 237, 0.25), 0 0 0 1px rgba(124, 58, 237, 0.1)',
        backdropFilter: 'blur(20px)',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 25 },
        shadowOpacity: 0.25,
        shadowRadius: 50,
        elevation: 25,
      },
    }),
  },

  // Modern Header
  popupHeader: {
    paddingTop: 32,
    paddingHorizontal: 32,
    paddingBottom: 24,
    alignItems: 'center',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(124, 58, 237, 0.1)',
    backgroundColor: 'rgba(124, 58, 237, 0.05)',
  },
  logoContainer: {
    marginBottom: 16,
    ...Platform.select({
      web: {
        filter: 'drop-shadow(0 0 15px rgba(124, 58, 237, 0.4))',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 0 },
        shadowOpacity: 0.4,
        shadowRadius: 15,
        elevation: 15,
      },
    }),
  },
  popupLogo: { width: 48, height: 48 },
  popupTitle: {
    fontSize: 22,
    fontWeight: '700',
    textAlign: 'center',
    marginBottom: 8,
  },
  popupSubtitle: {
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 20,
    opacity: 0.8,
  },

  // Content Area
  popupContent: { padding: 32 },
  modernCardHeader: { alignItems: 'center', marginBottom: 32 },
  glowEffect: {
    marginBottom: 16,
    ...Platform.select({
      web: {
        filter: 'drop-shadow(0 0 10px rgba(124, 58, 237, 0.3))',
      },
    }),
  },
  modernTitle: {
    fontSize: 24,
    fontWeight: '700',
    textAlign: 'center',
    marginBottom: 8,
  },
  modernSubtitle: {
    fontSize: 15,
    textAlign: 'center',
    lineHeight: 22,
    opacity: 0.9,
  },

  // Modern Options
  modernOptionsContainer: { marginBottom: 28, gap: 16 },
  modernOptionButton: {
    padding: 24,
    borderRadius: 20,
    borderWidth: 2,
    alignItems: 'center',
    minHeight: 130,
    justifyContent: 'center',
    position: 'relative',
    overflow: 'hidden',
    ...Platform.select({
      web: {
        boxShadow: '0 8px 25px rgba(124, 58, 237, 0.15)',
        transition: 'all 0.3s ease',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 8 },
        shadowOpacity: 0.15,
        shadowRadius: 25,
        elevation: 8,
      },
    }),
  },
  modernOptionIcon: {
    width: 60,
    height: 60,
    borderRadius: 30,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  modernOptionEmoji: { fontSize: 28 },
  modernOptionText: {
    fontSize: 18,
    fontWeight: '700',
    marginBottom: 8,
    textAlign: 'center',
  },
  modernOptionSubtext: {
    fontSize: 14,
    fontWeight: '500',
    textAlign: 'center',
    opacity: 0.9,
  },

  // Modern Google Button
  modernAuthSection: { marginTop: 8 },
  ultraModernGoogleButton: {
    height: 72,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    position: 'relative',
    overflow: 'hidden',
    ...Platform.select({
      web: {
        boxShadow: '0 8px 25px rgba(124, 58, 237, 0.2)',
        transition: 'all 0.3s ease',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 8 },
        shadowOpacity: 0.2,
        shadowRadius: 25,
        elevation: 8,
      },
    }),
  },
  modernGoogleContent: {
    flexDirection: 'row',
    alignItems: 'center',
    zIndex: 2,
  },
  modernLoadingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  modernLoadingDots: {
    width: 4,
    height: 4,
    borderRadius: 2,
  },
  modernGoogleIconContainer: { marginRight: 16 },
  modernGoogleIcon: {
    width: 32,
    height: 32,
    borderRadius: 8,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 4,
  },
  modernGoogleIconImage: { width: 24, height: 24 },
  modernGoogleTextContainer: { alignItems: 'flex-start' },
  modernGoogleText: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 2,
  },
  modernGoogleSubtext: {
    fontSize: 12,
    fontWeight: '500',
    opacity: 0.8,
  },
  modernGoogleGlow: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    opacity: 0.1,
  },

  // Modern Unavailable State
  modernUnavailableContainer: {
    padding: 24,
    borderRadius: 16,
    borderWidth: 1,
    alignItems: 'center',
  },
  modernUnavailableIcon: { marginBottom: 12 },
  modernUnavailableTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 8,
    textAlign: 'center',
  },
  modernUnavailableText: {
    fontSize: 14,
    textAlign: 'center',
    opacity: 0.8,
  },

  // Modern Mobile Message
  modernMobileMessage: {
    padding: 24,
    borderRadius: 16,
    borderWidth: 2,
    alignItems: 'center',
  },
  modernMobileIcon: { marginBottom: 16 },
  modernMobileTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 12,
    textAlign: 'center',
  },
  modernMobileText: {
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 20,
    marginBottom: 8,
  },
  modernMobileSubtext: {
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 18,
    opacity: 0.8,
  },

  // Modern Input Styles
  modernInputContainer: { marginBottom: 24 },
  modernInputLabel: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 12,
  },
  modernInputWrapper: {
    position: 'relative',
    borderWidth: 2,
    borderRadius: 16,
    overflow: 'hidden',
    backgroundColor: 'rgba(124, 58, 237, 0.05)',
  },
  modernPhoneInput: {
    height: 56,
    paddingHorizontal: 20,
    fontSize: 16,
    fontWeight: '500',
    zIndex: 2,
  },
  modernInputGlow: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    opacity: 0.1,
  },
  modernHelperText: {
    fontSize: 13,
    lineHeight: 18,
    marginTop: 8,
    textAlign: 'center',
    opacity: 0.8,
  },

  // Modern Buttons
  modernButtonContainer: { gap: 16 },
  ultraModernPrimaryButton: {
    height: 64,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
    overflow: 'hidden',
    ...Platform.select({
      web: {
        boxShadow: '0 10px 25px rgba(124, 58, 237, 0.4)',
        transition: 'all 0.3s ease',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 10 },
        shadowOpacity: 0.4,
        shadowRadius: 25,
        elevation: 10,
      },
    }),
  },
  modernButtonContent: {
    alignItems: 'center',
    zIndex: 2,
  },
  modernButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 2,
  },
  modernButtonSubtext: {
    color: 'rgba(255, 255, 255, 0.8)',
    fontSize: 12,
    fontWeight: '500',
  },
  modernButtonGlow: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    opacity: 0.2,
  },
  modernBackButton: {
    alignItems: 'center',
    paddingVertical: 16,
  },
  modernBackButtonText: {
    fontSize: 15,
    fontWeight: '500',
    opacity: 0.8,
  },

  // Modern Error Styles
  modernErrorContainer: {
    backgroundColor: 'rgba(255, 69, 58, 0.1)',
    borderWidth: 1,
    borderRadius: 12,
    padding: 16,
    marginTop: 16,
    alignItems: 'center',
  },
  modernErrorText: {
    fontSize: 14,
    fontWeight: '500',
    textAlign: 'center',
  },

  // Modern Footer
  modernFooter: {
    paddingHorizontal: 32,
    paddingVertical: 24,
    borderTopWidth: 1,
    alignItems: 'center',
    backgroundColor: 'rgba(124, 58, 237, 0.03)',
  },
  modernFooterText: {
    fontSize: 12,
    textAlign: 'center',
    lineHeight: 16,
    opacity: 0.7,
  },

  // Legacy styles for backward compatibility
  keyboardContainer: { flex: 1 },
  contentContainer: { flex: 1, padding: 24, paddingTop: 60 },
  header: { alignItems: 'center', marginBottom: 40 },
  appName: { fontSize: 18, fontWeight: '600' },

  // Legacy styles for backward compatibility
  card: {
    borderRadius: 20,
    padding: 32,
    marginBottom: 20,
    ...Platform.select({
      web: {
        boxShadow: '0 12px 25px rgba(0, 0, 0, 0.25)',
      },
      default: {
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 12 },
        shadowOpacity: 0.25,
        shadowRadius: 25,
        elevation: 15,
      },
    }),
    borderWidth: 1,
    borderColor: 'rgba(124, 58, 237, 0.1)',
  },
  cardHeader: { alignItems: 'center', marginBottom: 32 },
  title: { fontSize: 26, fontWeight: '700', marginBottom: 12, textAlign: 'center' },
  subtitle: { fontSize: 16, textAlign: 'center', lineHeight: 22 },
  optionsContainer: { marginBottom: 28, gap: 16 },
  optionButton: {
    padding: 28,
    borderRadius: 18,
    borderWidth: 2,
    alignItems: 'center',
    ...Platform.select({
      web: {
        boxShadow: '0 6px 12px rgba(124, 58, 237, 0.12)',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 6 },
        shadowOpacity: 0.12,
        shadowRadius: 12,
        elevation: 6,
      },
    }),
    minHeight: 120,
    justifyContent: 'center',
  },
  optionIcon: { marginBottom: 8 },
  optionEmoji: { fontSize: 24 },
  optionText: { fontSize: 17, fontWeight: '700', marginBottom: 6 },
  optionSubtext: { fontSize: 14, fontWeight: '500' },
  authSection: { marginTop: 8 },
  googleButton: {
    height: 60,
    borderRadius: 16,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(124, 58, 237, 0.3)',
    ...Platform.select({
      web: {
        boxShadow: '0 4px 8px rgba(124, 58, 237, 0.1)',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.1,
        shadowRadius: 8,
        elevation: 4,
      },
    }),
  },
  googleButtonContent: { flexDirection: 'row', alignItems: 'center' },
  googleIconContainer: {
    width: 24,
    height: 24,
    borderRadius: 4,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
    padding: 2,
  },
  googleIconImage: {
    width: 20,
    height: 20,
  },
  googleButtonText: { fontSize: 16, fontWeight: '600' },
  mobileAuthMessage: {
    padding: 20,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 8,
    alignItems: 'center',
  },
  mobileAuthTitle: { fontSize: 16, fontWeight: '600', marginBottom: 8 },
  mobileAuthText: { fontSize: 14, textAlign: 'center', lineHeight: 20 },
  inputContainer: { marginBottom: 24 },
  inputLabel: { fontSize: 16, fontWeight: '600', marginBottom: 8 },
  phoneInput: {
    height: 56,
    borderWidth: 1.5,
    borderRadius: 12,
    paddingHorizontal: 16,
    fontSize: 16,
    fontWeight: '500',
    marginBottom: 8,
  },
  helperText: { fontSize: 14, lineHeight: 20 },
  buttonContainer: { gap: 16 },
  primaryButton: {
    height: 60,
    borderRadius: 16,
    justifyContent: 'center',
    alignItems: 'center',
    ...Platform.select({
      web: {
        boxShadow: '0 6px 8px rgba(124, 58, 237, 0.3)',
      },
      default: {
        shadowColor: '#7C3AED',
        shadowOffset: { width: 0, height: 6 },
        shadowOpacity: 0.3,
        shadowRadius: 8,
        elevation: 8,
      },
    }),
  },
  primaryButtonText: { color: '#FFFFFF', fontSize: 16, fontWeight: '600' },
  backButton: { alignItems: 'center', paddingVertical: 12 },
  backButtonText: { fontSize: 16, fontWeight: '500' },
  errorText: { fontSize: 14, marginTop: 8 },
  footerText: { fontSize: 12, textAlign: 'center', marginTop: 20, lineHeight: 18 },
});