// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * BuildTopBar — full-width header strip for SmartAppBuilderScreen.
 *
 * Brand mark + goal text + kind badges + session id chip + close X.
 */

import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { QueueModeChip } from '../openclaw/QueueModePicker';
import OpsMark from '../brand/OpsMark';

const BRAND_GRADIENT = ['#1E3A8A', '#2563EB'];   // navy → steel blue

const KIND_BADGE = {
  // A dashboard is no longer a kind — it's an app page. Only 'app' and
  // 'workflow' are real artefacts. 'workflow' surfaces dynamically when the
  // builder authors one (possibly mid-conversation).
  app:      { label: 'App',      icon: 'apps-outline',        gradient: ['#1E3A8A', '#2563EB'] },
  workflow: { label: 'Workflow', icon: 'git-network-outline', gradient: ['#0F766E', '#14B8A6'] },
};

export default function BuildTopBar({
  goal,
  buildKinds = ['app'],
  sessionId,
  onClose,
  colors,
  draft = false,
  // NEW: queue-mode + handler. When provided, render the OpenClaw chip
  // before the session id so the BA can choose how mid-task messages
  // behave (steer / collect / followup / steer-backlog).
  queueMode,
  onPressQueueMode,
  // NEW: stop the live builder session — aborts the in-flight run (OpenClaw
  // chat.abort) so a runaway/looping build stops spending immediately.
  onStop,
  stopping = false,
}) {
  return (
    <View style={[styles.bar, { borderBottomColor: colors.border, backgroundColor: colors.background }]}>
      <OpsMark size={40} radius={10} gradient={BRAND_GRADIENT} style={styles.brand} />

      <View style={styles.titleCol}>
        <View style={styles.titleRow}>
          <Text style={[styles.title, { color: colors.text }]} numberOfLines={1}>
            {draft ? 'New SmartApp' : 'Building your SmartApp'}
          </Text>
          {!draft && buildKinds.map((k) => {
            const tok = KIND_BADGE[k];
            if (!tok) return null;
            return (
              <LinearGradient key={k} colors={tok.gradient} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.kindBadge}>
                <Ionicons name={tok.icon} size={11} color="#FFFFFF" />
                <Text style={styles.kindBadgeText}>{tok.label}</Text>
              </LinearGradient>
            );
          })}
        </View>
        <Text style={[styles.goal, { color: colors.textSecondary }]} numberOfLines={1}>
          {draft
            ? 'Tell me what you’d like to build — I’ll design, implement and ship it.'
            : (goal || 'Designing and shipping your app — chat to guide it.')}
        </Text>
      </View>

      {queueMode && onPressQueueMode ? (
        <QueueModeChip currentMode={queueMode} onPress={onPressQueueMode} />
      ) : null}

      {sessionId ? (
        <View style={[styles.sessionChip, { borderColor: colors.border }]}>
          <Ionicons name="hardware-chip-outline" size={12} color={colors.textSecondary} />
          <Text style={[styles.sessionChipText, { color: colors.textSecondary }]} numberOfLines={1}>
            {sessionId}
          </Text>
        </View>
      ) : null}

      {!draft && sessionId && onStop ? (
        <TouchableOpacity
          onPress={onStop}
          disabled={stopping}
          hitSlop={8}
          style={[styles.stopBtn, { borderColor: '#DC2626', opacity: stopping ? 0.5 : 1 }]}
        >
          <Ionicons name="stop-circle-outline" size={14} color="#DC2626" />
          <Text style={styles.stopBtnText}>{stopping ? 'Stopping…' : 'Stop'}</Text>
        </TouchableOpacity>
      ) : null}

      <TouchableOpacity onPress={onClose} hitSlop={10} style={styles.closeBtn}>
        <Ionicons name="close" size={22} color={colors.text} />
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderBottomWidth: 1,
    minHeight: 64,
  },
  // OpsMark sets its own 40px tile + radius; this only carries the shadow.
  brand: {
    shadowColor: '#1E3A8A',
    shadowOpacity: 0.3,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  titleCol: { flex: 1, minWidth: 0 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  title: { fontSize: 16, fontWeight: '700' },
  goal: { fontSize: 12, marginTop: 2 },
  kindBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999,
  },
  kindBadgeText: { color: '#FFFFFF', fontSize: 10, fontWeight: '700' },
  sessionChip: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 10, paddingVertical: 5, borderWidth: 1, borderRadius: 999,
    maxWidth: 220,
    ...(Platform.OS === 'web' ? { fontFamily: 'monospace' } : {}),
  },
  sessionChipText: { fontSize: 11, fontWeight: '500' },
  stopBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 12, paddingVertical: 6, borderWidth: 1, borderRadius: 999,
  },
  stopBtnText: { color: '#DC2626', fontSize: 12, fontWeight: '700' },
  closeBtn: {
    width: 36, height: 36, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },
});
