/**
 * ShareManager - React component and hook for managing public share links
 * Provides UI for creating, displaying, copying, and revoking share links
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Platform,
  Modal,
  TextInput,
  Pressable,
  Linking,
  Share,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import ShareService, { getPublicUrl } from '../services/ShareService';
import {
  getWhatsAppShareUrl,
  getLinkedInShareUrl,
  getTwitterShareUrl,
  getFacebookShareUrl,
  getEmailShareUrl,
  getTelegramShareUrl,
  openShareUrl,
} from '../services/ShareService';
import BrandingUpsellBanner from './BrandingUpsellBanner';
import * as Clipboard from 'expo-clipboard';
import { CONFIG } from '../config/config';

/**
 * Custom hook for managing share state
 * @param {string} contentType - 'diagram' | 'report' | 'chat'
 * @param {string} sourceId - The ID of the source document
 * @returns {Object} Share state and functions
 */
export const useShare = (contentType, sourceId) => {
  const [shareInfo, setShareInfo] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [isShared, setIsShared] = useState(false);

  // Check if content is already shared
  const checkShareStatus = useCallback(async () => {
    if (!sourceId || !contentType) return;

    try {
      setIsLoading(true);
      setError(null);
      const existingShare = await ShareService.getShareBySource(contentType, sourceId);
      if (existingShare) {
        setShareInfo(existingShare);
        setIsShared(true);
      } else {
        setShareInfo(null);
        setIsShared(false);
      }
    } catch (err) {
      console.error('[useShare] Check status error:', err);
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, [contentType, sourceId]);

  // Create or get share
  const createShare = useCallback(async (title = null) => {
    if (!sourceId || !contentType) return null;

    try {
      setIsLoading(true);
      setError(null);
      const result = await ShareService.createShare(contentType, sourceId, title);
      setShareInfo(result);
      setIsShared(true);
      return result;
    } catch (err) {
      console.error('[useShare] Create share error:', err);
      setError(err.message);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [contentType, sourceId]);

  // Revoke share
  const revokeShare = useCallback(async () => {
    if (!shareInfo?.share_token) return false;

    try {
      setIsLoading(true);
      setError(null);
      await ShareService.revokeShare(shareInfo.share_token);
      setShareInfo(null);
      setIsShared(false);
      return true;
    } catch (err) {
      console.error('[useShare] Revoke share error:', err);
      setError(err.message);
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [shareInfo]);

  // Copy URL to clipboard
  const copyUrl = useCallback(async () => {
    if (!shareInfo?.share_token) return false;
    const url = getPublicUrl(shareInfo.share_token);
    return await ShareService.copyShareUrl(url);
  }, [shareInfo]);

  return {
    shareInfo,
    shareUrl: shareInfo?.share_url || (shareInfo?.share_token ? getPublicUrl(shareInfo.share_token) : null),
    isShared,
    isLoading,
    error,
    createShare,
    revokeShare,
    copyUrl,
    refresh: checkShareStatus,
  };
};

import ShareCollaborationModal from './ShareCollaborationModal';

// ... (imports remain)

export const ShareButton = ({
  contentType,
  sourceId,
  title,
  theme = {},
  size = 'medium',
  showLabel = false,
  onShareCreated,
  onShareRevoked,
  apiConfig,
  authToken,
  userEmail,
  userType = 'free',
  onUpgrade,
  isSharedItem = false,
}) => {
  const {
    shareInfo,
    shareUrl,
    isShared,
    isLoading,
    createShare,
    revokeShare,
    copyUrl,
    refresh,
  } = useShare(contentType, sourceId);

  const [showModal, setShowModal] = useState(false);
  const [showCollaborateModal, setShowCollaborateModal] = useState(false);

  useEffect(() => {
    if (showModal) {
      console.log('[ShareButton] Modal Open. Info:', {
        contentType,
        sourceId,
        shareInfo,
        shareUrl,
        token: shareInfo?.share_token
      });
    }
  }, [showModal, shareInfo, shareUrl, contentType, sourceId]);
  const [copied, setCopied] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  const colors = {
    primary: theme.primary || '#3b82f6',
    background: theme.background || '#1a1a2e',
    surface: theme.surface || '#2d2d44',
    text: theme.text || '#e4e4e7',
    textSecondary: theme.textSecondary || '#a1a1aa',
    success: theme.success || '#22c55e',
    error: theme.error || '#ef4444',
    border: theme.border || 'rgba(255,255,255,0.1)',
  };

  const iconSize = size === 'small' ? 18 : size === 'large' ? 28 : 22;

  const handlePress = async () => {
    if (isShared && shareUrl) {
      // Already shared - open modal to show URL
      setShowModal(true);
    } else {
      // Create new share and show modal
      setIsCreating(true);
      const result = await createShare(title);
      setIsCreating(false);
      if (result && result.share_url) {
        setShowModal(true);
        onShareCreated?.(result);
      }
    }
  };

  const handleCopy = async () => {
    const success = await copyUrl();
    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleRevoke = async () => {
    const success = await revokeShare();
    if (success) {
      setShowModal(false);
      onShareRevoked?.();
    }
  };

  const handleClose = () => {
    setShowModal(false);
    setCopied(false);
  };

  return (
    <View style={styles.container}>
      <TouchableOpacity
        style={[
          styles.button,
          {
            backgroundColor: isShared ? colors.success + '20' : colors.surface,
            borderColor: isShared ? colors.success : colors.border,
          },
        ]}
        onPress={handlePress}
        disabled={isLoading || isCreating}
      >
        {(isLoading || isCreating) ? (
          <ActivityIndicator size="small" color={colors.primary} />
        ) : (
          <>
            <Ionicons
              name={isShared ? 'link' : 'share-outline'}
              size={iconSize}
              color={isShared ? colors.success : colors.text}
            />
            {showLabel && (
              <Text
                style={[
                  styles.buttonLabel,
                  { color: isShared ? colors.success : colors.text },
                ]}
              >
                {isShared ? 'Shared' : 'Share'}
              </Text>
            )}
          </>
        )}
      </TouchableOpacity>

      {/* Share Modal */}
      <Modal
        visible={showModal}
        transparent={true}
        animationType="fade"
        onRequestClose={handleClose}
      >
        <Pressable style={styles.modalOverlay} onPress={handleClose}>
          <Pressable style={[styles.modalContent, { backgroundColor: colors.surface }]} onPress={e => e.stopPropagation()}>
            {/* Modal Header */}
            <View style={styles.modalHeader}>
              <View style={styles.modalTitleRow}>
                <Ionicons name="share-social" size={24} color={colors.primary} />
                <Text style={[styles.modalTitle, { color: colors.text }]}>
                  Share Link
                </Text>
              </View>
              <TouchableOpacity onPress={handleClose} style={styles.modalCloseBtn}>
                <Ionicons name="close" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            {/* Status Badge */}
            <View style={[styles.statusBadge, { backgroundColor: colors.success + '20' }]}>
              <Ionicons name="checkmark-circle" size={16} color={colors.success} />
              <Text style={[styles.statusText, { color: colors.success }]}>
                Publicly Shared
              </Text>
            </View>

            {/* Branding upsell for free users */}
            {onUpgrade && (
              <BrandingUpsellBanner
                userType={userType}
                context="share"
                onUpgrade={onUpgrade}
                theme={theme}
              />
            )}

            {/* URL Display */}
            <Text style={[styles.urlLabel, { color: colors.textSecondary }]}>
              Share URL
            </Text>
            <View style={[styles.urlInputContainer, { backgroundColor: colors.background, borderColor: colors.border }]}>
              <TextInput
                style={[styles.urlInput, { color: colors.text }]}
                value={shareUrl || ''}
                editable={false}
                selectTextOnFocus={true}
                multiline={false}
              />
            </View>

            {/* Action Buttons */}
            <View style={styles.modalActions}>
              <TouchableOpacity
                style={[styles.modalButton, styles.copyButton, { backgroundColor: colors.primary }]}
                onPress={handleCopy}
              >
                <Ionicons
                  name={copied ? 'checkmark-circle' : 'copy-outline'}
                  size={20}
                  color="white"
                />
                <Text style={styles.modalButtonText}>
                  {copied ? 'Copied!' : 'Copy URL'}
                </Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[styles.modalButton, styles.revokeButton, { borderColor: colors.error }]}
                onPress={handleRevoke}
              >
                <Ionicons name="trash-outline" size={20} color={colors.error} />
                <Text style={[styles.modalButtonText, { color: colors.error }]}>
                  Revoke
                </Text>
              </TouchableOpacity>
            </View>

            {/* Social Share Buttons */}
            {shareUrl && (
              <View style={styles.socialSection}>
                <Text style={[styles.socialLabel, { color: colors.textSecondary }]}>Share on</Text>
                {Platform.OS === 'web' ? (
                  <View style={styles.socialRow}>
                    {[
                      { name: 'WhatsApp', icon: 'logo-whatsapp', color: '#25D366', getUrl: () => getWhatsAppShareUrl(shareUrl, ShareService.getShareText(contentType, title)) },
                      { name: 'LinkedIn', icon: 'logo-linkedin', color: '#0A66C2', getUrl: () => getLinkedInShareUrl(shareUrl, ShareService.getShareText(contentType, title)) },
                      { name: 'Twitter', icon: 'logo-twitter', color: '#1DA1F2', getUrl: () => getTwitterShareUrl(shareUrl, ShareService.getShareText(contentType, title)) },
                      { name: 'Facebook', icon: 'logo-facebook', color: '#1877F2', getUrl: () => getFacebookShareUrl(shareUrl, ShareService.getShareText(contentType, title)) },
                      { name: 'Email', icon: 'mail-outline', color: '#6B7280', getUrl: () => getEmailShareUrl(shareUrl, `${title || 'Shared content'} — Citra AI`, ShareService.getShareText(contentType, title)) },
                      { name: 'Telegram', icon: 'paper-plane-outline', color: '#0088CC', getUrl: () => getTelegramShareUrl(shareUrl, ShareService.getShareText(contentType, title)) },
                    ].map((platform) => (
                      <TouchableOpacity
                        key={platform.name}
                        style={[styles.socialButton, { backgroundColor: platform.color + '20' }]}
                        onPress={() => openShareUrl(platform.getUrl())}
                        accessibilityLabel={`Share on ${platform.name}`}
                      >
                        <Ionicons name={platform.icon} size={20} color={platform.color} />
                      </TouchableOpacity>
                    ))}
                  </View>
                ) : (
                  <TouchableOpacity
                    style={[styles.modalButton, { marginTop: 4, backgroundColor: colors.primary }]}
                    onPress={async () => {
                      try {
                        await Share.share({
                          message: ShareService.getShareText(contentType, title) + '\n' + shareUrl,
                          url: shareUrl,
                          title: title || 'Shared from Citra AI',
                        });
                      } catch (e) {
                        // User cancelled
                      }
                    }}
                  >
                    <Ionicons name="share-outline" size={20} color="white" />
                    <Text style={styles.modalButtonText}>Share</Text>
                  </TouchableOpacity>
                )}
              </View>
            )}

            {/* Manage Team Access — HIDDEN for now. Team collaboration is an
                advanced, not-fully-tested feature kept for future; the read-only /
                person / public-link sharing above stays available. Flip the `false`
                to re-enable. */}
            {false && !isSharedItem && (
              <TouchableOpacity
                style={[styles.modalButton, { marginTop: 12, backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.border }]}
                onPress={() => {
                  setShowModal(false);
                  setShowCollaborateModal(true);
                }}
              >
                <Ionicons name="people" size={20} color={colors.text} />
                <Text style={[styles.modalButtonText, { color: colors.text }]}>
                  Manage Team Access
                </Text>
              </TouchableOpacity>
            )}

            {/* Stats */}
            {shareInfo && (
              <View style={[styles.statsContainer, { borderTopColor: colors.border }]}>
                <View style={styles.statItem}>
                  <Ionicons name="eye-outline" size={16} color={colors.textSecondary} />
                  <Text style={[styles.statText, { color: colors.textSecondary }]}>
                    {shareInfo.access_count || 0} view{(shareInfo.access_count || 0) !== 1 ? 's' : ''}
                  </Text>
                </View>
                {shareInfo.created_at && (
                  <View style={styles.statItem}>
                    <Ionicons name="calendar-outline" size={16} color={colors.textSecondary} />
                    <Text style={[styles.statText, { color: colors.textSecondary }]}>
                      Created {new Date(shareInfo.created_at).toLocaleDateString()}
                    </Text>
                  </View>
                )}
              </View>
            )}

            {/* Info Text */}
            <Text style={[styles.infoText, { color: colors.textSecondary }]}>
              Anyone with this link can view this {contentType}. The link always shows the latest version.
            </Text>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Collaboration Modal */}
      <ShareCollaborationModal
        visible={showCollaborateModal}
        onClose={() => setShowCollaborateModal(false)}
        resource={{ id: sourceId, name: title || `Untitled ${contentType}` }}
        resourceType={contentType}
        theme={theme}
        apiConfig={apiConfig}
        authToken={authToken}
        userEmail={userEmail}
      />
    </View>
  );
};

/**
 * SharePanel - Larger panel showing share status and controls
 */
export const SharePanel = ({
  contentType,
  sourceId,
  title,
  theme = {},
  onClose,
}) => {
  const {
    shareInfo,
    shareUrl,
    isShared,
    isLoading,
    error,
    createShare,
    revokeShare,
    copyUrl,
  } = useShare(contentType, sourceId);

  const [copied, setCopied] = useState(false);

  const colors = {
    primary: theme.primary || '#3b82f6',
    background: theme.background || '#1a1a2e',
    surface: theme.surface || '#2d2d44',
    text: theme.text || '#e4e4e7',
    textSecondary: theme.textSecondary || '#a1a1aa',
    success: theme.success || '#22c55e',
    error: theme.error || '#ef4444',
    border: theme.border || 'rgba(255,255,255,0.1)',
  };

  const handleCopy = async () => {
    const success = await copyUrl();
    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleCreate = async () => {
    await createShare(title);
  };

  const handleRevoke = async () => {
    await revokeShare();
  };

  return (
    <View style={[styles.panel, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <View style={styles.panelHeader}>
        <Text style={[styles.panelTitle, { color: colors.text }]}>
          Share {contentType.charAt(0).toUpperCase() + contentType.slice(1)}
        </Text>
        {onClose && (
          <TouchableOpacity onPress={onClose}>
            <Ionicons name="close" size={24} color={colors.textSecondary} />
          </TouchableOpacity>
        )}
      </View>

      {error && (
        <View style={[styles.errorBox, { backgroundColor: colors.error + '20' }]}>
          <Ionicons name="alert-circle" size={16} color={colors.error} />
          <Text style={[styles.errorText, { color: colors.error }]}>{error}</Text>
        </View>
      )}

      {isLoading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={[styles.loadingText, { color: colors.textSecondary }]}>
            Loading...
          </Text>
        </View>
      ) : isShared ? (
        <View style={styles.sharedContent}>
          <View style={[styles.statusBadge, { backgroundColor: colors.success + '20' }]}>
            <Ionicons name="checkmark-circle" size={16} color={colors.success} />
            <Text style={[styles.statusText, { color: colors.success }]}>
              Publicly Shared
            </Text>
          </View>

          {shareInfo?.permission === 'edit' && (
            <View style={[styles.statusBadge, { backgroundColor: colors.primary + '20', marginLeft: 8 }]}>
              <Ionicons name="create-outline" size={16} color={colors.primary} />
              <Text style={[styles.statusText, { color: colors.primary }]}>
                Can Edit
              </Text>
            </View>
          )}

          <Text style={[styles.label, { color: colors.textSecondary }]}>
            Share Link
          </Text>
          <View style={[styles.urlBox, { backgroundColor: colors.background }]}>
            <Text
              style={[styles.urlTextLarge, { color: colors.text }]}
              selectable
              numberOfLines={2}
            >
              {shareUrl}
            </Text>
          </View>

          {showFallback && (
            <View style={{ marginTop: 16 }}>
              <Text style={[styles.label, { color: colors.textSecondary }]}>
                Default fallback link (Always works)
              </Text>
              <View style={[styles.urlBox, { backgroundColor: colors.background, flexDirection: 'row', alignItems: 'center', paddingRight: 6 }]}>
                <Text
                  style={[styles.urlTextLarge, { color: colors.text, flex: 1, fontSize: 13 }]}
                  selectable
                  numberOfLines={2}
                >
                  {fallbackUrl}
                </Text>
                <TouchableOpacity
                  onPress={async () => {
                    await Clipboard.setStringAsync(fallbackUrl);
                  }}
                  style={{ padding: 8, backgroundColor: colors.surface, borderRadius: 6, marginLeft: 8 }}
                >
                  <Ionicons name="copy-outline" size={16} color={colors.textSecondary} />
                </TouchableOpacity>
              </View>
            </View>
          )}

          <View style={styles.buttonRow}>
            <TouchableOpacity
              style={[styles.actionButton, { backgroundColor: colors.primary }]}
              onPress={handleCopy}
            >
              <Ionicons
                name={copied ? 'checkmark' : 'copy-outline'}
                size={18}
                color="white"
              />
              <Text style={styles.actionButtonText}>
                {copied ? 'Copied!' : 'Copy Link'}
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[styles.actionButton, { backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.error }]}
              onPress={handleRevoke}
            >
              <Ionicons name="trash-outline" size={18} color={colors.error} />
              <Text style={[styles.actionButtonText, { color: colors.error }]}>
                Revoke
              </Text>
            </TouchableOpacity>
          </View>

          {shareInfo?.access_count !== undefined && (
            <View style={styles.statsRow}>
              <View style={styles.statItem}>
                <Ionicons name="eye-outline" size={16} color={colors.textSecondary} />
                <Text style={[styles.statText, { color: colors.textSecondary }]}>
                  {shareInfo.access_count} view{shareInfo.access_count !== 1 ? 's' : ''}
                </Text>
              </View>
              {shareInfo.created_at && (
                <View style={styles.statItem}>
                  <Ionicons name="calendar-outline" size={16} color={colors.textSecondary} />
                  <Text style={[styles.statText, { color: colors.textSecondary }]}>
                    Created {new Date(shareInfo.created_at).toLocaleDateString()}
                  </Text>
                </View>
              )}
            </View>
          )}
        </View>
      ) : (
        <View style={styles.notSharedContent}>
          <Ionicons name="share-social-outline" size={48} color={colors.textSecondary} />
          <Text style={[styles.notSharedTitle, { color: colors.text }]}>
            Share this {contentType}
          </Text>
          <Text style={[styles.notSharedDesc, { color: colors.textSecondary }]}>
            Create a public link that anyone can view.
          </Text>

          {/* Permission Toggle */}
          <View style={{
            flexDirection: 'row',
            backgroundColor: colors.background,
            padding: 4,
            borderRadius: 8,
            marginBottom: 20
          }}>
            <TouchableOpacity
              style={{
                paddingVertical: 6,
                paddingHorizontal: 12,
                borderRadius: 6,
                backgroundColor: permission === 'read' ? colors.surface : 'transparent',
                borderWidth: permission === 'read' ? 1 : 0,
                borderColor: colors.border
              }}
              onPress={() => setPermission('read')}
            >
              <Text style={{ color: permission === 'read' ? colors.text : colors.textSecondary, fontSize: 13, fontWeight: '500' }}>Can View</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={{
                paddingVertical: 6,
                paddingHorizontal: 12,
                borderRadius: 6,
                backgroundColor: permission === 'edit' ? colors.primary + '20' : 'transparent',
                borderWidth: permission === 'edit' ? 1 : 0,
                borderColor: colors.primary
              }}
              onPress={() => setPermission('edit')}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <Ionicons name="create-outline" size={14} color={permission === 'edit' ? colors.primary : colors.textSecondary} />
                <Text style={{ color: permission === 'edit' ? colors.primary : colors.textSecondary, fontSize: 13, fontWeight: '500' }}>Can Edit</Text>
              </View>
            </TouchableOpacity>
          </View>

          <TouchableOpacity
            style={[styles.createButton, { backgroundColor: colors.primary }]}
            onPress={handleCreate}
          >
            <Ionicons name="link" size={20} color="white" />
            <Text style={styles.createButtonText}>Create {permission === 'edit' ? 'Editable ' : ''}Link</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    position: 'relative',
  },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
  },
  buttonLabel: {
    fontSize: 14,
    fontWeight: '500',
  },
  tooltip: {
    position: 'absolute',
    top: '100%',
    right: 0,
    marginTop: 8,
    width: 280,
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    zIndex: 1000,
    ...Platform.select({
      web: {
        boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
      },
    }),
  },
  tooltipTitle: {
    fontSize: 14,
    fontWeight: '600',
    marginBottom: 8,
  },
  urlContainer: {
    padding: 10,
    borderRadius: 6,
    marginBottom: 12,
  },
  urlText: {
    fontSize: 12,
    fontFamily: Platform.OS === 'web' ? 'monospace' : 'Courier',
  },
  tooltipActions: {
    flexDirection: 'row',
    gap: 8,
  },
  // Modal styles
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  modalContent: {
    width: '100%',
    maxWidth: 420,
    borderRadius: 16,
    padding: 24,
    ...Platform.select({
      web: {
        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
      },
      default: {
        elevation: 10,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 4 },
        shadowOpacity: 0.3,
        shadowRadius: 8,
      },
    }),
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  modalTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: '600',
  },
  modalCloseBtn: {
    padding: 4,
  },
  urlLabel: {
    fontSize: 12,
    fontWeight: '500',
    textTransform: 'uppercase',
    marginTop: 12,
    marginBottom: 8,
  },
  urlInputContainer: {
    borderRadius: 8,
    borderWidth: 1,
    padding: 12,
  },
  urlInput: {
    fontSize: 14,
    fontFamily: Platform.OS === 'web' ? 'monospace' : 'Courier',
  },
  modalActions: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 20,
  },
  modalButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 14,
    borderRadius: 10,
  },
  copyButton: {
    // backgroundColor set dynamically
  },
  modalButtonText: {
    fontSize: 15,
    fontWeight: '600',
    color: 'white',
  },
  revokeButton: {
    backgroundColor: 'transparent',
    borderWidth: 1,
  },
  statsContainer: {
    flexDirection: 'row',
    gap: 20,
    marginTop: 20,
    paddingTop: 16,
    borderTopWidth: 1,
  },
  statItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  statText: {
    fontSize: 13,
  },
  infoText: {
    fontSize: 12,
    marginTop: 16,
    lineHeight: 18,
    textAlign: 'center',
  },
  // Social share styles
  socialSection: {
    marginTop: 20,
  },
  socialLabel: {
    fontSize: 12,
    fontWeight: '500',
    textTransform: 'uppercase',
    marginBottom: 10,
  },
  socialRow: {
    flexDirection: 'row',
    gap: 10,
    flexWrap: 'wrap',
  },
  socialButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  // Legacy tooltip styles (can be removed if not used elsewhere)
  tooltipActions: {
    flexDirection: 'row',
    gap: 8,
  },
  tooltipButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 6,
  },
  tooltipButtonText: {
    color: 'white',
    fontSize: 13,
    fontWeight: '500',
  },
  accessCount: {
    fontSize: 11,
    marginTop: 10,
    textAlign: 'center',
  },
  closeButton: {
    position: 'absolute',
    top: 8,
    right: 8,
    padding: 4,
  },
  // Panel styles
  panel: {
    padding: 20,
    borderRadius: 12,
    borderWidth: 1,
  },
  panelHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  panelTitle: {
    fontSize: 18,
    fontWeight: '600',
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    padding: 12,
    borderRadius: 8,
    marginBottom: 16,
  },
  errorText: {
    fontSize: 13,
    flex: 1,
  },
  loadingContainer: {
    alignItems: 'center',
    padding: 32,
  },
  loadingText: {
    marginTop: 12,
    fontSize: 14,
  },
  sharedContent: {
    gap: 12,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    alignSelf: 'flex-start',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },
  statusText: {
    fontSize: 13,
    fontWeight: '500',
  },
  label: {
    fontSize: 12,
    textTransform: 'uppercase',
    marginTop: 8,
  },
  urlBox: {
    padding: 12,
    borderRadius: 8,
  },
  urlTextLarge: {
    fontSize: 13,
    fontFamily: Platform.OS === 'web' ? 'monospace' : 'Courier',
  },
  buttonRow: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 8,
  },
  actionButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 12,
    borderRadius: 8,
  },
  actionButtonText: {
    color: 'white',
    fontSize: 14,
    fontWeight: '500',
  },
  statsRow: {
    flexDirection: 'row',
    gap: 16,
    marginTop: 8,
  },
  notSharedContent: {
    alignItems: 'center',
    padding: 24,
  },
  notSharedTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginTop: 16,
    marginBottom: 8,
  },
  notSharedDesc: {
    fontSize: 14,
    textAlign: 'center',
    marginBottom: 20,
    lineHeight: 20,
  },
  createButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 8,
  },
  createButtonText: {
    color: 'white',
    fontSize: 15,
    fontWeight: '600',
  },
});

export default {
  useShare,
  ShareButton,
  SharePanel,
};
