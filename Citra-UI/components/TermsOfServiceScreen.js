// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import React from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet, SafeAreaView, Platform, Dimensions } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const { width } = Dimensions.get('window');

// Footer Component (matching IntroScreen footer)
const Footer = ({ onPrivacyPress, onTermsPress, onContactPress, colors }) => (
  <View style={[footerStyles.footer, {
    backgroundColor: colors.card,
    borderTopColor: colors.border
  }]}>
    <Text style={[footerStyles.footerText, { color: colors.mutedForeground }]}>
      © 2025 Citra AI. All rights reserved.
    </Text>
    <View style={[footerStyles.footerLinks, {
      flexDirection: width > 480 ? 'row' : 'column'
    }]}>
      <TouchableOpacity onPress={onPrivacyPress}>
        <Text style={[footerStyles.footerLink, { color: colors.primary }]}>Privacy Policy</Text>
      </TouchableOpacity>
      <TouchableOpacity onPress={onTermsPress}>
        <Text style={[footerStyles.footerLink, { color: colors.primary }]}>Terms of Service</Text>
      </TouchableOpacity>
      <TouchableOpacity onPress={onContactPress}>
        <Text style={[footerStyles.footerLink, { color: colors.primary }]}>Contact</Text>
      </TouchableOpacity>
    </View>
  </View>
);

