// AppVersionsPanel — "Version history & rollback" surface for a Decision App.
//
// Standalone content for the app card's dedicated "Versions" modal (separate
// from the Auto-Recommend / triggers modal).
//
// Every publish / spec-edit / promote snapshots the superseded (app_spec +
// agent_spec) revision. The last N (default 3) are kept per app. An editor can:
//   • see which version is live and what's in history,
//   • roll the app back to a retained snapshot.
// Rollback is FORWARD-ONLY (re-published as a new version, the current version
// is snapshotted first so it's reversible) and AI triggers come back DISABLED
// (the officer re-activates them in the app's Auto-Recommend panel).
// Backed by:
//   GET  /apps/{slug}/versions
//   POST /apps/{slug}/versions/{version}/rollback

import React, { useEffect, useState, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator, StyleSheet, Alert, Platform,
  ScrollView,
} from 'react-native';
import SmartAppService from '../services/SmartAppService';

function _fmtTime(v) {
  if (!v) return '';
  try {
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    return d.toLocaleString();
  } catch {
    return String(v);
  }
}

const REASON_LABEL = {
  publish: 'published',
  'spec-edit': 'edited',
  'promote-to-prod': 'promoted',
  'promote-preview': 'promoted',
  current: 'live',
};

function _reasonLabel(r) {
  if (!r) return '';
  if (REASON_LABEL[r]) return REASON_LABEL[r];
  if (r.startsWith('pre-rollback-to-v')) return `before rollback to v${r.slice('pre-rollback-to-v'.length)}`;
  return r;
}

