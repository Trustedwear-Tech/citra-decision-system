// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// MobileWebHeader.js - Simple top header bar for mobile web layout
// Shows logo, screen title, back/home button, and chat action bar

import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Platform, Image } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const MobileWebHeader = ({
  title = 'Citra AI',
  showBack = false,
  onBack,
  onHome,
  onNewChat,
  onChatHistory,
  onCurrentChat,
  showChatMenu = false,
  theme,
  // Vault selector props
  selectedFolders = [],
  folders = [],
  onSelectFolder,
}) => {
  const isDark = theme?.isDark ?? false;
  const bgColor = isDark ? '#1a1a2e' : '#ffffff';
  const textColor = isDark ? '#ffffff' : '#1F2937';
  const borderColor = isDark ? '#2d2d44' : '#E5E7EB';
  const primaryColor = theme?.primary || '#6366F1';
  const mutedColor = isDark ? '#9CA3AF' : '#6B7280';

  const chatActions = [
    { id: 'current', icon: 'chatbubble-outline', label: 'Current', onPress: onCurrentChat },
    { id: 'new', icon: 'add-circle-outline', label: 'New Chat', onPress: onNewChat },
    { id: 'history', icon: 'time-outline', label: 'History', onPress: onChatHistory },
  ];

  return (
    <View style={[styles.wrapper, { backgroundColor: bgColor }]}>
      {/* Row 1: Main header */}
      <View style={[styles.header, { borderBottomColor: showChatMenu ? 'transparent' : borderColor }]}>
        {/* Left: Back or Logo */}
        <View style={styles.leftSection}>
          {showBack ? (
            <TouchableOpacity
              onPress={onBack || onHome}
              style={styles.backButton}
              activeOpacity={0.7}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="arrow-back" size={22} color={primaryColor} />
            </TouchableOpacity>
          ) : (
            <View style={styles.logoContainer}>
              <Image
                source={require('../assets/citra-logo.png')}
                style={{ width: 22, height: 22 }}
                resizeMode="contain"
              />
            </View>
          )}
        </View>

        {/* Center: Title + Vault Chip */}
        <View style={styles.centerSection}>
          <Text style={[styles.title, { color: textColor }]} numberOfLines={1}>
            {title}
          </Text>
        </View>

        {/* Right: Home button */}
        <View style={styles.rightSection}>
          {showBack && onHome && (
            <TouchableOpacity
              onPress={onHome}
              style={styles.homeButton}
              activeOpacity={0.7}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="home-outline" size={20} color={primaryColor} />
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* Row 2: Chat action bar */}
      {showChatMenu && (
        <View style={[styles.chatActionBar, { borderBottomColor: borderColor }]}>
          {chatActions.map((action) => (
            <TouchableOpacity
              key={action.id}
              style={styles.chatActionButton}
              onPress={action.onPress}
              activeOpacity={0.7}
            >
              <Ionicons name={action.icon} size={18} color={primaryColor} />
              <Text style={[styles.chatActionLabel, { color: mutedColor }]}>
                {action.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  wrapper: {
    ...Platform.select({
      web: {
        position: 'sticky',
        top: 0,
        zIndex: 1000,
      },
      default: {},
    }),
  },
  header: {
    height: 52,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    borderBottomWidth: 1,
  },
  leftSection: {
    width: 44,
    alignItems: 'flex-start',
    justifyContent: 'center',
  },
  rightSection: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'flex-end',
  },
  logoContainer: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: 'rgba(99, 102, 241, 0.1)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  backButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  homeButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  centerSection: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  title: {
    fontSize: 16,
    fontWeight: '600',
  },
  chatActionBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
    paddingVertical: 6,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
  },
  chatActionButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 5,
    paddingHorizontal: 12,
    borderRadius: 16,
    gap: 5,
  },
  chatActionLabel: {
    fontSize: 12,
    fontWeight: '500',
  },
});

export default MobileWebHeader;
