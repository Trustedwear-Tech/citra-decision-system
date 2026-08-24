// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  Animated,
  Platform,
  Easing,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { modernStyles, SPACING, BORDER_RADIUS } from '../../styles/modernStyles';

export const ModernThemeToggle = ({ isDarkMode, onToggle, theme }) => {
  const translateAnim = useRef(new Animated.Value(isDarkMode ? 1 : 0)).current;
  const scaleAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    Animated.spring(translateAnim, {
      toValue: isDarkMode ? 1 : 0,
      useNativeDriver: Platform.OS !== 'web',
      tension: 120,
      friction: 7,
    }).start();
  }, [isDarkMode, translateAnim]);

  const handlePress = () => {
    // Add a subtle press animation
    Animated.sequence([
      Animated.timing(scaleAnim, {
        toValue: 0.95,
        duration: 100,
        useNativeDriver: Platform.OS !== 'web',
      }),
      Animated.timing(scaleAnim, {
        toValue: 1,
        duration: 100,
        useNativeDriver: Platform.OS !== 'web',
      }),
    ]).start();
    
    onToggle();
  };

  const thumbTranslateX = translateAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [0, 28],
  });

  return (
    <Animated.View style={{ transform: [{ scale: scaleAnim }] }}>
      <TouchableOpacity
        onPress={handlePress}
        style={[
          modernStyles.modernThemeToggle,
          {
            backgroundColor: isDarkMode ? theme.primary : theme.border,
            borderColor: isDarkMode ? theme.primary : theme.border,
            borderWidth: 1,
          }
        ]}
        activeOpacity={0.8}
        accessibilityRole="switch"
        accessibilityState={{ checked: isDarkMode }}
        accessibilityLabel={`Switch to ${isDarkMode ? 'light' : 'dark'} theme`}
      >
        <Animated.View
          style={[
            modernStyles.modernThemeToggleThumb,
            {
              backgroundColor: theme.background,
              transform: [{ translateX: thumbTranslateX }],
            }
          ]}
        >
          <Ionicons
            name={isDarkMode ? 'moon' : 'sunny'}
            size={16}
            color={isDarkMode ? theme.primary : theme.warning}
          />
        </Animated.View>
      </TouchableOpacity>
    </Animated.View>
  );
};

export const ModernButton = ({ 
  title, 
  onPress, 
  variant = 'primary', 
  size = 'medium',
  icon,
  disabled = false,
  loading = false,
  theme,
  style,
  ...props 
}) => {
  const scaleAnim = useRef(new Animated.Value(1)).current;

  const handlePressIn = () => {
    Animated.timing(scaleAnim, {
      toValue: 0.98,
      duration: 100,
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  };

  const handlePressOut = () => {
    Animated.timing(scaleAnim, {
      toValue: 1,
      duration: 100,
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  };

  const getButtonStyle = () => {
    const baseStyle = {
      ...modernStyles.modernButton,
      opacity: disabled ? 0.6 : 1,
    };

    if (size === 'small') {
      baseStyle.height = 36;
      baseStyle.paddingHorizontal = SPACING.md;
    } else if (size === 'large') {
      baseStyle.height = 56;
      baseStyle.paddingHorizontal = SPACING.xl;
    }

    switch (variant) {
      case 'primary':
        return {
          ...baseStyle,
          backgroundColor: theme.primary,
        };
      case 'secondary':
        return {
          ...baseStyle,
          backgroundColor: 'transparent',
          borderWidth: 2,
          borderColor: theme.primary,
        };
      case 'ghost':
        return {
          ...baseStyle,
          backgroundColor: 'transparent',
        };
      default:
        return {
          ...baseStyle,
          backgroundColor: theme.primary,
        };
    }
  };

  const getTextStyle = () => {
    const baseStyle = modernStyles.modernButtonText;
    
    switch (variant) {
      case 'primary':
        return {
          ...baseStyle,
          color: theme.textOnPrimary,
        };
      case 'secondary':
        return {
          ...baseStyle,
          color: theme.primary,
        };
      case 'ghost':
        return {
          ...baseStyle,
          color: theme.text,
        };
      default:
        return {
          ...baseStyle,
          color: theme.textOnPrimary,
        };
    }
  };

  return (
    <Animated.View style={{ transform: [{ scale: scaleAnim }] }}>
      <TouchableOpacity
        onPress={onPress}
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        disabled={disabled || loading}
        style={[getButtonStyle(), style]}
        activeOpacity={0.8}
        {...props}
      >
        {loading ? (
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Ionicons name="refresh" size={20} color={getTextStyle().color} />
            <Text style={[getTextStyle(), { marginLeft: SPACING.sm }]}>Loading...</Text>
          </View>
        ) : (
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            {icon && (
              <Ionicons 
                name={icon} 
                size={20} 
                color={getTextStyle().color}
                style={modernStyles.modernButtonIcon}
              />
            )}
            <Text style={getTextStyle()}>{title}</Text>
          </View>
        )}
      </TouchableOpacity>
    </Animated.View>
  );
};

export const ModernCard = ({ 
  title, 
  children, 
  actions,
  theme, 
  style,
  ...props 
}) => {
  return (
    <View
      style={[
        modernStyles.modernCard,
        {
          backgroundColor: theme.card,
          borderColor: theme.border,
          borderWidth: 1,
        },
        style
      ]}
      {...props}
    >
      {title && (
        <View style={modernStyles.modernCardHeader}>
          <Text style={[modernStyles.modernCardTitle, { color: theme.text }]}>
            {title}
          </Text>
        </View>
      )}
      <View style={modernStyles.modernCardContent}>
        {children}
      </View>
      {actions && (
        <View style={modernStyles.modernCardActions}>
          {actions}
        </View>
      )}
    </View>
  );
};

export const ModernToggleSwitch = ({ 
  value, 
  onValueChange, 
  label,
  theme,
  disabled = false,
  ...props 
}) => {
  const translateAnim = useRef(new Animated.Value(value ? 1 : 0)).current;
  const backgroundColorAnim = useRef(new Animated.Value(value ? 1 : 0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.spring(translateAnim, {
        toValue: value ? 1 : 0,
        useNativeDriver: Platform.OS !== 'web',
        tension: 120,
        friction: 7,
      }),
      Animated.timing(backgroundColorAnim, {
        toValue: value ? 1 : 0,
        duration: 200,
        useNativeDriver: false,
      }),
    ]).start();
  }, [value, translateAnim, backgroundColorAnim]);

  const handlePress = () => {
    if (!disabled) {
      onValueChange(!value);
    }
  };

  const thumbTranslateX = translateAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [2, 22],
  });

  const backgroundColor = backgroundColorAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [theme.disabled, theme.primary],
  });

  return (
    <TouchableOpacity
      onPress={handlePress}
      disabled={disabled}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        opacity: disabled ? 0.6 : 1,
      }}
      activeOpacity={0.8}
      {...props}
    >
      <Animated.View
        style={{
          width: 44,
          height: 24,
          borderRadius: 12,
          backgroundColor,
          justifyContent: 'center',
          marginRight: label ? SPACING.sm : 0,
        }}
      >
        <Animated.View
          style={{
            width: 20,
            height: 20,
            borderRadius: 10,
            backgroundColor: theme.background,
            transform: [{ translateX: thumbTranslateX }],
            shadowColor: '#000',
            shadowOffset: { width: 0, height: 1 },
            shadowOpacity: 0.2,
            shadowRadius: 2,
            elevation: 2,
          }}
        />
      </Animated.View>
      {label && (
        <Text style={[modernStyles.modernToggleText, { color: theme.text }]}>
          {label}
        </Text>
      )}
    </TouchableOpacity>
  );
};

