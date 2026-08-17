// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import React, { useState, useRef, useEffect } from 'react';
import { View, Text, TouchableOpacity, Animated, Platform, Image, Dimensions, Modal, TextInput, ActivityIndicator, Alert } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useUser } from '../UserProvider';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { modernStyles, SPACING, BORDER_RADIUS } from '../../styles/modernStyles';
import authService from '../../services/authService';
import { CONFIG as API_CONFIG } from '../../config/config';

const ProfileMenu = ({ theme }) => {
  const [open, setOpen] = useState(false);
  const { user: userResponse, clearAuthenticationState } = useUser();
  const user = userResponse?.data?.user;
  const fade = useRef(new Animated.Value(0)).current;
  // Safe require for react-dom on web to render a portal that can fully block underlying DOM clicks
  let ReactDOM = null;
  if (Platform.OS === 'web') {
    try { ReactDOM = require('react-dom'); } catch (e) { ReactDOM = null; }
  }

  useEffect(() => {
    Animated.timing(fade, {
      toValue: open ? 1 : 0,
      duration: 160,
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  }, [open, fade]);

  const handleLogout = async () => {
    setOpen(false);
    await clearAuthenticationState();
    // On web force a reload so top-level state/UI updates immediately
    if (typeof window !== 'undefined' && window?.location) {
      try { window.location.reload(); } catch (e) { /* ignore */ }
    }
  };

  // --- Delete Account state ---
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');

  const handleDeleteAccount = async () => {
    if (deleteConfirmText !== 'DELETE') return;
    setIsDeleting(true);
    setDeleteError('');
    try {
      const response = await authService.authenticatedFetch(
        `${API_CONFIG.AUTH.baseUrl}/delete-account`,
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmation: 'DELETE' }),
        }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `Failed to delete account (${response.status})`);
      }
      // Account deleted — clear local state and reload
      setShowDeleteModal(false);
      await clearAuthenticationState();
      if (typeof window !== 'undefined' && window?.location) {
        try { window.location.reload(); } catch (e) { /* ignore */ }
      }
    } catch (err) {
      console.error('❌ [ProfileMenu] Delete account error:', err);
      setDeleteError(err.message || 'Something went wrong. Please try again.');
    } finally {
      setIsDeleting(false);
    }
  };

  const [fallback, setFallback] = useState(null);

  const { isInitialized } = useUser();

  useEffect(() => {
    // If the user context isn't populated after initialization, try AsyncStorage fallback
    if (!user && isInitialized) {
      (async () => {
        try {
          const s = await AsyncStorage.getItem('user_profile');
          if (s) {
            try { setFallback(JSON.parse(s)); return; } catch (e) { /* ignore */ }
          }

          // Some parts of the app store the full auth object under '@user'
          const saved = await AsyncStorage.getItem('@user');
          if (saved) {
            try {
              const parsed = JSON.parse(saved);
              // auth object shape: { data: { user: { ... } , token } }
              if (parsed?.data?.user) {
                setFallback(parsed.data.user);
                return;
              }
            } catch (e) { /* ignore */ }
          }
        } catch (e) {
          // ignore storage errors
        }
      })();
    }
  }, [user, isInitialized]);

  const profile = user || fallback || {};
  // Fail fast if no valid user data is available instead of masking with Guest
  if (!profile || (!profile.email && !profile.username)) {
    // Return null to hide profile menu when not properly authenticated
    return null;
  }
  
  const displayName = profile?.name || profile?.fullName || profile?.displayName || (profile?.email ? profile.email.split('@')[0] : 'Unknown User');
  const displayEmail = profile?.email || profile?.username || '';

  return (
    <View style={{ position: 'relative' }} pointerEvents="box-none">
      <TouchableOpacity
        onPress={() => setOpen((v) => !v)}
        style={{ padding: 6, marginRight: 4 }}
        accessibilityLabel="User menu"
        accessibilityRole="button"
      >
        {profile?.profilePicture ? (
          <Image
            source={{ uri: profile.profilePicture }}
            style={{ width: 38, height: 38, borderRadius: 19, backgroundColor: theme.surface }}
          />
        ) : (
          <Ionicons name="person-circle" size={38} color={theme.primary} />
        )}
      </TouchableOpacity>

      {open && (
        // Debug: log stored values when opening the menu
        (async () => {
          try {
            const up = await AsyncStorage.getItem('user_profile');
            const at = await AsyncStorage.getItem('@user');
            console.log('ProfileMenu open - AsyncStorage user_profile:', up);
            console.log('ProfileMenu open - AsyncStorage @user:', at);
          } catch (e) {
            // ignore
          }
        })(),

        // If running on web and react-dom is available, render the overlay+menu into a portal attached to document.body
        (Platform.OS === 'web' && ReactDOM) ? ReactDOM.createPortal(
          <>
            <div style={{ position: 'fixed', inset: 0, zIndex: 99998 }} onClick={() => setOpen(false)} />

            <Animated.View
              pointerEvents="auto"
              onStartShouldSetResponder={() => true}
              onResponderStart={(e) => {
                try { e.stopPropagation && e.stopPropagation(); e.preventDefault && e.preventDefault(); } catch (err) {}
              }}
              style={{
                position: 'absolute',
                left: 30,
                top: 80,
                minWidth: 220,
                zIndex: 99999,
                elevation: 99999,
                opacity: fade,
              }}
            >
              <View style={[modernStyles.modernDropdownContainer, {
                backgroundColor: theme.surfaceVariant,
                borderColor: theme.border,
                borderRadius: BORDER_RADIUS.md,
                minWidth: 220,
                overflow: 'hidden',
              }]}>
                <View style={[modernStyles.modernDropdownItem, { paddingVertical: SPACING.sm, paddingHorizontal: SPACING.md, backgroundColor: 'transparent' }]}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ color: theme.text, fontWeight: '600' }} numberOfLines={1}>{displayName}</Text>
                    {displayEmail ? (
                      <Text style={{ color: theme.textSecondary, fontSize: 12 }} numberOfLines={1}>{displayEmail}</Text>
                    ) : null}
                  </View>
                </View>

                <TouchableOpacity
                  onPress={() => {
                    setOpen(false);
                    Alert.alert(
                      'Account deletion is admin-only',
                      'To prevent accidental loss of workflows and smart-apps that teammates depend on, account deletion is now handled by your org admin or dept admin. They will choose what happens to each of your resources (transfer to a service account, hand off to dept, archive, or delete) before removing the account.\n\nReach out to your admin to start the process.',
                      [{ text: 'OK' }],
                    );
                  }}
                  style={[modernStyles.modernDropdownItem, { paddingVertical: SPACING.md, paddingHorizontal: SPACING.md }]}
                  activeOpacity={0.7}
                >
                  <Ionicons name="information-circle-outline" size={16} color="#666" style={{ marginRight: SPACING.md }} />
                  <Text style={{ color: '#666', fontWeight: '500' }}>Request account deletion</Text>
                </TouchableOpacity>

                <View style={{ height: 1, backgroundColor: theme.border }} />

                <TouchableOpacity onPress={handleLogout} style={[modernStyles.modernDropdownItem, { paddingVertical: SPACING.md, paddingHorizontal: SPACING.md }]} activeOpacity={0.7}>
                  <Ionicons name="log-out-outline" size={16} color={theme.primary} style={{ marginRight: SPACING.md }} />
                  <Text style={{ color: theme.primary, fontWeight: '600' }}>Logout</Text>
                </TouchableOpacity>
              </View>
            </Animated.View>
          </>, document.body
        ) : (
          // Native fallback: place overlay and menu inline so touchables still block
          <>
            <TouchableOpacity
              activeOpacity={1}
              onPress={() => setOpen(false)}
              style={{
                position: Platform.OS === 'web' ? 'fixed' : 'absolute',
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                zIndex: 99998,
                backgroundColor: 'transparent'
              }}
            />

            <Animated.View
              pointerEvents="auto"
              onStartShouldSetResponder={() => true}
              onResponderStart={(e) => {
                try { e.stopPropagation && e.stopPropagation(); e.preventDefault && e.preventDefault(); } catch (err) {}
              }}
              style={{
                position: 'absolute',
                left: 30,
                top: Platform.OS === 'web' ? 80 : 50,
                minWidth: 220,
                zIndex: 99999,
                elevation: 99999,
                opacity: fade,
              }}
            >
              <View style={[modernStyles.modernDropdownContainer, {
                backgroundColor: theme.surfaceVariant,
                borderColor: theme.border,
                borderRadius: BORDER_RADIUS.md,
                minWidth: 220,
                overflow: 'hidden',
              }]}>
                <View style={[modernStyles.modernDropdownItem, { paddingVertical: SPACING.sm, paddingHorizontal: SPACING.md, backgroundColor: 'transparent' }]}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ color: theme.text, fontWeight: '600' }} numberOfLines={1}>{displayName}</Text>
                    {displayEmail ? (
                      <Text style={{ color: theme.textSecondary, fontSize: 12 }} numberOfLines={1}>{displayEmail}</Text>
                    ) : null}
                  </View>
                </View>

                <TouchableOpacity
                  onPress={() => {
                    setOpen(false);
                    Alert.alert(
                      'Account deletion is admin-only',
                      'To prevent accidental loss of workflows and smart-apps that teammates depend on, account deletion is now handled by your org admin or dept admin. They will choose what happens to each of your resources (transfer to a service account, hand off to dept, archive, or delete) before removing the account.\n\nReach out to your admin to start the process.',
                      [{ text: 'OK' }],
                    );
                  }}
                  style={[modernStyles.modernDropdownItem, { paddingVertical: SPACING.md, paddingHorizontal: SPACING.md }]}
                  activeOpacity={0.7}
                >
                  <Ionicons name="information-circle-outline" size={16} color="#666" style={{ marginRight: SPACING.md }} />
                  <Text style={{ color: '#666', fontWeight: '500' }}>Request account deletion</Text>
                </TouchableOpacity>

                <View style={{ height: 1, backgroundColor: theme.border }} />

                <TouchableOpacity onPress={handleLogout} style={[modernStyles.modernDropdownItem, { paddingVertical: SPACING.md, paddingHorizontal: SPACING.md }]} activeOpacity={0.7}>
                  <Ionicons name="log-out-outline" size={16} color={theme.primary} style={{ marginRight: SPACING.md }} />
                  <Text style={{ color: theme.primary, fontWeight: '600' }}>Logout</Text>
                </TouchableOpacity>
              </View>
            </Animated.View>
          </>
        )
      )}

      {/* Delete Account Confirmation Modal */}
      <Modal
        visible={showDeleteModal}
        transparent
        animationType="fade"
        onRequestClose={() => !isDeleting && setShowDeleteModal(false)}
      >
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', alignItems: 'center', padding: 20 }}>
          <View style={{ backgroundColor: theme.surfaceVariant || '#fff', borderRadius: BORDER_RADIUS.md, padding: SPACING.lg, maxWidth: 400, width: '100%' }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.md }}>
              <Ionicons name="warning-outline" size={24} color="#dc2626" style={{ marginRight: SPACING.sm }} />
              <Text style={{ color: '#dc2626', fontWeight: '700', fontSize: 18 }}>Delete Account</Text>
            </View>

            <Text style={{ color: theme.text, fontSize: 14, lineHeight: 20, marginBottom: SPACING.sm }}>
              This action is <Text style={{ fontWeight: '700' }}>permanent and irreversible</Text>. All your data will be deleted, including:
            </Text>
            <Text style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 20, marginBottom: SPACING.md }}>
              {'\u2022'} All documents, files, and vault contents{'\n'}
              {'\u2022'} Chat history and audio transcripts{'\n'}
              {'\u2022'} Notes, presentations, and diagrams{'\n'}
              {'\u2022'} All project and workspace data{'\n'}
              {'\u2022'} Your account and profile
            </Text>

            <Text style={{ color: theme.text, fontSize: 14, marginBottom: SPACING.sm }}>
              Type <Text style={{ fontWeight: '700', fontFamily: Platform.OS === 'web' ? 'monospace' : undefined }}>DELETE</Text> to confirm:
            </Text>

            <TextInput
              value={deleteConfirmText}
              onChangeText={setDeleteConfirmText}
              placeholder="Type DELETE"
              placeholderTextColor={theme.textSecondary}
              editable={!isDeleting}
              autoCapitalize="characters"
              style={{
                borderWidth: 1,
                borderColor: deleteConfirmText === 'DELETE' ? '#dc2626' : theme.border,
                borderRadius: BORDER_RADIUS.sm,
                paddingHorizontal: SPACING.sm,
                paddingVertical: SPACING.sm,
                color: theme.text,
                fontSize: 16,
                fontFamily: Platform.OS === 'web' ? 'monospace' : undefined,
                marginBottom: SPACING.sm,
                backgroundColor: theme.surface || '#fff',
              }}
            />

            {deleteError ? (
              <Text style={{ color: '#dc2626', fontSize: 13, marginBottom: SPACING.sm }}>{deleteError}</Text>
            ) : null}

            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: SPACING.sm, marginTop: SPACING.sm }}>
              <TouchableOpacity
                onPress={() => setShowDeleteModal(false)}
                disabled={isDeleting}
                style={{
                  paddingVertical: SPACING.sm,
                  paddingHorizontal: SPACING.md,
                  borderRadius: BORDER_RADIUS.sm,
                  borderWidth: 1,
                  borderColor: theme.border,
                }}
                activeOpacity={0.7}
              >
                <Text style={{ color: theme.text, fontWeight: '600' }}>Cancel</Text>
              </TouchableOpacity>

              <TouchableOpacity
                onPress={handleDeleteAccount}
                disabled={deleteConfirmText !== 'DELETE' || isDeleting}
                style={{
                  paddingVertical: SPACING.sm,
                  paddingHorizontal: SPACING.md,
                  borderRadius: BORDER_RADIUS.sm,
                  backgroundColor: deleteConfirmText === 'DELETE' ? '#dc2626' : '#999',
                  opacity: deleteConfirmText === 'DELETE' && !isDeleting ? 1 : 0.5,
                  flexDirection: 'row',
                  alignItems: 'center',
                }}
                activeOpacity={0.7}
              >
                {isDeleting ? (
                  <ActivityIndicator size="small" color="#fff" style={{ marginRight: 6 }} />
                ) : null}
                <Text style={{ color: '#fff', fontWeight: '700' }}>{isDeleting ? 'Deleting…' : 'Delete My Account'}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
};

