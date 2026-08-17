// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useState, useEffect } from 'react';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { MODERN_THEMES, getTheme, mapToLegacyTheme } from '../themes/modernThemes';

// Constants
const THEME_STORAGE_KEY = '@citra_ai_theme';
const SYSTEM_THEME_KEY = '@citra_ai_system_theme';

// Theme Context
export const ThemeContext = React.createContext({
  theme: MODERN_THEMES.light,
  isDarkMode: false,
  toggleTheme: () => {},
  setTheme: () => {},
  isSystemTheme: false,
  setSystemTheme: () => {},
});

// Modern Theme Provider
export const ModernThemeProvider = ({ children }) => {
  const [isDarkMode, setIsDarkMode] = useState(false); // Start with light mode to match UI
  const [isSystemTheme, setIsSystemTheme] = useState(false); // Start with manual theme, not system
  const [isLoading, setIsLoading] = useState(true);

  // Initialize theme from storage
  useEffect(() => {
    initializeTheme();
  }, []);

  // Listen to system theme changes on web
  useEffect(() => {
    if (Platform.OS === 'web' && isSystemTheme) {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      const handleSystemThemeChange = (e) => {
        setIsDarkMode(e.matches);
      };

      mediaQuery.addEventListener('change', handleSystemThemeChange);
      setIsDarkMode(mediaQuery.matches);

      return () => {
        mediaQuery.removeEventListener('change', handleSystemThemeChange);
      };
    }
  }, [isSystemTheme]);

  const initializeTheme = async () => {
    try {
      // Load saved theme preferences
      const savedIsDark = await AsyncStorage.getItem(THEME_STORAGE_KEY);
      const savedSystemTheme = await AsyncStorage.getItem(SYSTEM_THEME_KEY);
      
      if (savedIsDark !== null) {
        setIsDarkMode(JSON.parse(savedIsDark));
      } else {
        // Default to light theme if no preference saved
        setIsDarkMode(false);
      }
      
      if (savedSystemTheme !== null) {
        setIsSystemTheme(JSON.parse(savedSystemTheme));
      }
      
      console.log('Theme initialized from storage:', { 
        isDark: savedIsDark ? JSON.parse(savedIsDark) : false,
        isSystem: savedSystemTheme ? JSON.parse(savedSystemTheme) : false
      });
    } catch (error) {
      console.error('Error loading theme:', error);
      setIsDarkMode(false); // Default to light mode on error
    } finally {
      setIsLoading(false);
    }
  };

  const toggleTheme = async () => {
    try {
      const newIsDark = !isDarkMode;
      setIsDarkMode(newIsDark);
      setIsSystemTheme(false);
      
      // Save preferences
      await AsyncStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(newIsDark));
      await AsyncStorage.setItem(SYSTEM_THEME_KEY, JSON.stringify(false));
    } catch (error) {
      console.error('Error saving theme:', error);
    }
  };

  const setTheme = async (darkMode) => {
    try {
      setIsDarkMode(darkMode);
      setIsSystemTheme(false);
      
      // Save preferences
      await AsyncStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(darkMode));
      await AsyncStorage.setItem(SYSTEM_THEME_KEY, JSON.stringify(false));
    } catch (error) {
      console.error('Error saving theme:', error);
    }
  };

  const setSystemTheme = async (useSystem) => {
    try {
      setIsSystemTheme(useSystem);
      await AsyncStorage.setItem(SYSTEM_THEME_KEY, JSON.stringify(useSystem));
      
      if (useSystem && Platform.OS === 'web') {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        setIsDarkMode(prefersDark);
      }
    } catch (error) {
      console.error('Error saving system theme preference:', error);
    }
  };

  // Get current theme
  const currentTheme = getTheme(isDarkMode);
  
  // Map to legacy theme structure for backward compatibility
  const legacyTheme = mapToLegacyTheme(currentTheme);

  const value = {
    theme: legacyTheme,
    modernTheme: currentTheme,
    isDarkMode,
    toggleTheme,
    setTheme,
    isSystemTheme,
    setSystemTheme,
    isLoading,
  };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
};

// Hook to use theme
export const useTheme = () => {
  const context = React.useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ModernThemeProvider');
  }
  return context;
};

// Hook for theme-aware styles
export const useThemedStyles = (styleCreator) => {
  const { theme, modernTheme } = useTheme();
  return React.useMemo(() => styleCreator(theme, modernTheme), [theme, modernTheme, styleCreator]);
};

// Theme utilities
export const createThemedStyles = (styleFunction) => {
  return (theme, modernTheme) => styleFunction(theme, modernTheme);
};

// Theme animation hook
export const useThemeTransition = () => {
  const { isDarkMode } = useTheme();
  const [isTransitioning, setIsTransitioning] = useState(false);

  useEffect(() => {
    setIsTransitioning(true);
    const timer = setTimeout(() => {
      setIsTransitioning(false);
    }, 300);

    return () => clearTimeout(timer);
  }, [isDarkMode]);

  return { isTransitioning };
};

// Platform-specific theme helpers
export const getPlatformTheme = (baseTheme) => {
  if (Platform.OS === 'web') {
    return {
      ...baseTheme,
      // Web-specific theme adjustments
      shadow: baseTheme.isDark
        ? '0 2px 8px rgba(0, 0, 0, 0.3)'
        : '0 2px 8px rgba(0, 0, 0, 0.1)',
    };
  }
  
  return baseTheme;
};

// Theme validation
export const validateTheme = (theme) => {
  const requiredProperties = [
    'background', 'text', 'primary', 'surface'
  ];
  
  return requiredProperties.every(prop => theme.hasOwnProperty(prop));
};

// Export theme types for TypeScript users
export const THEME_MODES = {
  LIGHT: 'light',
  DARK: 'dark',
  SYSTEM: 'system',
};

export default {
  ModernThemeProvider,
  useTheme,
  useThemedStyles,
  createThemedStyles,
  useThemeTransition,
  getPlatformTheme,
  validateTheme,
  THEME_MODES,
};
