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

const PrivacyPolicyScreen = ({ onBack, theme }) => {
  const isDark = theme?.isDark || false;

  // Use IntroScreen color scheme for consistency
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
  };

  // Footer navigation handlers
  const handlePrivacyPress = () => {
    // Already on Privacy Policy page
  };

  const handleTermsPress = () => {
    // Would navigate to Terms of Service if needed
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
        <Text style={[styles.headerTitle, { color: colors.foreground }]}>Privacy Policy</Text>
        <View style={styles.placeholder} />
      </View>

      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
        <View style={styles.section}>
          {/* Hero Card */}
          <View style={[styles.heroCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <View style={[styles.iconContainer, { backgroundColor: colors.accent }]}>
              <Ionicons name="shield-checkmark" size={32} color="#ffffff" />
            </View>
            <Text style={[styles.heroTitle, { color: colors.foreground }]}>Your Privacy Matters</Text>
            <Text style={[styles.heroSubtitle, { color: colors.mutedForeground }]}>
              We're committed to protecting your data with industry-leading security and transparency.
            </Text>
            <Text style={[styles.lastUpdated, { color: colors.mutedForeground }]}>
              Last updated: July 15, 2025
            </Text>
          </View>

          {/* Introduction Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Introduction</Text>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              Welcome to Citra AI ("we," "our," or "us"). We are committed to protecting your privacy and ensuring the security of your personal information. This Privacy Policy explains how we collect, use, and safeguard your data when you use our AI-powered Citra AIance platform.
            </Text>
          </View>

          {/* Information Collection Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Information We Collect</Text>

            <View style={styles.subSection}>
              <Text style={[styles.subSectionTitle, { color: colors.accent }]}>Personal Information</Text>
              <View style={styles.bulletList}>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Email address and contact information</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Account credentials and authentication data</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Profile information you choose to provide</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Subscription and payment information</Text>
              </View>
            </View>

            <View style={styles.subSection}>
              <Text style={[styles.subSectionTitle, { color: colors.accent }]}>Content and Usage Data</Text>
              <View style={styles.bulletList}>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Documents, notes, and files you upload</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Chat conversations and AI interactions</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Search queries and usage patterns</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Device information and technical logs</Text>
              </View>
            </View>

            <View style={styles.subSection}>
              <Text style={[styles.subSectionTitle, { color: colors.accent }]}>Device Fingerprinting</Text>
              <Text style={[styles.paragraph, { color: colors.foreground }]}>
                We use client-side device fingerprinting (via FingerprintJS, open-source edition) to detect and prevent abuse such as free-plan exploitation and fraudulent account creation. This processing is based on our legitimate interest under GDPR Article 6(1)(f). Fingerprint data is stored as a hash and is not used for advertising or cross-site tracking.
              </Text>
            </View>

            <View style={styles.subSection}>
              <Text style={[styles.subSectionTitle, { color: colors.accent }]}>Cookies</Text>
              <Text style={[styles.paragraph, { color: colors.foreground }]}>
                We use cookies and similar technologies to improve your experience. Essential cookies required for the platform to function (such as authentication sessions and preference storage) are used without consent. You can manage cookie preferences in your browser settings.
              </Text>
            </View>
          </View>

          {/* Data Usage Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>How We Use Your Information</Text>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              We use your information to:
            </Text>
            <View style={styles.bulletList}>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Provide personalized AI assistance and memory services</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Process and analyze your documents and content</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Improve our AI models and platform functionality</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Manage your account and subscription</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Provide customer support and technical assistance</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Send important updates and service notifications</Text>
            </View>
          </View>

          {/* Security Card */}
          <View style={[styles.securityCard, { backgroundColor: colors.card, borderColor: colors.accent }]}>
            <View style={styles.securityHeader}>
              <Ionicons name="lock-closed" size={24} color={colors.accent} />
              <Text style={[styles.cardTitle, { color: colors.foreground, marginLeft: 8 }]}>Data Security and Encryption</Text>
            </View>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              Your privacy is our top priority. We implement industry-leading security measures:
            </Text>
            <View style={styles.bulletList}>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• End-to-end encryption for all your documents and conversations</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Your data is encrypted with your unique login credentials</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• We cannot access your encrypted content without your permission</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Regular security audits and compliance monitoring</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Secure cloud infrastructure with redundant backups</Text>
            </View>
          </View>

          {/* Data Sharing Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Data Sharing and Third Parties</Text>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              We do not sell, rent, or share your personal information with third parties except:
            </Text>
            <View style={styles.bulletList}>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• When required by law or legal process</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• To protect our rights and prevent fraud</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• With your explicit consent</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• With trusted service providers who help operate our platform (under strict confidentiality agreements)</Text>
            </View>

            <View style={[styles.subSection, { marginTop: 12 }]}>
              <Text style={[styles.subSectionTitle, { color: colors.accent }]}>Sub-Processors</Text>
              <Text style={[styles.paragraph, { color: colors.foreground }]}>
                We use the following third-party services to operate our platform. Each processes data under strict agreements:
              </Text>
              <View style={styles.bulletList}>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Google OAuth — Authentication (email, name, profile picture)</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• AWS S3 — Secure file storage (encrypted)</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Zilliz/Milvus — Vector database for AI features</Text>
                <Text style={[styles.bulletItem, { color: colors.foreground }]}>• FingerprintJS (open-source) — Abuse prevention (legitimate interest)</Text>
              </View>
            </View>
          </View>

          {/* Rights Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Your Rights and Choices</Text>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              Under applicable data protection laws (including GDPR), you have the right to:
            </Text>
            <View style={styles.bulletList}>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Access and update your personal information</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Delete your account and all associated data via the "Delete My Account" option in your profile menu</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Download your files directly from the Vault before account deletion</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Opt-out of non-essential communications</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Request clarification about our data practices</Text>
              <Text style={[styles.bulletItem, { color: colors.foreground }]}>• Lodge a complaint with a supervisory authority (e.g., your local Data Protection Authority)</Text>
            </View>
          </View>

          {/* Data Retention Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Data Retention</Text>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              You control the lifecycle of your data. We retain your data for as long as your account is active. There is no automatic data expiration. When you delete your account via the "Delete My Account" option, all of your data — including documents, files, chat history, notes, embeddings, and profile information — is permanently and irreversibly removed from all our systems (databases, file storage, and vector stores). We recommend downloading any files you need from the Vault before deleting your account.
            </Text>
          </View>

          {/* Children's Privacy Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Children's Privacy</Text>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              Our services are not intended for children under 13 years of age. We do not knowingly collect personal information from children under 13. If you believe we have collected information from a child under 13, please contact us immediately.
            </Text>
          </View>

          {/* Policy Changes Card */}
          <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Changes to This Policy</Text>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              We may update this Privacy Policy from time to time. We will notify you of any material changes by posting the new Privacy Policy on our platform and updating the "Last updated" date. Your continued use of our services after such changes constitutes acceptance of the updated policy.
            </Text>
          </View>

          {/* Contact Card */}
          <View style={[styles.contactCard, { backgroundColor: colors.card, borderColor: colors.accent }]}>
            <View style={styles.contactHeader}>
              <Ionicons name="mail" size={24} color={colors.accent} />
              <Text style={[styles.cardTitle, { color: colors.foreground, marginLeft: 8 }]}>Contact Us</Text>
            </View>
            <Text style={[styles.paragraph, { color: colors.foreground }]}>
              If you have any questions about this Privacy Policy or our data practices, please contact us:
            </Text>
            <View style={styles.contactDetails}>
              <Text style={[styles.contactItem, { color: colors.foreground }]}>📧contact@citra-ai.com</Text>
              <Text style={[styles.contactItem, { color: colors.foreground }]}>📍 Citra AI (A product of Trustedwear Tech Private Limited), #42, Maitri, Balaji Lake View Garden, Sampigehalli, Jakkur (Behind Cars24), Bengaluru 560064, Karnataka, India</Text>
              <Text style={[styles.contactItem, { color: colors.foreground }]}>📞 +91-84969-77722</Text>
            </View>
          </View>
        </View>

        <Footer
          onPrivacyPress={handlePrivacyPress}
          onTermsPress={handleTermsPress}
          onContactPress={handleContactPress}
          colors={colors}
        />
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
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  backButton: {
    padding: 8,
    borderRadius: 8,
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
    paddingHorizontal: 16,
  },
  section: {
    paddingVertical: 20,
    gap: 16,
    maxWidth: 1200,
    alignSelf: 'center',
  },
  heroCard: {
    padding: 24,
    borderRadius: 16,
    borderWidth: 1,
    alignItems: 'center',
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
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
    fontSize: 28,
    fontWeight: '700',
    marginBottom: 8,
    textAlign: 'center',
  },
  heroSubtitle: {
    fontSize: 16,
    lineHeight: 24,
    textAlign: 'center',
    marginBottom: 16,
  },
  lastUpdated: {
    fontSize: 14,
    fontStyle: 'italic',
    textAlign: 'center',
  },
  contentCard: {
    padding: 20,
    borderRadius: 12,
    borderWidth: 1,
    elevation: 1,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
  },
  securityCard: {
    padding: 20,
    borderRadius: 12,
    borderWidth: 2,
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
  },
  contactCard: {
    padding: 20,
    borderRadius: 12,
    borderWidth: 2,
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    marginBottom: 20,
  },
  securityHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  contactHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  cardTitle: {
    fontSize: 20,
    fontWeight: '600',
    marginBottom: 12,
  },
  subSection: {
    marginTop: 16,
  },
  subSectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 8,
  },
  paragraph: {
    fontSize: 16,
    lineHeight: 24,
    marginBottom: 8,
  },
  bulletList: {
    marginTop: 8,
    gap: 4,
  },
  bulletItem: {
    fontSize: 15,
    lineHeight: 22,
    paddingLeft: 8,
  },
  contactDetails: {
    marginTop: 12,
    gap: 8,
  },
  contactItem: {
    fontSize: 15,
    lineHeight: 22,
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

export default PrivacyPolicyScreen;
