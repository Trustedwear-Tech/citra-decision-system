// useIsMobileWeb.js - Hook to detect mobile web browser for responsive layout
// Returns { isMobileWeb, windowWidth, windowHeight }
// Mobile web: Platform.OS === 'web' AND viewport width < 768px AND mobile UA

import { useState, useEffect, useCallback } from 'react';
import { Platform, Dimensions } from 'react-native';

const MOBILE_BREAKPOINT = 768;

/**
 * Check if the user agent indicates a mobile device.
 */
const checkMobileUA = () => {
  if (Platform.OS !== 'web') return false;
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false;

  const userAgent = navigator.userAgent || navigator.vendor || window.opera;
  const mobileRegex = /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini|mobile/i;
  return mobileRegex.test(userAgent);
};

/**
 * Hook to detect if the app is running in a mobile web browser.
 * Combines UA detection with viewport width check (< 768px).
 * Listens to dimension changes for orientation / resize.
 */
const useIsMobileWeb = () => {
  const [dimensions, setDimensions] = useState(() => {
    const { width, height } = Dimensions.get('window');
    return { width, height };
  });

  const isMobileUA = Platform.OS === 'web' && checkMobileUA();

  const handleDimensionChange = useCallback(({ window }) => {
    setDimensions({ width: window.width, height: window.height });
  }, []);

  useEffect(() => {
    const subscription = Dimensions.addEventListener('change', handleDimensionChange);
    return () => {
      subscription?.remove();
    };
  }, [handleDimensionChange]);

  // Mobile web = web platform + (mobile UA OR narrow viewport)
  const isMobileWeb =
    Platform.OS === 'web' &&
    (isMobileUA || dimensions.width < MOBILE_BREAKPOINT);

  return {
    isMobileWeb,
    windowWidth: dimensions.width,
    windowHeight: dimensions.height,
    MOBILE_BREAKPOINT,
  };
};

export default useIsMobileWeb;
export { useIsMobileWeb, MOBILE_BREAKPOINT, checkMobileUA };
