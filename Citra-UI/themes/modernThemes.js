// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// Modern Design System with Dark and Light Themes
// Based on current design trends and professional UI standards

export const MODERN_THEMES = {
  light: {
    // Core Theme Properties
    isDark: false,
    name: 'Modern Light',
    
    // Primary Colors
    primary: '#2563eb',        // Modern blue
    primaryVariant: '#1d4ed8', // Darker blue
    secondary: '#10b981',      // Emerald green
    secondaryVariant: '#059669',
    accent: '#8b5cf6',         // Purple accent
    
    // Background Colors
    background: '#ffffff',          // Pure white
    surface: '#e5e7eb',            // Much darker grey surface for strong contrast
    surfaceVariant: '#d1d5db',     // Darker grey surface variant
    card: '#ffffff',               // Card background
    modal: '#ffffff',              // Modal background
    
    // Text Colors - HARDCODED BLACK FOR LIGHT THEME
    text: '#000000',               // Pure black for maximum visibility
    textSecondary: '#000000',      // Pure black for secondary text
    textTertiary: '#000000',       // Pure black for tertiary text
    textOnPrimary: '#ffffff',      // White text on primary
    
    // Interactive Elements
    button: '#2563eb',             // Primary button
    buttonText: '#ffffff',         // Button text
    buttonSecondary: '#f1f5f9',    // Secondary button
    buttonSecondaryText: '#000000', // Pure black secondary button text
    
    // Message Bubbles
    userMessage: '#2563eb',        // User message background
    userMessageText: '#ffffff',    // User message text
    botMessage: '#f8fafc',         // Bot message background
    botMessageText: '#000000',     // Pure black bot message text
    
    // Input Elements - HARDCODED BLACK TEXT FOR LIGHT THEME
    inputBackground: '#ffffff',     // Input background
    inputBorder: '#6b7280',        // Much darker input border for strong visibility
    inputText: '#000000',          // Pure black input text
    inputPlaceholder: '#000000',   // Pure black placeholder for maximum visibility
    
    // Navigation
    headerBackground: '#ffffff',    // Header background
    headerText: '#000000',         // Pure black header text
    menuBackground: '#ffffff',     // Menu background
    menuItem: '#f8fafc',          // Menu item background
    menuItemActive: '#eff6ff',     // Active menu item
    
    // Status Colors
    success: '#10b981',            // Success green
    warning: '#f59e0b',           // Warning amber
    error: '#ef4444',             // Error red
    info: '#3b82f6',              // Info blue
    
    // Borders and Dividers
    border: '#6b7280',            // Much darker grey border for strong visibility
    borderLight: '#9ca3af',       // Darker grey for secondary borders
    divider: '#6b7280',           // Much darker divider line
    
    // Shadows
    shadow: 'rgba(0, 0, 0, 0.3)',  // Much darker shadow for strong visibility
    shadowDark: 'rgba(0, 0, 0, 0.4)', // Very dark shadow
    
    // States
    disabled: '#d1d5db',          // Much darker disabled background
    disabledText: '#000000',      // Pure black disabled text
    hover: '#e5e7eb',             // Much darker hover state
    active: '#9ca3af',            // Very dark active state
    
    // Chat Specific
    sendButton: '#2563eb',         // Send button
    attachmentBackground: '#d1d5db', // Much darker attachment background
    typingIndicator: '#000000',    // Pure black typing indicator
    
    // Code Blocks
    codeBackground: '#e5e7eb',     // Much darker code background
    codeBorder: '#6b7280',        // Very dark code border
    codeText: '#000000',          // Pure black code text
    
    // Loading States
    loadingPrimary: '#2563eb',     // Primary loading color
    loadingSecondary: '#6b7280',   // Very dark secondary loading color
  },
  
  dark: {
    // Core Theme Properties
    isDark: true,
    name: 'Modern Dark',
    
    // Primary Colors
    primary: '#3b82f6',           // Brighter blue for dark mode
    primaryVariant: '#2563eb',    // Standard blue
    secondary: '#10b981',         // Emerald green
    secondaryVariant: '#059669',
    accent: '#a855f7',            // Brighter purple for dark mode
    
    // Background Colors
    background: '#0f172a',         // Very dark slate
    surface: '#1e293b',           // Dark slate
    surfaceVariant: '#334155',     // Medium dark slate
    card: '#1e293b',              // Card background
    modal: '#1e293b',             // Modal background
    
    // Text Colors - HARDCODED WHITE FOR DARK THEME
    text: '#ffffff',              // Pure white text
    textSecondary: '#ffffff',     // Pure white secondary text
    textTertiary: '#ffffff',      // Pure white tertiary text
    textOnPrimary: '#ffffff',     // White text on primary
    
    // Interactive Elements
    button: '#3b82f6',            // Primary button
    buttonText: '#ffffff',        // Button text
    buttonSecondary: '#334155',   // Secondary button
    buttonSecondaryText: '#ffffff', // Pure white secondary button text
    
    // Message Bubbles
    userMessage: '#3b82f6',       // User message background
    userMessageText: '#ffffff',   // User message text
    botMessage: '#1e293b',        // Bot message background
    botMessageText: '#ffffff',    // Pure white bot message text
    
    // Input Elements - HARDCODED WHITE TEXT FOR DARK THEME
    inputBackground: '#1e293b',    // Input background
    inputBorder: '#475569',       // Input border
    inputText: '#ffffff',         // Pure white input text
    inputPlaceholder: '#ffffff',  // Pure white placeholder text
    
    // Navigation
    headerBackground: '#1e293b',   // Header background
    headerText: '#ffffff',        // Pure white header text
    menuBackground: '#1e293b',    // Menu background
    menuItem: '#334155',          // Menu item background
    menuItemActive: '#1e40af',    // Active menu item
    
    // Status Colors
    success: '#10b981',           // Success green
    warning: '#f59e0b',          // Warning amber
    error: '#ef4444',            // Error red
    info: '#3b82f6',             // Info blue
    
    // Borders and Dividers
    border: '#475569',            // Standard border
    borderLight: '#334155',       // Light border
    divider: '#475569',           // Divider line
    
    // Shadows
    shadow: 'rgba(0, 0, 0, 0.3)',  // Standard shadow
    shadowDark: 'rgba(0, 0, 0, 0.5)', // Darker shadow
    
    // States
    disabled: '#334155',          // Disabled background
    disabledText: '#ffffff',      // Pure white disabled text
    hover: '#334155',             // Hover state
    active: '#475569',            // Active state
    
    // Chat Specific
    sendButton: '#3b82f6',        // Send button
    attachmentBackground: '#334155', // Attachment background
    typingIndicator: '#ffffff',   // Pure white typing indicator
    
    // Code Blocks
    codeBackground: '#0f172a',    // Code background
    codeBorder: '#475569',        // Code border
    codeText: '#ffffff',          // Pure white code text
    
    // Loading States
    loadingPrimary: '#3b82f6',    // Primary loading color
    loadingSecondary: '#475569',  // Secondary loading color
  }
};

