// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// MobileHomeScreen.js - Mobile web landing screen with feature cards
// Sections: Productivity (Decision Apps), Explore (Operations Chat), Tools,
// Data Stores. The Create section (Presentation / Report / Printable) is
// hidden — see CREATE_FEATURES below.
// No sidebar, no ribbon, no vault panel

import React from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  Platform,
  useWindowDimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { PERSONAL_VAULT_ENABLED } from '../config/featureFlags';

// Hidden to match HomePanel — Operations Presentation, Operations Visual
// Reports and the MS Word-style Document Creator are gated off the post-login
// home (hiddenOnHome in config/operationsCapabilities.js), and main chat no
// longer launches those composers either: the make_presentation / make_report
// tools were removed from Citra-Service/streaming_response.py, so typing
// "create a presentation" or "write a report" is answered inline instead of
// opening the presentation / printable UI.
//
// This array is now empty, so the "🎨 Create" section header is dropped too
// (the section list is filtered on features.length below). Their handlers
// (onOpenPresentation / onOpenReport / onOpenPrintable) stay wired in
// handlePress so this can be flipped back on by uncommenting.
const CREATE_FEATURES = [
  // {
  //   id: 'presentation',
  //   icon: 'easel',
  //   title: 'Presentations',
  //   subtitle: 'Board-quality slides from your data',
  //   gradient: ['#8B5CF6', '#A855F7'],
  // },
  // {
  //   id: 'report',
  //   icon: 'document-text',
  //   title: 'Document Creator',
  //   subtitle: 'Word-style reports from your systems',
  //   gradient: ['#10B981', '#059669'],
  // },
  // {
  //   id: 'printable',
  //   icon: 'print',
  //   title: 'Visual Reports',
  //   subtitle: 'Live KPI reports & dashboards',
  //   gradient: ['#F59E0B', '#D97706'],
  // },
];

const EXPLORE_FEATURES = [
  {
    id: 'chat',
    icon: 'chatbubbles',
    title: 'Operations Chat',
    subtitle: 'Governed answers across your enterprise',
    gradient: ['#6366F1', '#8B5CF6'],
  },
  // Hidden to match HomePanel — action-chat-service is parked (stopped on prod,
  // build commented out of deploy.yml). Restore alongside the
  // operations-analytics capability when the service returns.
  // {
  //   id: 'actionChat',
  //   icon: 'analytics',
  //   title: 'Operations Analytics',
  //   subtitle: 'Sandboxed deep-research analyst',
  //   gradient: ['#8B5CF6', '#6366F1'],
  // },
  // Hidden for launch focus — Quick Chat removed to match HomePanel; we lead with
  // the two governed chat surfaces (Operations Chat + Operations Analytics). Its
  // handler (onOpenQuickChat) stays wired below so it can be flipped back on.
  // Reader & Review and Diagram (the Visualize Data grab bag) were removed
  // earlier for the same reason.
];

const PRODUCTIVITY_FEATURES = [
  {
    id: 'smartApps',
    icon: 'apps',
    title: 'Self-Improving Decision Apps',
    subtitle: 'UI + agent + dashboards by your BA',
    gradient: ['#8B5CF6', '#EC4899'],
  },
];

const TOOLS_FEATURES = [
  // Hidden: Project Mgmt — generic PM contradicts the Smart App thesis.
  // Substance to be re-shipped as a Smart App template / Citra Operations module.
  // {
  //   id: 'projectManagement',
  //   icon: 'briefcase',
  //   title: 'Project Mgmt',
  //   subtitle: 'Tasks & Milestones',
  //   gradient: ['#6366F1', '#8B5CF6'],
  // },
];

// Personal Data Store cards. Empty while PERSONAL_VAULT_ENABLED is false —
// chat is an enterprise-only search surface (dept MCP + enterprise SOP
// library), so there is nothing personal to create, open, select or upload
// into. Handlers stay wired in App.js; flip the flag to bring the grid back.
const KB_FEATURES = PERSONAL_VAULT_ENABLED ? [
  // Hidden for launch focus — Audio Meeting + Notes removed to match HomePanel.
  // Data Store cards stay: mobile web has no vault panel, so this grid is the
  // only way to manage data stores on mobile.
  {
    id: 'createVault',
    icon: 'add-circle-outline',
    title: 'Create Data Store',
    color: '#10B981',
  },
  {
    id: 'vault',
    icon: 'folder-open-outline',
    title: 'Open Data Store',
    color: '#F59E0B',
  },
  {
    id: 'selectVault',
    icon: 'swap-horizontal-outline',
    title: 'Select Data Store',
    color: '#3B82F6',
  },
  {
    id: 'uploadToVault',
    icon: 'cloud-upload-outline',
    title: 'Upload Files',
    color: '#F59E0B',
  },
] : [];

