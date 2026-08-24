// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * DeviceFingerprintService.js
 * ===========================
 * Generates a stable device/browser fingerprint to prevent abuse
 * by free users creating multiple Google accounts on the same device.
 * 
 * Uses FingerprintJS (open-source) for browser fingerprinting on web.
 * Falls back to a localStorage-persisted UUID if FingerprintJS fails.
 * 
 * The fingerprint is sent during Google auth and checked server-side
 * before granting welcome bonus credits to new users.
 */

import { Platform } from 'react-native';
import FingerprintJS from '@fingerprintjs/fingerprintjs';

let cachedFingerprint = null;
let fpAgent = null;

/**
 * Initialize FingerprintJS agent (lazy, one-time)
 */
const initFingerprintAgent = async () => {
  if (fpAgent) return fpAgent;

  try {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      fpAgent = await FingerprintJS.load();
      return fpAgent;
    }
  } catch (err) {
    console.warn('[FINGERPRINT] Failed to load FingerprintJS:', err.message);
  }
  return null;
};

/**
 * Generate a fallback UUID v4
 */
const generateUUID = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for older browsers
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
};

/**
 * Generate a canvas-based fingerprint signal.
 * Canvas rendering varies by GPU/driver/browser — gives device-unique entropy
 * that persists even after localStorage is cleared.
 */
const getCanvasFingerprint = () => {
  try {
    if (typeof document === 'undefined') return '';
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) return '';
    canvas.width = 200;
    canvas.height = 50;
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillStyle = '#f60';
    ctx.fillRect(125, 1, 62, 20);
    ctx.fillStyle = '#069';
    ctx.fillText('Citra fp', 2, 15);
    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
    ctx.fillText('Citra fp', 4, 17);
    return canvas.toDataURL();
  } catch (e) {
    return '';
  }
};

/**
 * Collect screen/display signals for fingerprinting
 */
const getScreenSignals = () => {
  try {
    if (typeof window === 'undefined' || !window.screen) return '';
    const s = window.screen;
    const tz = Intl?.DateTimeFormat()?.resolvedOptions()?.timeZone || '';
    return `${s.width}x${s.height}x${s.colorDepth}|${window.devicePixelRatio || 1}|${tz}`;
  } catch (e) {
    return '';
  }
};

/**
 * Non-cryptographic hash (cyrb53) for deterministic fingerprint generation
 */
const simpleHash = (str) => {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
};

/**
 * Get or create a fallback fingerprint stored in localStorage.
 * Uses canvas + screen signals for stability across sessions.
 * Falls back to UUID if canvas is unavailable.
 */
const getFallbackFingerprint = () => {
  const STORAGE_KEY = '@device_fp';
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      let fp = window.localStorage.getItem(STORAGE_KEY);
      if (!fp) {
        const canvas = getCanvasFingerprint();
        const screen = getScreenSignals();
        if (canvas || screen) {
          fp = `browser_${simpleHash(canvas + screen)}`;
        } else {
          fp = `fallback_${generateUUID()}`;
        }
        window.localStorage.setItem(STORAGE_KEY, fp);
      }
      return fp;
    }
  } catch (err) {
    console.warn('[FINGERPRINT] localStorage fallback failed:', err.message);
  }
  // Last resort: in-memory only — server treats ephemeral_ prefix as untrusted
  return `ephemeral_${generateUUID()}`;
};

/**
 * Get the device fingerprint.
 * Uses FingerprintJS on web, falls back to localStorage UUID.
 * Result is cached in memory for the session.
 * 
 * @returns {Promise<string>} A stable visitor ID string
 */
export const getDeviceFingerprint = async () => {
  // Return cached value if available
  if (cachedFingerprint) {
    return cachedFingerprint;
  }

  try {
    if (Platform.OS === 'web') {
      const agent = await initFingerprintAgent();
      if (agent) {
        const result = await agent.get();
        cachedFingerprint = result.visitorId;
        console.log('[FINGERPRINT] Generated browser fingerprint');
        return cachedFingerprint;
      }
    }

    // For non-web platforms or if FingerprintJS failed, use fallback
    cachedFingerprint = getFallbackFingerprint();
    console.log('[FINGERPRINT] Using fallback fingerprint');
    return cachedFingerprint;
  } catch (err) {
    console.warn('[FINGERPRINT] Error generating fingerprint:', err.message);
    cachedFingerprint = getFallbackFingerprint();
    return cachedFingerprint;
  }
};

/**
 * Clear cached fingerprint (useful for testing)
 */
export const clearCachedFingerprint = () => {
  cachedFingerprint = null;
};

export default {
  getDeviceFingerprint,
  clearCachedFingerprint,
};
