import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  SafeAreaView,
  StatusBar,
  KeyboardAvoidingView,
  Platform,
  Linking
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import axios from 'axios';
import { CONFIG } from '../config/config';
import ModernAlert from './ModernAlert';
import { authService } from '../services/authService';

const SupportScreen = ({ theme, onBack, onNavigateHome }) => {
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

  const colors = {
    background: theme?.background || (isDark ? '#0f172a' : '#ffffff'),
    card: theme?.card || (isDark ? '#1e293b' : '#ffffff'),
    border: theme?.border || (isDark ? '#334155' : '#e2e8f0'),
    text: theme?.text || (isDark ? '#ffffff' : '#1e293b'),
    secondaryText: theme?.textSecondary || (isDark ? '#cbd5e1' : '#64748b'),
    primary: theme?.primary || '#3b82f7',
    accent: theme?.accent || '#a855f7',
    success: '#10b981',
    warning: '#f59e0b',
    destructive: '#ef4444',
    inputBackground: theme?.inputBackground || (isDark ? '#334155' : '#f8fafc')
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

  const handleInputChange = (field, value) => {
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));
  };

  const validateForm = () => {
    if (!formData.name.trim()) {
      showAlertMessage('Error', 'Please enter your name', 'error');
      return false;
    }
    if (!formData.email.trim()) {
      showAlertMessage('Error', 'Please enter your email address', 'error');
      return false;
    }
    if (!/\S+@\S+\.\S+/.test(formData.email)) {
      showAlertMessage('Error', 'Please enter a valid email address', 'error');
      return false;
    }
    if (!formData.mobile.trim()) {
      showAlertMessage('Error', 'Please enter your mobile number', 'error');
      return false;
    }
    if (!/^\+?[\d\s\-\(\)]+$/.test(formData.mobile.trim())) {
      showAlertMessage('Error', 'Please enter a valid mobile number', 'error');
      return false;
    }
    if (!formData.subject.trim()) {
      showAlertMessage('Error', 'Please enter a subject', 'error');
      return false;
    }
    if (!formData.message.trim()) {
      showAlertMessage('Error', 'Please enter your message', 'error');
      return false;
    }
    return true;
  };

  const handleSubmit = async () => {
    if (!validateForm()) return;

    try {
      setIsSubmitting(true);

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
          'Your support request has been submitted successfully. We will get back to you within 24 hours.',
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
        throw new Error(response.data.error || 'Failed to submit support request');
      }
    } catch (error) {
      console.error('Support form submission error:', error);

      let errorMessage = 'Failed to submit support request. Please try again.';
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

  const supportItems = [
    {
      icon: 'mail-outline',
      title: 'Email Support',
      subtitle: 'contact@citra-ai.com',
      action: () => Linking.openURL('mailto:contact@citra-ai.com')
    },
    {
      icon: 'call-outline',
      title: 'Phone Support',
      subtitle: '+91 84969 77722',
      action: () => Linking.openURL('tel:+918496977722')
    },
    {
      icon: 'chatbubble-outline',
      title: 'Live Chat',
      subtitle: 'Available 9 AM - 6 PM IST',
      action: () => showAlertMessage('Coming Soon', 'Live chat will be available soon!', 'info')
    },
    {
      icon: 'document-text-outline',
      title: 'Documentation',
      subtitle: 'Browse our help articles',
      action: () => showAlertMessage('Coming Soon', 'Documentation portal coming soon!', 'info')
    }
  ];

  const renderSupportItem = ({ icon, title, subtitle, action }) => (
    <TouchableOpacity
      key={title}
      style={[styles.supportItem, { backgroundColor: colors.card, borderColor: colors.border }]}
      onPress={action}
      activeOpacity={0.7}
    >
      <View style={[styles.supportIconContainer, { backgroundColor: `${colors.primary}20` }]}>
        <Ionicons name={icon} size={24} color={colors.primary} />
      </View>
      <View style={styles.supportContent}>
        <Text style={[styles.supportTitle, { color: colors.text }]}>
          {title}
        </Text>
        <Text style={[styles.supportSubtitle, { color: colors.secondaryText }]}>
          {subtitle}
        </Text>
      </View>
      <Ionicons name="chevron-forward" size={20} color={colors.secondaryText} />
    </TouchableOpacity>
  );

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: colors.background }]}>
      <StatusBar barStyle={isDark ? 'light-content' : 'dark-content'} />

      <View style={styles.header}>
        <View style={styles.headerLeft}>
          {onNavigateHome && (
            <TouchableOpacity onPress={onNavigateHome} style={styles.homeButton}>
              <Ionicons name="home" size={20} color={colors.primary} style={{ marginRight: 6 }} />
              <Text style={{ color: colors.primary, fontWeight: '600' }}>Home</Text>
            </TouchableOpacity>
          )}
          <Text style={[styles.headerTitle, { color: colors.text }]}>Support</Text>
        </View>
      </View>

      <KeyboardAvoidingView
        style={styles.keyboardView}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView style={styles.scrollView} showsVerticalScrollIndicator={false}>
          {/* Hero Section */}
          <View style={styles.heroSection}>
            <Ionicons
              name="help-circle"
              size={64}
              color={colors.primary}
              style={styles.heroIcon}
            />
            <Text style={[styles.heroTitle, { color: colors.text }]}>
              How can we help you?
            </Text>
            <Text style={[styles.heroSubtitle, { color: colors.secondaryText }]}>
              We're here to help! Choose how you'd like to get support or fill out the contact form below.
            </Text>
          </View>

          {/* Support Options */}
          <View style={styles.section}>
            <Text style={[styles.sectionTitle, { color: colors.text }]}>
              Get Support
            </Text>
            {supportItems.map(renderSupportItem)}
          </View>

          {/* Contact Form */}
          <View style={styles.section}>
            <Text style={[styles.sectionTitle, { color: colors.text }]}>
              Contact Us
            </Text>

            <View style={[styles.formCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
              <View style={styles.inputGroup}>
                <Text style={[styles.inputLabel, { color: colors.text }]}>Name *</Text>
                <TextInput
                  style={[
                    styles.textInput,
                    {
                      backgroundColor: colors.inputBackground,
                      borderColor: colors.border,
                      color: colors.text
                    }
                  ]}
                  placeholder="Enter your full name"
                  placeholderTextColor={colors.secondaryText}
                  value={formData.name}
                  onChangeText={(value) => handleInputChange('name', value)}
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={[styles.inputLabel, { color: colors.text }]}>Email *</Text>
                <TextInput
                  style={[
                    styles.textInput,
                    {
                      backgroundColor: colors.inputBackground,
                      borderColor: colors.border,
                      color: colors.text
                    }
                  ]}
                  placeholder="Enter your email address"
                  placeholderTextColor={colors.secondaryText}
                  value={formData.email}
                  onChangeText={(value) => handleInputChange('email', value)}
                  keyboardType="email-address"
                  autoCapitalize="none"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={[styles.inputLabel, { color: colors.text }]}>Mobile *</Text>
                <TextInput
                  style={[
                    styles.textInput,
                    {
                      backgroundColor: colors.inputBackground,
                      borderColor: colors.border,
                      color: colors.text
                    }
                  ]}
                  placeholder="Enter your mobile number"
                  placeholderTextColor={colors.secondaryText}
                  value={formData.mobile}
                  onChangeText={(value) => handleInputChange('mobile', value)}
                  keyboardType="phone-pad"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={[styles.inputLabel, { color: colors.text }]}>Subject *</Text>
                <TextInput
                  style={[
                    styles.textInput,
                    {
                      backgroundColor: colors.inputBackground,
                      borderColor: colors.border,
                      color: colors.text
                    }
                  ]}
                  placeholder="What's this about?"
                  placeholderTextColor={colors.secondaryText}
                  value={formData.subject}
                  onChangeText={(value) => handleInputChange('subject', value)}
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={[styles.inputLabel, { color: colors.text }]}>Message *</Text>
                <TextInput
                  style={[
                    styles.textInput,
                    styles.messageInput,
                    {
                      backgroundColor: colors.inputBackground,
                      borderColor: colors.border,
                      color: colors.text
                    }
                  ]}
                  placeholder="Please describe your issue or question in detail..."
                  placeholderTextColor={colors.secondaryText}
                  value={formData.message}
                  onChangeText={(value) => handleInputChange('message', value)}
                  multiline
                  numberOfLines={6}
                  textAlignVertical="top"
                />
              </View>

              <TouchableOpacity
                style={[
                  styles.submitButton,
                  {
                    backgroundColor: colors.primary,
                    opacity: isSubmitting ? 0.7 : 1
                  }
                ]}
                onPress={handleSubmit}
                disabled={isSubmitting}
                activeOpacity={0.8}
              >
                <Text style={styles.submitButtonText}>
                  {isSubmitting ? 'Sending...' : 'Send Message'}
                </Text>
                {!isSubmitting && (
                  <Ionicons name="send" size={18} color="#ffffff" style={styles.submitIcon} />
                )}
              </TouchableOpacity>
            </View>
          </View>

          <View style={styles.bottomSpacing} />
        </ScrollView>
      </KeyboardAvoidingView>

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
    paddingHorizontal: 24,
    paddingTop: 16,
    paddingBottom: 8,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: '700',
  },
  homeButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#f1f5f9', // Use theme color if available in context, defaulting to light gray
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    marginRight: 16,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  keyboardView: {
    flex: 1,
  },
  scrollView: {
    flex: 1,
  },
  heroSection: {
    alignItems: 'center',
    paddingHorizontal: 24,
    paddingVertical: 32,
  },
  heroIcon: {
    marginBottom: 16,
  },
  heroTitle: {
    fontSize: 24,
    fontWeight: '700',
    textAlign: 'center',
    marginBottom: 8,
  },
  heroSubtitle: {
    fontSize: 16,
    textAlign: 'center',
    lineHeight: 22,
  },
  section: {
    paddingHorizontal: 16,
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: '700',
    marginBottom: 16,
  },
  supportItem: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    marginBottom: 12,
  },
  supportIconContainer: {
    width: 48,
    height: 48,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 16,
  },
  supportContent: {
    flex: 1,
  },
  supportTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 2,
  },
  supportSubtitle: {
    fontSize: 14,
    lineHeight: 18,
  },
  formCard: {
    borderRadius: 16,
    borderWidth: 1,
    padding: 20,
  },
  inputGroup: {
    marginBottom: 20,
  },
  inputLabel: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 8,
  },
  textInput: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 16,
    lineHeight: 20,
  },
  messageInput: {
    height: 120,
    paddingTop: 14,
  },
  submitButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 16,
    borderRadius: 12,
    marginTop: 8,
  },
  submitButtonText: {
    color: '#ffffff',
    fontSize: 16,
    fontWeight: '600',
  },
  submitIcon: {
    marginLeft: 8,
  },
  bottomSpacing: {
    height: 32,
  },
});

export default SupportScreen;
