/**
 * Simple Performance Hooks - Safe for Metro bundler
 * Basic performance utilities without complex dependencies
 */

import { useState, useEffect, useCallback } from 'react';
import { Platform } from 'react-native';

// Simple app state hook - with web compatibility
export const useAppState = () => {
  const [appState, setAppState] = useState('active');

  useEffect(() => {
    if (Platform.OS === 'web') {
      // For web, we'll use document visibility API
      const handleVisibilityChange = () => {
        setAppState(document.hidden ? 'background' : 'active');
      };
      
      document.addEventListener('visibilitychange', handleVisibilityChange);
      return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
    } else {
      // For React Native platforms
      const { AppState } = require('react-native');
      
      const handleAppStateChange = (nextAppState) => {
        setAppState(nextAppState);
      };

      const subscription = AppState.addEventListener('change', handleAppStateChange);
      return () => subscription?.remove();
    }
  }, []);

  return {
    appState,
    isActive: appState === 'active',
    isBackground: appState.match(/inactive|background/)
  };
};

// Simple debounce hook
export const useDebounce = (value, delay) => {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delay]);

  return debouncedValue;
};

// Simple throttle hook
export const useThrottle = (callback, delay) => {
  const [isThrottled, setIsThrottled] = useState(false);

  return useCallback((...args) => {
    if (!isThrottled) {
      callback(...args);
      setIsThrottled(true);
      
      setTimeout(() => {
        setIsThrottled(false);
      }, delay);
    }
  }, [callback, delay, isThrottled]);
};

// Simple performance monitor
export const usePerformanceMonitor = (componentName) => {
  useEffect(() => {
    console.log(`🚀 [PERF] ${componentName} mounted`);
    const startTime = Date.now();

    return () => {
      const duration = Date.now() - startTime;
      console.log(`⏱️ [PERF] ${componentName} was active for ${duration}ms`);
    };
  }, [componentName]);

  return {
    logEvent: (eventName) => {
      console.log(`📊 [PERF] ${componentName} - ${eventName}`);
    }
  };
};