// Theme utilities
export const getTheme = (isDarkMode) => {
  return isDarkMode ? MODERN_THEMES.dark : MODERN_THEMES.light;
};

// Legacy theme mapping for backward compatibility
export const mapToLegacyTheme = (modernTheme) => {
  return {
    // Map modern theme to existing app structure
    isDark: modernTheme.isDark,
    background: modernTheme.background,
    text: modernTheme.text,                    // Pure white for dark, pure black for light
    textSecondary: modernTheme.textSecondary,  // Secondary text color
    placeholderText: modernTheme.textTertiary, // Pure white for dark, pure black for light
    userMessage: modernTheme.userMessage,
    userMessageText: modernTheme.userMessageText,
    botMessage: modernTheme.botMessage,
    botMessageText: modernTheme.botMessageText, // Pure white for dark, pure black for light
    inputBackground: modernTheme.inputBackground,
    inputContainerBackground: modernTheme.surface,
    inputText: modernTheme.inputText,          // Pure white for dark, pure black for light
    inputPlaceholder: modernTheme.inputPlaceholder, // Pure white for dark, pure black for light
    borderColor: modernTheme.border,
    border: modernTheme.border,
    borderLight: modernTheme.borderLight,
    divider: modernTheme.divider,
    sendButton: modernTheme.sendButton,
    
    // Add missing properties that the app expects
  // Ensure message bubble fallbacks exist for components that use "*Fallback" keys
  userMessageFallback: modernTheme.userMessage,
  botMessageFallback: modernTheme.botMessage,
    menuBackground: modernTheme.menuBackground,
    menuItem: modernTheme.menuItem,
    menuItemActive: modernTheme.menuItemActive,
    headerBackground: modernTheme.headerBackground,
    card: modernTheme.card,
    modal: modernTheme.modal,
    primary: modernTheme.primary,
    secondary: modernTheme.secondary,
    accent: modernTheme.accent,
    success: modernTheme.success,
    warning: modernTheme.warning,
    error: modernTheme.error,
    info: modernTheme.info,
    disabled: modernTheme.disabled,
    disabledText: modernTheme.disabledText,
    shadow: modernTheme.shadow,
    surface: modernTheme.surface,
    surfaceVariant: modernTheme.surfaceVariant,
    textOnPrimary: modernTheme.textOnPrimary,
    button: modernTheme.button,
    buttonText: modernTheme.buttonText,
    buttonSecondary: modernTheme.buttonSecondary,
    buttonSecondaryText: modernTheme.buttonSecondaryText,
    hover: modernTheme.hover,
    active: modernTheme.active,
    attachmentBackground: modernTheme.attachmentBackground,
    typingIndicator: modernTheme.typingIndicator,
    codeBackground: modernTheme.codeBackground,
    codeBorder: modernTheme.codeBorder,
    codeText: modernTheme.codeText,
    loadingPrimary: modernTheme.loadingPrimary,
    loadingSecondary: modernTheme.loadingSecondary,
  };
};

// Dynamic theme colors for time-based themes
export const getTimeBasedTheme = () => {
  const hour = new Date().getHours();
  const isNightTime = hour < 6 || hour > 20;
  
  return isNightTime ? MODERN_THEMES.dark : MODERN_THEMES.light;
};

// Accessibility compliant colors - HARDCODED FOR MAXIMUM CONTRAST
export const ACCESSIBILITY_COLORS = {
  light: {
    highContrast: '#000000',       // Pure black for light theme
    mediumContrast: '#000000',     // Pure black for light theme
    lowContrast: '#000000',        // Pure black for light theme
    background: '#ffffff',
  },
  dark: {
    highContrast: '#ffffff',       // Pure white for dark theme
    mediumContrast: '#ffffff',     // Pure white for dark theme
    lowContrast: '#ffffff',        // Pure white for dark theme
    background: '#000000',
  }
};

export default MODERN_THEMES;
