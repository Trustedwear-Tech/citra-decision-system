// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  Animated,
  Platform,
  Easing,
  Image,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { modernStyles, SPACING, BORDER_RADIUS } from '../../styles/modernStyles';
import ProfileMenu, { AccountActionsModal } from './ProfileMenu';
import { PERSONAL_VAULT_ENABLED } from '../../config/featureFlags';

// Animated menu item component with hover and press effects
const AnimatedMenuItem = ({ item, theme, isActive, isHighlighted, onPress, isCollapsed }) => {
  const [isHovered, setIsHovered] = useState(false);
  const scaleAnim = useRef(new Animated.Value(1)).current;

  const handleHoverIn = () => {
    if (Platform.OS === 'web') {
      setIsHovered(true);
      Animated.timing(scaleAnim, {
        toValue: 1.02,
        duration: 200,
        easing: Easing.out(Easing.quad),
        useNativeDriver: Platform.OS !== 'web',
      }).start();
    }
  };

  const handleHoverOut = () => {
    if (Platform.OS === 'web') {
      setIsHovered(false);
      Animated.spring(scaleAnim, {
        toValue: 1,
        tension: 300,
        friction: 10,
        useNativeDriver: Platform.OS !== 'web',
      }).start();
    }
  };

  return (
    <Animated.View style={{ transform: [{ scale: scaleAnim }] }}>
      <TouchableOpacity
        style={[
          modernStyles.modernSidebarMenuItem,
          {
            backgroundColor: isActive
              ? theme.primary + '20'
              : isHighlighted
                ? theme.accent + '15'
                : 'transparent',
            borderRadius: BORDER_RADIUS.md,
            marginHorizontal: SPACING.md,
            paddingVertical: SPACING.md,
            paddingHorizontal: isCollapsed ? SPACING.sm : SPACING.md,
            borderLeftWidth: isActive ? 3 : 0,
            borderLeftColor: isActive ? theme.primary : 'transparent',
          }
        ]}
        onPress={onPress}
        onPressIn={() => {
          Animated.spring(scaleAnim, {
            toValue: 0.98,
            useNativeDriver: Platform.OS !== 'web',
          }).start();
        }}
        onPressOut={() => {
          Animated.spring(scaleAnim, {
            toValue: 1,
            useNativeDriver: Platform.OS !== 'web',
          }).start();
        }}
        onMouseEnter={Platform.OS === 'web' ? handleHoverIn : undefined}
        onMouseLeave={Platform.OS === 'web' ? handleHoverOut : undefined}
        activeOpacity={0.7}
      >
        <View style={modernStyles.modernSidebarMenuItemContent}>
          <Ionicons
            name={item.icon}
            size={22}
            color={isActive ? theme.primary : (theme.isDark ? '#ffffff' : '#2C3E50')}
            style={{
              marginRight: isCollapsed ? 0 : SPACING.md,
              minWidth: 22,
              textAlign: 'center'
            }}
          />

          {!isCollapsed && (
            <View style={{ flex: 1 }}>
              <Text style={[
                modernStyles.modernSidebarMenuItemTitle,
                {
                  color: isActive ? theme.primary : theme.text,
                  fontWeight: isActive ? '600' : '500',
                  fontSize: 14,
                }
              ]}>
                {item.title}
              </Text>
              {item.type === 'dropdown' && item.value && (
                <Text style={[
                  modernStyles.modernSidebarMenuItemSubtitle,
                  {
                    color: theme.textSecondary,
                    fontSize: 12,
                    marginTop: 2,
                  }
                ]} numberOfLines={1}>
                  {item.value.length > 20 ? item.value.substring(0, 17) + '...' : item.value}
                </Text>
              )}
              {item.subtitle && (
                <Text style={[
                  modernStyles.modernSidebarMenuItemSubtitle,
                  {
                    color: theme.textSecondary,
                    fontSize: 11,
                    marginTop: 2,
                    fontStyle: 'italic',
                  }
                ]} numberOfLines={1}>
                  {item.subtitle}
                </Text>
              )}
            </View>
          )}

          {!isCollapsed && item.type === 'dropdown' && (
            <Ionicons
              name={item.isOpen ? 'chevron-up' : 'chevron-down'}
              size={16}
              color={theme.isDark ? '#ffffff' : '#000000'}
              style={{ marginLeft: SPACING.sm }}
            />
          )}
        </View>
      </TouchableOpacity>
    </Animated.View>
  );
};

