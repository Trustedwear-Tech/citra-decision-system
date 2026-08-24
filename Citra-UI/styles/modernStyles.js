// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import { StyleSheet, Platform, Dimensions } from 'react-native';

const { width, height } = Dimensions.get('window');

// Modern styling constants
const MODERN_SPACING = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
};

const MODERN_BORDER_RADIUS = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  round: 50,
};

const MODERN_TYPOGRAPHY = {
  h1: { fontSize: 32, fontWeight: '700', lineHeight: 40 },
  h2: { fontSize: 24, fontWeight: '600', lineHeight: 32 },
  h3: { fontSize: 20, fontWeight: '600', lineHeight: 28 },
  body: { fontSize: 16, fontWeight: '400', lineHeight: 24 },
  bodySmall: { fontSize: 14, fontWeight: '400', lineHeight: 20 },
  caption: { fontSize: 12, fontWeight: '400', lineHeight: 16 },
  button: { fontSize: 16, fontWeight: '600', lineHeight: 24 },
};

const MODERN_SHADOWS = {
  sm: Platform.select({
    ios: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.1,
      shadowRadius: 2,
    },
    android: { elevation: 2 },
    web: { boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)' },
  }),
  md: Platform.select({
    ios: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.15,
      shadowRadius: 4,
    },
    android: { elevation: 4 },
    web: { boxShadow: '0 2px 8px rgba(0, 0, 0, 0.15)' },
  }),
  lg: Platform.select({
    ios: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.2,
      shadowRadius: 8,
    },
    android: { elevation: 8 },
    web: { boxShadow: '0 4px 16px rgba(0, 0, 0, 0.2)' },
  }),
};

