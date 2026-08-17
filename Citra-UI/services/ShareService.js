// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * ShareService - Handles public sharing functionality for diagrams, reports, and chats
 * Provides API calls and utilities for creating, managing, and tracking share links
 */

import { CONFIG as API_CONFIG } from '../config/config';
import { authService } from './authService';
import { Platform, Linking } from 'react-native';

const BASE_URL = API_CONFIG.CITRA_SERVICE_URL;
const SHARE_API_PREFIX = '/api/public-share';

/**
 * Create or get existing share link for content
 * @param {string} contentType - 'diagram' | 'report' | 'chat'
 * @param {string} sourceId - The ID of the source document
 * @param {string} title - Optional title for the share
 * @param {string} permission - 'read' | 'edit' (default: 'read')
 * @returns {Promise<Object>} Share details including URL and token
 */
export const createShare = async (contentType, sourceId, title = null, permission = 'read') => {
  try {
    const url = `${BASE_URL}${SHARE_API_PREFIX}/share/${contentType}/${sourceId}`;
    const response = await authService.authenticatedFetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ title, permission }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to create share');
    }

    return await response.json();
  } catch (error) {
    console.error('[ShareService] Create share error:', error);
    throw error;
  }
};

/**
 * Get existing share info for a source if it exists
 * @param {string} contentType - 'diagram' | 'report' | 'chat'
 * @param {string} sourceId - The ID of the source document
 * @returns {Promise<Object>} Share info or null if not shared
 */
export const getShareBySource = async (contentType, sourceId) => {
  try {
    const url = `${BASE_URL}${SHARE_API_PREFIX}/share/by-source/${contentType}/${sourceId}`;
    const response = await authService.authenticatedFetch(url, {
      method: 'GET',
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to get share info');
    }

    const data = await response.json();
    return data.exists ? data.share : null;
  } catch (error) {
    console.error('[ShareService] Get share by source error:', error);
    throw error;
  }
};

/**
 * Accept a shared item (via deep link or invite)
 * Adds the item to the user's "Shared with Me" list
 * @param {string} contentType - 'diagram' | 'report'
 * @param {string} shareToken - The token from the URL
 * @returns {Promise<Object>} Result with content info
 */
export const acceptShare = async (contentType, shareToken) => {
  try {
    const url = `${BASE_URL}${SHARE_API_PREFIX}/accept/${contentType}/${shareToken}`;
    const response = await authService.authenticatedFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to accept share');
    }

    return await response.json();
  } catch (error) {
    console.error('[ShareService] Accept share error:', error);
    throw error;
  }
};

/**
 * List all shares created by the current user
 * @param {string} contentType - Optional filter: 'diagram' | 'report' | 'chat'
 * @returns {Promise<Array>} List of share objects
 */
export const listMyShares = async (contentType = null) => {
  try {
    const params = new URLSearchParams();
    if (contentType) params.append('content_type', contentType);

    // Check if we should also fetch "Shared With Me"? 
    // Usually separate endpoints, but maybe merge logic here or in UI.
    // Base implementation just lists my owned shares.

    const url = `${BASE_URL}${SHARE_API_PREFIX}/share/my-shares?${params.toString()}`;

    const response = await authService.authenticatedFetch(url, {
      method: 'GET',
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to list shares');
    }

    const data = await response.json();
    return data.shares || [];
  } catch (error) {
    console.error('[ShareService] List shares error:', error);
    throw error;
  }
};

/**
 * Revoke (delete) a share link
 * @param {string} shareToken - The share token to revoke
 * @returns {Promise<Object>} Success response
 */
export const revokeShare = async (shareToken) => {
  try {
    const url = `${BASE_URL}${SHARE_API_PREFIX}/share/${shareToken}`;
    const response = await authService.authenticatedFetch(url, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to revoke share');
    }

    return await response.json();
  } catch (error) {
    console.error('[ShareService] Revoke share error:', error);
    throw error;
  }
};

/**
 * Copy share URL to clipboard
 * @param {string} shareUrl - The URL to copy
 * @returns {Promise<boolean>} True if successful
 */
export const copyShareUrl = async (shareUrl) => {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(shareUrl);
      return true;
    }

    // Fallback for older browsers
    const textArea = document.createElement('textarea');
    textArea.value = shareUrl;
    textArea.style.position = 'fixed';
    textArea.style.left = '-9999px';
    document.body.appendChild(textArea);
    textArea.select();
    document.execCommand('copy');
    document.body.removeChild(textArea);
    return true;
  } catch (error) {
    console.error('[ShareService] Copy to clipboard error:', error);
    return false;
  }
};

