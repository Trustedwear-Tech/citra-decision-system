// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * OpsMark.js — the Decision-Apps brand mark ("Ops Core").
 *
 * A single source of truth for the operations-themed glyph used across the
 * Smart-App / Decision-Apps surfaces (library rail + header + empty state,
 * builder top bar, builder chat empty state). Replaces the old generic
 * `sparkles` Ionicon so the product reads as operations tooling, not a
 * generic "AI" toy.
 *
 * The glyph: a rounded hexagon shell (operations / industrial) enclosing a
 * decision node that branches to two outcomes (approve / reject) — the core
 * loop every Decision App runs.
 *
 * Rendered as an SVG glyph centred inside a gradient tile (expo-linear-gradient,
 * matching every other tile in the app). Pass `bare` to render just the glyph
 * on a caller-supplied background.
 */

import React from 'react';
import { View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Svg, { Path, Circle } from 'react-native-svg';

// Steel-navy brand gradient — the corporate default. Callers may override.
export const OPS_MARK_GRADIENT = ['#1E3A8A', '#2563EB']; // navy → blue

// The glyph itself, drawn in a 40×40 viewBox so stroke weights scale with size.
function OpsGlyph({ size = 22, color = '#FFFFFF' }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 40 40" fill="none">
      {/* hexagon shell */}
      <Path
        d="M20 4 L32.1 11 V25 L20 32 L7.9 25 V11 Z"
        stroke={color}
        strokeWidth={2.1}
        strokeLinejoin="round"
        opacity={0.55}
      />
      {/* central decision node */}
      <Circle cx={20} cy={18} r={3.3} fill={color} />
      {/* branches to two outcomes */}
      <Path
        d="M20 21.3 V26 M20 26 L14 29.5 M20 26 L26 29.5"
        stroke={color}
        strokeWidth={2.1}
        strokeLinecap="round"
      />
      <Circle cx={14} cy={29.5} r={1.9} fill={color} />
      <Circle cx={26} cy={29.5} r={1.9} fill={color} />
    </Svg>
  );
}

/**
 * @param {number}   size      Tile edge (px). Default 36.
 * @param {number}   radius    Tile corner radius. Default size * 0.28 (soft square).
 * @param {string[]} gradient  2-stop gradient for the tile.
 * @param {string}   glyphColor Glyph stroke/fill colour.
 * @param {boolean}  bare      Render only the glyph (no tile). `size` becomes the glyph box.
 * @param {object}   style     Extra tile style (e.g. shadow).
 */
export default function OpsMark({
  size = 36,
  radius,
  gradient = OPS_MARK_GRADIENT,
  glyphColor = '#FFFFFF',
  bare = false,
  style,
}) {
  const glyphSize = Math.round(size * (bare ? 1 : 0.56));
  if (bare) {
    return (
      <View style={[{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }, style]}>
        <OpsGlyph size={glyphSize} color={glyphColor} />
      </View>
    );
  }
  return (
    <LinearGradient
      colors={gradient}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[
        {
          width: size,
          height: size,
          borderRadius: radius ?? Math.round(size * 0.28),
          alignItems: 'center',
          justifyContent: 'center',
        },
        style,
      ]}
    >
      <OpsGlyph size={glyphSize} color={glyphColor} />
    </LinearGradient>
  );
}
