/**
 * ShareCollaborationModal.js - Generic modal for sharing resources with other users
 * (Team sharing removed along with Teams — email sharing only.)
 * 
 * This component provides:
 * - Email input for sharing resource access
 * - Permission level selection (read/write)
 * - List of current shares with ability to revoke
 * - Success/error feedback
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  Modal,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Alert,
  Animated,
  Platform,
  Easing
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { API_CONFIG } from '../config/config';
import authService from '../services/authService';

const ShareCollaborationModal = ({
  visible,
  onClose,
  resource,
  resourceType = 'vault', // 'vault', 'presentation', 'report', 'diagram', 'printable'
  theme,
  apiConfig: apiConfigProp,
  authToken: authTokenProp,
  userEmail
}) => {
  // Use provided apiConfig or fall back to imported API_CONFIG
  const apiConfig = apiConfigProp || API_CONFIG;

  // Resolve auth token - authService.getToken() is async, so we must use state
  const [authToken, setAuthToken] = useState(authTokenProp || null);

  useEffect(() => {
    if (authTokenProp) {
      setAuthToken(authTokenProp);
      return;
    }
    // Async fallback: load token from authService
    const loadToken = async () => {
      try {
        const token = await authService.getToken();
        setAuthToken(token);
      } catch (err) {
        console.warn('[ShareCollaborationModal] Failed to load auth token:', err);
      }
    };
    loadToken();
  }, [authTokenProp]);
  
  const [email, setEmail] = useState('');
  const [permission, setPermission] = useState('read');
  const [isSharing, setIsSharing] = useState(false);
  const [shares, setShares] = useState([]);
  const [isLoadingShares, setIsLoadingShares] = useState(false);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);

  // Animation refs
  const scaleAnim = useRef(new Animated.Value(0.9)).current;
  const opacityAnim = useRef(new Animated.Value(0)).current;

  // Animate modal entrance
  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.spring(scaleAnim, {
          toValue: 1,
          tension: 100,
          friction: 10,
          useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(opacityAnim, {
          toValue: 1,
          duration: 200,
          easing: Easing.out(Easing.quad),
          useNativeDriver: Platform.OS !== 'web',
        })
      ]).start();

      // Load existing shares
      if (resource?.id && authToken) {
        loadShares();
      }
    } else {
      scaleAnim.setValue(0.9);
      opacityAnim.setValue(0);
    }
  }, [visible, resource?.id, authToken]);

  const loadShares = async () => {
    if (!resource?.id || !apiConfig?.API_URL || !authToken) return;

    setIsLoadingShares(true);
    try {
      const response = await fetch(
        `${apiConfig.API_URL}/api/sharing/permissions/${resourceType}/${resource.id}`,
        {
          headers: {
            'Authorization': `Bearer ${authToken}`
          }
        }
      );

      if (response.ok) {
        const data = await response.json();
        setShares(data.shared_with || []);
      } else {
        console.error('Failed to load shares:', response.status);
      }
    } catch (err) {
      console.error('Error loading shares:', err);
    } finally {
      setIsLoadingShares(false);
    }
  };

  const handleShare = async () => {
    setError(null);
    setSuccessMessage(null);

    let targetId = null;

    // Validate inputs
    if (!email.trim()) {
      setError('Please enter an email address');
      return;
    }
    // Basic email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email.trim())) {
      setError('Please enter a valid email address');
      return;
    }
    // Don't allow sharing with yourself
    if (email.trim().toLowerCase() === userEmail?.toLowerCase()) {
      setError('You cannot share a resource with yourself');
      return;
    }

    setIsSharing(true);

    try {
      // Sharing with a user by email — find their ID first
      const searchResp = await fetch(
        `${apiConfig.API_URL}/api/sharing/users/search?query=${encodeURIComponent(email.trim())}`,
        {
          headers: { 'Authorization': `Bearer ${authToken}` }
        }
      );

      if (searchResp.ok) {
        const searchData = await searchResp.json();
        // Find exact match
        const user = searchData.users?.find(u => u.email.toLowerCase() === email.trim().toLowerCase());

        if (!user) {
          setError('User not found. Please check the email address.');
          setIsSharing(false);
          return;
        }
        targetId = user.user_id;
      } else {
        setError('Failed to search for user.');
        setIsSharing(false);
        return;
      }

      const response = await fetch(
        `${apiConfig.API_URL}/api/sharing/share`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${authToken}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            resource_id: resource.id,
            resource_type: resourceType,
            target_id: targetId,
            target_type: 'user',
            permission: permission
          })
        }
      );

      const data = await response.json();

      if (response.ok && data.success) {
        setSuccessMessage(`Shared successfully with ${email}`);
        setEmail('');
        loadShares(); // Refresh the list
      } else {
        setError(data.detail?.message || data.error || 'Failed to share resource');
      }
    } catch (err) {
      console.error('Error sharing resource:', err);
      setError('Failed to share resource. Please try again.');
    } finally {
      setIsSharing(false);
    }
  };

  const handleRevokeShare = async (targetId, type) => {
    const doRevoke = async () => {
      try {
        const response = await fetch(
          `${apiConfig.API_URL}/api/sharing/revoke`,
          {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${authToken}`,
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({
              resource_id: resource.id,
              resource_type: resourceType,
              target_id: targetId,
              target_type: type
            })
          }
        );

        if (response.ok) {
          // Refresh shares list to show the revocation
          loadShares();
        } else {
          if (Platform.OS === 'web') {
            window.alert('Failed to revoke access');
          } else {
            Alert.alert('Error', 'Failed to revoke access');
          }
        }
      } catch (err) {
        console.error('Error revoking share:', err);
        if (Platform.OS === 'web') {
          window.alert('Failed to revoke share');
        } else {
          Alert.alert('Error', 'Failed to revoke share');
        }
      }
    };

    if (Platform.OS === 'web') {
      if (window.confirm(`Remove access to this ${resourceType}?`)) {
        await doRevoke();
      }
    } else {
      Alert.alert(
        'Revoke Access',
        `Remove access to this ${resourceType}?`,
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Revoke', style: 'destructive', onPress: doRevoke }
        ]
      );
    }
  };

  const handleClose = () => {
    setEmail('');
    setError(null);
    setSuccessMessage(null);
    setPermission('read');
    onClose();
  };

  if (!visible) return null;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="none"
      onRequestClose={handleClose}
    >
      <View style={styles.overlay}>
        <Animated.View
          style={[
            styles.modalContainer,
            {
              backgroundColor: theme.surface,
              transform: [{ scale: scaleAnim }],
              opacity: opacityAnim
            }
          ]}
        >
          {/* Header */}
          <View style={[styles.header, { borderBottomColor: theme.border }]}>
            <View style={styles.headerLeft}>
              <Ionicons name="share-social" size={24} color={theme.primary} />
              <Text style={[styles.title, { color: theme.text }]}>
                Share {resourceType.charAt(0).toUpperCase() + resourceType.slice(1)}
              </Text>
            </View>
            <TouchableOpacity onPress={handleClose} style={styles.closeButton}>
              <Ionicons name="close" size={24} color={theme.text} />
            </TouchableOpacity>
          </View>

          {/* Resource Info */}
          <View style={[styles.vaultInfo, { backgroundColor: theme.background }]}>
            <Ionicons name={resourceType === 'vault' ? 'folder' : 'document-text'} size={20} color={theme.primary} />
            <Text style={[styles.vaultName, { color: theme.text }]}>
              {resource?.name || resource?.title || `Unnamed ${resourceType}`}
            </Text>
          </View>

          {/* Share Form */}
          <View style={styles.formSection}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>
              Share with email
            </Text>

            <View style={[styles.inputRow, { borderColor: theme.border }]}>
              <TextInput
                style={[styles.emailInput, { color: theme.text }]}
                value={email}
                onChangeText={(text) => {
                  setEmail(text);
                  setError(null);
                  setSuccessMessage(null);
                }}
                placeholder="Enter email address"
                placeholderTextColor={theme.textSecondary}
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
              />
            </View>

            {/* Submit Button */}
            <TouchableOpacity
              style={[
                styles.shareButton,
                {
                  backgroundColor: email.trim() ? theme.primary : theme.disabled,
                  marginTop: 12,
                  borderRadius: 8
                }
              ]}
              onPress={handleShare}
              disabled={!email.trim() || isSharing}
            >
              {isSharing ? (
                <ActivityIndicator size="small" color="white" />
              ) : (
                <Text style={{ color: 'white', fontWeight: '600' }}>Share</Text>
              )}
            </TouchableOpacity>

            {/* Permission Selector */}
            <View style={styles.permissionSection}>
              <Text style={[styles.label, { color: theme.textSecondary }]}>
                Permission
              </Text>
              <View style={styles.permissionOptions}>
                <TouchableOpacity
                  style={[
                    styles.permissionOption,
                    {
                      backgroundColor: permission === 'read' ? theme.primary + '20' : 'transparent',
                      borderColor: permission === 'read' ? theme.primary : theme.border
                    }
                  ]}
                  onPress={() => setPermission('read')}
                >
                  <Ionicons
                    name="eye-outline"
                    size={16}
                    color={permission === 'read' ? theme.primary : theme.text}
                  />
                  <Text style={[
                    styles.permissionText,
                    { color: permission === 'read' ? theme.primary : theme.text }
                  ]}>
                    View only
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[
                    styles.permissionOption,
                    {
                      backgroundColor: permission === 'write' ? theme.primary + '20' : 'transparent',
                      borderColor: permission === 'write' ? theme.primary : theme.border
                    }
                  ]}
                  onPress={() => setPermission('write')}
                >
                  <Ionicons
                    name="create-outline"
                    size={16}
                    color={permission === 'write' ? theme.primary : theme.text}
                  />
                  <Text style={[
                    styles.permissionText,
                    { color: permission === 'write' ? theme.primary : theme.text }
                  ]}>
                    Can edit
                  </Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* Error/Success Messages */}
            {error && (
              <View style={[styles.messageBox, { backgroundColor: '#ef444420' }]}>
                <Ionicons name="alert-circle" size={16} color="#ef4444" />
                <Text style={[styles.messageText, { color: '#ef4444' }]}>{error}</Text>
              </View>
            )}
            {successMessage && (
              <View style={[styles.messageBox, { backgroundColor: '#10b98120' }]}>
                <Ionicons name="checkmark-circle" size={16} color="#10b981" />
                <Text style={[styles.messageText, { color: '#10b981' }]}>{successMessage}</Text>
              </View>
            )}
          </View>

          {/* Current Shares */}
          <View style={styles.sharesSection}>
            <Text style={[styles.sectionTitle, { color: theme.text }]}>
              Shared with
            </Text>
            {isLoadingShares ? (
              <ActivityIndicator size="small" color={theme.primary} />
            ) : shares.length > 0 ? (
              <ScrollView style={styles.sharesList}>
                {/* User Shares */}
                {shares.map((share, index) => (
                  <View
                    key={`user-${index}`}
                    style={[styles.shareItem, { borderBottomColor: theme.border }]}
                  >
                    <View style={styles.shareInfo}>
                      <Ionicons name="person-circle" size={32} color={theme.textSecondary} />
                      <View style={styles.shareDetails}>
                        <Text style={[styles.shareEmail, { color: theme.text }]}>
                          {share.user_id || share.email}
                        </Text>
                        <Text style={[styles.sharePermission, { color: theme.textSecondary }]}>
                          {share.permission === 'write' ? 'Can edit' : 'View only'}
                        </Text>
                      </View>
                    </View>
                    <TouchableOpacity
                      onPress={() => handleRevokeShare(share.user_id || share.email, 'user')}
                      style={styles.revokeButton}
                    >
                      <Ionicons name="close-circle" size={20} color={theme.error || '#ef4444'} />
                    </TouchableOpacity>
                  </View>
                ))}
              </ScrollView>
            ) : (
              <View style={styles.emptyShares}>
                <Ionicons name="people-outline" size={32} color={theme.textSecondary} />
                <Text style={[styles.emptyText, { color: theme.textSecondary }]}>
                  This {resourceType} isn't shared with anyone yet
                </Text>
              </View>
            )}
          </View>
        </Animated.View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  modalContainer: {
    width: '100%',
    maxWidth: 420,
    borderRadius: 16,
    overflow: 'hidden',
    maxHeight: '80%',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    borderBottomWidth: 1,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  title: {
    fontSize: 18,
    fontWeight: '600',
  },
  closeButton: {
    padding: 4,
  },
  vaultInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 12,
    marginHorizontal: 16,
    marginTop: 16,
    borderRadius: 8,
  },
  vaultName: {
    fontSize: 14,
    fontWeight: '500',
  },
  formSection: {
    padding: 16,
  },
  tabContainer: {
    flexDirection: 'row',
    marginBottom: 16,
    backgroundColor: 'rgba(0,0,0,0.05)',
    padding: 4,
    borderRadius: 8,
  },
  tabButton: {
    flex: 1,
    paddingVertical: 8,
    alignItems: 'center',
    borderRadius: 6,
  },
  tabText: {
    fontSize: 13,
    fontWeight: '600',
  },
  label: {
    fontSize: 12,
    fontWeight: '500',
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: 8,
    overflow: 'hidden',
  },
  emailInput: {
    flex: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
  },
  shareButton: {
    padding: 12,
    justifyContent: 'center',
    alignItems: 'center',
  },
  permissionSection: {
    marginTop: 16,
  },
  permissionOptions: {
    flexDirection: 'row',
    gap: 12,
  },
  permissionOption: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
  },
  permissionText: {
    fontSize: 13,
    fontWeight: '500',
  },
  messageBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    padding: 10,
    borderRadius: 8,
    marginTop: 12,
  },
  messageText: {
    fontSize: 13,
    flex: 1,
  },
  sharesSection: {
    padding: 16,
    paddingTop: 0,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '600',
    marginBottom: 12,
  },
  sharesList: {
    maxHeight: 200,
  },
  shareItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderBottomWidth: 1,
  },
  shareInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  shareIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  shareDetails: {
    gap: 2,
  },
  shareEmail: {
    fontSize: 14,
    fontWeight: '500',
  },
  sharePermission: {
    fontSize: 12,
  },
  revokeButton: {
    padding: 4,
  },
  emptyShares: {
    alignItems: 'center',
    padding: 20,
    gap: 8,
  },
  emptyText: {
    fontSize: 13,
    textAlign: 'center',
  },
  // Dropdown style
  dropdownContainer: {
    borderWidth: 1,
    borderRadius: 8,
    maxHeight: 150,
    marginTop: 4,
  },
  teamItem: {
    padding: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.05)',
  },
  teamName: {
    fontSize: 14,
    fontWeight: '500',
  },
});

export default ShareCollaborationModal;