export default function AppVersionsPanel({ slug }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyVersion, setBusyVersion] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await SmartAppService.listAppVersions(slug);
      setData(r);
    } catch (e) {
      setError(e?.message || 'Failed to load version history');
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  const doRollback = useCallback(async (v) => {
    setBusyVersion(v.version);
    setNotice(null);
    setError(null);
    try {
      const r = await SmartAppService.rollbackAppVersion(slug, v.version);
      setNotice(
        `Rolled back to v${r.rolled_back_to} — now live as v${r.new_version}. ` +
        "AI triggers were left OFF; re-activate them in the app's Auto-Recommend panel.",
      );
      // load() flips `loading` true → shows the spinner (not the empty state)
      // while the new live version is re-fetched.
      await load();
    } catch (e) {
      setError(e?.message || 'Rollback failed');
    } finally {
      setBusyVersion(null);
    }
  }, [slug, load]);

  const confirmRollback = useCallback((v) => {
    const msg =
      `Roll back to v${v.version}? This re-publishes the v${v.version} spec as a ` +
      'new version. The current version is saved to history first, so you can undo ' +
      'this. AI triggers will be turned OFF and must be re-activated.';
    // Alert.alert button callbacks don't fire on React Native Web — branch to
    // window.confirm there, like archiveApp in PowerAppsScreen.
    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined' && window.confirm(msg)) doRollback(v);
      return;
    }
    Alert.alert(
      `Roll back to v${v.version}?`,
      msg,
      [
        { text: 'Cancel', style: 'cancel' },
        { text: `Roll back to v${v.version}`, style: 'destructive', onPress: () => doRollback(v) },
      ],
    );
  }, [doRollback]);

  const versions = Array.isArray(data?.versions) ? data.versions : [];

  return (
    // Single ScrollView root (same pattern as AITriggersPanel) so the list
    // scrolls reliably inside the modal card — no nested flex:1 to collapse.
    <ScrollView style={s.wrap} contentContainerStyle={{ paddingBottom: 16 }}>
      <View style={s.headerRow}>
        <Text style={s.sub}>
          The last {data?.max_kept ?? 3} versions are kept. Roll back to re-publish an
          earlier spec — it becomes a new version and triggers come back OFF.
        </Text>
        {data?.current_version != null && (
          <View style={s.liveBadge}><Text style={s.liveBadgeTxt}>live: v{data.current_version}</Text></View>
        )}
      </View>

      {loading && <ActivityIndicator color="#3B82F6" style={{ marginTop: 10 }} />}
      {!!error && (
        <View style={s.errRow}>
          <Text style={s.err}>{error}</Text>
          <TouchableOpacity onPress={load} style={s.retry}><Text style={s.retryTxt}>Retry</Text></TouchableOpacity>
        </View>
      )}
      {!!notice && <Text style={s.notice}>{notice}</Text>}

      {!loading && !error && versions.map((v) => (
        <View key={v.version} style={[s.card, v.is_current && s.cardCurrent]}>
          <View style={s.titleRow}>
            <Text style={s.vName}>
              v{v.version}{v.is_current ? ' · live' : ''}
            </Text>
            {v.is_current ? (
              <View style={s.currentBadge}><Text style={s.currentBadgeTxt}>current</Text></View>
            ) : (
              <TouchableOpacity
                style={[s.rbBtn, busyVersion === v.version && s.rbBtnOff]}
                disabled={busyVersion != null}
                onPress={() => confirmRollback(v)}
              >
                {busyVersion === v.version
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Text style={s.rbTxt}>Roll back</Text>}
              </TouchableOpacity>
            )}
          </View>
          {!!v.title && <Text style={s.vTitle} numberOfLines={1}>{v.title}</Text>}
          <Text style={s.vMeta}>
            {[
              _reasonLabel(v.reason),
              v.snapshotted_at ? _fmtTime(v.snapshotted_at) : '',
              v.snapshotted_by || '',
            ].filter(Boolean).join(' · ')}
          </Text>
          <Text style={s.vMeta}>
            {v.trigger_count || 0} trigger(s)
            {v.active_trigger_count ? `, ${v.active_trigger_count} active` : ''}
            {v.agent_version != null ? ` · agent v${v.agent_version}` : ''}
          </Text>
        </View>
      ))}

      {!loading && !error && versions.length <= 1 && (
        <Text style={s.empty}>
          No earlier versions yet — history fills in as you publish or edit this app.
        </Text>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 12 },
  headerRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' },
  liveBadge: { backgroundColor: '#ecfdf5', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, marginLeft: 8 },
  liveBadgeTxt: { fontSize: 11, color: '#047857', fontWeight: '700' },
  sub: { flex: 1, fontSize: 12, color: '#6b7280', marginBottom: 8, lineHeight: 17 },
  errRow: { flexDirection: 'row', alignItems: 'center', marginTop: 8, gap: 10 },
  card: { borderWidth: 1, borderColor: '#e5e7eb', borderRadius: 10, padding: 12, marginBottom: 10, backgroundColor: '#fff' },
  cardCurrent: { borderColor: '#a7f3d0', backgroundColor: '#f0fdf4' },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  vName: { fontSize: 14, fontWeight: '700', color: '#111827' },
  vTitle: { fontSize: 13, color: '#374151', marginTop: 3 },
  vMeta: { fontSize: 11, color: '#6b7280', marginTop: 3 },
  currentBadge: { backgroundColor: '#d1fae5', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  currentBadgeTxt: { fontSize: 11, color: '#065f46', fontWeight: '700' },
  rbBtn: { backgroundColor: '#b45309', borderRadius: 6, paddingHorizontal: 14, paddingVertical: 8, minWidth: 88, alignItems: 'center' },
  rbBtnOff: { opacity: 0.6 },
  rbTxt: { color: '#fff', fontWeight: '600', fontSize: 13 },
  err: { flex: 1, fontSize: 13, color: '#b91c1c' },
  retry: { paddingHorizontal: 12, paddingVertical: 6, borderWidth: 1, borderColor: '#d1d5db', borderRadius: 6 },
  retryTxt: { color: '#374151', fontWeight: '600', fontSize: 12 },
  notice: { fontSize: 12, color: '#065f46', marginTop: 8, lineHeight: 17 },
  empty: { fontSize: 12, color: '#9ca3af', marginTop: 2, marginBottom: 8 },
});