export const ModernSidebar = ({
  theme,
  personaText,
  activeScreen,
  onNavigate,
  showDropdowns,
  onToggleDropdown,
  onNewChat,
  onLoadChatHistory,
  isCollapsed = true,
  onToggleCollapse,
  currentView = 'home',
  setCurrentView,
  onManageDeptSources,
  ...props
}) => {
  const fadeAnim = useRef(new Animated.Value(1)).current;
  const COLLAPSED_BAR_WIDTH = 56;
  const widthAnim = useRef(new Animated.Value(isCollapsed ? COLLAPSED_BAR_WIDTH : 280)).current;
  const [isHamburgerHovered, setIsHamburgerHovered] = useState(false);
  const [isNewHovered, setIsNewHovered] = useState(false);
  const [showAccountModal, setShowAccountModal] = useState(false);

  // Animate width when collapse state changes (collapsed bar width <-> 280)
  useEffect(() => {
    Animated.timing(widthAnim, {
      toValue: isCollapsed ? COLLAPSED_BAR_WIDTH : 280,
      duration: 280,
      useNativeDriver: false,
    }).start();
  }, [isCollapsed, widthAnim]);

  const menuItems = useMemo(() => [
    {
      id: 'new-chat',
      icon: 'add-circle-outline',
      title: 'New Chat',
      type: 'button',
      highlighted: true,
      onPress: onNewChat,
    },
    {
      id: 'current-chat',
      icon: 'chatbubble-outline',
      title: 'Current Chat',
      type: 'button',
      active: activeScreen === 'chat',
      onPress: () => onNavigate('chat'),
    },
    {
      id: 'history',
      icon: 'time-outline',
      title: 'History',
      type: 'dropdown',
      isOpen: showDropdowns?.history,
      onToggle: () => onToggleDropdown('history'),
      subItems: [
        {
          icon: 'chatbubbles-outline',
          title: 'Chat History',
          onPress: onLoadChatHistory,
        },
        // {
        //   icon: 'flask-outline',
        //   title: 'Deep Research Reports',
        //   onPress: () => {
        //     props.onShowDeepResearchReports?.();
        //     onToggleDropdown('history');
        //   },
        // },
      ],
    },
    {
      id: 'admin',
      icon: 'server-outline',
      title: 'Admin',
      type: 'dropdown',
      isOpen: showDropdowns?.admin,
      onToggle: () => onToggleDropdown('admin'),
      subItems: [
        {
          icon: 'cube-outline',
          title: 'Data Sources',
          onPress: () => {
            onManageDeptSources?.();
            onToggleDropdown('admin');
          },
        },
      ],
    },
    // {
    //   id: 'preload-content',
    //   icon: 'document-text-outline',
    //   title: personaText?.menuPersonalDrive || 'Vaults',
    //   type: 'button',
    //   active: activeScreen === 'preload',
    //   onPress: () => onNavigate('preload'),
    // },
    // ENTERPRISE FEATURE REMOVED - Personal Shared Repository disabled
    // {
    //   id: 'enterprise-upload',
    //   icon: 'people-outline',
    //   title: personaText?.menuEnterpriseDrive || 'Personal Shared Repository',
    //   subtitle: 'Entity-based evidence collection',
    //   type: 'button',
    //   active: activeScreen === 'enterprise-upload',
    //   onPress: () => onNavigate('enterprise-upload'),
    // },
    // {
    //   id: 'usage',
    //   icon: 'stats-chart-outline',
    //   title: 'Usage Settings',
    //   type: 'button',
    //   active: activeScreen === 'usage',
    //   onPress: () => onNavigate('usage'),
    // },
    // {
    //   id: 'upcoming',
    //   icon: 'settings-outline',
    //   title: 'Up Coming',
    //   type: 'button',
    //   active: activeScreen === 'upcoming',
    //   onPress: () => onNavigate('upcoming'),
    // },
    // Personal Data Store. Dropped while PERSONAL_VAULT_ENABLED is false —
    // chat is an enterprise-only search surface, so there are no personal
    // folders to browse. The 'data-store' screen in App.js is unmounted too.
    ...(PERSONAL_VAULT_ENABLED ? [{
      id: 'data-store',
      icon: 'folder-open-outline',
      title: 'Data Store',
      subtitle: 'Your personal folders',
      type: 'button',
      active: activeScreen === 'data-store',
      onPress: () => onNavigate('data-store'),
    }] : []),
    {
      id: 'credits',
      icon: 'stats-chart-outline',
      title: 'Tokens',
      subtitle: 'Token usage & management',
      type: 'button',
      active: activeScreen === 'credits',
      onPress: () => onNavigate('credits'),
    },
    {
      id: 'support',
      icon: 'card',
      title: 'Support',
      type: 'button',
      active: activeScreen === 'support',
      onPress: () => onNavigate('support'),
    },
    // Workflows is IT-only: only present in the menu when the user holds
    // a workflow-access role (mirrors backend gate).
    ...(props.hasWorkflowAccess ? [{
      id: 'workflow-builder',
      icon: 'git-network-outline',
      title: 'Workflows',
      subtitle: 'Agent workflow builder',
      type: 'button',
      active: props.isWorkflowBuilderActive,
      onPress: () => props.onShowWorkflowBuilder?.(),
    }] : []),
  ], [activeScreen, showDropdowns, personaText, onNavigate, onNewChat, onToggleDropdown, onLoadChatHistory, props]);

  // Context-sensitive menu items based on currentView
  const displayItems = useMemo(() => {
    // Home button for chat mode
    const homeButton = {
      id: 'home',
      icon: 'home-outline',
      title: 'Home',
      type: 'button',
      highlighted: true,
      onPress: () => {
        // Navigate to chat screen with home view
        if (activeScreen !== 'chat') {
          onNavigate('chat');
        }
        setCurrentView && setCurrentView('home');
      },
    };

    // Chat button for home mode
    const chatButton = {
      id: 'go-to-chat',
      icon: 'chatbubbles-outline',
      title: 'Go to Chat',
      type: 'button',
      highlighted: true,
      onPress: () => {
        // Navigate to chat screen with chat view
        if (activeScreen !== 'chat') {
          onNavigate('chat');
        }
        setCurrentView && setCurrentView('chat');
      },
    };

    // Separate customization items for collapsed mini toolbar.
    // Operations Branding is hidden from the side menu — see the commented
    // 'enterprise-branding' entry above.
    // Quick Chat mode: Show only Home button to return to HomePanel
    if (activeScreen === 'quick-chat') {
      return [homeButton];
    }

    if (currentView === 'chat') {
      // Chat mode: Show Home button first, then chat-specific items
      // In collapsed mode, show workspace as separate icon; in expanded mode, show as dropdown
      const chatItems = ['new-chat', 'current-chat', 'history', 'workspace'];
      return [
        homeButton,
        ...menuItems.filter(item => chatItems.includes(item.id))
      ];
    } else {
      // Home mode: Always include Home button first, then vault/profile items.
      // Collapsed and expanded show the same set now that Operations Branding
      // is hidden — it was the only item that differed between them.
      return [
        homeButton,
        ...menuItems.filter(item =>
          ['data-store', 'credits', 'support'].includes(item.id)
        )
      ];
    }
  }, [menuItems, currentView, setCurrentView, props, activeScreen]);

  const renderDropdownContent = (item) => {
    if (item.type === 'dropdown' && item.isOpen) {
      return (
        <View style={[
          modernStyles.modernDropdownContainer,
          {
            backgroundColor: theme.surfaceVariant,
            borderColor: theme.border,
            marginLeft: SPACING.lg,
            marginRight: SPACING.md,
            marginTop: SPACING.xs,
            borderRadius: BORDER_RADIUS.md,
          }
        ]}>
          {/** Sub-items dropdown (AI Model removed) */}
          {item.subItems?.map((subItem, index) => (
            <TouchableOpacity
              key={index}
              style={[
                modernStyles.modernDropdownItem,
                {
                  borderBottomColor: theme.border,
                  paddingVertical: SPACING.sm,
                  paddingHorizontal: SPACING.md,
                  flexDirection: 'row',
                  alignItems: 'center',
                }
              ]}
              onPress={subItem.onPress}
            >
              <Ionicons
                name={subItem.icon}
                size={18}
                color={theme.isDark ? '#ffffff' : '#2C3E50'}
                style={{ marginRight: SPACING.md }}
              />
              <Text style={[
                modernStyles.modernDropdownText,
                { color: theme.text, flex: 1 }
              ]}>
                {subItem.title}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      );
    }
    return null;
  };

  const renderMenuItem = (item) => {
    // Determine which single item should be active/highlighted
    let isActive = false;

    switch (activeScreen) {
      case 'chat':
        // Highlight Current Chat when on chat screen
        isActive = item.id === 'current-chat';
        break;
      case 'history':
        isActive = item.id === 'history';
        break;
      case 'notes':
        isActive = item.id === 'history'; // Notes is part of history dropdown
        break;
      case 'transcripts':
        isActive = item.id === 'history'; // Transcripts is part of history dropdown
        break;
      case 'documents':
        isActive = item.id === 'history'; // Documents is part of history dropdown
        break;
      case 'preload':
        isActive = item.id === 'preload-content';
        break;
      // case 'enterprise-upload':
      //   isActive = item.id === 'enterprise-upload';
      //   break;
      case 'upcoming':
        isActive = item.id === 'upcoming';
        break;
      // case 'subscription':
      //   isActive = item.id === 'subscription';
      //   break;
      case 'support':
        isActive = item.id === 'support';
        break;
      case 'data-store':
        isActive = item.id === 'data-store';
        break;
      // case 'usage':
      //   isActive = item.id === 'usage';
      //   break;
      default:
        isActive = false;
    }

    // No highlighted state if already active (single highlight only)
    const isHighlighted = false;

    return (
      <View key={item.id} style={{ marginVertical: SPACING.xs }}>
        <AnimatedMenuItem
          item={item}
          theme={theme}
          isActive={isActive}
          isHighlighted={isHighlighted}
          onPress={item.type === 'dropdown' ? item.onToggle : item.onPress}
          isCollapsed={isCollapsed}
        />

        {!isCollapsed && renderDropdownContent(item)}
      </View>
    );
  };

  return (
    <Animated.View
      style={[
        modernStyles.modernSidebar,
        {
          backgroundColor: theme.background,
          borderRightWidth: 1,
          borderRightColor: theme.border,
          width: widthAnim,
          minWidth: isCollapsed ? COLLAPSED_BAR_WIDTH : 280,
          maxWidth: isCollapsed ? COLLAPSED_BAR_WIDTH : 280,
          overflow: 'hidden',
          position: 'relative',
        },
      ]}
    >
      {isCollapsed ? (
        // Slim vertical bar with icons
        <View style={{ flex: 1, alignItems: 'center', paddingTop: SPACING.md, gap: SPACING.sm }}>
          <TouchableOpacity
            onPress={onToggleCollapse}
            onMouseEnter={Platform.OS === 'web' ? () => setIsHamburgerHovered(true) : undefined}
            onMouseLeave={Platform.OS === 'web' ? () => setIsHamburgerHovered(false) : undefined}
            style={{
              width: 40,
              height: 40,
              borderRadius: 20,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: isHamburgerHovered ? theme.menuItemActive + '60' : 'transparent',
              ...Platform.select({ web: { cursor: 'pointer' }, default: {} }),
            }}
            activeOpacity={0.8}
            accessibilityLabel="Open menu"
          >
            <Ionicons name="menu" size={22} color={isHamburgerHovered ? theme.primary : theme.text} />
          </TouchableOpacity>

          {/* Account icon — collapsed mode */}
          <View style={{ marginTop: SPACING.sm }}>
            <TouchableOpacity
              onPress={() => setShowAccountModal(true)}
              style={{
                width: 40,
                height: 40,
                borderRadius: 20,
                alignItems: 'center',
                justifyContent: 'center',
                ...Platform.select({ web: { cursor: 'pointer' }, default: {} }),
              }}
              activeOpacity={0.8}
              accessibilityLabel="Account"
            >
              <Ionicons name="person-circle-outline" size={22} color={theme.text} />
            </TouchableOpacity>
          </View>

          {displayItems.map(item => (
            <View key={item.id} style={{ marginTop: SPACING.sm }}>
              <TouchableOpacity
                onPress={() => {
                  // Special handling for dropdowns in collapsed mode
                  if (item.id === 'history') {
                    // History: open Chat History directly
                    onLoadChatHistory();
                  } else {
                    item.type === 'dropdown' ? item.onToggle() : item.onPress && item.onPress();
                  }
                }}
                onMouseEnter={Platform.OS === 'web' ? () => setIsNewHovered(item.id) : undefined}
                onMouseLeave={Platform.OS === 'web' ? () => setIsNewHovered(null) : undefined}
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 20,
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: item.active || (activeScreen === 'chat' && item.id === 'current-chat') || (item.highlighted) ? theme.menuItemActive + '60' : 'transparent',
                  ...Platform.select({ web: { cursor: 'pointer' }, default: {} }),
                  borderWidth: item.highlighted ? 1 : 0,
                  borderColor: item.highlighted ? theme.primary + '40' : 'transparent',
                }}
                activeOpacity={0.8}
                accessibilityLabel={item.title}
              >
                <Ionicons
                  name={item.icon}
                  size={22}
                  color={(item.active || (activeScreen === 'chat' && item.id === 'current-chat') || item.highlighted) ? theme.primary : theme.text}
                />
              </TouchableOpacity>
            </View>
          ))}
        </View>
      ) : (
        <>
          {/* Header */}
          <View
            style={[
              modernStyles.modernSidebarHeader,
              {
                backgroundColor: theme.surface,
                borderBottomWidth: 1,
                borderBottomColor: theme.border,
                paddingVertical: SPACING.lg,
                paddingHorizontal: SPACING.lg,
              },
            ]}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1 }}>
              <Image
                source={require('../../assets/citra-logo.png')}
                style={{ width: 26, height: 26, marginRight: SPACING.md }}
                resizeMode="contain"
              />
              <Text
                style={[
                  modernStyles.modernSidebarTitle,
                  {
                    color: theme.text,
                    fontSize: 18,
                    fontWeight: '700',
                    flex: 1,
                  },
                ]}
              >
                Citra AI
              </Text>
            </View>

            {/* Collapse toggle */}
            <TouchableOpacity
              onPress={onToggleCollapse}
              style={{
                padding: SPACING.sm,
                borderRadius: BORDER_RADIUS.sm,
                backgroundColor: theme.surface,
                marginLeft: SPACING.sm,
              }}
              activeOpacity={0.7}
              accessibilityLabel="Collapse menu"
            >
              <Ionicons name="chevron-back" size={20} color={theme.text} />
            </TouchableOpacity>

            <ProfileMenu theme={theme} />
          </View>

          {/* Workspace / team selector — Teams removed, "no use of this,
              remove fully." Was already {false &&} (hidden, not fully tested);
              now the component it rendered is gone too. */}

          {/* Account entry */}
          <TouchableOpacity
            onPress={() => setShowAccountModal(true)}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              paddingHorizontal: SPACING.md,
              paddingVertical: SPACING.sm + 2,
              borderBottomWidth: 1,
              borderBottomColor: theme.border,
            }}
            activeOpacity={0.7}
            accessibilityLabel="Account"
          >
            <Ionicons name="person-circle-outline" size={20} color={theme.text} style={{ marginRight: SPACING.md }} />
            <Text style={{ color: theme.text, fontSize: 14, fontWeight: '500', flex: 1 }}>Account</Text>
            <Ionicons name="chevron-forward" size={16} color={theme.textSecondary} />
          </TouchableOpacity>

          {/* Content */}
          <ScrollView
            style={modernStyles.modernSidebarContent}
            showsVerticalScrollIndicator={false}
            contentContainerStyle={{ paddingVertical: SPACING.md }}
          >
            {displayItems.map(renderMenuItem)}
          </ScrollView>
        </>
      )}

      {/* Account Actions Modal — shared between collapsed icon and expanded Account row */}
      <AccountActionsModal
        visible={showAccountModal}
        onClose={() => setShowAccountModal(false)}
        theme={theme}
      />
    </Animated.View>
  );
};

export default ModernSidebar;
