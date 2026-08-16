/**
 * DownloadArtifactCard — download card for files produced by a turn that
 * was bridged to Action Chat / Deep Research.
 *
 * Shape mirrors ActionChatArtifactCard exactly so the user sees the
 * same download UI regardless of which chat surface they're in. The
 * card renders on `artifact` SSE events emitted by the bridge in
 * Citra-Service (services/action_chat_bridge.py), which forwards
 * Action Chat's `artifact` payload through verbatim.
 *
 * Tap on web → opens the presigned URL in a new tab.
 * Tap on native → uses Linking.openURL.
 */
import React from 'react';
import { Platform, StyleSheet, Text, TouchableOpacity, View, Linking } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const ICON_BY_KIND = {
  pdf:  { name: 'document-text-outline', color: '#B91C1C' },
  pptx: { name: 'easel-outline',         color: '#C2410C' },
  ppt:  { name: 'easel-outline',         color: '#C2410C' },
  xlsx: { name: 'grid-outline',          color: '#047857' },
  csv:  { name: 'list-outline',          color: '#047857' },
  png:  { name: 'image-outline',         color: '#6D28D9' },
  jpg:  { name: 'image-outline',         color: '#6D28D9' },
  jpeg: { name: 'image-outline',         color: '#6D28D9' },
  html: { name: 'code-slash-outline',    color: '#1D4ED8' },
  json: { name: 'code-outline',          color: '#1D4ED8' },
  docx: { name: 'document-outline',      color: '#1D4ED8' },
  txt:  { name: 'document-outline',      color: '#6B7280' },
};

const formatSize = (bytes) => {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const inferKind = (artifact) => {
  if (!artifact) return 'txt';
  if (artifact.kind) return String(artifact.kind).toLowerCase();
  const name = (artifact.filename || artifact.title || '').toLowerCase();
  const m = name.match(/\.([a-z0-9]+)$/);
  return m ? m[1] : 'txt';
};

const DownloadArtifactCard = ({ artifact, theme }) => {
  if (!artifact || !artifact.url) return null;
  const { title, filename, url, size, summary, source } = artifact;
  const kind = inferKind(artifact);
  const icon = ICON_BY_KIND[kind] || ICON_BY_KIND.txt;

  const handleOpen = () => {
    if (!url) return;
    if (Platform.OS === 'web') {
      window.open(url, '_blank', 'noopener,noreferrer');
    } else {
      Linking.openURL(url).catch(() => {});
    }
  };

  const cardBg = theme?.card || '#F9FAFB';
  const cardBorder = theme?.border || '#E5E7EB';
  const textPrimary = theme?.text || '#111827';
  const textMuted = theme?.secondaryText || '#6B7280';

  return (
    <TouchableOpacity
      onPress={handleOpen}
      activeOpacity={0.7}
      style={[styles.card, { backgroundColor: cardBg, borderColor: cardBorder }]}
      accessibilityLabel={`Download ${title || filename || kind}`}
    >
      <View style={[styles.iconWrap, { backgroundColor: `${icon.color}15` }]}>
        <Ionicons name={icon.name} size={22} color={icon.color} />
      </View>
      <View style={styles.body}>
        <Text style={[styles.title, { color: textPrimary }]} numberOfLines={1}>
          {title || filename || `${kind.toUpperCase()} file`}
        </Text>
        <Text style={[styles.meta, { color: textMuted }]} numberOfLines={1}>
          {kind.toUpperCase()}
          {size ? ` · ${formatSize(size)}` : ''}
          {filename && filename !== title ? ` · ${filename}` : ''}
          {source === 'action_chat' ? ' · via Deep Research' : ''}
        </Text>
        {summary ? (
          <Text style={[styles.summary, { color: textMuted }]} numberOfLines={2}>
            {summary}
          </Text>
        ) : null}
      </View>
      <Ionicons name="download-outline" size={18} color={textMuted} />
    </TouchableOpacity>
  );
};

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 10,
    borderRadius: 10,
    borderWidth: 1,
    marginTop: 8,
  },
  iconWrap: {
    width: 36, height: 36, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },
  body: { flex: 1, minWidth: 0 },
  title: { fontSize: 13, fontWeight: '600' },
  meta: { fontSize: 11, marginTop: 2 },
  summary: { fontSize: 11, marginTop: 2 },
});

export default DownloadArtifactCard;
