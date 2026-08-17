// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * HistoryItem.js
 * 
 * Enhanced history item component with loading animations and improved timestamps
 */

import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  Animated,
  ActivityIndicator,
  StyleSheet,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { formatDetailedTime, formatShortTime } from '../utils/dateUtils';

const HistoryItem = ({
  item,
  theme,
  onPress,
  onDelete,
  onEdit,
  onView,
  onDownload,
  type = 'chat', // 'chat', 'note', 'transcript', 'document'
  isLoading = false,
  showActions = true,
}) => {
  const [showFullTimestamp, setShowFullTimestamp] = useState(false);
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const scaleAnim = useRef(new Animated.Value(0.95)).current;

  const resolveDisplayName = () => {
    // Use only topic_or_filename field (unified field)
    if (item.topic_or_filename && typeof item.topic_or_filename === 'string' && item.topic_or_filename.trim()) {
      return item.topic_or_filename.trim();
    }

    // Fallback to ID-based name if topic_or_filename is not available
    if (item.id || item.document_id) {
      return `Item ${String(item.id || item.document_id).slice(0, 8)}`;
    }

    // Final fallback based on type
    return type === 'note' ? 'Untitled Note' : type === 'transcript' ? 'Untitled Transcript' : 'Untitled Item';
  };

  useEffect(() => {
    // Entrance animation
    Animated.parallel([
      Animated.timing(fadeAnim, {
        toValue: 1,
        duration: 300,
        useNativeDriver: true,
      }),
      Animated.timing(scaleAnim, {
        toValue: 1,
        duration: 300,
        useNativeDriver: true,
      }),
    ]).start();
  }, []);

  const getIcon = () => {
    switch (type) {
      case 'chat':
        return 'chatbubble-ellipses';
      case 'note':
        return 'document-text';
      case 'transcript':
        return 'mic';
      case 'document':
        return 'document';
      default:
        return 'chatbubble-ellipses';
    }
  };

  const getTitle = () => {
    switch (type) {
      case 'chat':
        return item.title || resolveDisplayName;
      case 'note':
        return resolveDisplayName;
      case 'transcript':
        return resolveDisplayName;
      case 'document':
        return resolveDisplayName;
      default:
        return resolveDisplayName;
    }
  };

  const getSubtitle = () => {
    switch (type) {
      case 'chat':
        return item.summary;
      case 'note':
        return item.text?.substring(0, 100) + (item.text?.length > 100 ? '...' : '');
      case 'transcript':
        return item.duration ? `Duration: ${item.duration}` : 'Audio transcript';
      case 'document':
        return item.fileType || 'Document file';
      default:
        return null;
    }
  };

  const handlePress = () => {
    if (onPress) {
      onPress(item);
    }
  };

  const handleTimestampPress = () => {
    setShowFullTimestamp(!showFullTimestamp);
  };

  if (isLoading) {
    return (
      <View style={[styles.container, { backgroundColor: theme.card, borderColor: theme.border }]}>
        <View style={styles.loadingContent}>
          <ActivityIndicator size="small" color={theme.accent} />
          <Text style={[styles.loadingText, { color: theme.textSecondary }]}>Loading...</Text>
        </View>
      </View>
    );
  }

  return (
    <Animated.View
      style={[
        styles.container,
        {
          backgroundColor: theme.card,
          borderColor: theme.border,
          opacity: fadeAnim,
          transform: [{ scale: scaleAnim }],
        },
      ]}
    >
      <TouchableOpacity
        style={styles.content}
        onPress={handlePress}
        activeOpacity={0.7}
      >
        <View style={styles.iconContainer}>
          <View style={[styles.iconCircle, { backgroundColor: theme.accent + '20' }]}>
            <Ionicons
              name={getIcon()}
              size={20}
              color={theme.accent}
            />
          </View>
        </View>

        <View style={styles.textContainer}>
          <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>
            {getTitle()}
          </Text>
          
          {getSubtitle() && (
            <Text style={[styles.subtitle, { color: theme.textSecondary }]} numberOfLines={2}>
              {getSubtitle()}
            </Text>
          )}

          <TouchableOpacity onPress={handleTimestampPress} style={styles.timestampContainer}>
            <Ionicons name="time-outline" size={12} color={theme.textTertiary} />
            <Text style={[styles.timestamp, { color: theme.textTertiary }]}>
              {showFullTimestamp ? formatDetailedTime(item.timestamp) : formatShortTime(item.timestamp)}
            </Text>
          </TouchableOpacity>
        </View>

        {showActions && (
          <View style={styles.actionsContainer}>
            {onView && (
              <TouchableOpacity
                style={[styles.actionButton, { backgroundColor: theme.primary + '15' }]}
                onPress={() => onView(item)}
              >
                <Ionicons name="eye" size={16} color={theme.primary} />
              </TouchableOpacity>
            )}
            
            {onEdit && (
              <TouchableOpacity
                style={[styles.actionButton, { backgroundColor: theme.warning + '15' }]}
                onPress={() => onEdit(item)}
              >
                <Ionicons name="pencil" size={16} color={theme.warning} />
              </TouchableOpacity>
            )}
            
            {onDownload && (
              <TouchableOpacity
                style={[styles.actionButton, { backgroundColor: theme.success + '15' }]}
                onPress={() => onDownload(item)}
              >
                <Ionicons name="download" size={16} color={theme.success} />
              </TouchableOpacity>
            )}
            
            {onDelete && (
              <TouchableOpacity
                style={[styles.actionButton, { backgroundColor: theme.error + '15' }]}
                onPress={() => onDelete(item.id)}
              >
                <Ionicons name="trash" size={16} color={theme.error} />
              </TouchableOpacity>
            )}
          </View>
        )}
      </TouchableOpacity>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  container: {
    marginHorizontal: 16,
    marginVertical: 6,
    borderRadius: 12,
    borderWidth: 1,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  content: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    padding: 16,
  },
  loadingContent: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
  },
  iconContainer: {
    marginRight: 12,
  },
  iconCircle: {
    width: 40,
    height: 40,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  textContainer: {
    flex: 1,
    minHeight: 40,
    justifyContent: 'center',
  },
  title: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 4,
    lineHeight: 20,
  },
  subtitle: {
    fontSize: 14,
    lineHeight: 18,
    marginBottom: 6,
  },
  timestampContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 2,
  },
  timestamp: {
    fontSize: 12,
    fontWeight: '500',
  },
  actionsContainer: {
    flexDirection: 'row',
    gap: 8,
    marginLeft: 8,
  },
  actionButton: {
    width: 32,
    height: 32,
    borderRadius: 16,
    justifyContent: 'center',
    alignItems: 'center',
  },
});

export default HistoryItem;
