// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useState } from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet, SafeAreaView, TextInput, Alert, Linking, Platform, Dimensions } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import axios from 'axios';
import { CONFIG } from '../config/config';
import ModernAlert from './ModernAlert';
import { authService } from '../services/authService';

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

const ContactScreen = ({ onBack, theme }) => {
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    mobile: '',
    subject: '',
    message: ''
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showAlert, setShowAlert] = useState(false);
  const [alertConfig, setAlertConfig] = useState({});

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

  const showAlertMessage = (title, message, type = 'info') => {
    setAlertConfig({
      title,
      message,
      type,
      buttons: [{ text: 'OK', style: 'default' }]
    });
    setShowAlert(true);
  };

  // Footer navigation handlers
  const handlePrivacyPress = () => {
    // Would navigate to Privacy Policy if needed
  };

  const handleTermsPress = () => {
    // Would navigate to Terms of Service if needed
  };

  const handleContactPress = () => {
    // Already on Contact page
  };

  const handleInputChange = (field, value) => {
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));
  };

  const handleSubmit = async () => {
    // Validate required fields
    if (!formData.name.trim()) {
      showAlertMessage('Error', 'Please enter your name', 'error');
      return;
    }
    if (!formData.email.trim()) {
      showAlertMessage('Error', 'Please enter your email address', 'error');
      return;
    }
    if (!/\S+@\S+\.\S+/.test(formData.email)) {
      showAlertMessage('Error', 'Please enter a valid email address', 'error');
      return;
    }
    if (!formData.mobile.trim()) {
      showAlertMessage('Error', 'Please enter your mobile number', 'error');
      return;
    }
    if (!/^\+?[\d\s\-\(\)]+$/.test(formData.mobile.trim())) {
      showAlertMessage('Error', 'Please enter a valid mobile number', 'error');
      return;
    }
    if (!formData.subject.trim()) {
      showAlertMessage('Error', 'Please enter a subject', 'error');
      return;
    }
    if (!formData.message.trim()) {
      showAlertMessage('Error', 'Please enter your message', 'error');
      return;
    }

    setIsSubmitting(true);
    try {
      // Get authentication headers
      const authHeaders = await authService.getAuthHeaders({
        'Content-Type': 'application/json'
      });

      const response = await axios.post(
        `${CONFIG.USER_SERVICE_URL}/api/send-contact-email`,
        {
          name: formData.name.trim(),
          email: formData.email.trim(),
          mobile: formData.mobile.trim(),
          subject: formData.subject.trim(),
          message: formData.message.trim()
        },
        {
          timeout: 30000, // 30 seconds timeout
          headers: authHeaders
        }
      );

      if (response.data.success) {
        showAlertMessage(
          'Thank You!',
          'Your message has been sent successfully! We will get back to you within 24 hours.',
          'success'
        );
        // Reset form
        setFormData({
          name: '',
          email: '',
          mobile: '',
          subject: '',
          message: ''
        });
      } else {
        throw new Error(response.data.error || 'Failed to send message');
      }
    } catch (error) {
      console.error('Contact form submission error:', error);

      let errorMessage = 'Failed to send message. Please try again.';
      if (error.response?.data?.error) {
        errorMessage = error.response.data.error;
      } else if (error.message) {
        errorMessage = `Network error: ${error.message}`;
      }

      showAlertMessage('Submission Failed', errorMessage, 'error');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleEmailPress = () => {
    Linking.openURL('mailto:contact@citra-ai.com');
  };

  const handlePhonePress = () => {
    Linking.openURL('tel:+918496977722');
  };

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View style={[styles.header, { backgroundColor: colors.card, borderBottomColor: colors.border }]}>
        <TouchableOpacity style={styles.backButton} onPress={onBack}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={[styles.headerTitle, { color: colors.foreground }]}>Contact Us</Text>
        <View style={styles.placeholder} />
      </View>

      <ScrollView
        style={styles.content}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: 100 }}
      >
        {/* Hero Card */}
        <View style={[styles.heroCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={[styles.iconContainer, { backgroundColor: `${colors.primary}20` }]}>
            <Ionicons name="chatbubbles" size={32} color={colors.primary} />
          </View>
          <Text style={[styles.heroTitle, { color: colors.foreground }]}>Get in Touch</Text>
          <Text style={[styles.heroDescription, { color: colors.mutedForeground }]}>
            We're here to help! Contact us for support, questions, or feedback about Citra AI.
            Our team is committed to providing excellent customer service.
          </Text>
        </View>

        {/* Contact Information Card */}
        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="information-circle" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Contact Information</Text>
          </View>

          <TouchableOpacity style={styles.contactItem} onPress={handleEmailPress}>
            <View style={[styles.contactIconContainer, { backgroundColor: `${colors.primary}15` }]}>
              <Ionicons name="mail" size={20} color={colors.primary} />
            </View>
            <View style={styles.contactText}>
              <Text style={[styles.contactLabel, { color: colors.foreground }]}>Email Support</Text>
              <Text style={[styles.contactValue, { color: colors.primary }]}>contact@citra-ai.com</Text>
              <Text style={[styles.contactSubtext, { color: colors.mutedForeground }]}>
                Primary support channel • 24-hour response time
              </Text>
            </View>
          </TouchableOpacity>

          <TouchableOpacity style={styles.contactItem} onPress={handlePhonePress}>
            <View style={[styles.contactIconContainer, { backgroundColor: `${colors.success}15` }]}>
              <Ionicons name="call" size={20} color={colors.success} />
            </View>
            <View style={styles.contactText}>
              <Text style={[styles.contactLabel, { color: colors.foreground }]}>Phone Support</Text>
              <Text style={[styles.contactValue, { color: colors.success }]}>+91-8496977722</Text>
              <Text style={[styles.contactSubtext, { color: colors.mutedForeground }]}>
                Available during business hours • India
              </Text>
            </View>
          </TouchableOpacity>

          <View style={styles.contactItem}>
            <View style={[styles.contactIconContainer, { backgroundColor: `${colors.accent}15` }]}>
              <Ionicons name="location" size={20} color={colors.accent} />
            </View>
            <View style={styles.contactText}>
              <Text style={[styles.contactLabel, { color: colors.foreground }]}>Address</Text>
              <Text style={[styles.contactValue, { color: colors.foreground }]}>
                Citra AI{'\n'}
                (A product of Trustedwear Tech Private Limited){'\n'}
                #42, Maitri, Balaji Lake View Garden{'\n'}
                Sampigehalli, Jakkur (Behind Cars24){'\n'}
                Bengaluru 560064, Karnataka, India
              </Text>
              <Text style={[styles.contactSubtext, { color: colors.mutedForeground }]}>
                Visit https://citra-ai.com for more information
              </Text>
            </View>
          </View>

          <View style={styles.contactItem}>
            <View style={[styles.contactIconContainer, { backgroundColor: `${colors.warning}15` }]}>
              <Ionicons name="time" size={20} color={colors.warning} />
            </View>
            <View style={styles.contactText}>
              <Text style={[styles.contactLabel, { color: colors.foreground }]}>Support Hours</Text>
              <Text style={[styles.contactValue, { color: colors.foreground }]}>
                Monday - Friday: 9:00 AM - 6:00 PM IST{'\n'}
                Saturday: 10:00 AM - 4:00 PM IST{'\n'}
                Sunday: Closed
              </Text>
            </View>
          </View>
        </View>

        {/* Contact Form Card */}
        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="mail-open" size={24} color={colors.primary} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Send us a Message</Text>
          </View>

          <TextInput
            style={[styles.input, {
              backgroundColor: colors.input,
              color: colors.foreground,
              borderColor: colors.border
            }]}
            placeholder="Your Name"
            placeholderTextColor={colors.placeholderText}
            value={formData.name}
            onChangeText={(value) => handleInputChange('name', value)}
          />

          <TextInput
            style={[styles.input, {
              backgroundColor: colors.input,
              color: colors.foreground,
              borderColor: colors.border
            }]}
            placeholder="Email Address"
            placeholderTextColor={colors.placeholderText}
            value={formData.email}
            onChangeText={(value) => handleInputChange('email', value)}
            keyboardType="email-address"
            autoCapitalize="none"
          />

          <TextInput
            style={[styles.input, {
              backgroundColor: colors.input,
              color: colors.foreground,
              borderColor: colors.border
            }]}
            placeholder="Mobile Number *"
            placeholderTextColor={colors.placeholderText}
            value={formData.mobile}
            onChangeText={(value) => handleInputChange('mobile', value)}
            keyboardType="phone-pad"
          />

          <TextInput
            style={[styles.input, {
              backgroundColor: colors.input,
              color: colors.foreground,
              borderColor: colors.border
            }]}
            placeholder="Subject"
            placeholderTextColor={colors.placeholderText}
            value={formData.subject}
            onChangeText={(value) => handleInputChange('subject', value)}
          />

          <TextInput
            style={[styles.textArea, {
              backgroundColor: colors.input,
              color: colors.foreground,
              borderColor: colors.border
            }]}
            placeholder="Your Message"
            placeholderTextColor={colors.placeholderText}
            value={formData.message}
            onChangeText={(value) => handleInputChange('message', value)}
            multiline
            numberOfLines={6}
            textAlignVertical="top"
          />

          <TouchableOpacity
            style={[styles.submitButton, {
              backgroundColor: colors.primary,
              opacity: isSubmitting ? 0.6 : 1
            }]}
            onPress={handleSubmit}
            disabled={isSubmitting}
          >
            <Ionicons name="send" size={20} color="#ffffff" style={{ marginRight: 8 }} />
            <Text style={styles.submitButtonText}>
              {isSubmitting ? 'Sending...' : 'Send Message'}
            </Text>
          </TouchableOpacity>
        </View>

        {/* FAQ Card */}
        <View style={[styles.contentCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.cardHeader}>
            <Ionicons name="help-circle" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground }]}>Frequently Asked Questions</Text>
          </View>

          <View style={styles.faqItem}>
            <Text style={[styles.faqQuestion, { color: colors.foreground }]}>How quickly will I receive a response?</Text>
            <Text style={[styles.faqAnswer, { color: colors.mutedForeground }]}>
              We typically respond to all inquiries within 24 hours during business days.
            </Text>
          </View>

          <View style={styles.faqItem}>
            <Text style={[styles.faqQuestion, { color: colors.foreground }]}>What information should I include in my message?</Text>
            <Text style={[styles.faqAnswer, { color: colors.mutedForeground }]}>
              Please include your account email, a detailed description of the issue, and any relevant screenshots or error messages.
            </Text>
          </View>

          <View style={styles.faqItem}>
            <Text style={[styles.faqQuestion, { color: colors.foreground }]}>Do you offer phone support?</Text>
            <Text style={[styles.faqAnswer, { color: colors.mutedForeground }]}>
              Currently, we provide support primarily through email and our in-app messaging system for the fastest response times.
            </Text>
          </View>

          <View style={styles.faqItem}>
            <Text style={[styles.faqQuestion, { color: colors.foreground }]}>Can I schedule a demo or consultation?</Text>
            <Text style={[styles.faqAnswer, { color: colors.mutedForeground }]}>
              Yes! Contact us to schedule a personalized demo or consultation to see how Citra AI can benefit your workflow.
            </Text>
          </View>
        </View>

        {/* Support Hours Card */}
        <View style={[styles.supportCard, { backgroundColor: colors.card, borderColor: colors.accent }]}>
          <View style={styles.supportHeader}>
            <Ionicons name="time" size={24} color={colors.accent} />
            <Text style={[styles.cardTitle, { color: colors.foreground, marginLeft: 8 }]}>Need Immediate Help?</Text>
          </View>
          <Text style={[styles.supportText, { color: colors.mutedForeground }]}>
            For urgent technical issues, use our in-app chat support or email us directly.
            We're committed to helping you get the most out of Citra AI.
          </Text>
          <View style={styles.supportButtons}>
            <TouchableOpacity
              style={[styles.supportButton, { backgroundColor: colors.primary }]}
              onPress={handleEmailPress}
            >
              <Ionicons name="mail" size={16} color="#ffffff" />
              <Text style={styles.supportButtonText}>Email Support</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.supportButton, { backgroundColor: colors.success }]}
              onPress={handlePhonePress}
            >
              <Ionicons name="call" size={16} color="#ffffff" />
              <Text style={styles.supportButtonText}>Call Support</Text>
            </TouchableOpacity>
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

      <ModernAlert
        visible={showAlert}
        title={alertConfig.title}
        message={alertConfig.message}
        type={alertConfig.type}
        buttons={alertConfig.buttons}
        onDismiss={() => setShowAlert(false)}
        theme={theme}
      />
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
    paddingBottom: 100, // Add bottom padding to prevent cutoff
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
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 20,
  },
  cardTitle: {
    fontSize: 20,
    fontWeight: '600',
    marginLeft: 8,
  },
  contactItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 20,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.05)',
  },
  contactIconContainer: {
    width: 40,
    height: 40,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 16,
  },
  contactText: {
    flex: 1,
  },
  contactLabel: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 4,
  },
  contactValue: {
    fontSize: 15,
    lineHeight: 20,
    marginBottom: 4,
  },
  contactSubtext: {
    fontSize: 13,
    lineHeight: 18,
    fontStyle: 'italic',
  },
  input: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 16,
    fontSize: 16,
    marginBottom: 16,
  },
  textArea: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 16,
    fontSize: 16,
    marginBottom: 20,
    minHeight: 120,
  },
  submitButton: {
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'center',
  },
  submitButtonText: {
    color: '#ffffff',
    fontSize: 16,
    fontWeight: '600',
  },
  faqItem: {
    marginBottom: 20,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.05)',
  },
  faqQuestion: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 8,
    lineHeight: 22,
  },
  faqAnswer: {
    fontSize: 15,
    lineHeight: 22,
  },
  supportCard: {
    borderRadius: 16,
    padding: 20,
    marginBottom: 20,
    borderWidth: 2,
  },
  supportHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  supportText: {
    fontSize: 15,
    lineHeight: 22,
    marginBottom: 16,
  },
  supportButtons: {
    flexDirection: 'row',
    gap: 12,
  },
  supportButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 8,
    gap: 6,
  },
  supportButtonText: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: '600',
  },
  bottomSpacing: {
    height: 80, // Increased from 40 to ensure content is not cut off
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

export default ContactScreen;
