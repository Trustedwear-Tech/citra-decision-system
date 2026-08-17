// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * MemoryScreen.js — the per-app Memory surface ("what the team taught the system").
 *
 * ONE platform screen (launched from HomePanel's Admin section, or deep-linked
 * from an app card's Learning modal with the app preselected). Three tabs:
 *
 *   Judgements — officer judgements the app has learned from corrections
 *                (individual → team). RULES are the SOP and always win; the
 *                learned layer is never called "rules" anywhere on this
 *                screen. Curators retire / quarantine / resolve SOP conflicts.
 *   Precedents — read-only ledger browse (item_decision_records): the model's
 *                verdict + the officer's disposition per image/document.
 *                Curators can EXCLUDE a row from precedent retrieval (or lift
 *                it) — the record itself is never edited or deleted.
 *   Stats      — loop health + memory inventory from /loop-metrics.
 *
 * Access mirrors the platform model: reads are dept-scoped server-side; the
 * two writes 403 for non-curators. `canCurate` only hides the buttons — the
 * server is the gate.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator,
  StyleSheet, ScrollView, Platform, TextInput,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import SmartAppService from '../services/SmartAppService';

const DEFAULT_THEME = {
  background: '#FFFFFF', surface: '#F9FAFB', surfaceAlt: '#F3F4F6',
  text: '#111827', textSecondary: '#6B7280', primary: '#3B82F6',
  border: '#E5E7EB', danger: '#DC2626',
};

const AMBER = '#D97706';
const RED = '#DC2626';

const TABS = [
  { id: 'clauses', label: 'Judgements it has learned' },
  { id: 'precedents', label: 'Past decisions' },
  { id: 'stats', label: "How it's doing" },
];

const DISPOSITION_FILTERS = [
  { id: '', label: 'All' },
  { id: 'accept', label: 'Approved' },
  { id: 'reject', label: 'Rejected' },
  { id: 'proposed', label: 'Not yet decided' },
];

const fmtWhen = (v) => {
  if (!v) return '—';
  try { return new Date(v).toLocaleString(); } catch { return String(v); }
};
const fmtPct = (r) => (r === null || r === undefined ? '—' : `${Math.round(r * 100)}%`);

// ── Plain-language helpers ── memory is keyed internally by (modality · task_type),
// e.g. "image · asset-inspection-defect". Non-technical curators shouldn't have to
// read that, so these turn the internal keys/states into everyday phrasing.
const MODALITY_WORD = { document: 'documents', image: 'photos', record: 'records', text: 'text' };
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const humanKind = (modality, taskType) => {
  const m = MODALITY_WORD[modality] || (modality ? `${modality}s` : 'items');
  const t = (taskType || '').replace(/[-_]/g, ' ').trim();
  return t ? `${cap(t)} (${m})` : cap(m);
};
const humanDisposition = (d) => {
  if (d === 'accept') return 'Approved';
  if (d === 'reject') return 'Rejected';
  if (!d || d === 'proposed') return 'Not yet decided';
  return d;
};
// One-line, plain-English explainer shown under each tab so a first-time viewer
// immediately understands what they're looking at.
const TAB_HELP = {
  clauses: 'Officer judgements this app has learned — experience your team taught it, on top of the SOP (which always wins). Tap one to see the corrections behind it.',
  precedents: 'Every photo and document the app has looked at, what it suggested, and what your team decided. This history is never changed or deleted.',
  stats: "How well the app's memory is working — plus a plain summary of everything it has learned so far.",
};

export default function MemoryScreen({ visible, onClose, theme, initialSlug = null, canCurate = true }) {
  const colors = useMemo(() => ({ ...DEFAULT_THEME, ...(theme || {}) }), [theme]);

  const [apps, setApps] = useState([]);
  const [slug, setSlug] = useState(initialSlug);
  const [tab, setTab] = useState('clauses');

  const [clauses, setClauses] = useState(null);
  const [clauseMeta, setClauseMeta] = useState(null);
  // {clause, corrections} when a judgement's provenance drawer is open.
  const [openClause, setOpenClause] = useState(null);
  // The dismissed-officer drill: every judgement one officer helped teach. The
  // endpoint and client method existed and nothing called them, so "that
  // intern's judgements are in 40 apps — show me" was not doable from here.
  const [taughtBy, setTaughtBy] = useState(null);   // { officer, clauses[] }
  const [challenging, setChallenging] = useState(null);  // clause_id
  const [challengeText, setChallengeText] = useState('');
  const [provLoading, setProvLoading] = useState(false);
  const [items, setItems] = useState([]);
  const [nextCursor, setNextCursor] = useState(null);
  const [dispFilter, setDispFilter] = useState('');
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Slugs whose judgements are blocked on a human (SOP conflict / dissent).
  // Drives the dot on the app chips: the HomePanel badge says HOW MANY are
  // waiting, this says WHERE. null = not known (never render as "all clear").
  const [attentionApps, setAttentionApps] = useState(null);
  // In-DOM notices + confirmation. React Native's Alert maps to window.alert /
  // window.confirm on web, and BOTH are suppressed inside the embedded browser
  // the platform ships in — an Alert-gated action is simply a dead button
  // there (this is how the Retire button silently did nothing). Every
  // confirmation and result on this screen renders in the DOM instead.
  const [notice, setNotice] = useState(null);   // {tone:'info'|'error', text}
  const [armedRetire, setArmedRetire] = useState(null);  // clause_id, 6s window
  const armTimer = useRef(null);
  useEffect(() => () => { if (armTimer.current) clearTimeout(armTimer.current); }, []);

  // Governed edit state — one bucket at a time, explicit Save only.
  const [exporting, setExporting] = useState(false);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => { if (initialSlug) setSlug(initialSlug); }, [initialSlug]);

  // App selector — every app the caller can see; server scopes by audience.
  useEffect(() => {
    if (!visible) return;
    (async () => {
      try {
        const d = await SmartAppService.listApps({ scope: 'all' });
        const rows = (d?.apps || d?.items || []).filter((a) => a.slug);
        setApps(rows);
        if (!initialSlug && !slug && rows.length) setSlug(rows[0].slug);
      } catch (e) {
        setError(e?.message || 'Could not load apps.');
      }
      try {
        const impact = await SmartAppService.orgMemoryImpact();
        setAttentionApps(new Set(impact?.attention_apps || []));
      } catch (e) {
        // Enrichment only — the chips lose their dot, the tab still works.
        // Not silent: a swallowed read here would look like "nothing waiting".
        console.warn('[memory] attention apps unavailable:', e?.message || e);
        setAttentionApps(null);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const loadTab = useCallback(async (targetSlug, targetTab, { reset = true } = {}) => {
    if (!targetSlug) return;
    setLoading(true); setError('');
    try {
      if (targetTab === 'clauses') {
        const d = await SmartAppService.getMemoryClauses(targetSlug);
        setClauses(d?.clauses || []);
        setClauseMeta({ inventory: d?.inventory, corrections: d?.corrections,
          gateNotice: d?.gate_notice });
      } else if (targetTab === 'precedents') {
        const d = await SmartAppService.getMemoryItems(targetSlug, {
          disposition: dispFilter || undefined,
          cursor: reset ? undefined : nextCursor,
        });
        const items = Array.isArray(d?.items) ? d.items : [];
        setItems((prev) => (reset ? items : [...prev, ...items]));
        setNextCursor(d?.next_cursor || null);
      } else if (targetTab === 'stats') {
        const d = await SmartAppService.getLoopMetrics(targetSlug, 90);
        setMetrics(d);
      }
    } catch (e) {
      setError(e?.message || 'Could not load memory.');
    } finally {
      setLoading(false);
    }
  }, [dispFilter, nextCursor]);

  useEffect(() => {
    if (visible && slug) {
      setOpenClause(null);
      loadTab(slug, tab, { reset: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, slug, tab, dispFilter]);

  // Open a judgement's provenance: the actual rejects that taught it. This is the
  // answer to "why does it say that?" — the blob could never give one.
  const openProvenance = useCallback(async (clauseId) => {
    if (!slug) return;
    setProvLoading(true);
    try {
      setOpenClause(await SmartAppService.getClauseProvenance(slug, clauseId));
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not load the evidence: ${e?.message || 'provenance unavailable.'}` });
    } finally {
      setProvLoading(false);
    }
  }, [slug]);

  // Quarantine: a HOLD, not a verdict — excluded from use, evidence intact,
  // reversible. The dismissed-officer drill.
  const quarantine = useCallback(async (clauseId) => {
    try {
      await SmartAppService.quarantineClause(slug, clauseId, 'quarantined from the Memory screen');
      setOpenClause(null);
      setNotice({ tone: 'info', text: 'Held. This judgement is not used until you lift the hold; its evidence is untouched.' });
      loadTab(slug, 'clauses', { reset: true });
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not quarantine: ${e?.message || 'unknown error.'}` });
    }
  }, [slug, loadTab]);

  const showTaughtBy = useCallback(async (officer) => {
    if (!officer || !slug) return;
    try {
      const d = await SmartAppService.clausesTaughtBy(slug, officer);
      setTaughtBy({ officer, clauses: d?.clauses || [] });
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not load what ${officer} taught: ${e?.message || 'unknown error.'}` });
    }
  }, [slug]);

  // A challenge STOPS a judgement pending adjudication. It is not a vote and it
  // is not weighted: corroboration is a headcount, so three officers who share a
  // misconception outrank the one who knows better — this is the lever that
  // person did not have. Who raised it, when, and why are all recorded.
  const challenge = useCallback(async (clauseId, reason) => {
    try {
      await SmartAppService.challengeClause(slug, clauseId, reason);
      setChallenging(null); setChallengeText(''); setOpenClause(null);
      setNotice({ tone: 'info', text: 'Stopped. This judgement is not used until someone adjudicates it — your reason is on the record.' });
      loadTab(slug, 'clauses', { reset: true });
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not challenge: ${e?.message || 'unknown error.'}` });
    }
  }, [slug, loadTab]);

  const reinstate = useCallback(async (clauseId) => {
    try {
      await SmartAppService.reinstateClause(slug, clauseId, 'reinstated from the Memory screen');
      setOpenClause(null);
      setNotice({ tone: 'info', text: 'Back in service. Its record is reset, so the next cases decide whether it stays.' });
      loadTab(slug, 'clauses', { reset: true });
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not put it back: ${e?.message || 'unknown error.'}` });
    }
  }, [slug, loadTab]);

  const resolveChallenge = useCallback(async (clauseId, action) => {
    try {
      await SmartAppService.resolveClauseChallenge(slug, clauseId, action, '');
      setOpenClause(null);
      setNotice({ tone: 'info', text: action === 'uphold'
        ? 'Retired. The corrections that taught it stay on record, so a corrected version can form.'
        : 'Back in service. The challenge stays on the record with both names on it.' });
      loadTab(slug, 'clauses', { reset: true });
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not resolve: ${e?.message || 'unknown error.'}` });
    }
  }, [slug, loadTab]);

  // The two-tap SOP-conflict resolution: SOP is right (retire) or the
  // officers are right (the SOP is the stale one — judgement returns to
  // service with the disagreement recorded).
  const resolveSop = useCallback(async (clauseId, action) => {
    try {
      await SmartAppService.resolveClauseSopConflict(slug, clauseId, action);
      setOpenClause(null);
      setNotice({
        tone: 'info',
        text: action === 'retire'
          ? 'Retired. The SOP stands, and this judgement is no longer used.'
          : 'Recorded. The judgement is back in service and the SOP is flagged as needing an update.',
      });
      loadTab(slug, 'clauses', { reset: true });
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not resolve: ${e?.message || 'unknown error.'}` });
    }
  }, [slug, loadTab]);

  // Retire, never edit: a judgement's text is provenanced to specific corrections,
  // so rewriting it would leave it citing evidence that does not say that.
  // Two taps, both in the DOM: the first arms and explains, the second commits.
  // Disarms itself after 6s so a stray click can never retire on the next one.
  const retire = useCallback(async (clauseId) => {
    if (armedRetire !== clauseId) {
      setArmedRetire(clauseId);
      if (armTimer.current) clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => setArmedRetire(null), 6000);
      return;
    }
    if (armTimer.current) clearTimeout(armTimer.current);
    setArmedRetire(null);
    try {
      await SmartAppService.retireClause(slug, clauseId, 'retired from the Memory screen');
      setOpenClause(null);
      setNotice({
        tone: 'info',
        text: 'Retired. It stops being used now — the corrections behind it are kept, so your team teaching it again brings it back.',
      });
      loadTab(slug, 'clauses', { reset: true });
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not retire: ${e?.message || 'unknown error.'}` });
    }
  }, [slug, loadTab, armedRetire]);

  // Manual export — fetch the open-schema document and download it client-side.
  // authenticatedFetch carries the JWT, so we can't just navigate the browser
  // to the URL; fetch JSON, then Blob-download (web) so the header is attached.
  const exportMemory = useCallback(async () => {
    if (!slug) return;
    setExporting(true);
    try {
      const doc = await SmartAppService.getMemoryExport(slug);
      const json = JSON.stringify(doc, null, 2);
      if (Platform.OS === 'web' && typeof document !== 'undefined') {
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `citra-memory-${slug}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        const c = doc.counts || {};
        const summary = `Downloaded ${c.decision_records || 0} decisions, ${c.item_decision_records || 0} items, ${c.smartapp_clauses || 0} judgements.`;
        if (doc.partial) {
          setNotice({
            tone: 'error',
            text: `Export is PARTIAL — ${summary} Some collections could not be read (${Object.keys(doc.errors || {}).join(', ')}), so this file is NOT complete. Retry before relying on it.`,
          });
        } else {
          setNotice({ tone: 'info', text: summary });
        }
      } else {
        setNotice({ tone: 'info',
          text: 'Memory export is a web download for now. Open App Memory in the browser to download the file.' });
      }
    } catch (e) {
      setNotice({ tone: 'error', text: e?.message || 'Could not export memory.' });
    } finally {
      setExporting(false);
    }
  }, [slug]);

  // Manual push of the memory asset to the customer's own bucket (gzipped
  // JSONL). On-demand only — scheduling is a future addition. Incremental:
  // pushes rows changed since the last push and advances the watermark.
  const syncToBucket = useCallback(async () => {
    if (!slug) return;
    setSyncing(true);
    try {
      const res = await SmartAppService.runMemoryExport(slug, 'incremental');
      const c = res?.collections || {};
      const n = Object.values(c).reduce((s, v) => s + (v?.exported || 0), 0);
      setNotice({ tone: 'info',
        text: `Pushed ${n} changed record(s) to ${res?.bucket || 'the export bucket'}.` });
    } catch (e) {
      setNotice({ tone: 'error', text: `Sync failed: ${e?.message || 'could not push memory to the bucket.'}` });
    } finally {
      setSyncing(false);
    }
  }, [slug]);

  const toggleExclusion = useCallback(async (row) => {
    const excluding = !row.retrieval_excluded;
    try {
      await SmartAppService.setItemExclusion(slug, row.item_id, excluding);
      setItems((prev) => prev.map((r) => (
        r.item_id === row.item_id ? { ...r, retrieval_excluded: excluding } : r
      )));
    } catch (e) {
      setNotice({ tone: 'error', text: e?.message || 'Could not change what the app learns from.' });
    }
  }, [slug]);

  if (!visible) return null;

  const selectedApp = apps.find((a) => a.slug === slug);

  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <View style={{ flex: 1 }}>
          <Text style={[styles.title, { color: colors.text }]}>App Memory</Text>
          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
            Everything this app has learned from your team — the judgements it applies on top of your SOP (the SOP is the rules, and always wins), every decision it has reviewed, and how well it's doing. Your corrections are what make it smarter.
          </Text>
        </View>
        {canCurate && slug && (
          <TouchableOpacity onPress={exportMemory} disabled={exporting} style={[styles.exportBtn, { borderColor: colors.border }]}>
            <Ionicons name="download-outline" size={15} color={colors.primary} />
            <Text style={{ color: colors.primary, fontSize: 13, fontWeight: '600' }}>
              {exporting ? 'Exporting…' : 'Export'}
            </Text>
          </TouchableOpacity>
        )}
        {canCurate && slug && (
          <TouchableOpacity onPress={syncToBucket} disabled={syncing} style={[styles.exportBtn, { borderColor: colors.border }]}>
            <Ionicons name="cloud-upload-outline" size={15} color={colors.primary} />
            <Text style={{ color: colors.primary, fontSize: 13, fontWeight: '600' }}>
              {syncing ? 'Syncing…' : 'Sync to bucket'}
            </Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={onClose} hitSlop={10}>
          <Ionicons name="close" size={24} color={colors.textSecondary} />
        </TouchableOpacity>
      </View>

      {/* App selector */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.appRow} contentContainerStyle={{ gap: 8, paddingHorizontal: 16 }}>
        {apps.map((a) => (
          <TouchableOpacity
            key={a.slug}
            onPress={() => setSlug(a.slug)}
            style={[styles.chip, {
              borderColor: a.slug === slug ? colors.primary : colors.border,
              backgroundColor: a.slug === slug ? `${colors.primary}14` : colors.surface,
            }]}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              {attentionApps?.has(a.slug) && (
                <View
                  style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: AMBER }}
                  accessibilityLabel="Has judgements awaiting a decision"
                />
              )}
              <Text style={{ color: a.slug === slug ? colors.primary : colors.text, fontSize: 13 }} numberOfLines={1}>
                {a.title || a.slug}
              </Text>
            </View>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Tabs */}
      <View style={[styles.tabRow, { borderBottomColor: colors.border }]}>
        {TABS.map((t) => (
          <TouchableOpacity key={t.id} onPress={() => setTab(t.id)} style={[styles.tab, tab === t.id && { borderBottomColor: colors.primary, borderBottomWidth: 2 }]}>
            <Text style={{ color: tab === t.id ? colors.primary : colors.textSecondary, fontWeight: tab === t.id ? '700' : '500' }}>
              {t.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 16, gap: 12 }}>
        {!slug && <Text style={{ color: colors.textSecondary }}>Select an app to view its memory.</Text>}
        {loading && (
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <ActivityIndicator size="small" color={colors.text} />
            <Text style={{ color: colors.text }}>Loading…</Text>
          </View>
        )}
        {!!error && <Text style={{ color: colors.danger }}>{error}</Text>}

        {/* Results and failures land here, in the DOM — never in a native
            popup the embedded browser would swallow. */}
        {!!notice && (
          <View style={[styles.card, {
            borderColor: notice.tone === 'error' ? colors.danger : colors.primary,
            backgroundColor: colors.surface, flexDirection: 'row', alignItems: 'flex-start',
          }]}>
            <Text style={{
              color: notice.tone === 'error' ? colors.danger : colors.text,
              fontSize: 12, lineHeight: 18, flex: 1,
            }}>{notice.text}</Text>
            <TouchableOpacity onPress={() => setNotice(null)} hitSlop={8}>
              <Ionicons name="close" size={16} color={colors.textSecondary} />
            </TouchableOpacity>
          </View>
        )}

        {/* Plain-English explainer for whichever tab is open. */}
        {slug && !loading && (
          <View style={[styles.helpCard, { backgroundColor: colors.surfaceAlt }]}>
            <Text style={{ color: colors.textSecondary, fontSize: 12, lineHeight: 18 }}>
              {TAB_HELP[tab]}
            </Text>
          </View>
        )}

        {/* ── Judgements it has learned ── */}
        {tab === 'clauses' && !loading && clauseMeta && (
          <View style={[styles.card, { borderColor: colors.border, backgroundColor: colors.surface }]}>
            <Text style={{ color: colors.text, fontSize: 13 }}>
              {(clauseMeta.inventory?.total || 0) === 0
                ? `No judgements yet — ${clauseMeta.corrections?.total || 0} correction(s) recorded so far.`
                : `${clauseMeta.inventory.by_status?.active || 0} team judgement(s)` +
                  ((clauseMeta.inventory.by_status?.candidate || 0) > 0
                    ? ` · ${clauseMeta.inventory.by_status.candidate} individual (awaiting corroboration)`
                    : '') +
                  ` · learned from ${clauseMeta.corrections?.total || 0} correction(s)`}
            </Text>
            {!!clauseMeta.gateNotice && (
              <Text style={{ color: colors.textSecondary, fontSize: 12, marginTop: 6 }}>
                {clauseMeta.gateNotice}
              </Text>
            )}
            {(clauseMeta.corrections?.too_brief || 0) > 0 && (
              <Text style={{ color: AMBER, fontSize: 12, marginTop: 6 }}>
                {clauseMeta.corrections.too_brief} correction(s) were too brief to
                learn from — ask officers for one concrete sentence.
              </Text>
            )}
            {(clauseMeta.inventory?.by_status?.sop_conflict || 0) > 0 && (
              <Text style={{ color: AMBER, fontSize: 12, marginTop: 6, fontWeight: '600' }}>
                ⚠ {clauseMeta.inventory.by_status.sop_conflict} judgement(s) conflict
                with the SOP and are suspended until you decide. The app is
                following the SOP meanwhile — open one to resolve it.
              </Text>
            )}
            {(clauseMeta.inventory?.dissented || []).length > 0 && (
              <Text style={{ color: AMBER, fontSize: 12, marginTop: 6 }}>
                ⚠ {clauseMeta.inventory.dissented.length} judgement(s) your team disagrees about — these are shown to the app as an open question, not asserted.
              </Text>
            )}
          </View>
        )}

        {tab === 'clauses' && !loading && clauses && clauses.map((c) => (
          <TouchableOpacity
            key={c.clause_id}
            onPress={() => openProvenance(c.clause_id)}
            style={[styles.card, { borderColor: colors.border, backgroundColor: colors.surface }]}
          >
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Text style={{ color: colors.text, fontSize: 13, lineHeight: 19, flex: 1 }}>
                {c.text}
              </Text>
              {c.status !== 'active' && (
                <Text style={{
                  color: ['dissented', 'sop_conflict', 'challenged', 'underperforming'].includes(c.status)
                    ? AMBER : colors.textSecondary,
                  fontSize: 11, marginLeft: 8,
                }}>
                  {c.status === 'candidate'
                    ? `individual — ${c.support_count === 1 ? 'one officer' : `${c.support_count} officers`}, awaiting corroboration`
                    : c.status === 'sop_conflict'
                    ? 'conflicts with SOP — needs your call'
                    : c.status === 'quarantined'
                    ? 'on hold'
                    : c.status === 'challenged'
                    ? 'stopped — someone disagreed, waiting on a decision'
                    : c.status === 'underperforming'
                    ? 'withdrawn — your team kept overruling it'
                    : c.status}
                </Text>
              )}
            </View>

            {/* Scope, as plain chips — what kind of case this judgement applies to. */}
            {(c.scope_facets || []).length > 0 && (
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                {c.scope_facets.map((f) => (
                  <Text key={f} style={[styles.chip, {
                    borderColor: colors.border, color: colors.textSecondary, fontSize: 11,
                  }]}>{f.replace(':', ': ')}</Text>
                ))}
              </View>
            )}
            {(c.scope_facets || []).length === 0 && (
              <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 6 }}>
                Applies to every case of this kind
              </Text>
            )}

            <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 6 }}>
              {c.status === 'active' ? 'team · ' : ''}{c.support_count} officer{c.support_count === 1 ? '' : 's'} ·{' '}
              {c.fired_count > 0 ? `used ${c.fired_count}×` : 'not used yet'}
              {c.precision !== null && c.precision !== undefined
                ? ` · right ${Math.round(c.precision * 100)}% of the time` : ''}
              {' · '}tap to see why
            </Text>
          </TouchableOpacity>
        ))}

        {tab === 'clauses' && !loading && clauses && clauses.length === 0 && (
          <Text style={{ color: colors.textSecondary }}>
            No judgements yet. The very first officer correction becomes an
            individual judgement, used immediately and clearly labeled; several
            officers agreeing upgrades it to a team judgement.
          </Text>
        )}

        {/* Everything one officer helped teach. The tool for "this person has
            left / turned out to be wrong / was an intern — what did they put
            into the app?" Read-only: acting on it is per-judgement, so each
            row opens that judgement's own controls. */}
        {!!taughtBy && (
          <View style={[styles.card, { borderColor: AMBER, backgroundColor: colors.surface }]}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <Text style={{ color: colors.text, fontWeight: '700', flex: 1 }}>
                What {taughtBy.officer} helped teach ({(taughtBy.clauses || []).length})
              </Text>
              <TouchableOpacity onPress={() => setTaughtBy(null)}>
                <Ionicons name="close" size={18} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            {(taughtBy.clauses || []).length === 0 && (
              <Text style={{ color: colors.textSecondary, fontSize: 12, marginTop: 6 }}>
                Nothing in this app carries their input.
              </Text>
            )}
            {(taughtBy.clauses || []).map((c) => (
              <TouchableOpacity
                key={c.clause_id}
                onPress={() => { setTaughtBy(null); openProvenance(c.clause_id); }}
                style={{ marginTop: 10 }}
              >
                <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
                  {c.clause_id} · {c.status}
                  {typeof c.support_count === 'number' ? ` · ${c.support_count} officer${c.support_count === 1 ? '' : 's'}` : ''}
                </Text>
                <Text style={{ color: colors.text, fontSize: 12, lineHeight: 18 }}>{c.text}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        {/* Provenance drawer — the rejects that taught one judgement. */}
        {!!openClause && (
          <View style={[styles.card, { borderColor: colors.primary, backgroundColor: colors.surface }]}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <Text style={{ color: colors.text, fontWeight: '700', flex: 1 }}>
                Why it learned this
              </Text>
              <TouchableOpacity onPress={() => setOpenClause(null)}>
                <Ionicons name="close" size={18} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <Text style={{ color: colors.text, fontSize: 13, marginTop: 6 }}>
              {openClause.clause?.text}
            </Text>
            {(openClause.corrections || []).map((cr) => (
              <View key={cr.correction_id} style={{ marginTop: 10 }}>
                <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
                  {cr.officer ? (
                    <Text onPress={() => showTaughtBy(cr.officer)}
                          style={{ color: colors.primary, textDecorationLine: 'underline' }}>
                      {cr.officer}
                    </Text>
                  ) : 'an officer'} · {fmtWhen(cr.at)}
                  {cr.reason_inferred ? ' · category inferred' : ''}
                </Text>
                <Text style={{ color: colors.text, fontSize: 12, lineHeight: 18 }}>
                  “{cr.reason_text || '(no reason recorded)'}”
                </Text>
              </View>
            ))}
            {(openClause.corrections || []).length === 0 && (
              <Text style={{ color: colors.textSecondary, fontSize: 12, marginTop: 8 }}>
                The corrections behind this judgement are no longer available.
              </Text>
            )}
            {canCurate && openClause.clause?.status === 'sop_conflict' && (
              <View style={{ marginTop: 10, gap: 6 }}>
                <Text style={{ color: AMBER, fontSize: 12 }}>
                  This judgement conflicts with the SOP
                  {openClause.clause?.sop_conflict?.note
                    ? `: ${openClause.clause.sop_conflict.note}` : ''}.
                  The SOP wins until you decide.
                </Text>
                <View style={{ flexDirection: 'row', gap: 12 }}>
                  <TouchableOpacity onPress={() => resolveSop(openClause.clause.clause_id, 'retire')} style={styles.inlineBtn}>
                    <Ionicons name="trash-outline" size={14} color={RED} />
                    <Text style={{ color: RED, fontSize: 13 }}>SOP is right — retire it</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => resolveSop(openClause.clause.clause_id, 'acknowledge')} style={styles.inlineBtn}>
                    <Ionicons name="checkmark-circle-outline" size={14} color={colors.primary} />
                    <Text style={{ color: colors.primary, fontSize: 13 }}>Officers are right — SOP needs updating</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
            {canCurate && openClause.clause?.status === 'challenged' && (
              <View style={{ marginTop: 10, gap: 6 }}>
                <Text style={{ color: AMBER, fontSize: 12 }}>
                  {openClause.clause?.challenge?.by || 'An officer'} stopped this judgement
                  {openClause.clause?.challenge?.reason ? `: “${openClause.clause.challenge.reason}”` : ''}.
                  It is not being used until you decide.
                </Text>
                <View style={{ flexDirection: 'row', gap: 12 }}>
                  <TouchableOpacity onPress={() => resolveChallenge(openClause.clause.clause_id, 'uphold')} style={styles.inlineBtn}>
                    <Ionicons name="trash-outline" size={14} color={RED} />
                    <Text style={{ color: RED, fontSize: 13 }}>They're right — retire it</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => resolveChallenge(openClause.clause.clause_id, 'dismiss')} style={styles.inlineBtn}>
                    <Ionicons name="checkmark-circle-outline" size={14} color={colors.primary} />
                    <Text style={{ color: colors.primary, fontSize: 13 }}>Judgement stands — put it back</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
            {openClause.clause?.status === 'underperforming' && (
              <View style={{ marginTop: 8, gap: 6 }}>
                <Text style={{ color: AMBER, fontSize: 12 }}>
                  Your team overruled this on {openClause.clause?.blamed_count ?? '—'} of the{' '}
                  {openClause.clause?.fired_count ?? '—'} cases it was applied to, so it stopped
                  being used.
                </Text>
                {canCurate && (
                  <TouchableOpacity onPress={() => reinstate(openClause.clause.clause_id)} style={styles.inlineBtn}>
                    <Ionicons name="refresh-outline" size={14} color={colors.primary} />
                    <Text style={{ color: colors.primary, fontSize: 13 }}>
                      Put it back and watch it again
                    </Text>
                  </TouchableOpacity>
                )}
              </View>
            )}
            {canCurate && challenging === openClause.clause?.clause_id && (
              <View style={{ marginTop: 10, gap: 6 }}>
                <Text style={{ color: colors.text, fontSize: 12 }}>
                  Why is this wrong? Whoever adjudicates sees this, with your name on it.
                </Text>
                <TextInput
                  value={challengeText}
                  onChangeText={setChallengeText}
                  placeholder="e.g. a police report is not obtainable within 24h in this district"
                  placeholderTextColor={colors.textSecondary}
                  multiline
                  style={{
                    borderWidth: 1, borderColor: colors.border, borderRadius: 8,
                    padding: 8, minHeight: 56, color: colors.text, fontSize: 12,
                  }}
                />
                <View style={{ flexDirection: 'row', gap: 12 }}>
                  <TouchableOpacity
                    disabled={!challengeText.trim()}
                    onPress={() => challenge(openClause.clause.clause_id, challengeText.trim())}
                    style={[styles.inlineBtn, { opacity: challengeText.trim() ? 1 : 0.5 }]}
                  >
                    <Ionicons name="hand-left-outline" size={14} color={AMBER} />
                    <Text style={{ color: AMBER, fontSize: 13 }}>Stop it pending review</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => { setChallenging(null); setChallengeText(''); }} style={styles.inlineBtn}>
                    <Text style={{ color: colors.textSecondary, fontSize: 13 }}>Cancel</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
            {canCurate && !['retired', 'sop_conflict', 'challenged'].includes(openClause.clause?.status) && (
              <View style={{ flexDirection: 'row', gap: 14, marginTop: 4 }}>
                {/* Only something IN SERVICE can be stopped. Offering it on an
                    already-parked judgement did nothing and used to open a
                    route back to active via "dismiss". */}
                {challenging !== openClause.clause?.clause_id
                  && ['active', 'candidate', 'dissented'].includes(openClause.clause?.status) && (
                  <TouchableOpacity onPress={() => { setChallenging(openClause.clause.clause_id); setChallengeText(''); }} style={styles.inlineBtn}>
                    <Ionicons name="hand-left-outline" size={14} color={AMBER} />
                    <Text style={{ color: AMBER, fontSize: 13 }}>Challenge this</Text>
                  </TouchableOpacity>
                )}
                <TouchableOpacity onPress={() => retire(openClause.clause.clause_id)} style={styles.inlineBtn}>
                  <Ionicons
                    name={armedRetire === openClause.clause?.clause_id ? 'alert-circle' : 'trash-outline'}
                    size={14} color={RED} />
                  <Text style={{ color: RED, fontSize: 13, fontWeight: armedRetire === openClause.clause?.clause_id ? '700' : '400' }}>
                    {armedRetire === openClause.clause?.clause_id
                      ? 'Tap again to retire — it stops being used now'
                      : 'Retire this judgement'}
                  </Text>
                </TouchableOpacity>
                {openClause.clause?.status !== 'quarantined' && (
                  <TouchableOpacity onPress={() => quarantine(openClause.clause.clause_id)} style={styles.inlineBtn}>
                    <Ionicons name="pause-circle-outline" size={14} color={AMBER} />
                    <Text style={{ color: AMBER, fontSize: 13 }}>Quarantine (hold, reversible)</Text>
                  </TouchableOpacity>
                )}
              </View>
            )}
          </View>
        )}
        {provLoading && <ActivityIndicator color={colors.primary} />}

        {/* ── Precedents ── */}
        {tab === 'precedents' && (
          <>
            <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
              {DISPOSITION_FILTERS.map((f) => (
                <TouchableOpacity key={f.id || 'all'} onPress={() => setDispFilter(f.id)} style={[styles.chip, {
                  borderColor: dispFilter === f.id ? colors.primary : colors.border,
                  backgroundColor: dispFilter === f.id ? `${colors.primary}14` : colors.surface,
                }]}>
                  <Text style={{ color: dispFilter === f.id ? colors.primary : colors.text, fontSize: 12 }}>{f.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
            {!loading && items.map((row) => (
              <View key={`${row.correlation_id || 'x'}-${row.item_id}`} style={[styles.card, { borderColor: colors.border, backgroundColor: colors.surface, opacity: row.retrieval_excluded ? 0.6 : 1 }]}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text style={{ color: colors.text, fontWeight: '600' }} numberOfLines={1}>
                    {row.subject || row.item_id}
                  </Text>
                  <Text style={{ color: colors.textSecondary, fontSize: 11 }}>{fmtWhen(row.created_at)}</Text>
                </View>
                <Text style={{ color: colors.textSecondary, fontSize: 12 }} numberOfLines={1}>
                  {humanKind(row.modality, row.task_type)} · {row.item_id}
                </Text>
                <Text style={{ color: colors.text, fontSize: 12 }}>
                  App suggested: {row.recommendation || '—'}  ·  Your team: {humanDisposition(row.disposition)}
                  {row.outcome?.label ? `  ·  Result: ${row.outcome.label}` : ''}
                  {row.precedents_used?.total ? `  ·  used ${row.precedents_used.total} past example${row.precedents_used.total === 1 ? '' : 's'}` : ''}
                </Text>
                {!!row.disposition_reason && (
                  <Text style={{ color: colors.textSecondary, fontSize: 12, fontStyle: 'italic' }}>
                    “{row.disposition_reason}” — {row.disposition_actor || 'officer'}{row.disposition_at ? ` · ${fmtWhen(row.disposition_at)}` : ''}
                  </Text>
                )}
                {/* Attribution for dispositions with no free-text reason (e.g. a plain
                    Accept): the officer + timestamp are on the ledger row — surface them
                    so every settled item shows who decided and when, not just reasoned ones. */}
                {!row.disposition_reason && row.disposition && row.disposition !== 'proposed' && !!row.disposition_actor && (
                  <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
                    {humanDisposition(row.disposition)} by {row.disposition_actor}{row.disposition_at ? ` · ${fmtWhen(row.disposition_at)}` : ''}
                  </Text>
                )}
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                  {row.retrieval_excluded ? (
                    <Text style={{ color: colors.danger, fontSize: 11, fontWeight: '600' }}>
                      Not used as an example — still on record
                    </Text>
                  ) : <View />}
                  {canCurate && (
                    <TouchableOpacity onPress={() => toggleExclusion(row)} style={styles.inlineBtn}>
                      <Ionicons name={row.retrieval_excluded ? 'refresh-outline' : 'eye-off-outline'} size={14} color={colors.primary} />
                      <Text style={{ color: colors.primary, fontSize: 12 }}>
                        {row.retrieval_excluded ? 'Use as an example again' : "Don't use as an example"}
                      </Text>
                    </TouchableOpacity>
                  )}
                </View>
              </View>
            ))}
            {!loading && items.length === 0 && (
              <Text style={{ color: colors.textSecondary }}>
                Nothing reviewed yet. This fills in as the app looks at photos and documents and your team decides on them.
              </Text>
            )}
            {!loading && nextCursor && (
              <TouchableOpacity onPress={() => loadTab(slug, 'precedents', { reset: false })} style={[styles.saveBtn, { backgroundColor: colors.surfaceAlt, alignSelf: 'center' }]}>
                <Text style={{ color: colors.text }}>Load more</Text>
              </TouchableOpacity>
            )}
          </>
        )}

        {/* ── Stats ── */}
        {tab === 'stats' && !loading && metrics && !metrics.error && (
          <>
            <View style={{ flexDirection: 'row', gap: 18, flexWrap: 'wrap' }}>
              {[['Changed by your team', fmtPct(metrics.override_rate)],
                ['Turned out right', fmtPct(metrics.good_rate)],
                ['Handled automatically', fmtPct(metrics.automation_rate)],
                ['Decisions (90 days)', String(metrics.total ?? 0)]].map(([label, value]) => (
                  <View key={label}>
                    <Text style={{ color: colors.textSecondary, fontSize: 11 }}>{label}</Text>
                    <Text style={{ color: colors.text, fontSize: 20, fontWeight: '700' }}>{value}</Text>
                  </View>
                ))}
            </View>
            {metrics.memory && (
              <View style={[styles.card, { borderColor: colors.border, backgroundColor: colors.surface }]}>
                <Text style={{ color: colors.text, fontWeight: '700' }}>What it has learned so far</Text>
                {(metrics.memory.clauses?.by_status
                  ? Object.entries(metrics.memory.clauses.by_status).map(
                      ([k, v]) => ({ modality: k, task_type: '', corrections: v, version: '' }))
                  : []).map((r) => (
                  <Text key={`${r.modality}-${r.task_type}`} style={{ color: colors.text, fontSize: 12 }}>
                    {humanKind(r.modality, r.task_type)}: learned from {r.corrections} correction{r.corrections === 1 ? '' : 's'}
                  </Text>
                ))}
                <Text style={{ color: colors.text, fontSize: 12 }}>
                  Reviewed {metrics.memory.items?.total ?? 0} item{(metrics.memory.items?.total ?? 0) === 1 ? '' : 's'}
                  {metrics.memory.items?.by_disposition?.accept ? ` · ${metrics.memory.items.by_disposition.accept} approved` : ''}
                  {metrics.memory.items?.by_disposition?.reject ? ` · ${metrics.memory.items.by_disposition.reject} rejected` : ''}
                  {metrics.memory.items?.precedent_grounded ? ` · ${metrics.memory.items.precedent_grounded} used past examples` : ''}
                </Text>
                {metrics.memory.grounding?.total_samples != null && (
                  <Text style={{ color: colors.text, fontSize: 12 }}>
                    Examples on hand to guide new work: {metrics.memory.grounding.total_samples}
                  </Text>
                )}
              </View>
            )}
          </>
        )}
      </ScrollView>

      {selectedApp && (
        <View style={[styles.footer, { borderTopColor: colors.border }]}>
          <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
            The decision history is never edited or deleted — you only choose which past items the app learns from. Judgements are never rewritten either: each one is tied to the corrections that taught it, so you retire or hold it instead. Your SOP stays the rules, and always wins.
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: 1,
  },
  title: { fontSize: 18, fontWeight: '700' },
  appRow: { maxHeight: 46, marginTop: 10 },
  chip: {
    borderWidth: 1, borderRadius: 16, paddingHorizontal: 12, paddingVertical: 6,
    maxWidth: 220,
  },
  tabRow: { flexDirection: 'row', gap: 4, paddingHorizontal: 16, borderBottomWidth: 1, marginTop: 8 },
  tab: { paddingVertical: 10, paddingHorizontal: 12 },
  card: { borderWidth: 1, borderRadius: 10, padding: 12, gap: 6 },
  helpCard: { borderRadius: 8, padding: 10 },
  inlineBtn: { flexDirection: 'row', alignItems: 'center', gap: 5, alignSelf: 'flex-start', paddingVertical: 2 },
  saveBtn: { borderRadius: 8, paddingHorizontal: 14, paddingVertical: 9 },
  exportBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 5, borderWidth: 1,
    borderRadius: 8, paddingHorizontal: 12, paddingVertical: 7,
  },
  footer: { borderTopWidth: 1, paddingHorizontal: 16, paddingVertical: 8 },
});
