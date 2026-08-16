// LandingScreen.js
//
// The OSS build's landing page, standing in for IntroScreen.js — the
// citra-ai.com marketing site — which stays in the private repo only (see
// docs/open-source-release-plan.md §7.4). IntroScreen is 5,766 LOC of
// commercial content: real third-party endorsement names, usage counters
// that would be false on a fresh install, and externally-hosted assets.
// None of that belongs in a source-available release.
//
// Land -> sign in -> the product opens (HomePanel for the Decision System).
// No pricing, no trial, no contact form, no external hosts — see §7.4.2.
//
// This file ships from the private repo unused (Citra-AI's own MainApp.js
// keeps rendering IntroScreen); only the OSS generator swaps it in, patching
// MainApp.js's INTRO route to render this instead.

import React from 'react';
import { View, Text, TouchableOpacity, ScrollView, Platform, Linking } from 'react-native';

const CAPABILITIES = [
  {
    title: 'Chat over your own sources',
    body: 'Ask questions and get answers grounded in your department’s SOPs and enterprise data, connected through MCP — not a generic model guess.',
  },
  {
    title: 'Cited, precedent-backed recommendations',
    body: 'Every recommendation links back to the SOP passage or prior decision that produced it, so an approver can check the reasoning, not just the answer.',
  },
  {
    title: 'Build your own decision apps',
    body: 'The SmartApp builder turns a recurring approval or triage workflow into a governed, auditable app — no separate workflow engine required.',
  },
  {
    title: 'Runs on your infrastructure',
    body: 'Self-hosted. Point it at your own MongoDB, vector database, and model endpoint — your data and prompts never leave your network.',
  },
];

const LandingScreen = ({ onAction, isAuthenticated }) => {
  const handleSignIn = () => {
    onAction?.({ type: 'direct_login' });
  };

  const openArchitecture = () => {
    // Same-repo doc; works whether the reader opened this from GitHub or a
    // local checkout, so a hosted URL would be more likely to go stale.
    Linking.openURL('ARCHITECTURE.md').catch(() => {});
  };

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: '#0a0b14' }}
      contentContainerStyle={{ padding: 24, paddingTop: Platform.OS === 'web' ? 64 : 48, maxWidth: 720, alignSelf: 'center', width: '100%' }}
    >
      <Text style={{ color: '#ffffff', fontSize: 30, fontWeight: '700', marginBottom: 8 }}>
        Citra AI — Decision System
      </Text>
      <Text style={{ color: '#a0a6b8', fontSize: 16, lineHeight: 24, marginBottom: 32 }}>
        A self-hosted decision system: chat grounded in your department’s SOPs
        and data, with cited recommendations and a builder for turning
        recurring decisions into governed apps.
      </Text>

      <TouchableOpacity
        onPress={handleSignIn}
        style={{
          backgroundColor: '#6366f1',
          borderRadius: 8,
          paddingVertical: 14,
          alignItems: 'center',
          marginBottom: 40,
        }}
      >
        <Text style={{ color: '#ffffff', fontSize: 16, fontWeight: '600' }}>
          {isAuthenticated ? 'Continue' : 'Sign In'}
        </Text>
      </TouchableOpacity>

      {CAPABILITIES.map((cap) => (
        <View key={cap.title} style={{ marginBottom: 20 }}>
          <Text style={{ color: '#ffffff', fontSize: 16, fontWeight: '600', marginBottom: 4 }}>
            {cap.title}
          </Text>
          <Text style={{ color: '#8b92a8', fontSize: 14, lineHeight: 20 }}>
            {cap.body}
          </Text>
        </View>
      ))}

      <View style={{ height: 1, backgroundColor: '#1f2233', marginVertical: 24 }} />

      <Text style={{ color: '#ffffff', fontSize: 14, fontWeight: '600', marginBottom: 8 }}>
        Self-hosting
      </Text>
      <Text style={{ color: '#8b92a8', fontSize: 13, lineHeight: 20, marginBottom: 4 }}>
        Needs MongoDB, a vector database (Milvus by default), and an
        OpenAI-compatible model endpoint of your choosing.
      </Text>
      {Platform.OS === 'web' ? (
        <Text
          onPress={openArchitecture}
          style={{ color: '#6366f1', fontSize: 13, marginBottom: 16 }}
        >
          See ARCHITECTURE.md for the full setup
        </Text>
      ) : (
        <Text style={{ color: '#8b92a8', fontSize: 13, marginBottom: 16 }}>
          See ARCHITECTURE.md in the repo for the full setup.
        </Text>
      )}

      <Text style={{ color: '#5a6178', fontSize: 12, lineHeight: 18 }}>
        Licensed under the Business Source License 1.1 (BUSL-1.1), converting
        to Apache-2.0 four years after release. Free for development and
        testing; production use requires a commercial license —
        contact@citra-ai.com · https://citra-ai.com. © Trustedwear
        Tech Private Limited.
      </Text>
    </ScrollView>
  );
};

export default LandingScreen;
