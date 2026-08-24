// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// ReasoningCard.js
//
// Renders the model's chain-of-thought / "thinking" tokens, streamed from the
// backend as incremental `reasoning` SSE events. Mirrors PlanCard's collapsible
// bordered card, but coalesces the many small deltas into a single block of
// text (the panel UX from Action Chat's "Reasoning" tab).
//
// Default OPEN while the model is still thinking and no answer text has
// arrived; auto-collapses once the final answer starts streaming so it doesn't
// compete with the answer for attention. Backend gates emission behind
// LLM_EXPOSE_REASONING, so when reasoning is hidden `reasoning` is empty and
// this component renders nothing.

import React, { useEffect, useState, useMemo, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

export const ReasoningCard = ({ reasoning, isStreaming, hasAnswerText, theme }) => {
  const [expanded, setExpanded] = useState(true);
  // Once the user manually toggles, stop auto-collapsing on their behalf.
  const [userToggled, setUserToggled] = useState(false);
  const scrollRef = useRef(null);

  // Auto-collapse when the answer starts (unless the user pinned it open).
  useEffect(() => {
    if (hasAnswerText && !userToggled) {
      setExpanded(false);
    }
  }, [hasAnswerText, userToggled]);

  // Coalesce the incremental deltas into one continuous string.
  const text = useMemo(() => {
    if (!reasoning || reasoning.length === 0) return '';
    return reasoning.map((r) => (typeof r === 'string' ? r : r?.text || '')).join('');
  }, [reasoning]);

  // Follow the tail while the model is still thinking so the user sees the
  // latest tokens, not the start of the trace. Once the answer arrives the
  // card collapses anyway.
  useEffect(() => {
    if (expanded && isStreaming && !hasAnswerText && scrollRef.current) {
      scrollRef.current.scrollToEnd?.({ animated: false });
    }
  }, [text, expanded, isStreaming, hasAnswerText]);

  if (!text) return null;

  const textColor = theme?.secondaryText || (theme?.isDark ? '#bbb' : '#555');
  const muted = theme?.secondaryText || '#666';
  const accent = theme?.primary || '#2F56E9';
  const surface = theme?.isDark ? 'rgba(255,255,255,0.04)' : 'rgba(47,86,233,0.04)';
  const border = theme?.isDark ? 'rgba(255,255,255,0.10)' : 'rgba(47,86,233,0.18)';

  return (
    <View style={[styles.container, { backgroundColor: surface, borderColor: border }]}>
      <TouchableOpacity
        onPress={() => {
          setUserToggled(true);
          setExpanded((v) => !v);
        }}
        style={styles.header}
        accessibilityLabel={expanded ? 'Hide reasoning' : 'Show reasoning'}
      >
        <Ionicons name="bulb-outline" size={14} color={accent} />
        <Text style={[styles.headerText, { color: accent }]}>
          {isStreaming && !hasAnswerText ? 'Reasoning…' : 'Reasoning'}
        </Text>
        <View style={{ flex: 1 }} />
        <Ionicons
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={14}
          color={muted}
        />
      </TouchableOpacity>

      {expanded && (
        <ScrollView
          ref={scrollRef}
          style={styles.body}
          nestedScrollEnabled
          showsVerticalScrollIndicator
        >
          <Text style={[styles.reasoningText, { color: textColor }]} selectable>
            {text}
          </Text>
        </ScrollView>
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
    maxHeight: 220,
  },
  reasoningText: {
    fontSize: 12,
    lineHeight: 18,
    fontStyle: 'italic',
  },
});

export default ReasoningCard;