/**
 * AccountActionsModal — shared account actions modal (Logout + Delete My Account).
 * Reuses the exact same handlers and confirmation flow as ProfileMenu.
 * Import and render this wherever an "Account" action entry is needed.
 */
export const AccountActionsModal = ({ visible, onClose, theme }) => {
  const { user: userResponse, clearAuthenticationState, isInitialized } = useUser();
  const user = userResponse?.data?.user;
  const [fallback, setFallback] = useState(null);

  useEffect(() => {
    if (!user && isInitialized) {
      (async () => {
        try {
          const s = await AsyncStorage.getItem('user_profile');
          if (s) {
            try { setFallback(JSON.parse(s)); return; } catch (e) { /* ignore */ }
          }
          const saved = await AsyncStorage.getItem('@user');
          if (saved) {
            try {
              const parsed = JSON.parse(saved);
              if (parsed?.data?.user) { setFallback(parsed.data.user); return; }
            } catch (e) { /* ignore */ }
          }
        } catch (e) { /* ignore */ }
      })();
    }
  }, [user, isInitialized]);

  const profile = user || fallback || {};
  const displayName = profile?.name || profile?.fullName || profile?.displayName || (profile?.email ? profile.email.split('@')[0] : 'Unknown User');
  const displayEmail = profile?.email || profile?.username || '';

  // --- Handlers (same logic as ProfileMenu) ---
  const handleLogout = async () => {
    onClose();
    await clearAuthenticationState();
    if (typeof window !== 'undefined' && window?.location) {
      try { window.location.reload(); } catch (e) { /* ignore */ }
    }
  };

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');

  const handleDeleteAccount = async () => {
    if (deleteConfirmText !== 'DELETE') return;
    setIsDeleting(true);
    setDeleteError('');
    try {
      const response = await authService.authenticatedFetch(
        `${API_CONFIG.AUTH.baseUrl}/delete-account`,
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmation: 'DELETE' }),
        }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `Failed to delete account (${response.status})`);
      }
      setShowDeleteConfirm(false);
      onClose();
      await clearAuthenticationState();
      if (typeof window !== 'undefined' && window?.location) {
        try { window.location.reload(); } catch (e) { /* ignore */ }
      }
    } catch (err) {
      console.error('❌ [AccountActionsModal] Delete account error:', err);
      setDeleteError(err.message || 'Something went wrong. Please try again.');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleClose = () => {
    if (!isDeleting) {
      setShowDeleteConfirm(false);
      setDeleteConfirmText('');
      setDeleteError('');
      onClose();
    }
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={handleClose}
    >
      <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', alignItems: 'center', padding: 20 }}>
        {!showDeleteConfirm ? (
          /* ── Main account actions card ── */
          <View style={{ backgroundColor: theme.surfaceVariant || '#fff', borderRadius: BORDER_RADIUS.md, padding: SPACING.lg, maxWidth: 400, width: '100%' }}>
            {/* User info + close */}
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.md }}>
              <Ionicons name="person-circle-outline" size={26} color={theme.primary} style={{ marginRight: SPACING.sm }} />
              <View style={{ flex: 1 }}>
                <Text style={{ color: theme.text, fontWeight: '700', fontSize: 15 }} numberOfLines={1}>{displayName}</Text>
                {displayEmail ? (
                  <Text style={{ color: theme.textSecondary, fontSize: 12 }} numberOfLines={1}>{displayEmail}</Text>
                ) : null}
              </View>
              <TouchableOpacity onPress={handleClose} style={{ padding: 4 }} activeOpacity={0.7} accessibilityLabel="Close account menu">
                <Ionicons name="close" size={20} color={theme.textSecondary} />
              </TouchableOpacity>
            </View>

            <View style={{ height: 1, backgroundColor: theme.border, marginBottom: SPACING.sm }} />

            {/* Logout */}
            <TouchableOpacity
              onPress={handleLogout}
              style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: SPACING.md, paddingHorizontal: SPACING.sm, borderRadius: BORDER_RADIUS.sm }}
              activeOpacity={0.7}
            >
              <Ionicons name="log-out-outline" size={18} color={theme.primary} style={{ marginRight: SPACING.md }} />
              <Text style={{ color: theme.primary, fontWeight: '600', fontSize: 15 }}>Logout</Text>
            </TouchableOpacity>

            {/* Delete My Account hidden — account deletion is admin-only (see Phase E plan).
                Kept in code for potential re-enable. */}
            {false && (
              <>
                <View style={{ height: 1, backgroundColor: theme.border }} />

                <TouchableOpacity
                  onPress={() => Alert.alert(
                    'Account deletion is admin-only',
                    'To prevent accidental loss of workflows and smart-apps that teammates depend on, account deletion is now handled by your org admin or dept admin. Reach out to your admin to start the per-resource picker flow.',
                    [{ text: 'OK' }],
                  )}
                  style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: SPACING.md, paddingHorizontal: SPACING.sm, borderRadius: BORDER_RADIUS.sm }}
                  activeOpacity={0.7}
                >
                  <Ionicons name="trash-outline" size={18} color="#dc2626" style={{ marginRight: SPACING.md }} />
                  <Text style={{ color: '#dc2626', fontWeight: '600', fontSize: 15 }}>Delete My Account</Text>
                </TouchableOpacity>
              </>
            )}
          </View>
        ) : (
          /* ── Delete confirmation card (identical UX to ProfileMenu) ── */
          <View style={{ backgroundColor: theme.surfaceVariant || '#fff', borderRadius: BORDER_RADIUS.md, padding: SPACING.lg, maxWidth: 400, width: '100%' }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.md }}>
              <Ionicons name="warning-outline" size={24} color="#dc2626" style={{ marginRight: SPACING.sm }} />
              <Text style={{ color: '#dc2626', fontWeight: '700', fontSize: 18 }}>Delete Account</Text>
            </View>

            <Text style={{ color: theme.text, fontSize: 14, lineHeight: 20, marginBottom: SPACING.sm }}>
              This action is <Text style={{ fontWeight: '700' }}>permanent and irreversible</Text>. All your data will be deleted, including:
            </Text>
            <Text style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 20, marginBottom: SPACING.md }}>
              {'\u2022'} All documents, files, and vault contents{'\n'}
              {'\u2022'} Chat history and audio transcripts{'\n'}
              {'\u2022'} Notes, presentations, and diagrams{'\n'}
              {'\u2022'} All project and workspace data{'\n'}
              {'\u2022'} Your account and profile
            </Text>

            <Text style={{ color: theme.text, fontSize: 14, marginBottom: SPACING.sm }}>
              Type <Text style={{ fontWeight: '700', fontFamily: Platform.OS === 'web' ? 'monospace' : undefined }}>DELETE</Text> to confirm:
            </Text>

            <TextInput
              value={deleteConfirmText}
              onChangeText={setDeleteConfirmText}
              placeholder="Type DELETE"
              placeholderTextColor={theme.textSecondary}
              editable={!isDeleting}
              autoCapitalize="characters"
              style={{
                borderWidth: 1,
                borderColor: deleteConfirmText === 'DELETE' ? '#dc2626' : theme.border,
                borderRadius: BORDER_RADIUS.sm,
                paddingHorizontal: SPACING.sm,
                paddingVertical: SPACING.sm,
                color: theme.text,
                fontSize: 16,
                fontFamily: Platform.OS === 'web' ? 'monospace' : undefined,
                marginBottom: SPACING.sm,
                backgroundColor: theme.surface || '#fff',
              }}
            />

            {deleteError ? (
              <Text style={{ color: '#dc2626', fontSize: 13, marginBottom: SPACING.sm }}>{deleteError}</Text>
            ) : null}

            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: SPACING.sm, marginTop: SPACING.sm }}>
              <TouchableOpacity
                onPress={() => { setShowDeleteConfirm(false); setDeleteConfirmText(''); setDeleteError(''); }}
                disabled={isDeleting}
                style={{ paddingVertical: SPACING.sm, paddingHorizontal: SPACING.md, borderRadius: BORDER_RADIUS.sm, borderWidth: 1, borderColor: theme.border }}
                activeOpacity={0.7}
              >
                <Text style={{ color: theme.text, fontWeight: '600' }}>Cancel</Text>
              </TouchableOpacity>

              <TouchableOpacity
                onPress={handleDeleteAccount}
                disabled={deleteConfirmText !== 'DELETE' || isDeleting}
                style={{
                  paddingVertical: SPACING.sm,
                  paddingHorizontal: SPACING.md,
                  borderRadius: BORDER_RADIUS.sm,
                  backgroundColor: deleteConfirmText === 'DELETE' ? '#dc2626' : '#999',
                  opacity: deleteConfirmText === 'DELETE' && !isDeleting ? 1 : 0.5,
                  flexDirection: 'row',
                  alignItems: 'center',
                }}
                activeOpacity={0.7}
              >
                {isDeleting ? (
                  <ActivityIndicator size="small" color="#fff" style={{ marginRight: 6 }} />
                ) : null}
                <Text style={{ color: '#fff', fontWeight: '700' }}>{isDeleting ? 'Deleting…' : 'Delete My Account'}</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}
      </View>
    </Modal>
  );
};

export default ProfileMenu;