const MobileFeatureCard = ({ feature, onPress, containerWidth }) => {
  // Use flex: 1 within featureRow for automatic width balancing

  return (
    <TouchableOpacity
      style={styles.card}
      onPress={() => onPress(feature.id)}
      activeOpacity={0.85}
    >
      <LinearGradient
        colors={feature.gradient}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      {/* Glassmorphism overlay */}
      <View style={styles.glassOverlay} />

      <View style={styles.cardContent}>
        <View style={styles.iconContainer}>
          <Ionicons name={feature.icon} size={32} color="#FFFFFF" />
        </View>
        <Text style={styles.cardTitle}>{feature.title}</Text>
        <Text style={styles.cardSubtitle}>{feature.subtitle}</Text>
      </View>
    </TouchableOpacity>
  );
};

const ACCOUNT_FEATURES = [
  {
    id: 'credits',
    icon: 'stats-chart-outline',
    title: 'Tokens',
    color: '#8B5CF6',
  },
  {
    id: 'support',
    icon: 'headset-outline',
    title: 'Support',
    color: '#EF4444',
  },
];

const MobileHomeScreen = ({
  onOpenChat,
  onOpenQuickChat,
  onOpenActionChat,
  onOpenPresentation,
  onOpenReport,
  onOpenPrintable,
  onAudioRecording,
  onOpenNotes,
  onOpenVault,
  onSelectVault,
  onOpenCredits,
  onOpenSupport,
  onOpenDiagram,
  onOpenReader,
  onOpenProjectManagement,
  onOpenPowerApps,
  onCreateVault,
  onUploadToVault,
  theme,
  userEmail,
}) => {
  const { width: screenWidth } = useWindowDimensions();
  const isDark = theme?.isDark ?? false;
  const kbCardWidth = (screenWidth - 52) / 3; // 3 columns, 16px padding on sides, 10px gap

  const handlePress = (featureId) => {
    switch (featureId) {
      case 'chat':
        onOpenChat?.();
        break;
      case 'quickChat':
        onOpenQuickChat?.();
        break;
      case 'actionChat':
        onOpenActionChat?.();
        break;
      case 'presentation':
        onOpenPresentation?.();
        break;
      case 'report':
        onOpenReport?.();
        break;
      case 'printable':
        onOpenPrintable?.();
        break;
      case 'diagram':
        onOpenDiagram?.();
        break;
      case 'reader':
        onOpenReader?.();
        break;
      case 'projectManagement':
        onOpenProjectManagement?.();
        break;
      case 'smartApps':
        onOpenPowerApps?.();
        break;
    }
  };

  const handleKBPress = (featureId) => {
    switch (featureId) {
      case 'audio':
        onAudioRecording?.();
        break;
      case 'notes':
        onOpenNotes?.();
        break;
      case 'createVault':
        onCreateVault?.();
        break;
      case 'vault':
        onOpenVault?.();
        break;
      case 'selectVault':
        onSelectVault?.();
        break;
      case 'uploadToVault':
        onUploadToVault?.();
        break;
    }
  };

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: isDark ? '#0f0f23' : '#F9FAFB' }]}
      contentContainerStyle={styles.scrollContent}
      showsVerticalScrollIndicator={false}
    >
      {/* Welcome Section */}
      <View style={styles.welcomeSection}>
        <Text style={[styles.welcomeSubtitle, { color: isDark ? '#9CA3AF' : '#6B7280' }]}>
          Create, generate, and explore with AI
        </Text>
      </View>

      {/* Feature Cards Grid (Two per row, stacked) - grouped by section.
          Productivity sits at the top because Smart Apps is the BU-built
          surface every officer uses every day — Chat/Create are
          secondary launchpads from here. */}
      {[
        { title: '🚀 Productivity', features: PRODUCTIVITY_FEATURES },
        { title: '🧭 Explore', features: EXPLORE_FEATURES },
        { title: '🎨 Create', features: CREATE_FEATURES },
        { title: '🛠️ Tools', features: TOOLS_FEATURES },
      ].filter((section) => section.features.length > 0).map((section) => (
        <View key={section.title} style={{ marginBottom: 8 }}>
          <Text style={[styles.kbSectionTitle, { color: isDark ? '#D1D5DB' : '#374151' }]}>
            {section.title}
          </Text>
          <View style={styles.grid}>
            {(() => {
              const rows = [];
              for (let i = 0; i < section.features.length; i += 2) {
                rows.push(section.features.slice(i, i + 2));
              }
              return rows.map((row, rowIndex) => (
                <View key={`${section.title}-row-${rowIndex}`} style={styles.featureRow}>
                  {row.map((feature) => (
                    <MobileFeatureCard
                      key={feature.id}
                      feature={feature}
                      onPress={handlePress}
                      containerWidth={screenWidth}
                    />
                  ))}
                  {/* Spacer for odd number of items in a row to maintain grid alignment */}
                  {row.length === 1 && <View style={{ flex: 1, margin: 8 }} />}
                </View>
              ));
            })()}
          </View>
        </View>
      ))}

      {/* Knowledge Base Section — the heading goes with the cards. An empty
          KB_FEATURES must not leave a titled, blank block behind. */}
      {KB_FEATURES.length > 0 && (
      <View style={styles.kbSection}>
        <Text style={[styles.kbSectionTitle, { color: isDark ? '#D1D5DB' : '#374151' }]}>
          Knowledge Base
        </Text>
        <View style={styles.kbGrid}>
          {KB_FEATURES.map((feature) => (
            <TouchableOpacity
              key={feature.id}
              style={[
                styles.kbCard,
                {
                  backgroundColor: isDark ? '#1F2937' : '#FFFFFF',
                  borderColor: isDark ? '#374151' : '#E5E7EB',
                  width: kbCardWidth,
                },
              ]}
              onPress={() => handleKBPress(feature.id)}
              activeOpacity={0.7}
            >
              <View style={[styles.kbIconContainer, { backgroundColor: feature.color + '15' }]}>
                <Ionicons name={feature.icon} size={22} color={feature.color} />
              </View>
              <Text
                style={[styles.kbCardTitle, { color: isDark ? '#E5E7EB' : '#374151' }]}
                numberOfLines={1}
              >
                {feature.title}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>
      )}

      {/* Credit & Support Section */}
      <View style={styles.kbSection}>
        <Text style={[styles.kbSectionTitle, { color: isDark ? '#D1D5DB' : '#374151' }]}>
          Credit & Support
        </Text>
        <View style={styles.kbGrid}>
          {ACCOUNT_FEATURES.map((feature) => (
            <TouchableOpacity
              key={feature.id}
              style={[
                styles.kbCard,
                {
                  backgroundColor: isDark ? '#1F2937' : '#FFFFFF',
                  borderColor: isDark ? '#374151' : '#E5E7EB',
                  width: kbCardWidth,
                },
              ]}
              onPress={() => {
                if (feature.id === 'credits') onOpenCredits?.();
                else if (feature.id === 'support') onOpenSupport?.();
              }}
              activeOpacity={0.7}
            >
              <View style={[styles.kbIconContainer, { backgroundColor: feature.color + '15' }]}>
                <Ionicons name={feature.icon} size={22} color={feature.color} />
              </View>
              <Text
                style={[styles.kbCardTitle, { color: isDark ? '#E5E7EB' : '#374151' }]}
                numberOfLines={1}
              >
                {feature.title}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  scrollContent: {
    padding: 16,
    paddingBottom: 40,
  },
  welcomeSection: {
    alignItems: 'center',
    marginTop: 24,
    marginBottom: 32,
  },
  welcomeEmoji: {
    fontSize: 48,
    marginBottom: 8,
  },
  welcomeTitle: {
    fontSize: 28,
    fontWeight: '700',
    marginBottom: 4,
  },
  welcomeSubtitle: {
    fontSize: 15,
    fontWeight: '400',
  },
  grid: {
    gap: 16,
  },
  featureRow: {
    flexDirection: 'row',
    gap: 16,
    marginBottom: 16,
  },
  card: {
    flex: 1,
    aspectRatio: 1.05,
    borderRadius: 16,
    overflow: 'hidden',
    ...Platform.select({
      web: {
        cursor: 'pointer',
        boxShadow: '0 4px 14px rgba(0,0,0,0.12)',
      },
      default: {
        elevation: 4,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.12,
        shadowRadius: 8,
      },
    }),
  },
  glassOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  cardContent: {
    flex: 1,
    padding: 16,
    justifyContent: 'center',
    alignItems: 'center',
  },
  iconContainer: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: 'rgba(255,255,255,0.2)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.25)',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: '#FFFFFF',
    marginBottom: 4,
    textAlign: 'center',
  },
  cardSubtitle: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.85)',
    textAlign: 'center',
    lineHeight: 16,
  },
  kbSection: {
    marginTop: 28,
  },
  kbSectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 12,
    paddingHorizontal: 4,
  },
  kbGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  kbCard: {
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: 4,
    borderRadius: 12,
    borderWidth: 1,
    ...Platform.select({
      web: {
        cursor: 'pointer',
        boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
      },
      default: {
        elevation: 1,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 1 },
        shadowOpacity: 0.06,
        shadowRadius: 4,
      },
    }),
  },
  kbIconContainer: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 8,
  },
  kbCardTitle: {
    fontSize: 12,
    fontWeight: '600',
    textAlign: 'center',
    lineHeight: 16,
  },
});

export default MobileHomeScreen;
