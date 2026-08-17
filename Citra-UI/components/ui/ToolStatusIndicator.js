// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useRef, useEffect } from 'react';
import { View, Text, Animated, Platform, StyleSheet } from 'react-native';

const TOOL_LABELS = {
  internet_search: 'Searching the internet',
  execute_code: 'Running code',
};

export const ToolStatusIndicator = ({ toolName, theme }) => {
  const dot1 = useRef(new Animated.Value(0)).current;
  const dot2 = useRef(new Animated.Value(0)).current;
  const dot3 = useRef(new Animated.Value(0)).current;
  const shouldUseNativeDriver = Platform.OS !== 'web';

  useEffect(() => {
    const createDotAnimation = (dot, delay) => {
      return Animated.loop(
        Animated.sequence([
          Animated.delay(delay),
          Animated.timing(dot, {
            toValue: 1,
            duration: 400,
            useNativeDriver: shouldUseNativeDriver,
          }),
          Animated.timing(dot, {
            toValue: 0,
            duration: 400,
            useNativeDriver: shouldUseNativeDriver,
          }),
        ])
      );
    };

    const a1 = createDotAnimation(dot1, 0);
    const a2 = createDotAnimation(dot2, 200);
    const a3 = createDotAnimation(dot3, 400);
    a1.start(); a2.start(); a3.start();

    return () => { a1.stop(); a2.stop(); a3.stop(); };
  }, [dot1, dot2, dot3]);

  const label = TOOL_LABELS[toolName] || 'Processing';
  const textColor = theme.secondaryText || theme.botMessageText || '#888';
  const dotColor = theme.primary || '#2F56E9';

  return (
    <View style={indicatorStyles.container}>
      <Text style={[indicatorStyles.label, { color: textColor }]}>{label}</Text>
      <View style={indicatorStyles.dotsContainer}>
        {[dot1, dot2, dot3].map((dot, index) => (
          <Animated.View
            key={index}
            style={[indicatorStyles.dot, { backgroundColor: dotColor, opacity: dot }]}
          />
        ))}
      </View>
    </View>
  );
};

const indicatorStyles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 10,
    paddingVertical: 4,
  },
  label: {
    fontSize: 14,
    fontStyle: 'italic',
    marginRight: 6,
  },
  dotsContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
});

export default ToolStatusIndicator;
