// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * QueueModePicker — shared modal for choosing the OpenClaw queue mode.
 * Consumed by both ActionChatScreen and SmartAppBuilderScreen so the
 * UX (labels, descriptions, layout) cannot drift between surfaces.
 *
 * Props:
 *   visible: boolean
 *   currentMode: one of QueueMode values
 *   onSelect: (mode) => void   (called with the chosen mode; closes itself)
 *   onClose: () => void
 *
 * The picker does NOT call any service. The parent screen is responsible
 * for forwarding the chosen mode to the right backend (action-chat or
 * smart-app builder) — both ultimately route through OpenClaw chat.send.
 */
import React from 'react';
import {
  Modal,
  TouchableOpacity,
  View,
  Text,
  StyleSheet,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import {
  QueueMode,
  QUEUE_MODE_LABELS,
  QUEUE_MODE_SHORT,
  QUEUE_MODE_DESCRIPTIONS,
} from '../../services/openClawShared';

export default function QueueModePicker({
  visible,
  currentMode,
  onSelect,
  onClose,
}) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <TouchableOpacity
        style={styles.backdrop}
        activeOpacity={1}
        onPress={onClose}
      >
        <TouchableOpacity
          activeOpacity={1}
          style={styles.card}
          onPress={(e) => e.stopPropagation?.()}
        >
          <Text style={styles.title}>How should mid-task messages behave?</Text>
          <Text style={styles.subtitle}>
            When you type while the agent is working, the Citra AI agent
            decides what to do based on this setting.
          </Text>
          {Object.values(QueueMode).map((mode) => {
            const selected = mode === currentMode;
            return (
              <TouchableOpacity
                key={mode}
                style={[styles.row, selected && styles.rowSelected]}
                onPress={() => onSelect(mode)}
              >
                <View style={styles.rowHeader}>
                  <Ionicons
                    name={selected ? 'radio-button-on' : 'radio-button-off'}
                    size={16}
                    color={selected ? '#8B5CF6' : '#9CA3AF'}
                  />
                  <Text style={[styles.label, selected && styles.labelSelected]}>
                    {QUEUE_MODE_LABELS[mode]}
                  </Text>
                </View>
                <Text style={styles.description}>
                  {QUEUE_MODE_DESCRIPTIONS[mode]}
                </Text>
              </TouchableOpacity>
            );
          })}
          <TouchableOpacity style={styles.dismiss} onPress={onClose}>
            <Text style={styles.dismissText}>Close</Text>
          </TouchableOpacity>
        </TouchableOpacity>
      </TouchableOpacity>
    </Modal>
  );
}

/**
 * Compact chip that shows the current queue mode and opens the picker.
 * Use anywhere a header has spare room.
 */
export function QueueModeChip({ currentMode, onPress, style }) {
  const short = QUEUE_MODE_SHORT[currentMode] || QUEUE_MODE_LABELS[currentMode] || currentMode;
  return (
    <TouchableOpacity
      onPress={onPress}
      style={[styles.chip, style]}
      accessibilityLabel={`mid-task message behavior: ${short}`}
    >
      <Ionicons name="git-branch-outline" size={12} color="#6D28D9" />
      <Text style={styles.chipText} numberOfLines={1}>
        Mid-task: {short}
      </Text>
      <Ionicons name="chevron-down" size={11} color="#6D28D9" />
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
  },
  card: {
    width: '100%',
    maxWidth: 480,
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 18,
  },
  title: {
    fontSize: 16,
    fontWeight: '700',
    color: '#111827',
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 12,
    color: '#6B7280',
    marginBottom: 14,
    lineHeight: 17,
  },
  row: {
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 8,
  },
  rowSelected: { borderColor: '#8B5CF6', backgroundColor: '#FAF5FF' },
  rowHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  label: { fontSize: 13, fontWeight: '600', color: '#374151' },
  labelSelected: { color: '#6D28D9' },
  description: {
    fontSize: 11,
    color: '#6B7280',
    lineHeight: 16,
    paddingLeft: 22,
  },
  dismiss: {
    alignSelf: 'flex-end',
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginTop: 4,
  },
  dismissText: { fontSize: 12, fontWeight: '600', color: '#6B7280' },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: '#F3E8FF',
    maxWidth: 220,
  },
  chipText: { fontSize: 11, fontWeight: '600', color: '#6D28D9' },
});
