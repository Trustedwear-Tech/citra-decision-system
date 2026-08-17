// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// PlanCard.js
//
// Renders a `<plan>...</plan>` block emitted by the model as a structured
// `plan` SSE event. The plan text is shown in a collapsible bordered card
// above the streaming answer. Default open while the message is still
// streaming and no answer text has arrived yet; auto-collapses once the
// final answer starts so it doesn't compete for attention.

import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

export const PlanCard = ({ plans, isStreaming, hasAnswerText, theme }) => {
  const [expanded, setExpanded] = useState(true);

  // Auto-collapse once the assistant has started producing visible answer
  // text (the plan has served its purpose). Stay expanded until then.
  useEffect(() => {
    if (hasAnswerText) {
      setExpanded(false);
    }
  }, [hasAnswerText]);

  if (!plans || plans.length === 0) return null;

  const textColor = theme?.botMessageText || '#222';
  const muted = theme?.secondaryText || '#666';
  const accent = theme?.primary || '#2F56E9';
  const surface = theme?.isDark ? 'rgba(255,255,255,0.04)' : 'rgba(47,86,233,0.05)';
  const border = theme?.isDark ? 'rgba(255,255,255,0.10)' : 'rgba(47,86,233,0.20)';

  // If multiple plans were emitted (rare; e.g. multi-tool round), stack
  // them. Most queries will have a single plan.
  return (
    <View style={[styles.container, { backgroundColor: surface, borderColor: border }]}>
      <TouchableOpacity
        onPress={() => setExpanded((v) => !v)}
        style={styles.header}
        accessibilityLabel={expanded ? 'Hide plan' : 'Show plan'}
      >
        <Ionicons name="list-circle-outline" size={14} color={accent} />
        <Text style={[styles.headerText, { color: accent }]}>
          {plans.length === 1 ? 'Plan' : `Plan (${plans.length})`}
        </Text>
        <View style={{ flex: 1 }} />
        <Ionicons
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={14}
          color={muted}
        />
      </TouchableOpacity>

      {expanded && (
        <View style={styles.body}>
          {plans.map((p, idx) => (
            <Text
              key={`${p.round}-${p.ts}-${idx}`}
              style={[styles.planText, { color: textColor }]}
              selectable
            >
              {p.text}
            </Text>
          ))}
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    marginVertical: 6,
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  headerText: {
    fontSize: 12,
    fontWeight: '600',
  },
  body: {
    marginTop: 6,
    paddingTop: 6,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: 'rgba(0,0,0,0.10)',
  },
  planText: {
    fontSize: 12,
    lineHeight: 18,
    fontFamily: undefined, // use system; backend usually emits plain prose
    marginBottom: 4,
  },
});

export default PlanCard;