const TermsOfServiceScreen = ({ onBack, theme }) => {
  const isDark = theme?.isDark || false;

  // Use IntroScreen light color scheme for consistency
  const colors = {
    background: '#F8FAFC', // Slate 50
    foreground: '#0F172A', // Slate 900
    card: '#FFFFFF', // White
    cardForeground: '#0F172A',
    primary: '#7C3AED', // Violet 600
    primaryForeground: '#FFFFFF',
    secondary: '#E2E8F0', // Slate 200 (for borders/secondary)
    secondaryForeground: '#0F172A',
    muted: '#F1F5F9', // Slate 100
    mutedForeground: '#475569', // Slate 600
    accent: '#3B82F6', // Blue 500
    accentForeground: '#FFFFFF',
    border: '#E2E8F0', // Slate 200
    input: '#FFFFFF', // White input
    ring: '#7C3AED', // Violet ring
    text: '#0F172A',
    secondaryText: '#475569',
    success: '#10B981', // Natural Green
    warning: '#F59E0B', // Amber
    placeholderText: '#94A3B8' // Slate 400
  };

  // Footer navigation handlers
  const handlePrivacyPress = () => {
    // Would navigate to Privacy Policy if needed
  };

  const handleTermsPress = () => {
    // Already on Terms of Service page
  };

  const handleContactPress = () => {
    // Would navigate to Contact page if needed
  };

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View style={[styles.header, { backgroundColor: colors.card, borderBottomColor: colors.border }]}>
        <TouchableOpacity style={styles.backButton} onPress={onBack}>
          <Ionicons name="arrow-back" size={24} color={colors.foreground} />
        </TouchableOpacity>
        <Text style={[styles.headerTitle, { color: colors.foreground }]}>Terms of Service</Text>
        <View style={styles.placeholder} />
      </View>

      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
        {/* Hero Card */}
        <View style={[styles.heroCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={[styles.iconContainer, { backgroundColor: `${colors.primary}20` }]}>
            <Ionicons name="document-text" size={32} color={colors.primary} />
          </View>
          <Text style={[styles.heroTitle, { color: colors.foreground }]}>Terms of Service</Text>
          <Text style={[styles.lastUpdated, { color: colors.mutedForeground }]}>
            Last updated: August 12, 2025
          </Text>
          <Text style={[styles.heroDescription, { color: colors.mutedForeground }]}>
            Please read these terms carefully before using Citra AI.
            By using our service, you agree to be bound by these terms.
          </Text>
        </View>

        {/* Content Cards */}
        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="checkmark-circle" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Acceptance of Terms</Text>
          </View>
          <Text style={[styles.paragraph, { color: colors.foreground }]}>
            By accessing and using Citra AI ("the Service"), you accept and agree to be bound by the terms and provision of this agreement. If you do not agree to abide by the above, please do not use this service.
          </Text>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="apps" size={24} color={colors.primary} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Description of Service</Text>
          </View>
          <Text style={[styles.paragraph, { color: colors.foreground }]}>
            Citra AI is an artificial intelligence-powered platform that helps users organize, search, and interact with their documents and information. Our service includes:
          </Text>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• AI-powered document analysis and summarization</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Intelligent search and retrieval capabilities</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Conversational AI assistance</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Secure document storage and management</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Deep research and analysis tools</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="person" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>User Accounts and Responsibilities</Text>
          </View>
          <Text style={[styles.paragraph, { color: colors.foreground }]}>
            To use our Service, you must:
          </Text>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Provide accurate and complete registration information</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Maintain the security of your account credentials</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Be responsible for all activities under your account</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Notify us immediately of any unauthorized access</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Use the Service only for lawful purposes</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Respect intellectual property rights of others</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="shield-checkmark" size={24} color={colors.primary} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Acceptable Use Policy</Text>
          </View>
          <Text style={[styles.paragraph, { color: colors.foreground }]}>
            You agree NOT to use the Service to:
          </Text>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Upload illegal, harmful, or inappropriate content</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Violate any laws or regulations</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Infringe on intellectual property rights</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Attempt to gain unauthorized access to our systems</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Distribute malware or harmful code</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Harass, abuse, or harm other users</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Use the Service for commercial purposes without permission</Text>
          </View>
        </View>
        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="library" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Content and Intellectual Property</Text>
          </View>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• You retain all rights to your uploaded content</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• You grant us a license to process your content to provide our services</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Our AI models, software, and platform are protected by intellectual property laws</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• You may not reverse engineer or copy our technology</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We respect copyright and will respond to valid DMCA notices</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="card" size={24} color={colors.primary} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Subscription and Payment Terms</Text>
          </View>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Subscription fees are billed monthly or annually as selected</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• All fees are non-refundable except as required by law</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We may change pricing with 30 days advance notice</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Failure to pay may result in service suspension</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Additional billing charges apply when you exceed plan limits</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• You can cancel your subscription at any time</Text>
          </View>
        </View>

        <View style={[styles.securityCard, { backgroundColor: colors.card, borderColor: colors.accent }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="lock-closed" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Data Security and Privacy</Text>
          </View>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We implement industry-standard security measures</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Your data is encrypted and protected</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We do not access your encrypted content without permission</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• You are responsible for maintaining secure access credentials</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• See our Privacy Policy for detailed information</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="server" size={24} color={colors.primary} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Service Availability and Modifications</Text>
          </View>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We strive for 99.9% uptime but cannot guarantee uninterrupted service</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We may modify, suspend, or discontinue features with notice</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Maintenance windows may cause temporary service interruptions</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We are not liable for service disruptions beyond our control</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="warning" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Limitation of Liability</Text>
          </View>
          <Text style={[styles.paragraph, { color: colors.foreground }]}>
            To the maximum extent permitted by law:
          </Text>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We provide the Service "as is" without warranties</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We are not liable for indirect, incidental, or consequential damages</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Our total liability is limited to the amount you paid in the last 12 months</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• You use the Service at your own risk</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We are not responsible for third-party content or services</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="close-circle" size={24} color={colors.primary} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Termination</Text>
          </View>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Either party may terminate this agreement at any time</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We may suspend or terminate accounts for violations</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Upon termination, your access to the Service will cease</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• You may download your data before termination</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Certain provisions survive termination (e.g., intellectual property, limitation of liability)</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="balance" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Governing Law and Disputes</Text>
          </View>
          <View style={styles.bulletList}>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• These terms are governed by Indian law</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Disputes will be resolved through binding arbitration</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Legal proceedings must be filed in Indian courts</Text>
            <Text style={[styles.bulletItem, { color: colors.foreground }]}>• If any provision is invalid, the rest remains enforceable</Text>
          </View>
        </View>

        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="refresh" size={24} color={colors.primary} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Changes to Terms</Text>
          </View>
          <Text style={[styles.paragraph, { color: colors.foreground }]}>
            We may update these Terms of Service from time to time. We will notify you of material changes by posting the updated terms on our platform and updating the "Last updated" date. Your continued use of the Service after such changes constitutes acceptance of the new terms.
          </Text>
        </View>

        {/* Contact Card */}
        <View style={[styles.contactCard, { backgroundColor: colors.card, borderColor: colors.accent }]}>
          <View style={styles.contactHeader}>
            <Ionicons name="mail" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground, marginLeft: 8 }]}>Contact Us</Text>
          </View>
          <Text style={[styles.paragraph, { color: colors.foreground }]}>
            For questions about these Terms of Service, please contact us:
          </Text>
          <View style={styles.contactDetails}>
            <Text style={[styles.contactItem, { color: colors.foreground }]}>📧 contact@citra-ai.com</Text>
            <Text style={[styles.contactItem, { color: colors.foreground }]}>📍 Citra AI (A product of Trustedwear Tech Private Limited), #42, Maitri, Balaji Lake View Garden, Sampigehalli, Jakkur (Behind Cars24), Bengaluru 560064, Karnataka, India</Text>
            <Text style={[styles.contactItem, { color: colors.foreground }]}>📞 +91-84969-77722</Text>
          </View>
        </View>

        <Footer
          onPrivacyPress={handlePrivacyPress}
          onTermsPress={handleTermsPress}
          onContactPress={handleContactPress}
          colors={colors}
        />

        <View style={styles.bottomSpacing} />
      </ScrollView>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 16,
    borderBottomWidth: 1,
  },
  backButton: {
    padding: 8,
  },
  headerTitle: {
    fontSize: 20,
    fontWeight: '700',
  },
  placeholder: {
    width: 40,
  },
  content: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 20,
    maxWidth: 1200,
    alignSelf: 'center',
  },
  heroCard: {
    borderRadius: 16,
    padding: 24,
    marginBottom: 20,
    borderWidth: 1,
    alignItems: 'center',
  },
  iconContainer: {
    width: 64,
    height: 64,
    borderRadius: 32,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 16,
  },
  heroTitle: {
    fontSize: 24,
    fontWeight: '700',
    marginBottom: 8,
    textAlign: 'center',
  },
  lastUpdated: {
    fontSize: 14,
    fontStyle: 'italic',
    marginBottom: 12,
    textAlign: 'center',
  },
  heroDescription: {
    fontSize: 16,
    lineHeight: 24,
    textAlign: 'center',
  },
  contentCard: {
    borderRadius: 16,
    padding: 20,
    marginBottom: 20,
    borderWidth: 1,
  },
  securityCard: {
    borderRadius: 16,
    padding: 20,
    marginBottom: 20,
    borderWidth: 2,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  cardTitle: {
    fontSize: 20,
    fontWeight: '600',
    marginLeft: 8,
  },
  paragraph: {
    fontSize: 16,
    lineHeight: 24,
    marginBottom: 12,
  },
  bulletList: {
    marginTop: 8,
  },
  bulletItem: {
    fontSize: 15,
    lineHeight: 22,
    marginBottom: 8,
    paddingLeft: 8,
  },
  contactCard: {
    borderRadius: 16,
    padding: 20,
    marginBottom: 20,
    borderWidth: 2,
  },
  contactHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  contactDetails: {
    marginTop: 12,
  },
  contactItem: {
    fontSize: 15,
    lineHeight: 24,
    marginBottom: 4,
  },
  bottomSpacing: {
    height: 40,
  },
});

// Footer Styles (matching IntroScreen footer)
const footerStyles = StyleSheet.create({
  footer: {
    borderTopWidth: 1,
    paddingVertical: 32,
    paddingHorizontal: 24,
    alignItems: 'center',
  },
  footerText: {
    fontSize: 14,
    marginBottom: 16,
  },
  footerLinks: {
    gap: width > 480 ? 24 : 12,
    alignItems: 'center',
  },
  footerLink: {
    fontSize: 14,
  },
});

export default TermsOfServiceScreen;