/**
 * Generate the public share URL from a token
 * Uses frontend environment config to generate correct URL:
 * - Development: http://localhost:8085/api/public-share/public/{token}
 * - Production: https://api.citra-ai.com/citra-ai/api/public-share/public/{token}
 * @param {string} shareToken - The share token
 * @returns {string} The full public URL
 */
export const getPublicUrl = (shareToken) => {
  // Use the unified route at the root level
  return `${BASE_URL}/s/${shareToken}`;
};

/**
 * Invite a specific user by email
 * @param {string} contentType - 'diagram' | 'report' | 'chat'
 * @param {string} sourceId - ID of the content
 * @param {string} email - Email to invite
 * @param {string} role - 'viewer' | 'editor'
 * @returns {Promise<Object>} Invitation result
 */
export const inviteUser = async (contentType, sourceId, email, role = 'editor') => {
  try {
    const url = `${BASE_URL}${SHARE_API_PREFIX}/invite`;
    const response = await authService.authenticatedFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content_type: contentType, source_id: sourceId, email, role }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to send invitation');
    }

    return await response.json();
  } catch (error) {
    console.error('[ShareService] Invite user error:', error);
    throw error;
  }
};

// ============================================================================
// Social Share URL Builders
// ============================================================================

/**
 * Generate share text based on content type and title
 */
const getShareText = (contentType, title) => {
  const typeLabels = {
    diagram: 'diagram',
    report: 'report',
    chat: 'chat',
    presentation: 'presentation',
    printable: 'printable',
  };
  const label = typeLabels[contentType] || 'content';
  return title
    ? `Check out this ${label}: "${title}" — made with Citra AI`
    : `Check out this ${label} made with Citra AI`;
};

export const getWhatsAppShareUrl = (url, text) =>
  `https://wa.me/?text=${encodeURIComponent(text + '\n' + url)}`;

export const getLinkedInShareUrl = (url, text) =>
  `https://www.linkedin.com/shareArticle?mini=true&url=${encodeURIComponent(url)}${text ? '&summary=' + encodeURIComponent(text) : ''}`;

export const getTwitterShareUrl = (url, text) =>
  `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}`;

export const getFacebookShareUrl = (url, text) =>
  `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}${text ? '&quote=' + encodeURIComponent(text) : ''}`;

export const getEmailShareUrl = (url, subject, body) =>
  `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body + '\n\n' + url)}`;

export const getTelegramShareUrl = (url, text) =>
  `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`;

/**
 * Open a share URL — uses window.open on web, Linking on native
 */
export const openShareUrl = (url) => {
  if (Platform.OS === 'web') {
    window.open(url, '_blank', 'noopener,width=600,height=500');
  } else {
    Linking.openURL(url);
  }
};

export default {
  createShare,
  getShareBySource,
  listMyShares,
  revokeShare,
  copyShareUrl,
  getPublicUrl,
  inviteUser,
  getShareText,
  getWhatsAppShareUrl,
  getLinkedInShareUrl,
  getTwitterShareUrl,
  getFacebookShareUrl,
  getEmailShareUrl,
  getTelegramShareUrl,
  openShareUrl,
};
