// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import React from 'react';
import {
  View,
  Text,
  Modal,
  ScrollView,
  TouchableOpacity,
  Switch,
  StyleSheet,
  useWindowDimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { PERSONAL_VAULT_ENABLED } from '../../config/featureFlags';

// Integration configs with icons and colors (SaaS integrations removed — shifted to Citra Agent desktop app)
const INTEGRATION_CONFIGS = {
  'google-drive': { icon: 'folder', color: '#4285F4', name: 'Google Drive' },
};

// Non-toggleable info row for always-enabled features (visualization only)
const ConnectionInfoRow = ({
  icon,
  label,
  description,
  iconColor,
  theme
}) => (
  <View style={[styles.toggleContainer, { backgroundColor: theme.card }]}>
    <View style={styles.toggleLeft}>
      <View style={[styles.iconContainer, { backgroundColor: iconColor ? `${iconColor}20` : '#10b98120' }]}>
        <Ionicons
          name={icon}
          size={24}
          color={iconColor || '#10b981'}
        />
      </View>
      <View style={styles.toggleText}>
        <View style={styles.labelRow}>
          <Text style={[styles.toggleLabel, { color: theme.text }]}>{label}</Text>
          <View style={[styles.badge, { backgroundColor: '#10b981' }]}>
            <Text style={styles.badgeText}>Always On</Text>
          </View>
        </View>
        {description && (
          <Text style={[styles.toggleDescription, { color: theme.secondaryText }]}>
            {description}
          </Text>
        )}
      </View>
    </View>
    {/* Always-on indicator - no toggle */}
    <View style={styles.alwaysOnIndicator}>
      <Ionicons name="checkmark-circle" size={28} color="#10b981" />
    </View>
  </View>
);

const ConnectionToggle = ({
  icon,
  label,
  description,
  value,
  onChange,
  badge,
  iconColor,
  theme
}) => (
  <View style={[styles.toggleContainer, { backgroundColor: theme.card }]}>
    <View style={styles.toggleLeft}>
      <View style={[styles.iconContainer, { backgroundColor: iconColor ? `${iconColor}20` : theme.buttonBackground }]}>
        <Ionicons
          name={icon}
          size={24}
          color={iconColor || theme.sendButton}
        />
      </View>
      <View style={styles.toggleText}>
        <View style={styles.labelRow}>
          <Text style={[styles.toggleLabel, { color: theme.text }]}>{label}</Text>
          {badge && (
            <View style={[
              styles.badge,
              { backgroundColor: badge === 'Connected' ? '#10b981' : '#ef4444' }
            ]}>
              <Text style={styles.badgeText}>{badge}</Text>
            </View>
          )}
        </View>
        {description && (
          <Text style={[styles.toggleDescription, { color: theme.secondaryText }]}>
            {description}
          </Text>
        )}
      </View>
    </View>
    <Switch
      value={value}
      onValueChange={onChange}
      trackColor={{ false: theme.divider, true: theme.sendButton }}
      thumbColor={value ? '#ffffff' : '#f4f3f4'}
    />
  </View>
);

// AI description - always generic (General Professional only)
const getAIDescription = () => {
  return 'Citra AI model';
};

export default function ConnectionScopeModal({
  visible,
  onClose,
  theme,
  // Connection states
  useVault,
  onUseVaultChange,
  useInternet,
  onUseInternetChange,
  // Enterprise is Always On — these are still accepted (App.js passes them and
  // other surfaces read the same state) but this modal no longer toggles it.
  useEnterprise,
  onUseEnterpriseChange,
  renderEnterpriseSearch,
  // Warning modal state (shared from App.js)
  showInternetWarningModal = false,
  onConfirmDisableInternet,
  onCancelDisableInternet,
}) {
  const getTotalActiveCount = () => {
    // Citra AI + Database and the Enterprise Data Store are both Always On.
    let count = 2;
    if (PERSONAL_VAULT_ENABLED && useVault) count++;
    if (useInternet) count++;
    return count;
  };

  const { width: screenWidth } = useWindowDimensions();
  const isMobile = screenWidth < 768;

  const getProviderConfig = (provider) => {
    return INTEGRATION_CONFIGS[provider] || {
      icon: 'apps',
      color: theme.sendButton,
      name: provider,
    };
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent={true}
      onRequestClose={onClose}
    >
      <View style={styles.modalOverlay}>
        <View style={[styles.modalContainer, { backgroundColor: theme.background }, isMobile && styles.modalContainerMobile]}>
          {/* Header */}
          <View style={[styles.header, { borderBottomColor: theme.divider }]}>
            <Text style={[styles.headerTitle, { color: theme.text }]}>
              Select Query Sources
            </Text>
            <TouchableOpacity onPress={onClose} style={styles.closeButton}>
              <Ionicons name="close" size={24} color={theme.text} />
            </TouchableOpacity>
          </View>

          {/* Content */}
          <ScrollView style={styles.scrollView}>
            {/* Section: Core Data Sources */}
            <Text style={[styles.sectionTitle, { color: theme.secondaryText }]}>
              Core Data Sources
            </Text>

            {/* 0. Citra AI + Database - Always enabled, non-toggleable */}
            <ConnectionInfoRow
              icon="sparkles"
              label="Citra AI + Database"
              description={getAIDescription()}
              iconColor="#6366f1"
              theme={theme}
            />

            {/* 1. Vault Toggle — personal uploads are not a chat source any
                more. Main chat searches dept MCP sources and the enterprise
                SOP library only. See config/featureFlags.js. */}
            {PERSONAL_VAULT_ENABLED && (
              <ConnectionToggle
                icon="cloud-done"
                label="Data Store"
                description="Search your uploaded documents"
                value={useVault}
                onChange={onUseVaultChange}
                theme={theme}
              />
            )}

            {/* 2. Internet Toggle */}
            <ConnectionToggle
              icon="globe"
              label="Research using Internet"
              description="Search the web for information"
              value={useInternet}
              onChange={onUseInternetChange}
              theme={theme}
            />

            {/* 3. Enterprise Data Store — ALWAYS ON, not a toggle. The dept
                MCP sources and the enterprise SOP library are the only data
                sources this chat has now that the personal Data Store is gone
                (config/featureFlags.js). Letting a user switch them off would
                leave a chat that can search nothing. Citra-Service forces
                use_enterprise_data=true on /query/stream regardless of what
                the client sends. */}
            <ConnectionInfoRow
              icon="business"
              label="Enterprise Data Store"
              description="Governed MCP sources and your SOP library"
              iconColor="#f59e0b"
              theme={theme}
            />

            {/* ENTERPRISE FEATURE REMOVED - Shared Repository disabled */}
            {/* Section: Enterprise Apps - DISABLED */}
            {/* 
            {connectedIntegrations.length > 0 && (
              <>
                <Text style={[styles.sectionTitle, { color: theme.secondaryText, marginTop: 24 }]}>
                  Enterprise Apps
                </Text>

                {connectedIntegrations.map((integration) => {
                  const config = getProviderConfig(integration.provider);
                  return (
                    <ConnectionToggle
                      key={integration.provider}
                      icon={config.icon}
                      iconColor={config.color}
                      label={config.name}
                      description={`Search ${config.name} data`}
                      value={selectedIntegrations.includes(integration.provider)}
                      onChange={(val) => onIntegrationToggle(integration.provider, val)}
                      badge={integration.status === 'active' ? 'Connected' : 'Error'}
                      theme={theme}
                    />
                  );
                })}
              </>
            )}

            {connectedIntegrations.length === 0 && (
              <View style={[styles.emptyState, { backgroundColor: theme.card }]}>
                <Ionicons name="apps-outline" size={48} color={theme.secondaryText} />
                <Text style={[styles.emptyStateText, { color: theme.secondaryText }]}>
                  No enterprise apps connected yet
                </Text>
                <Text style={[styles.emptyStateSubtext, { color: theme.secondaryText }]}>
                  Connect apps from the Connection tab in the ribbon menu
                </Text>
              </View>
            )}
            */}
          </ScrollView>

          {/* Footer Summary */}
          <View style={[styles.footer, { borderTopColor: theme.divider, backgroundColor: theme.background }]}>
            <View style={styles.summaryContainer}>
              <Ionicons name="checkmark-circle" size={20} color="#10b981" />
              <Text style={[styles.summaryText, { color: theme.text }]}>
                {getTotalActiveCount()} {getTotalActiveCount() === 1 ? 'source' : 'sources'} selected
              </Text>
            </View>
            <TouchableOpacity
              style={[styles.doneButton, { backgroundColor: theme.sendButton }]}
              onPress={onClose}
            >
              <Text style={styles.doneButtonText}>Done</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>

      {/* Internet Search Warning Modal */}
      <Modal
        visible={showInternetWarningModal}
        animationType="fade"
        transparent={true}
        onRequestClose={onCancelDisableInternet}
      >
        <View style={styles.warningModalOverlay}>
          <View style={[styles.warningModalContainer, { backgroundColor: theme.background }]}>
            {/* Warning Icon */}
            <View style={styles.warningIconContainer}>
              <Ionicons name="warning" size={48} color="#f59e0b" />
            </View>

            {/* Warning Title */}
            <Text style={[styles.warningTitle, { color: theme.text }]}>
              Disable Internet Research?
            </Text>

            {/* Warning Message */}
            <Text style={[styles.warningMessage, { color: theme.secondaryText }]}>
              Turning off internet search will significantly impact your results and research capabilities.
              You will lose access to the latest information, recent developments, and current events.
            </Text>

            {/* Warning Details */}
            <View style={[styles.warningDetailsContainer, { backgroundColor: theme.card }]}>
              <Text style={[styles.warningDetailsTitle, { color: theme.text }]}>
                You will lose access to:
              </Text>
              <View style={styles.warningDetailItem}>
                <Ionicons name="close-circle" size={16} color="#ef4444" />
                <Text style={[styles.warningDetailText, { color: theme.secondaryText }]}>
                  Latest news and current events
                </Text>
              </View>
              <View style={styles.warningDetailItem}>
                <Ionicons name="close-circle" size={16} color="#ef4444" />
                <Text style={[styles.warningDetailText, { color: theme.secondaryText }]}>
                  Recent industry updates and developments
                </Text>
              </View>
              <View style={styles.warningDetailItem}>
                <Ionicons name="close-circle" size={16} color="#ef4444" />
                <Text style={[styles.warningDetailText, { color: theme.secondaryText }]}>
                  Up-to-date research and publications
                </Text>
              </View>
              <View style={styles.warningDetailItem}>
                <Ionicons name="close-circle" size={16} color="#ef4444" />
                <Text style={[styles.warningDetailText, { color: theme.secondaryText }]}>
                  Real-time information verification
                </Text>
              </View>
            </View>

            {/* Action Buttons */}
            <View style={styles.warningButtonsContainer}>
              <TouchableOpacity
                style={[styles.warningCancelButton, { borderColor: theme.border }]}
                onPress={onCancelDisableInternet}
              >
                <Text style={[styles.warningCancelButtonText, { color: theme.text }]}>
                  Keep Enabled
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.warningConfirmButton, { backgroundColor: '#ef4444' }]}
                onPress={onConfirmDisableInternet}
              >
                <Text style={styles.warningConfirmButtonText}>
                  Disable Anyway
                </Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'flex-end',
    alignItems: 'center',
  },
  modalContainer: {
    height: '80%',
    width: '50%',
    maxWidth: 800,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -2 },
    shadowOpacity: 0.25,
    shadowRadius: 4,
    elevation: 5,
  },
  modalContainerMobile: {
    width: '100%',
    maxWidth: '100%',
    height: '70%',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
  },
  headerTitle: {
    fontSize: 20,
    fontWeight: '600',
  },
  closeButton: {
    padding: 4,
  },
  scrollView: {
    flex: 1,
    padding: 16,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 12,
    marginTop: 8,
  },
  toggleContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    borderRadius: 12,
    marginBottom: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 1,
  },
  toggleLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    marginRight: 12,
  },
  iconContainer: {
    width: 48,
    height: 48,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  toggleText: {
    flex: 1,
  },
  labelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
  },
  toggleLabel: {
    fontSize: 16,
    fontWeight: '500',
  },
  badge: {
    marginLeft: 8,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 12,
  },
  badgeText: {
    color: 'white',
    fontSize: 10,
    fontWeight: '600',
  },
  toggleDescription: {
    fontSize: 13,
    lineHeight: 18,
  },
  nestedComponent: {
    marginLeft: 16,
    marginBottom: 12,
    padding: 12,
    borderRadius: 8,
  },
  emptyState: {
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    borderRadius: 12,
    marginTop: 16,
  },
  emptyStateText: {
    fontSize: 16,
    fontWeight: '500',
    marginTop: 12,
  },
  emptyStateSubtext: {
    fontSize: 14,
    marginTop: 4,
    textAlign: 'center',
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    borderTopWidth: 1,
  },
  summaryContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  summaryText: {
    fontSize: 14,
    fontWeight: '500',
    marginLeft: 8,
  },
  doneButton: {
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
  },
  doneButtonText: {
    color: 'white',
    fontSize: 16,
    fontWeight: '600',
  },
  alwaysOnIndicator: {
    padding: 4,
  },
  // Warning Modal Styles
  warningModalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  warningModalContainer: {
    width: '90%',
    maxWidth: 500,
    borderRadius: 16,
    padding: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 10,
  },
  warningIconContainer: {
    alignItems: 'center',
    marginBottom: 16,
  },
  warningTitle: {
    fontSize: 22,
    fontWeight: '700',
    textAlign: 'center',
    marginBottom: 12,
  },
  warningMessage: {
    fontSize: 15,
    lineHeight: 22,
    textAlign: 'center',
    marginBottom: 20,
  },
  warningDetailsContainer: {
    borderRadius: 12,
    padding: 16,
    marginBottom: 24,
  },
  warningDetailsTitle: {
    fontSize: 14,
    fontWeight: '600',
    marginBottom: 12,
  },
  warningDetailItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  warningDetailText: {
    fontSize: 14,
    marginLeft: 8,
    flex: 1,
  },
  warningButtonsContainer: {
    flexDirection: 'row',
    gap: 12,
  },
  warningCancelButton: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 10,
    borderWidth: 2,
    alignItems: 'center',
  },
  warningCancelButtonText: {
    fontSize: 16,
    fontWeight: '600',
  },
  warningConfirmButton: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
  },
  warningConfirmButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: 'white',
  },
});
