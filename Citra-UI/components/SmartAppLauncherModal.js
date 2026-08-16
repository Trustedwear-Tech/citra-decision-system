/**
 * SmartAppLauncherModal — in-app launcher for Smart Apps on mobile.
 *
 * Why this exists: opening a Smart App via `window.open(url, '_blank')`
 * works on desktop (new browser tab) but breaks the in-app feel on mobile:
 *
 *   • Mobile PWA (display-mode: standalone) — `_blank` escapes the PWA
 *     into the system browser. The user loses the home screen / tab bar
 *     and has to re-enter the PWA after dismissing the runtime.
 *   • Mobile web browser — opening a new tab is technically possible but
 *     mobile tab UX is awkward (tab switcher, address bar chrome eating
 *     vertical space).
 *
 * This modal hosts the citra-app-runtime URL inside an iframe filling the
 * Citra-UI viewport, with a slim header for "back" + "open in new tab".
 * The user stays inside the Citra shell; dismissing returns them to the
 * Smart Apps list with state preserved.
 *
 * Auth handoff: the iframe loads the runtime URL directly. If the runtime
 * lives on the same origin (or a cookie-sharing subdomain), the user's
 * existing session is reused. If cross-origin and auth fails, the user
 * sees the runtime's login screen inside the iframe — falling back to
 * the "Open in new tab" link is the escape hatch.
 *
 * Desktop callers should NOT use this modal; they get the new-tab path
 * directly. See PowerAppsScreen.openApp().
 */
import React, { useEffect } from 'react';
import {
  Modal, View, Text, TouchableOpacity, ActivityIndicator,
  Platform, StyleSheet,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const DEFAULT_THEME = {
  background:    '#FFFFFF',
  text:          '#111827',
  textSecondary: '#6B7280',
  primary:       '#3B82F6',
  border:        '#E5E7EB',
};

/**
 * @param {{
 *   visible: boolean,
 *   slug: string,
 *   url: string,            // canonical runtime URL (SmartAppService.appUrl)
 *   title?: string,
 *   onClose: () => void,
 *   theme?: object,
 * }} props
 */
export default function SmartAppLauncherModal({
  visible,
  slug,
  url,
  title,
  onClose,
  theme = DEFAULT_THEME,
}) {
  const colors = { ...DEFAULT_THEME, ...theme };
  const [loading, setLoading] = React.useState(true);

  useEffect(() => {
    if (visible) setLoading(true);
  }, [visible, url]);

  // ESC closes the modal on web.
  useEffect(() => {
    if (Platform.OS !== 'web' || !visible) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [visible, onClose]);

  const openInNewTab = () => {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  };

  if (!visible) return null;

  return (
    <Modal
      visible={visible}
      onRequestClose={onClose}
      animationType="slide"
      transparent={false}
    >
      <View style={[styles.root, { backgroundColor: colors.background }]}>
        <View style={[styles.topBar, { borderBottomColor: colors.border }]}>
          <TouchableOpacity onPress={onClose} hitSlop={10} style={styles.iconBtn}>
            <Ionicons name="arrow-back" size={22} color={colors.text} />
          </TouchableOpacity>
          <View style={{ flex: 1, marginHorizontal: 8 }}>
            <Text style={{ color: colors.text, fontSize: 14, fontWeight: '600' }} numberOfLines={1}>
              {title || slug}
            </Text>
            <Text style={{ color: colors.textSecondary, fontSize: 11 }} numberOfLines={1}>
              Decision App
            </Text>
          </View>
          <TouchableOpacity onPress={openInNewTab} hitSlop={10} style={styles.iconBtn}>
            <Ionicons name="open-outline" size={20} color={colors.textSecondary} />
          </TouchableOpacity>
        </View>

        <View style={{ flex: 1, position: 'relative' }}>
          {loading && (
            <View style={styles.loadingOverlay} pointerEvents="none">
              <ActivityIndicator color={colors.primary} size="small" />
            </View>
          )}
          {Platform.OS === 'web' ? (
            <iframe
              src={url}
              title={title || slug}
              onLoad={() => setLoading(false)}
              style={{
                position: 'absolute', top: 0, left: 0,
                width: '100%', height: '100%',
                border: 'none', display: 'block',
              }}
              allow="clipboard-read; clipboard-write; fullscreen"
              referrerPolicy="no-referrer-when-downgrade"
            />
          ) : (
            // Native shell — not yet supported by this modal. Caller
            // should branch to Linking.openURL or a WebView component.
            <View style={styles.nativeFallback}>
              <Text style={{ color: colors.textSecondary, fontSize: 13 }}>
                In-app Decision App launch is web-only for now. Tap the open
                icon above to launch in your browser.
              </Text>
            </View>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
  },
  iconBtn: {
    width: 36, height: 36,
    alignItems: 'center', justifyContent: 'center',
    borderRadius: 8,
  },
  loadingOverlay: {
    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
    alignItems: 'center', justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.6)',
    zIndex: 1,
  },
  nativeFallback: {
    flex: 1, padding: 24, alignItems: 'center', justifyContent: 'center',
  },
});