export const modernStyles = StyleSheet.create({
  // Layout Containers
  modernContainer: {
    flex: 1,
  },
  
  modernSafeArea: {
    flex: 1,
  },
  
  modernHeader: {
    height: 64,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: MODERN_SPACING.md,
    borderBottomWidth: 1,
    ...MODERN_SHADOWS.sm,
  },
  
  modernHeaderTitle: {
    ...MODERN_TYPOGRAPHY.h3,
    flex: 1,
    textAlign: 'center',
  },
  
  modernHeaderButton: {
    width: 44,
    height: 44,
    borderRadius: MODERN_BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
  },
  
  // Chat Interface
  modernChatContainer: {
    flex: 1,
    paddingHorizontal: MODERN_SPACING.md,
  },
  
  modernMessageWrapper: {
    marginVertical: MODERN_SPACING.sm,
    maxWidth: '85%',
  },
  
  modernUserMessageWrapper: {
    alignSelf: 'flex-end',
  },
  
  modernBotMessageWrapper: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  
  modernBotAvatar: {
    width: 36,
    height: 36,
    borderRadius: 18,
    marginRight: MODERN_SPACING.sm,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 4,
  },
  
  modernMessageBubble: {
    paddingVertical: MODERN_SPACING.sm,
    paddingHorizontal: MODERN_SPACING.md,
    borderRadius: MODERN_BORDER_RADIUS.lg,
    minHeight: 44,
    justifyContent: 'center',
    ...MODERN_SHADOWS.sm,
  },
  
  modernUserMessageBubble: {
    borderBottomRightRadius: MODERN_BORDER_RADIUS.sm,
  },
  
  modernBotMessageBubble: {
    borderBottomLeftRadius: MODERN_BORDER_RADIUS.sm,
    flex: 1,
  },
  
  modernMessageText: {
    ...MODERN_TYPOGRAPHY.body,
    // Enable text selection for message content
    WebkitUserSelect: 'text',
    MozUserSelect: 'text',
    msUserSelect: 'text',
    userSelect: 'text',
  },
  
  // Input System
  modernInputContainer: {
    padding: MODERN_SPACING.md,
    // Clean container with no borders
  },
  
  modernInputWrapper: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    borderRadius: MODERN_BORDER_RADIUS.lg,
    borderWidth: 0, // Completely remove the border
    paddingHorizontal: MODERN_SPACING.lg,
    paddingVertical: MODERN_SPACING.md,
    minHeight: 60, // Comfortable height
    maxHeight: 150,
    // Full width utilization
    width: '100%',
  },
  
  modernTextInput: {
    ...MODERN_TYPOGRAPHY.body,
    flex: 1,
    maxHeight: 120, // Allow more text height
    textAlignVertical: 'center',
    paddingVertical: Platform.OS === 'ios' ? 12 : 8, // More padding for bigger feel
    fontSize: 16, // Ensure good readable size
  },
  
  modernInputActions: {
    flexDirection: 'row',
    alignItems: 'center',
    marginLeft: MODERN_SPACING.sm,
    gap: MODERN_SPACING.sm,
  },

  // Web-specific Input System (clean, no mobile controls)
  modernWebInputContainer: {
    paddingHorizontal: 0, // Remove all side padding for web
    paddingVertical: MODERN_SPACING.md,
    // Full width utilization on web
  },
  
  modernWebInputWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: MODERN_BORDER_RADIUS.lg,
    borderWidth: 0, // No borders at all
    paddingHorizontal: MODERN_SPACING.lg,
    paddingVertical: MODERN_SPACING.md,
    minHeight: 60,
    maxHeight: 150,
    width: '100%', // Full width
    margin: 0, // No margins
  },
  
  modernWebTextInput: {
    ...MODERN_TYPOGRAPHY.body,
    flex: 1,
    maxHeight: 120,
    textAlignVertical: 'center',
    paddingVertical: 12,
    fontSize: 16,
    borderWidth: 0, // Ensure no border on text input
    outlineWidth: 0, // Remove web outline
    padding: 0, // Remove default web padding
    margin: 0, // Remove default web margins
    // Enable text selection
    WebkitUserSelect: 'text',
    MozUserSelect: 'text',
    msUserSelect: 'text',
    userSelect: 'text',
  },
  
  modernActionButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    justifyContent: 'center',
    alignItems: 'center',
    ...MODERN_SHADOWS.sm,
  },
  
  modernSendButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    justifyContent: 'center',
    alignItems: 'center',
    ...MODERN_SHADOWS.md,
  },
  
  // Side Menu (Mobile)
  modernSideMenuOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    zIndex: 1000,
  },
  
  modernSideMenu: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    width: width * 0.85,
    maxWidth: 320,
    borderTopRightRadius: MODERN_BORDER_RADIUS.lg,
    borderBottomRightRadius: MODERN_BORDER_RADIUS.lg,
    ...MODERN_SHADOWS.lg,
  },
  
  modernSideMenuHeader: {
    height: 80,
    justifyContent: 'center',
    alignItems: 'center',
    borderBottomWidth: 1,
  },
  
  modernSideMenuTitle: {
    ...MODERN_TYPOGRAPHY.h2,
  },
  
  modernSideMenuContent: {
    flex: 1,
    paddingTop: MODERN_SPACING.lg,
  },
  
  modernMenuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: MODERN_SPACING.md,
    paddingHorizontal: MODERN_SPACING.lg,
    marginHorizontal: MODERN_SPACING.md,
    borderRadius: MODERN_BORDER_RADIUS.md,
    marginVertical: 2,
  },
  
  modernMenuItemIcon: {
    width: 24,
    height: 24,
    marginRight: MODERN_SPACING.md,
  },
  
  modernMenuItemText: {
    ...MODERN_TYPOGRAPHY.body,
    fontWeight: '500',
  },

  // Modern Sidebar (Web)
  modernSidebar: {
  // Default expanded width; allow collapse by not forcing minWidth
  width: 280,
  minWidth: 0,
  maxWidth: 280,
    ...MODERN_SHADOWS.sm,
  },

  modernSidebarHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },

  modernSidebarLogo: {
    ...MODERN_SHADOWS.sm,
  },

  modernSidebarTitle: {
    ...MODERN_TYPOGRAPHY.h3,
  },

  modernSidebarContent: {
    flex: 1,
  },

  modernSidebarMenuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    ...MODERN_SHADOWS.sm,
    transition: Platform.OS === 'web' ? 'all 0.2s ease' : undefined,
  },

  modernSidebarMenuItemContent: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
  },

  modernSidebarMenuItemIconContainer: {
    ...MODERN_SHADOWS.sm,
  },

  modernSidebarMenuItemTitle: {
    ...MODERN_TYPOGRAPHY.body,
  },

  modernSidebarMenuItemSubtitle: {
    ...MODERN_TYPOGRAPHY.caption,
  },

  // Dropdown Styles
  modernDropdownContainer: {
    borderWidth: 1,
    borderRadius: MODERN_BORDER_RADIUS.md,
    overflow: 'hidden',
    ...MODERN_SHADOWS.sm,
  },

  modernDropdownItem: {
    flexDirection: 'row',
    alignItems: 'center',
    borderBottomWidth: 0.5,
    transition: Platform.OS === 'web' ? 'background-color 0.15s ease' : undefined,
  },

  modernDropdownText: {
    ...MODERN_TYPOGRAPHY.bodySmall,
  },
  
  // Web Layout
  modernWebContainer: {
    flexDirection: 'row',
    flex: 1,
  },
  
  modernWebSidebar: {
    width: 320,
    borderRightWidth: 1,
    ...MODERN_SHADOWS.sm,
  },
  
  modernWebSidebarHeader: {
    height: 64,
    justifyContent: 'center',
    alignItems: 'center',
    borderBottomWidth: 1,
  },
  
  modernWebMainContent: {
    flex: 1,
    flexDirection: 'column',
  },
  
  modernWebChatContainer: {
    flex: 1,
    maxWidth: 800,
    alignSelf: 'center',
    width: '100%',
  },
  
  // Buttons
  modernButton: {
    height: 48,
    borderRadius: MODERN_BORDER_RADIUS.md,
    paddingHorizontal: MODERN_SPACING.lg,
    justifyContent: 'center',
    alignItems: 'center',
    flexDirection: 'row',
    ...MODERN_SHADOWS.sm,
  },
  
  modernButtonText: {
    ...MODERN_TYPOGRAPHY.button,
  },
  
  modernButtonPrimary: {
    minWidth: 100,
  },
  
  modernButtonSecondary: {
    borderWidth: 2,
  },
  
  modernButtonIcon: {
    marginRight: MODERN_SPACING.sm,
  },
  
  // Theme Toggle
  modernThemeToggle: {
    width: 60,
    height: 32,
    borderRadius: 16,
    paddingHorizontal: 4,
    justifyContent: 'center',
    ...MODERN_SHADOWS.sm,
  },
  
  modernThemeToggleThumb: {
    width: 24,
    height: 24,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    ...MODERN_SHADOWS.sm,
  },
  
  // Modals
  modernModalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: MODERN_SPACING.lg,
  },
  
  modernModalContent: {
    width: '100%',
    maxWidth: 480,
    maxHeight: '90%',
    borderRadius: MODERN_BORDER_RADIUS.lg,
    ...MODERN_SHADOWS.lg,
  },
  
  modernModalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: MODERN_SPACING.lg,
    borderBottomWidth: 1,
  },
  
  modernModalTitle: {
    ...MODERN_TYPOGRAPHY.h3,
  },
  
  modernModalBody: {
    flex: 1,
    padding: MODERN_SPACING.lg,
  },
  
  modernModalFooter: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    padding: MODERN_SPACING.lg,
    borderTopWidth: 1,
    gap: MODERN_SPACING.md,
  },
  
  // Cards
  modernCard: {
    borderRadius: MODERN_BORDER_RADIUS.md,
    padding: MODERN_SPACING.md,
    marginVertical: MODERN_SPACING.sm,
    marginHorizontal: MODERN_SPACING.md,
    ...MODERN_SHADOWS.sm,
  },
  
  modernCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: MODERN_SPACING.sm,
  },
  
  modernCardTitle: {
    ...MODERN_TYPOGRAPHY.h3,
  },
  
  modernCardContent: {
    flex: 1,
  },
  
  modernCardActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: MODERN_SPACING.md,
    gap: MODERN_SPACING.sm,
  },
  
  // Toggle Switches
  modernToggleContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: MODERN_SPACING.md,
    gap: MODERN_SPACING.md,
    paddingHorizontal: MODERN_SPACING.md,
  },
  
  modernToggleButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: MODERN_SPACING.md,
    paddingVertical: MODERN_SPACING.sm,
    borderRadius: MODERN_BORDER_RADIUS.xl,
    borderWidth: 2,
    minWidth: 120,
    justifyContent: 'center',
    gap: MODERN_SPACING.sm,
  },
  
  modernToggleText: {
    ...MODERN_TYPOGRAPHY.bodySmall,
    fontWeight: '600',
  },
  
  // Attachments
  modernAttachmentContainer: {
    flexDirection: 'row',
    paddingHorizontal: MODERN_SPACING.md,
    paddingVertical: MODERN_SPACING.sm,
    borderRadius: MODERN_BORDER_RADIUS.md,
    marginHorizontal: MODERN_SPACING.md,
    marginBottom: MODERN_SPACING.sm,
  },
  
  modernAttachmentItem: {
    width: 48,
    height: 48,
    borderRadius: MODERN_BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: MODERN_SPACING.sm,
    position: 'relative',
    borderWidth: 2,
    ...MODERN_SHADOWS.sm,
  },
  
  modernAttachmentRemove: {
    position: 'absolute',
    top: -6,
    right: -6,
    width: 20,
    height: 20,
    borderRadius: 10,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
  },
  
  // Loading States
  modernLoadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: MODERN_SPACING.xl,
  },
  
  modernLoadingText: {
    ...MODERN_TYPOGRAPHY.body,
    marginTop: MODERN_SPACING.md,
    textAlign: 'center',
  },
  
  // Empty States
  modernEmptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: MODERN_SPACING.xl,
  },
  
  modernEmptyIcon: {
    marginBottom: MODERN_SPACING.lg,
  },
  
  modernEmptyTitle: {
    ...MODERN_TYPOGRAPHY.h3,
    textAlign: 'center',
    marginBottom: MODERN_SPACING.md,
  },
  
  modernEmptyText: {
    ...MODERN_TYPOGRAPHY.body,
    textAlign: 'center',
    lineHeight: 24,
  },
  
  // Lists
  modernList: {
    flex: 1,
  },
  
  modernListItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: MODERN_SPACING.md,
    paddingHorizontal: MODERN_SPACING.md,
    borderRadius: MODERN_BORDER_RADIUS.md,
    marginVertical: 2,
    marginHorizontal: MODERN_SPACING.md,
  },
  
  modernListItemContent: {
    flex: 1,
    marginLeft: MODERN_SPACING.md,
  },
  
  modernListItemTitle: {
    ...MODERN_TYPOGRAPHY.body,
    fontWeight: '600',
  },
  
  modernListItemSubtitle: {
    ...MODERN_TYPOGRAPHY.bodySmall,
    marginTop: 2,
  },
  
  modernListItemActions: {
    flexDirection: 'row',
    gap: MODERN_SPACING.sm,
  },
  
  // Accessibility
  modernFocusRing: {
    borderWidth: 2,
    borderColor: '#3b82f6',
  },
  
  modernHighContrast: {
    borderWidth: 1,
    borderColor: '#000',
  },
  
  // Responsive Design
  modernTablet: {
    maxWidth: 768,
    alignSelf: 'center',
  },
  
  modernDesktop: {
    maxWidth: 1200,
    alignSelf: 'center',
  },
});

// Export spacing and other constants for use in components
export const SPACING = MODERN_SPACING;
export const BORDER_RADIUS = MODERN_BORDER_RADIUS;
export const TYPOGRAPHY = MODERN_TYPOGRAPHY;
export const SHADOWS = MODERN_SHADOWS;

export default modernStyles;
