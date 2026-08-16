// BrandingUpsellBanner.js - Non-blocking upsell banner for free users in export/share modals
import React from 'react';
import { View, Text, TouchableOpacity, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

/**
 * BrandingUpsellBanner - Shows an inline upsell card for free users
 * 
 * Props:
 *   userType: 'free' | 'paid' - hidden when paid
 *   context: 'export' | 'share' - determines messaging
 *   onUpgrade: () => void - callback when Upgrade button is pressed
 *   theme: object - theme colors
 */
const BrandingUpsellBanner = ({ userType = 'free', context = 'export', onUpgrade, theme = {} }) => {
  // License model: all users have unlimited access, no upsell needed
  return null;

  const messages = {
    export: {
      title: 'Remove Citra Branding',
      body: 'Your exports include a Citra AI watermark. Upgrade to remove it and apply your own logo & brand colors.',
    },
    share: {
      title: 'Customize Shared Links',
      body: 'Shared links show Citra AI branding. Upgrade to reduce branding and customize with your own logo.',
    },
  };

  const { title, body } = messages[context] || messages.export;

  const colors = {
    text: theme.text || '#e4e4e7',
    textSecondary: theme.textSecondary || theme.placeholderText || '#a1a1aa',
    surface: theme.surface || theme.inputBackground || '#2d2d44',
    border: theme.border || theme.borderColor || 'rgba(255,255,255,0.1)',
  };

  return (
    <View style={bannerStyles.container}>
      <View style={bannerStyles.iconContainer}>
        <Ionicons name="color-palette-outline" size={22} color="#F59E0B" />
      </View>
      <View style={bannerStyles.textContainer}>
        <Text style={[bannerStyles.title, { color: colors.text }]}>{title}</Text>
        <Text style={[bannerStyles.body, { color: colors.textSecondary }]}>{body}</Text>
      </View>
      {onUpgrade && (
        <TouchableOpacity style={bannerStyles.upgradeButton} onPress={onUpgrade}>
          <Ionicons name="arrow-up-circle" size={14} color="#fff" style={{ marginRight: 4 }} />
          <Text style={bannerStyles.upgradeButtonText}>Upgrade</Text>
        </TouchableOpacity>
      )}
    </View>
  );
};

const bannerStyles = {
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(245, 158, 11, 0.08)',
    borderLeftWidth: 3,
    borderLeftColor: '#F59E0B',
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
    gap: 10,
  },
  iconContainer: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },
  textContainer: {
    flex: 1,
  },
  title: {
    fontSize: 13,
    fontWeight: '600',
    marginBottom: 2,
  },
  body: {
    fontSize: 11.5,
    lineHeight: 16,
  },
  upgradeButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F59E0B',
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 6,
    flexShrink: 0,
    ...Platform.select({
      web: { cursor: 'pointer' },
      default: {},
    }),
  },
  upgradeButtonText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '700',
  },
};

export default BrandingUpsellBanner;
