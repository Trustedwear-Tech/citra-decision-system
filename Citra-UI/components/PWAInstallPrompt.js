import React, { useState, useEffect } from 'react';
import { View, Text, TouchableOpacity, Platform, Modal, StyleSheet, Animated, Image } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

/**
 * PWA Install Prompt Component
 * 
 * Handles Progressive Web App installation prompts across different devices:
 * - Desktop: Bottom-right floating card
 * - Mobile Android/Chrome: Bottom sticky banner
 * - iOS Safari: Instructional modal
 * 
 * Features:
 * - Detects install availability via beforeinstallprompt
 * - Respects user dismissal preferences
 * - Checks if already installed (standalone mode)
 */
const PWAInstallPrompt = () => {
  // Only render on web platform
  if (Platform.OS !== 'web') return null;

  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [showInstallPrompt, setShowInstallPrompt] = useState(false);
  const [showIOSInstructions, setShowIOSInstructions] = useState(false);
  const [isStandalone, setIsStandalone] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isIOS, setIsIOS] = useState(false);
  const fadeAnim = useState(new Animated.Value(0))[0];
  const slideAnim = useState(new Animated.Value(50))[0];
  const scaleAnim = useState(new Animated.Value(0.8))[0];
  const floatAnim = useState(new Animated.Value(0))[0];

  // Browser and device detection
  useEffect(() => {
    if (typeof window === 'undefined') return;

    console.log('[PWA] Install prompt initializing...');

    // Check if already installed (standalone mode)
    const standalone = window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone ||
      document.referrer.includes('android-app://');

    setIsStandalone(standalone);
    console.log('[PWA] Standalone mode:', standalone);

    // Detect mobile
    const mobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
      navigator.userAgent
    ) || window.innerWidth < 768;
    setIsMobile(mobile);
    console.log('[PWA] Mobile detected:', mobile);

    // Detect iOS
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    setIsIOS(ios);
    console.log('[PWA] iOS detected:', ios);

    // Check localStorage for dismissal
    const dismissed = localStorage.getItem('pwa_install_dismissed');
    const dismissedTime = localStorage.getItem('pwa_install_dismissed_time');
    
    // Show again after 1 day
    const oneDayMs = 1 * 24 * 60 * 60 * 1000;
    const shouldShowAgain = !dismissedTime || (Date.now() - parseInt(dismissedTime)) > oneDayMs;

    console.log('[PWA] Dismissal check:', { dismissed, shouldShowAgain });

    if (standalone) {
      console.log('[PWA] App already installed in standalone mode - not showing prompt');
      return;
    }

    if (dismissed && !shouldShowAgain) {
      console.log('[PWA] Install prompt dismissed by user - waiting for cooldown');
      return;
    }

    // Helper to animate the prompt in
    const animateIn = () => {
      Animated.parallel([
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 300,
          useNativeDriver: false,
        }),
        Animated.timing(slideAnim, {
          toValue: 0,
          duration: 300,
          useNativeDriver: false,
        }),
        Animated.spring(scaleAnim, {
          toValue: 1,
          tension: 50,
          friction: 3,
          useNativeDriver: false,
        }),
      ]).start(() => {
        Animated.loop(
          Animated.sequence([
            Animated.timing(floatAnim, {
              toValue: -5,
              duration: 2000,
              useNativeDriver: false,
            }),
            Animated.timing(floatAnim, {
              toValue: 0,
              duration: 2000,
              useNativeDriver: false,
            }),
          ])
        ).start();
      });
    };

    // Listen for beforeinstallprompt event (Chrome, Edge, Samsung Internet)
    // This captures the event so we can trigger native install later
    const handleBeforeInstallPrompt = (e) => {
      console.log('[PWA] beforeinstallprompt event fired - native install available');
      e.preventDefault();
      setDeferredPrompt(e);
      
      // Now that we have the prompt, show the install UI
      console.log('[PWA] Showing install prompt after capturing beforeinstallprompt');
      setShowInstallPrompt(true);
      animateIn();
    };

    // Check if beforeinstallprompt was already fired and stored on window
    if (window.deferredPrompt) {
      console.log('[PWA] Using pre-cached beforeinstallprompt event');
      setDeferredPrompt(window.deferredPrompt);
      setShowInstallPrompt(true);
      animateIn();
    }

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);

    // Listen for successful installation
    const handleAppInstalled = () => {
      console.log('[PWA] App successfully installed');
      setShowInstallPrompt(false);
      setDeferredPrompt(null);
      window.deferredPrompt = null;
      localStorage.removeItem('pwa_install_dismissed');
      localStorage.removeItem('pwa_install_dismissed_time');
    };

    window.addEventListener('appinstalled', handleAppInstalled);

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
      window.removeEventListener('appinstalled', handleAppInstalled);
    };
  }, []);

  // Handle install button click
  const handleInstallClick = async () => {
    if (isIOS) {
      // Show iOS instructions modal
      setShowIOSInstructions(true);
      return;
    }

    if (deferredPrompt) {
      // Native install prompt is available - use it
      console.log('[PWA] Triggering native install prompt');
      try {
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        console.log(`[PWA] User response: ${outcome}`);

        if (outcome === 'accepted') {
          console.log('[PWA] User accepted the install prompt');
        } else {
          console.log('[PWA] User dismissed the install prompt');
        }

        // Clear the deferred prompt
        setDeferredPrompt(null);
        setShowInstallPrompt(false);
      } catch (error) {
        console.error('[PWA] Error triggering install prompt:', error);
        // Fallback to instructions if native prompt fails
        setShowIOSInstructions(true);
      }
    } else {
      // No native prompt captured yet
      console.log('[PWA] No native prompt available - deferredPrompt is null');
      console.log('[PWA] isMobile:', isMobile, 'isIOS:', isIOS);
      
      // For Android/Chrome mobile, show instructions modal instead of alert
      // The native prompt may not be available immediately or browser doesn't support it
      if (isMobile && !isIOS) {
        console.log('[PWA] Showing Android mobile install instructions modal');
        setShowIOSInstructions(true); // Reuse the modal for Android too
      } else if (!isMobile) {
        // Desktop - can use simpler alert
        alert('To install Citra AI:\n\n1. Click the install icon in the address bar (⊕)\n2. Or open browser menu → "Install Citra AI..."');
      }
    }
  };

  // Handle close/dismiss
  const handleDismiss = () => {
    Animated.parallel([
      Animated.timing(fadeAnim, {
        toValue: 0,
        duration: 200,
        useNativeDriver: false,
      }),
      Animated.timing(slideAnim, {
        toValue: 50,
        duration: 200,
        useNativeDriver: false,
      }),
      Animated.timing(scaleAnim, {
        toValue: 0.8,
        duration: 200,
        useNativeDriver: false,
      }),
    ]).start(() => {
      setShowInstallPrompt(false);
      localStorage.setItem('pwa_install_dismissed', 'true');
      localStorage.setItem('pwa_install_dismissed_time', Date.now().toString());
    });
  };

  if (!showInstallPrompt || isStandalone) {
    return null;
  }

  // Desktop UI: Small compact pill on bottom-right
  if (!isMobile) {
    return (
      <Animated.View
        style={[
          webStyles.desktopPill,
          {
            opacity: fadeAnim,
            transform: [
              { translateY: Animated.add(slideAnim, floatAnim) },
              { scale: scaleAnim }
            ],
          },
        ]}
      >
        <TouchableOpacity
          style={webStyles.desktopPillTouchable}
          onPress={handleInstallClick}
          accessibilityLabel="Install Citra AI app"
          accessibilityRole="button"
          activeOpacity={0.85}
        >
          <Image
            source={require('../assets/citra-logo.png')}
            style={webStyles.desktopPillLogo}
            resizeMode="contain"
          />
          <Text style={webStyles.desktopPillText}>Install Citra for faster access</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={webStyles.desktopPillClose}
          onPress={handleDismiss}
          accessibilityLabel="Dismiss install prompt"
          accessibilityRole="button"
        >
          <Ionicons name="close" size={12} color="#94A3B8" />
        </TouchableOpacity>
      </Animated.View>
    );
  }

  // Mobile UI: Compact floating button with logo + Install
  return (
    <>
      <Animated.View
        style={[
          webStyles.mobilePill,
          {
            opacity: fadeAnim,
            transform: [
              { translateY: slideAnim },
              { scale: scaleAnim }
            ],
          },
        ]}
      >
        <TouchableOpacity
          style={webStyles.mobilePillTouchable}
          onPress={handleInstallClick}
          accessibilityLabel="Install Citra AI app"
          accessibilityRole="button"
          activeOpacity={0.85}
        >
          <Image
            source={require('../assets/citra-logo.png')}
            style={webStyles.mobilePillLogo}
            resizeMode="contain"
          />
          <Text style={webStyles.mobilePillText}>
            {isIOS ? 'Install' : 'Install'}
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={webStyles.mobilePillClose}
          onPress={handleDismiss}
          accessibilityLabel="Dismiss"
          accessibilityRole="button"
        >
          <Ionicons name="close" size={11} color="#94A3B8" />
        </TouchableOpacity>
      </Animated.View>

      {/* Mobile Install Instructions Modal (iOS and Android) */}
      {(isIOS || isMobile) && (
        <Modal
          visible={showIOSInstructions}
          transparent={true}
          animationType="fade"
          onRequestClose={() => setShowIOSInstructions(false)}
        >
          <View style={webStyles.modalOverlay}>
            <View style={webStyles.modalContent}>
              <View style={webStyles.modalHeader}>
                <Text style={webStyles.modalTitle}>Install Citra AI</Text>
                <TouchableOpacity
                  onPress={() => setShowIOSInstructions(false)}
                  accessibilityLabel="Close instructions"
                  accessibilityRole="button"
                >
                  <Ionicons name="close" size={24} color="#64748B" />
                </TouchableOpacity>
              </View>

              <View style={webStyles.instructionsContainer}>
                {isIOS ? (
                  <>
                    <Text style={webStyles.instructionsText}>
                      To install Citra AI on your iPhone or iPad:
                    </Text>

                    <View style={webStyles.instructionStep}>
                      <View style={webStyles.stepNumber}>
                        <Text style={webStyles.stepNumberText}>1</Text>
                      </View>
                      <View style={webStyles.stepContent}>
                        <Text style={webStyles.stepText}>
                          Tap the <Ionicons name="share-outline" size={16} color="#8B5CF6" /> Share button
                          at the bottom of your screen
                        </Text>
                      </View>
                    </View>

                    <View style={webStyles.instructionStep}>
                      <View style={webStyles.stepNumber}>
                        <Text style={webStyles.stepNumberText}>2</Text>
                      </View>
                      <View style={webStyles.stepContent}>
                        <Text style={webStyles.stepText}>
                          Scroll down and tap "Add to Home Screen"
                        </Text>
                      </View>
                    </View>

                    <View style={webStyles.instructionStep}>
                      <View style={webStyles.stepNumber}>
                        <Text style={webStyles.stepNumberText}>3</Text>
                      </View>
                      <View style={webStyles.stepContent}>
                        <Text style={webStyles.stepText}>
                          Tap "Add" in the top right corner
                        </Text>
                      </View>
                    </View>
                  </>
                ) : (
                  <>
                    <Text style={webStyles.instructionsText}>
                      To install Citra AI on your device:
                    </Text>

                    <View style={webStyles.instructionStep}>
                      <View style={webStyles.stepNumber}>
                        <Text style={webStyles.stepNumberText}>1</Text>
                      </View>
                      <View style={webStyles.stepContent}>
                        <Text style={webStyles.stepText}>
                          Tap the menu button (⋮) in your browser
                        </Text>
                      </View>
                    </View>

                    <View style={webStyles.instructionStep}>
                      <View style={webStyles.stepNumber}>
                        <Text style={webStyles.stepNumberText}>2</Text>
                      </View>
                      <View style={webStyles.stepContent}>
                        <Text style={webStyles.stepText}>
                          Select "Add to Home screen" or "Install app"
                        </Text>
                      </View>
                    </View>

                    <View style={webStyles.instructionStep}>
                      <View style={webStyles.stepNumber}>
                        <Text style={webStyles.stepNumberText}>3</Text>
                      </View>
                      <View style={webStyles.stepContent}>
                        <Text style={webStyles.stepText}>
                          Tap "Add" or "Install" to confirm
                        </Text>
                      </View>
                    </View>
                  </>
                )}

                <View style={webStyles.iosIconDemo}>
                  <Image
                    source={require('../assets/citra-logo.png')}
                    style={webStyles.iosLogoImage}
                    resizeMode="contain"
                  />
                  <Text style={webStyles.iosDemoText}>
                    Citra AI will appear on your home screen
                  </Text>
                </View>
              </View>

              <TouchableOpacity
                style={webStyles.modalButton}
                onPress={() => setShowIOSInstructions(false)}
              >
                <Text style={webStyles.modalButtonText}>Got it</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
      )}
    </>
  );
};

