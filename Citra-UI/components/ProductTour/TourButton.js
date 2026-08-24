// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * TourButton - Button to trigger the product tour from the menu ribbon
 */

import React from 'react';
import { TouchableOpacity, Text, StyleSheet, View, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTour } from './TourProvider';

const TourButton = ({ theme, style, compact = false }) => {
  const { isRunning, restartTour, getProgress, tourCompleted } = useTour();

  const handlePress = () => {
    // Auto-select the first vault checkbox before starting tour (web only)
    if (Platform.OS === 'web') {
      // Close the ribbon menu so it doesn't obstruct the tour steps
      if (window.__ribbonMenuControl && typeof window.__ribbonMenuControl.closeRibbon === 'function') {
        window.__ribbonMenuControl.closeRibbon();
      }

      const container = document.querySelector('[data-tour="vault-select-checkbox"]');
      if (container) {
        // Check if already selected by checking the inner div's background color
        const innerDiv = container.firstElementChild;
        const computedStyle = window.getComputedStyle(innerDiv);
        const backgroundColor = computedStyle.backgroundColor;
        const isSelected = backgroundColor !== 'rgba(0, 0, 0, 0)' && backgroundColor !== 'transparent';
        if (!isSelected) {
          const touchable = innerDiv.firstElementChild?.firstElementChild;
          if (touchable) {
            touchable.click();
          }
        }
      }
    }
    restartTour();
  };

  const progress = getProgress();
  const showBadge = !tourCompleted && progress.completedCount > 0 && progress.completedCount < progress.totalSteps;

  if (compact) {
    return (
      <TouchableOpacity
        style={[styles.compactButton, style]}
        onPress={handlePress}
        data-tour="tour-button"
      >
        <Ionicons
          name="school"
          size={20}
          color={isRunning ? (theme?.sendButton || '#3b82f6') : (theme?.text || '#888')}
        />
        {showBadge && (
          <View style={[styles.badge, { backgroundColor: theme?.sendButton || '#3b82f6' }]}>
            <Text style={styles.badgeText}>{progress.completedCount}</Text>
          </View>
        )}
      </TouchableOpacity>
    );
  }

  return (
    <TouchableOpacity
      style={[
        styles.button,
        {
          backgroundColor: isRunning
            ? (theme?.sendButton || '#3b82f6')
            : (theme?.isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.05)')
        },
        style
      ]}
      onPress={handlePress}
      data-tour="tour-button"
    >
      <Ionicons
        name="school"
        size={24}
        color={isRunning ? '#ffffff' : (theme?.text || '#888')}
      />
      <Text
        style={[
          styles.buttonText,
          { color: isRunning ? '#ffffff' : (theme?.text || '#888') }
        ]}
      >
        Tour
      </Text>
      {showBadge && (
        <View style={[styles.inlineBadge, { backgroundColor: isRunning ? '#ffffff' : (theme?.sendButton || '#3b82f6') }]}>
          <Text style={[styles.inlineBadgeText, { color: isRunning ? (theme?.sendButton || '#3b82f6') : '#ffffff' }]}>
            {progress.completedCount}/{progress.totalSteps}
          </Text>
        </View>
      )}
    </TouchableOpacity>
  );
};

const styles = StyleSheet.create({
  button: {
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 1.5,
    minWidth: 110,
    maxWidth: 140,
    minHeight: 85,
    maxHeight: 85,
    gap: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
    elevation: 2,
  },
  buttonText: {
    fontSize: 12,
    fontWeight: '500',
    textAlign: 'center',
    lineHeight: 16,
  },
  compactButton: {
    padding: 8,
    borderRadius: 6,
    position: 'relative',
  },
  badge: {
    position: 'absolute',
    top: -2,
    right: -2,
    width: 16,
    height: 16,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badgeText: {
    color: '#ffffff',
    fontSize: 10,
    fontWeight: '700',
  },
  inlineBadge: {
    position: 'absolute',
    top: 4,
    right: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 8,
  },
  inlineBadgeText: {
    fontSize: 8,
    fontWeight: '700',
  },
});

export default TourButton;