export const ModernIconButton = ({ 
  icon, 
  onPress, 
  size = 44,
  variant = 'ghost',
  theme,
  disabled = false,
  style,
  ...props 
}) => {
  const scaleAnim = useRef(new Animated.Value(1)).current;

  const handlePressIn = () => {
    Animated.timing(scaleAnim, {
      toValue: 0.95,
      duration: 100,
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  };

  const handlePressOut = () => {
    Animated.timing(scaleAnim, {
      toValue: 1,
      duration: 100,
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  };

  const getButtonStyle = () => {
    const baseStyle = {
      width: size,
      height: size,
      borderRadius: size / 2,
      justifyContent: 'center',
      alignItems: 'center',
      opacity: disabled ? 0.6 : 1,
    };

    switch (variant) {
      case 'primary':
        return {
          ...baseStyle,
          backgroundColor: theme.primary,
        };
      case 'secondary':
        return {
          ...baseStyle,
          backgroundColor: theme.surface,
          borderWidth: 1,
          borderColor: theme.border,
        };
      case 'ghost':
      default:
        return {
          ...baseStyle,
          backgroundColor: 'transparent',
        };
    }
  };

  const getIconColor = () => {
    switch (variant) {
      case 'primary':
        return theme.textOnPrimary;
      case 'secondary':
        return theme.text;
      case 'ghost':
      default:
        return theme.text;
    }
  };

  return (
    <Animated.View style={{ transform: [{ scale: scaleAnim }] }}>
      <TouchableOpacity
        onPress={onPress}
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        disabled={disabled}
        style={[getButtonStyle(), style]}
        activeOpacity={0.8}
        {...props}
      >
        <Ionicons 
          name={icon} 
          size={size * 0.5} 
          color={getIconColor()} 
        />
      </TouchableOpacity>
    </Animated.View>
  );
};

export const ModernLoadingSpinner = ({ size = 24, color, theme }) => {
  const rotateAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const rotate = Animated.loop(
      Animated.timing(rotateAnim, {
        toValue: 1,
        duration: 1000,
        easing: Easing.linear,
        useNativeDriver: Platform.OS !== 'web',
      })
    );
    rotate.start();

    return () => rotate.stop();
  }, [rotateAnim]);

  const rotateInterpolate = rotateAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '360deg'],
  });

  return (
    <Animated.View
      style={{
        width: size,
        height: size,
        transform: [{ rotate: rotateInterpolate }],
      }}
    >
      <Ionicons 
        name="refresh" 
        size={size} 
        color={color || theme.primary} 
      />
    </Animated.View>
  );
};

export default {
  ModernThemeToggle,
  ModernButton,
  ModernCard,
  ModernToggleSwitch,
  ModernIconButton,
  ModernLoadingSpinner,
};
