// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// BuildKindPickerModal.js
//
// Shown the moment a BA clicks "Build new", BEFORE the conversational builder
// opens. Its whole job is to make the OFFERING explicit — the BA is never
// locked into "an app with UI". They pick a surface up front:
//
//   • App             — working UI screens your team opens and works in
//   • Embedded card   — the decision card rendered INSIDE a system you already have
//   • API             — headless: no UI, exposed as /run + /approve for your own front-end
//   • Dashboard       — a live KPI / chart page
//   • App + Dashboard — working screens plus a dashboard page
//   • Let's talk it through — the agent asks and decides (the old chat-first default)
//
// Embedded sits between App and API on purpose. It is the middle answer to
// "integrate with our system": with API the customer's developers rebuild the
// reason-capture UI themselves, and that is the first thing cut under deadline —
// when it goes, the officer's *why* is never recorded and the app never learns.
//
// The pick only SEEDS the build (headless flag + primary page kind). It is not
// a lock: the builder agent can still change the surface mid-conversation, and
// the modal copy says so. Every artefact is still stored as kind='app' — these
// are surface choices, not separate product kinds.
//
// onPick(choice) receives:
//   { id, buildKinds:['app'], headless:boolean, primaryPageKind:'standard'|'dashboard'|null }
// The 'conversational' choice returns headless=false + primaryPageKind=null so
// nothing is pre-seeded and the first chat turn drives everything.

import React from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, ScrollView, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';

// Each option maps a tile to the real build levers. Keep the gradients in step
// with KIND_TOKENS in PowerAppsScreen so the surface a BA picks here reads the
// same in the list afterwards (navy=app, emerald=api, indigo=dashboard).
const OPTIONS = [
  {
    id: 'app',
    label: 'App',
    tagline: 'UI screens your team opens and works in — queues, forms, detail views.',
    icon: 'apps-outline',
    gradient: ['#1E3A8A', '#2563EB'],
    buildKinds: ['app'],
    headless: false,
    primaryPageKind: 'standard',
  },
  {
    id: 'embed',
    label: 'Embedded card',
    tagline: 'The recommendation and approve/reject rendered inside a screen you already have — one script tag, your officers never leave your own system.',
    icon: 'browsers-outline',
    gradient: ['#0F766E', '#14B8A6'],
    buildKinds: ['app'],
    headless: false,
    primaryPageKind: 'embed',
  },
  {
    id: 'api',
    label: 'API',
    tagline: 'Headless — no UI. The decision engine as an API (/run + /approve) you plug into your own front-end.',
    icon: 'code-slash-outline',
    gradient: ['#065F46', '#059669'],
    buildKinds: ['app'],
    headless: true,
    primaryPageKind: null,
  },
  {
    id: 'dashboard',
    label: 'Dashboard',
    tagline: 'A live KPI / chart view — the executive read on the decisions.',
    icon: 'bar-chart-outline',
    gradient: ['#4338CA', '#6366F1'],
    buildKinds: ['app'],
    headless: false,
    primaryPageKind: 'dashboard',
  },
  {
    id: 'app_dashboard',
    label: 'App + Dashboard',
    tagline: 'Working screens AND a dashboard page — the operator view plus the manager read, in one app.',
    icon: 'grid-outline',
    gradient: ['#1E3A8A', '#6366F1'],
    buildKinds: ['app'],
    headless: false,
    primaryPageKind: 'dashboard',
  },
  {
    id: 'conversational',
    label: "Let's talk it through",
    tagline: "Not sure yet? Describe the decision and the agent recommends the right surface — you choose during the build.",
    icon: 'chatbubbles-outline',
    gradient: ['#7C3AED', '#8B5CF6'],
    buildKinds: ['app'],
    headless: false,
    primaryPageKind: null,
  },
];

export default function BuildKindPickerModal({ visible, theme = {}, onPick, onClose }) {
  const colors = {
    background: theme.background || '#FFFFFF',
    surface: theme.surface || '#F9FAFB',
    text: theme.text || '#111827',
    textSecondary: theme.textSecondary || '#6B7280',
    border: theme.border || '#E5E7EB',
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.overlay}>
        <View style={[styles.card, { backgroundColor: colors.background, borderColor: colors.border }]}>
          <View style={styles.headerRow}>
            <View style={{ flex: 1 }}>
              <Text style={[styles.title, { color: colors.text }]}>What do you want to build?</Text>
              <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
                Pick a surface to start with — you can change it while you build.
              </Text>
            </View>
            <TouchableOpacity onPress={onClose} hitSlop={10} style={styles.closeBtn}>
              <Ionicons name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          </View>

          <ScrollView
            contentContainerStyle={styles.tileGrid}
            showsVerticalScrollIndicator={false}
          >
            {OPTIONS.map((opt) => (
              <TouchableOpacity
                key={opt.id}
                style={[styles.tile, { borderColor: colors.border, backgroundColor: colors.surface }]}
                activeOpacity={0.85}
                onPress={() => onPick?.(opt)}
              >
                <LinearGradient
                  colors={opt.gradient}
                  start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                  style={styles.tileIcon}
                >
                  <Ionicons name={opt.icon} size={22} color="#FFFFFF" />
                </LinearGradient>
                <Text style={[styles.tileLabel, { color: colors.text }]}>{opt.label}</Text>
                <Text style={[styles.tileTagline, { color: colors.textSecondary }]}>{opt.tagline}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  card: {
    width: '100%',
    maxWidth: 760,
    maxHeight: '90%',
    borderRadius: 20,
    borderWidth: 1,
    padding: 24,
    ...Platform.select({
      web: { boxShadow: '0 20px 60px rgba(0,0,0,0.3)' },
      default: {
        elevation: 10,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 10 },
        shadowOpacity: 0.3,
        shadowRadius: 20,
      },
    }),
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 20,
  },
  title: {
    fontSize: 20,
    fontWeight: '800',
    letterSpacing: -0.2,
  },
  subtitle: {
    fontSize: 13,
    marginTop: 4,
    lineHeight: 18,
  },
  closeBtn: {
    padding: 4,
    marginLeft: 12,
  },
  tileGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 14,
  },
  tile: {
    flexGrow: 1,
    flexBasis: 220,
    minWidth: 200,
    maxWidth: 340,
    borderRadius: 16,
    borderWidth: 1,
    padding: 18,
    ...Platform.select({
      web: {
        cursor: 'pointer',
        transition: 'transform 0.15s ease, box-shadow 0.2s ease',
        boxShadow: '0 2px 10px rgba(0,0,0,0.05)',
      },
    }),
  },
  tileIcon: {
    width: 48,
    height: 48,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  tileLabel: {
    fontSize: 15,
    fontWeight: '700',
    marginBottom: 6,
  },
  tileTagline: {
    fontSize: 12.5,
    lineHeight: 18,
  },
});
