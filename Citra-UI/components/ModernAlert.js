// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import React from 'react';
import { 
  Modal, 
  View, 
  Text, 
  TouchableOpacity, 
  StyleSheet,
  Animated,
  Dimensions,
  Platform
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const { width } = Dimensions.get('window');

const ModernAlert = ({
  visible,
  title,
  message,
  buttons = [{ text: 'OK', style: 'default' }],
  onDismiss,
  type = 'info', // 'info', 'warning', 'error', 'success'
  theme
}) => {
  const isDark = theme?.isDark || false;
  
  const colors = {
    background: theme?.background || (isDark ? '#1e293b' : '#ffffff'),
    card: theme?.card || (isDark ? '#334155' : '#f8fafc'),
    text: theme?.text || (isDark ? '#ffffff' : '#1e293b'),
    secondaryText: theme?.textSecondary || (isDark ? '#cbd5e1' : '#64748b'),
    primary: theme?.primary || '#3b82f6',
    destructive: '#ef4444',
    warning: '#f59e0b',
    success: '#10b981',
    overlay: isDark ? 'rgba(0, 0, 0, 0.8)' : 'rgba(0, 0, 0, 0.5)'
  };

  const getIconAndColor = () => {
    switch (type) {
      case 'error':
        return { icon: 'alert-circle', color: colors.destructive };
      case 'warning':
        return { icon: 'warning', color: colors.warning };
      case 'success':
        return { icon: 'checkmark-circle', color: colors.success };
      default:
        return { icon: 'information-circle', color: colors.primary };
    }
  };

  const { icon, color } = getIconAndColor();

  const handleButtonPress = (button) => {
    if (button.onPress) {
      button.onPress();
    }
    if (onDismiss) {
      onDismiss();
    }
  };

  if (!visible) return null;

  return (
    <Modal
      transparent
      visible={visible}
      animationType="fade"
      onRequestClose={onDismiss}
    >
      <View style={[styles.overlay, { backgroundColor: colors.overlay }]}>
        <View style={[styles.alertContainer, { backgroundColor: colors.background }]}>
          {/* Header with icon */}
          <View style={styles.header}>
            <Ionicons 
              name={icon} 
              size={32} 
              color={color}
              style={styles.icon}
            />
            {title && (
              <Text style={[styles.title, { color: colors.text }]}>
                {title}
              </Text>
            )}
          </View>

          {/* Message */}
          {message && (
            <Text style={[styles.message, { color: colors.secondaryText }]}>
              {message}
            </Text>
          )}

          {/* Buttons */}
          <View style={styles.buttonContainer}>
            {buttons.map((button, index) => {
              const isDestructive = button.style === 'destructive';
              const isCancel = button.style === 'cancel';
              const isPrimary = button.style === 'default' || (!isDestructive && !isCancel);
              
              return (
                <TouchableOpacity
                  key={index}
                  style={[
                    styles.button,
                    isPrimary && { backgroundColor: colors.primary },
                    isDestructive && { backgroundColor: colors.destructive },
                    isCancel && { backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.secondaryText },
                    buttons.length > 1 && index < buttons.length - 1 && styles.buttonMargin
                  ]}
                  onPress={() => handleButtonPress(button)}
                  activeOpacity={0.7}
                >
                  <Text style={[
                    styles.buttonText,
                    isPrimary && { color: '#ffffff' },
                    isDestructive && { color: '#ffffff' },
                    isCancel && { color: colors.text }
                  ]}>
                    {button.text}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 20,
  },
  alertContainer: {
    width: Math.min(width - 40, 400),
    borderRadius: 16,
    padding: 24,
    boxShadow: '0 4px 8px rgba(0, 0, 0, 0.3)',
    elevation: 8,
  },
  header: {
    alignItems: 'center',
    marginBottom: 16,
  },
  icon: {
    marginBottom: 8,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    textAlign: 'center',
    lineHeight: 24,
  },
  message: {
    fontSize: 16,
    lineHeight: 22,
    textAlign: 'center',
    marginBottom: 24,
  },
  buttonContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12,
  },
  button: {
    flex: 1,
    paddingVertical: 12,
    paddingHorizontal: 20,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
  },
  buttonMargin: {
    marginRight: 8,
  },
  buttonText: {
    fontSize: 16,
    fontWeight: '600',
    textAlign: 'center',
  },
});

export default ModernAlert;
