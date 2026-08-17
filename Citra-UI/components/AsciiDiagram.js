// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { memo, useState, useCallback } from 'react';
import { View, Text, ScrollView, Platform, TouchableOpacity } from 'react-native';

/**
 * AsciiDiagram Component
 *
 * Lightweight alternative to MermaidDiagram for rendering Unicode box-drawing
 * diagrams (e.g. ┌─┐ │ └─┘ ├ ┬ ┼ plus arrows → ← ↑ ↓) that Claude and other
 * models emit for quick conceptual sketches. Pure text + CSS — no JS runtime,
 * no CDN fetch, adds ~2KB vs. Mermaid's ~1MB lazy bundle.
 *
 * Used for fenced code blocks with language: ascii, diagram, asciiflow,
 * boxdraw, or txt-diagram. Also invoked via auto-detection for unlabeled
 * blocks dense in U+2500-U+257F characters.
 *
 * Critical styling: must preserve exact spacing. No ligatures, no letter-
 * spacing, no line-wrapping — any of these destroy box alignment.
 */
const AsciiDiagram = memo(({ content, theme }) => {
  const code = (content || '').replace(/\s+$/g, '');
  const [copied, setCopied] = useState(false);

  const isDark = theme?.isDark !== false;
  const bg = isDark ? '#1a1a1a' : '#f6f8fa';
  const border = isDark ? '#30363d' : '#d0d7de';
  const headerBg = isDark ? '#262626' : '#f1f3f4';
  const fg = isDark ? '#e6edf3' : '#24292f';
  const subtle = isDark ? '#7d8590' : '#656d76';

  const handleCopy = useCallback(() => {
    try {
      if (Platform.OS === 'web' && typeof navigator !== 'undefined' && navigator.clipboard) {
        navigator.clipboard.writeText(code);
      } else {
        // Lazy require to avoid web bundle bloat
        const Clipboard = require('expo-clipboard');
        if (Clipboard && Clipboard.setStringAsync) Clipboard.setStringAsync(code);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (_) {
      // ignore
    }
  }, [code]);

  if (!code) return null;

  if (Platform.OS === 'web') {
    return (
      <View
        style={{
          marginVertical: 12,
          borderRadius: 8,
          borderWidth: 1,
          borderColor: border,
          backgroundColor: bg,
          overflow: 'hidden',
        }}
      >
        <View
          style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            paddingHorizontal: 12,
            paddingVertical: 6,
            backgroundColor: headerBg,
            borderBottomWidth: 1,
            borderBottomColor: border,
          }}
        >
          <Text
            style={{
              color: subtle,
              fontSize: 11,
              fontWeight: '600',
              textTransform: 'uppercase',
              letterSpacing: 0.5,
            }}
          >
            Diagram
          </Text>
          <TouchableOpacity
            onPress={handleCopy}
            style={{
              paddingHorizontal: 8,
              paddingVertical: 2,
              borderRadius: 4,
              borderWidth: 1,
              borderColor: border,
              backgroundColor: isDark ? '#21262d' : '#ffffff',
            }}
          >
            <Text style={{ color: subtle, fontSize: 11, fontWeight: '600' }}>
              {copied ? 'Copied!' : 'Copy'}
            </Text>
          </TouchableOpacity>
        </View>
        <pre
          className="web-ascii-diagram"
          style={{
            margin: 0,
            padding: 14,
            color: fg,
            // Inline fallbacks in case web.css hasn't loaded yet
            whiteSpace: 'pre',
            overflowX: 'auto',
            fontFamily:
              "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace",
            fontSize: 13,
            lineHeight: 1.25,
            fontVariantLigatures: 'none',
            tabSize: 4,
          }}
        >
          {code}
        </pre>
      </View>
    );
  }

  // Native: horizontal ScrollView preserves alignment; letterSpacing MUST be 0.
  return (
    <View
      style={{
        marginVertical: 12,
        borderRadius: 8,
        borderWidth: 1,
        borderColor: border,
        backgroundColor: bg,
        overflow: 'hidden',
      }}
    >
      <View
        style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'center',
          paddingHorizontal: 12,
          paddingVertical: 8,
          backgroundColor: headerBg,
          borderBottomWidth: 1,
          borderBottomColor: border,
        }}
      >
        <Text
          style={{
            color: subtle,
            fontSize: 12,
            fontWeight: '600',
            textTransform: 'uppercase',
          }}
        >
          Diagram
        </Text>
        <TouchableOpacity
          onPress={handleCopy}
          style={{
            paddingHorizontal: 8,
            paddingVertical: 4,
            borderRadius: 4,
            borderWidth: 1,
            borderColor: border,
            backgroundColor: isDark ? '#21262d' : '#ffffff',
          }}
        >
          <Text style={{ color: subtle, fontSize: 12, fontWeight: '600' }}>
            {copied ? 'Copied!' : 'Copy'}
          </Text>
        </TouchableOpacity>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator>
        <Text
          selectable
          style={{
            padding: 12,
            color: fg,
            fontSize: 13,
            lineHeight: 16,
            letterSpacing: 0,
            fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
          }}
        >
          {code}
        </Text>
      </ScrollView>
    </View>
  );
});

export default AsciiDiagram;