// Web-specific styles (CSS-in-JS for React Native Web)
const webStyles = StyleSheet.create({
  // Desktop: Compact pill on bottom-right
  desktopPill: {
    position: 'fixed',
    bottom: 20,
    right: 20,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    borderRadius: 20,
    paddingVertical: 6,
    paddingLeft: 6,
    paddingRight: 4,
    ...Platform.select({
      web: {
        boxShadow: '0 4px 16px rgba(0, 0, 0, 0.12), 0 0 0 1px rgba(0, 0, 0, 0.04)',
      },
    }),
    elevation: 6,
    zIndex: 9999,
  },
  desktopPillTouchable: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    ...Platform.select({
      web: {
        cursor: 'pointer',
      },
    }),
  },
  desktopPillLogo: {
    width: 22,
    height: 22,
    borderRadius: 4,
  },
  desktopPillText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#4B5563',
    letterSpacing: -0.2,
  },
  desktopPillClose: {
    marginLeft: 6,
    padding: 3,
    borderRadius: 10,
    ...Platform.select({
      web: {
        cursor: 'pointer',
      },
    }),
  },

  // Mobile: Compact floating pill on bottom-right
  mobilePill: {
    position: 'fixed',
    bottom: 16,
    right: 12,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    borderRadius: 18,
    paddingVertical: 5,
    paddingLeft: 5,
    paddingRight: 3,
    ...Platform.select({
      web: {
        boxShadow: '0 3px 12px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.04)',
      },
    }),
    elevation: 6,
    zIndex: 9999,
  },
  mobilePillTouchable: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    ...Platform.select({
      web: {
        cursor: 'pointer',
      },
    }),
  },
  mobilePillLogo: {
    width: 20,
    height: 20,
    borderRadius: 4,
  },
  mobilePillText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#4B5563',
  },
  mobilePillClose: {
    marginLeft: 4,
    padding: 2,
    borderRadius: 8,
    ...Platform.select({
      web: {
        cursor: 'pointer',
      },
    }),
  },

  // iOS Instructions Modal
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  modalContent: {
    backgroundColor: '#FFFFFF',
    borderRadius: 20,
    padding: 24,
    width: '100%',
    maxWidth: 400,
    ...Platform.select({
      web: {
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
      },
    }),
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: '#1E293B',
  },
  instructionsContainer: {
    gap: 20,
  },
  instructionsText: {
    fontSize: 15,
    color: '#64748B',
    lineHeight: 22,
  },
  instructionStep: {
    flexDirection: 'row',
    gap: 12,
    alignItems: 'flex-start',
  },
  stepNumber: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: '#8B5CF6',
    justifyContent: 'center',
    alignItems: 'center',
  },
  stepNumberText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  stepContent: {
    flex: 1,
    paddingTop: 4,
  },
  stepText: {
    fontSize: 15,
    color: '#1E293B',
    lineHeight: 22,
  },
  iosIconDemo: {
    alignItems: 'center',
    gap: 12,
    paddingVertical: 20,
    backgroundColor: '#F9FAFB',
    borderRadius: 12,
    marginTop: 8,
  },
  iosLogoImage: {
    width: 48,
    height: 48,
  },
  iosDemoText: {
    fontSize: 13,
    color: '#64748B',
    textAlign: 'center',
  },
  modalButton: {
    backgroundColor: '#8B5CF6',
    paddingVertical: 14,
    paddingHorizontal: 24,
    borderRadius: 10,
    marginTop: 20,
    alignItems: 'center',
  },
  modalButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
});

export default PWAInstallPrompt;
