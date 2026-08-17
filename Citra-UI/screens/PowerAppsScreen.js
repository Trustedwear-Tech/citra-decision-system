// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * PowerAppsScreen.js — "Smart Apps" surface (full-screen).
 *
 * Lists apps / dashboards from smart-app-service in a single unified BA
 * workspace. Designed to render as a full-viewport modal launched from
 * HomePanel.js. Two-column layout on wide web (left rail + main grid);
 * collapses to single-column on narrow / native.
 *
 * Data flow:
 *   - GET  /apps?kind=…           list apps/dashboards
 *   - POST /build                  spawn a builder pod (build_kinds[])
 *   - POST /apps/{slug}/edit       re-open in builder (seeds spec)
 *   - DELETE /apps/{slug}          archive
 *   - POST /apps/{slug}/promote-to-prod            promote a test app
 *   - GET/PATCH/POST /apps/{slug}/ai-triggers[...]  AI-trigger config + Run-now
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, FlatList, ActivityIndicator,
  TextInput, StyleSheet, Platform, RefreshControl, Linking, Alert,
  ScrollView, useWindowDimensions, Pressable, Modal,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import SmartAppService from '../services/SmartAppService';
import AdminUserService from '../services/AdminUserService';
import authService from '../services/authService';
import OperationsControlScreen from './OperationsControlScreen';
import { highestAdminScopeForCurrentUser } from '../services/adminScope';
import SmartAppBuilderScreen from './SmartAppBuilderScreen';
import SmartAppLauncherModal from '../components/SmartAppLauncherModal';
import BuildKindPickerModal from '../components/BuildKindPickerModal';
import TransferResourceModal from '../components/admin/TransferResourceModal';
import AITriggersPanel from '../components/AITriggersPanel';
import AppVersionsPanel from '../components/AppVersionsPanel';
import SmartAppAuditScreen from './SmartAppAuditScreen';
import OpsMark from '../components/brand/OpsMark';
import useIsMobileWeb from '../hooks/useIsMobileWeb';

// Both entry points — the builder card and the "My Decision Apps" / "My
// Dashboards" consumer cards — carry the same three lenses, matching the only
// three ways a user reaches an app: they own it (mine), it was published to
// them (shared), or they oversee it as an admin (admin).
//
// The server scope is still called "shared" for wire-compat, but it means
// PUBLISHED TO ME: it is the complement of "mine" (owner_id $nin my SAs) over
// everything visible, and visibility IS the publish targets — team:<SA>,
// dept:<id>, audience "org", or membership of the owning SA. Publishing sets
// `audience` and never `owner_id`, so a consumer's "My apps" is always empty
// and this tab is the only place their apps appear. Sharing-to-arbitrary-teams
// is gone; nothing else populates it.
//
// Test is the one builder-only lens (see SCOPE_TABS): it lists your apps
// awaiting promotion, and the consumer cards never build or promote.
const SCOPE_TABS_BASE = [
  { id: 'mine',   label: 'My apps' },
  { id: 'shared', label: 'Published to me' },
  { id: 'test',   label: 'Test' },
];
const SCOPE_ADMIN_LABELS = {
  platform: 'Admin',
  org:      'Admin',
  dept:     'Admin',
};
// One plain-language line shown under the active tab — no jargon.
const SCOPE_HINTS = {
  mine:   'Apps you created',
  shared: 'Apps published to you — by your team, department or organisation',
  test:   'Your apps in test, not yet promoted to production',
  admin:  'Every app you oversee',
};
// The same lenses in the consumer voice. The cards are list-only — nothing is
// created or promoted there, so the builder wording above ("Apps you created")
// is wrong for a persona that can't build — and they name the kind they were
// opened for. Keeps the "tap Open" affordance the consumer copy carried before
// these tabs existed.
function consumerScopeHint(scope, kind) {
  const noun = kind === 'dashboard' ? 'Dashboards' : 'Decision Apps';
  const verb = kind === 'dashboard' ? 'view' : 'use';
  const lens =
    scope === 'mine'  ? 'you own' :
    scope === 'admin' ? 'you oversee' :
                        'published to you';
  return `${noun} ${lens} — tap Open to ${verb}`;
}

const DEFAULT_THEME = {
  background:    '#FFFFFF',
  surface:       '#F9FAFB',
  surfaceAlt:    '#F3F4F6',
  text:          '#111827',
  textSecondary: '#6B7280',
  primary:       '#3B82F6',
  border:        '#E5E7EB',
  danger:        '#DC2626',
};

// Kind tokens — each kind owns a 2-stop gradient + flat fallbacks for
// badges, small chips, and dot indicators. Keep the gradients in the
// same lightness band so the UI feels cohesive across kinds.
const KIND_TOKENS = {
  app: {
    bg: '#E0EAFB', fg: '#1E3A8A', accent: '#2563EB',
    icon: 'apps-outline', label: 'App',
    gradient:    ['#1E3A8A', '#2563EB'],     // navy → steel blue
  },
  dashboard: {
    bg: '#E7E7FB', fg: '#4338CA', accent: '#6366F1',
    icon: 'bar-chart-outline', label: 'Dashboard',
    gradient:    ['#4338CA', '#6366F1'],     // indigo (muted, no fuchsia)
  },
  // Headless / decision-API apps — no UI, exposed as /run + /approve for an
  // external front-end. Emerald keeps them visually distinct from navy (app)
  // and indigo (dashboard).
  api: {
    bg: '#DCFCE7', fg: '#065F46', accent: '#059669',
    icon: 'code-slash-outline', label: 'API',
    gradient:    ['#065F46', '#059669'],     // emerald
  },
  // Embeddable cards — rendered inside a CUSTOMER's own application by
  // citra.js. Teal sits between navy (app) and emerald (api), which is also
  // where the surface sits: their front-end, but they don't build the UI.
  embed: {
    bg: '#CCFBF1', fg: '#0F766E', accent: '#14B8A6',
    icon: 'browsers-outline', label: 'Embedded',
    gradient:    ['#0F766E', '#14B8A6'],     // teal
  },
};

// Every SmartApp is stored as kind='app'; the list buckets it into one of four
// display kinds by precedence:
//   - headless (no UI, decision-API only)            → 'api'
//   - has an embed page (page.kind==='embed')        → 'embed'
//   - has a dashboard page (page.kind==='dashboard') → 'dashboard'
//   - otherwise (working UI screens)                 → 'app'
// A headless app has no pages, so it can never also be an embed or dashboard.
// Embed is checked before dashboard: an app carrying both is primarily an
// embeddable card with a dashboard page beside it, and the card the customer
// integrates is the one worth badging. Neither is a stored artefact kind.
const displayKind = (a) =>
  a && a.headless             ? 'api'
  : a && a.has_embed_page     ? 'embed'
  : a && a.has_dashboard_page ? 'dashboard'
  : ((a && a.kind) || 'app');

// Brand gradient — used for the global "Build new" CTA and rail brand.
// Steel-navy: corporate, matches the OpsMark brand glyph.
const BRAND_GRADIENT  = ['#1E3A8A', '#2563EB'];   // navy → steel blue
const HEADER_GRADIENT = ['#FFFFFF', '#F8FAFC'];   // subtle paper
const RAIL_GRADIENT   = ['#FFFFFF', '#F1F5F9'];   // ditto, cooler

const KIND_TABS = [
  { id: 'all',       label: 'All' },
  { id: 'app',       label: 'Apps' },
  { id: 'embed',     label: 'Embedded' },
  { id: 'api',       label: 'APIs' },
  { id: 'dashboard', label: 'Dashboards' },
];

const SORT_OPTIONS = [
  { id: 'recent', label: 'Recently deployed' },
  { id: 'name',   label: 'Name (A–Z)' },
  { id: 'status', label: 'Status' },
];

const RAIL_BREAKPOINT = 1024;
const TWO_COL_BREAKPOINT = 760;
const THREE_COL_BREAKPOINT = 1180;

export default function PowerAppsScreen({
  visible,
  onClose,
  theme = DEFAULT_THEME,
  // 'builder' (default) = full CRUD + Build new, opened from the flagship card.
  // 'consumer' = list-only, filtered to one kind, no Build/edit/API — opened
  // from the "My Decision Apps" / "My Dashboards" home cards. In consumer mode
  // the user only sees apps/dashboards published to them and can only Open.
  mode = 'builder',
  // In consumer mode, the kind to show ('app' | 'dashboard'). Ignored in builder.
  initialKind = null,
  // Deep-link into the platform Memory screen with this app preselected
  // (rendered by App.js; modals stack). Optional — link hidden when absent.
  onOpenMemory,
}) {
  const isConsumer = mode === 'consumer';
  const colors = { ...DEFAULT_THEME, ...theme };
  const { width: winWidth } = useWindowDimensions();
  const showRail = Platform.OS === 'web' && winWidth >= RAIL_BREAKPOINT;
  // Mobile web (browser or installed PWA) gets an in-app iframe launcher
  // instead of window.open new-tab. On desktop web, new tab is the right
  // behaviour and the modal is unused.
  const { isMobileWeb } = useIsMobileWeb();
  const numColumns =
    winWidth >= THREE_COL_BREAKPOINT ? 3
    : winWidth >= TWO_COL_BREAKPOINT ? 2
    : 1;

  // ── scope tabs (Mine / Shared / Admin) ─────────────────────────────
  const adminScope = useMemo(() => highestAdminScopeForCurrentUser(), []);
  const SCOPE_TABS = useMemo(() => {
    // Consumer cards drop Test — it lists your own apps awaiting promotion,
    // which is a build-time concern the list-only surface can't act on.
    const base = isConsumer
      ? SCOPE_TABS_BASE.filter((t) => t.id !== 'test')
      : SCOPE_TABS_BASE;
    if (!adminScope) return base;
    return [
      ...base,
      { id: 'admin', label: SCOPE_ADMIN_LABELS[adminScope] || 'Admin' },
    ];
  }, [adminScope, isConsumer]);

  // ── data ───────────────────────────────────────────────────────────
  const [apps, setApps] = useState([]);
  const [serverTotal, setServerTotal] = useState(null); // authoritative count from GET /apps `total`
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  // ── filters / sort / search ───────────────────────────────────────
  // Consumer mode lands on the requested kind (app/dashboard); builder on 'all'.
  const [kindFilter, setKindFilter] = useState(isConsumer ? (initialKind || 'app') : 'all');
  // Both modes land on 'mine' — you start from the apps you own. A consumer
  // owns none (publishing sets `audience`, never `owner_id`), so an empty 'mine'
  // on a consumer card falls through to 'shared' once; see autoSwitchedRef
  // below. The old 'all' (Everything) tab was removed: it merged mine+shared but
  // NOT test (separate store), so its number never added up and it answered no
  // question the other tabs don't.
  const [scope, setScope] = useState('mine');
  const [includeArchived, setIncludeArchived] = useState(false);

  // ── kill switches (halts/pauses) ───────────────────────────────────────
  const [haltState, setHaltState] = useState({ controls: [] });
  const [showHaltModal, setShowHaltModal] = useState(false);
  const loadHalt = useCallback(async () => {
    try {
      const r = await SmartAppService.listHalt();
      setHaltState(r && Array.isArray(r.controls) ? r : { controls: [] });
    } catch { /* non-admins get 403 — leave empty, no banner */ }
  }, []);

  const _controls = haltState.controls || [];
  const globalHalt = _controls.find((c) => c.scope_type === 'global') || null;
  const scopeHalt = globalHalt
    || _controls.find((c) => c.scope_type === 'org' || c.scope_type === 'dept') || null;
  const pausedSlugs = new Set(
    _controls.filter((c) => c.scope_type === 'app').map((c) => c.scope_id)
  );

  // Per-app pause / resume.
  const togglePause = useCallback(async (item, paused) => {
    try {
      if (paused) await SmartAppService.resumeApp(item.slug);
      else await SmartAppService.pauseApp(item.slug, 'Paused from console');
      await loadHalt();
    } catch (e) { setError(e.message || 'Pause failed'); }
  }, [loadHalt]);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('recent');

  // ── builder ────────────────────────────────────────────────────────
  // Chat-first: there is no goal field and no kind picker. "New SmartApp"
  // opens the builder modal in draft; the BA's first chat message spawns
  // the session. Every build is an app — the agent adds dashboard pages /
  // workflows through the conversation.
  const [buildBusy, setBuildBusy] = useState(false);
  const [buildError, setBuildError] = useState('');
  const [activeBuildSession, setActiveBuildSession] = useState(null);
  const [showBuildPicker, setShowBuildPicker] = useState(false); // surface picker shown before the builder opens
  const [publishing, setPublishing] = useState(null);  // app whose audience is being edited
  const [transferring, setTransferring] = useState(null); // app item being transferred (admin)
  const [aiTriggersApp, setAiTriggersApp] = useState(null); // app whose AI-trigger (schedule/webhook) panel is open
  const [versionsApp, setVersionsApp] = useState(null); // app whose version-history panel is open
  const [auditApp, setAuditApp] = useState(null); // app whose run/decision audit trail is open (owner-accessible, not admin-only)
  const [launching, setLaunching] = useState(null);  // app being rendered in the iframe launcher modal
  const [promoting, setPromoting] = useState(null);  // test app being promoted to prod (opens audience picker)
  const [groundingJob, setGroundingJob] = useState(null);  // {slug,title,run_id,status,phase,progress,counts,result,error} of the active/last grounding refresh
  const [selfLearn, setSelfLearn] = useState({});  // slug -> auto_refresh bool (per-app auto-learning; undefined = unknown)
  const [groundingStatus, setGroundingStatus] = useState({});  // slug -> {never_refreshed, last_refreshed_at, sample_count, ...}
  const [metricsApp, setMetricsApp] = useState(null);  // app whose loop-metrics modal is open
  const [groundingInfoApp, setGroundingInfoApp] = useState(null);  // app whose grounding-freshness modal is open
  const [autoLearnApp, setAutoLearnApp] = useState(null);  // app whose auto-learn explainer modal is open
  const [pauseConfirmApp, setPauseConfirmApp] = useState(null);  // app pending pause confirm
  const [fraudReport, setFraudReport] = useState(null);  // {slug,title,loading?,report?,error?} of the L3 fraud-calibration modal
  const [metrics, setMetrics] = useState(null);        // fetched loop-metrics ({...} | {error})
  const [metricsLoading, setMetricsLoading] = useState(false);
  // Headless decision-API test playground
  const [testApp, setTestApp] = useState(null);
  const [testContract, setTestContract] = useState(null);
  const [testAction, setTestAction] = useState('');
  const [testInputs, setTestInputs] = useState('{}');
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState(null);   // RunResponse from /run
  const [testCid, setTestCid] = useState(null);         // correlation_id for /approve
  const [testOverrides, setTestOverrides] = useState('[]');
  const [testBusy, setTestBusy] = useState(false);      // approve in flight
  const [testCommitted, setTestCommitted] = useState(null);
  const [testError, setTestError] = useState('');

  // Spec viewer / editor (view → copy → edit → save)
  const [sigBusy, setSigBusy] = useState(false);
  const [sigOpen, setSigOpen] = useState(true);
  const [specApp, setSpecApp] = useState(null);
  const [specAppText, setSpecAppText] = useState('');
  const [specAgentText, setSpecAgentText] = useState('');
  const [specTab, setSpecTab] = useState('app');     // 'app' | 'agent'
  const [specEditing, setSpecEditing] = useState(false);
  const [specSaving, setSpecSaving] = useState(false);
  const [specLoading, setSpecLoading] = useState(false);
  const [specError, setSpecError] = useState('');
  // Advisory review (lint) — a SEPARATE band; never touches the spec JSON.
  const [lint, setLint] = useState(null);          // { findings, counts, live, live_error }
  const [lintLoading, setLintLoading] = useState(false);
  const [lintOpen, setLintOpen] = useState(true);

  // ── load ───────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    setError('');
    setLoading(true);
    try {
      // Scope-driven server query (mine / shared / admin). Fetch all
      // kinds within that scope and filter the kind chips client-side
      // so the left-rail / chip counts stay accurate without a second
      // request.
      const resp = await SmartAppService.listApps({ scope, includeArchived });
      setApps(Array.isArray(resp?.apps) ? resp.apps : []);
      setServerTotal(Number.isFinite(resp?.total) ? resp.total : null);
    } catch (err) {
      setError(err.message || 'Failed to load apps');
      setApps([]);
      setServerTotal(null);
    } finally {
      setLoading(false);
    }
  }, [scope, includeArchived]);

  // Open the spec viewer for an app/API: fetch the full app_spec + agent_spec.
  // Run the advisory review. live=true augments with real cardinality (slower).
  const runLint = useCallback(async (slug, live = false) => {
    setLintLoading(true);
    try {
      const r = await SmartAppService.lintSpec(slug, { live });
      setLint(r || { findings: [] });
    } catch (e) {
      setLint({ findings: [], lint_error: e?.message || 'Review failed' });
    } finally {
      setLintLoading(false);
    }
  }, []);

  const openSpec = useCallback(async (item) => {
    setSpecApp(item); setSpecEditing(false); setSpecError(''); setSpecTab('app');
    setSpecAppText(''); setSpecAgentText(''); setSpecLoading(true);
    setLint(null); setLintOpen(true);
    try {
      const d = await SmartAppService.getApp(item.slug);
      setSpecAppText(JSON.stringify(d?.app_spec || {}, null, 2));
      setSpecAgentText(d?.agent_spec ? JSON.stringify(d.agent_spec, null, 2) : '');
    } catch (e) {
      setSpecError(e?.message || 'Failed to load spec');
    } finally {
      setSpecLoading(false);
    }
    runLint(item.slug, false);   // static review fires immediately, alongside the spec
  }, [runLint]);

  // ── Case signature: the facet families, in plain language ────────────────
  //
  // These decide the SCOPE of every judgement the app learns — a clause fires
  // only if its scope is a subset of the case's facets. They were authored by
  // the builder agent and, before CS-04, nobody was ever required to look at
  // them; the only way to see them at all was to read them out of the spec
  // JSON. Read from specAppText so it always reflects what is on screen,
  // including straight after a save.
  const caseSig = useMemo(() => {
    try {
      const sig = (JSON.parse(specAppText || '{}') || {}).case_signature;
      const facets = (sig && sig.facets) || [];
      if (!facets.length) return null;
      const families = facets.map((f) => f && f.family).filter(Boolean).sort();
      const confirmed = (sig.confirmed_families || []).slice().sort();
      return {
        facets,
        families,
        confirmedBy: sig.confirmed_by || '',
        confirmedAt: sig.confirmed_at || '',
        // Stale when someone confirmed a DIFFERENT list — the confirmation
        // describes a spec that no longer exists, which is exactly what CS-04
        // rejects at publish.
        stale: !!sig.confirmed_by && JSON.stringify(confirmed) !== JSON.stringify(families),
      };
    } catch { return null; }
  }, [specAppText]);

  const confirmSig = useCallback(async () => {
    if (!specApp || !caseSig) return;
    setSigBusy(true); setSpecError('');
    try {
      await SmartAppService.confirmCaseSignature(specApp.slug, caseSig.families);
      const d = await SmartAppService.getApp(specApp.slug);
      setSpecAppText(JSON.stringify(d?.app_spec || {}, null, 2));
      load();
    } catch (e) {
      setSpecError(e?.message || 'Could not record the confirmation.');
    } finally {
      setSigBusy(false);
    }
  }, [specApp, caseSig, load]);

  // Validate + persist the edited spec, then refresh the list.
  const saveSpec = useCallback(async () => {
    if (!specApp) return;
    let app_spec, agent_spec = null;
    try {
      app_spec = JSON.parse(specAppText);
      agent_spec = specAgentText.trim() ? JSON.parse(specAgentText) : null;
    } catch (e) {
      setSpecError('Invalid JSON: ' + (e?.message || e));
      return;
    }
    setSpecSaving(true); setSpecError('');
    try {
      const d = await SmartAppService.saveSpec(specApp.slug, { app_spec, agent_spec });
      setSpecAppText(JSON.stringify(d?.app_spec || {}, null, 2));
      if (d?.agent_spec) setSpecAgentText(JSON.stringify(d.agent_spec, null, 2));
      setSpecEditing(false);
      Alert.alert('Saved', 'Spec validated and saved.');
      runLint(specApp.slug, false);   // refresh review against the saved spec
      load();
    } catch (e) {
      setSpecError(e?.message || 'Save failed (spec rejected by validation).');
    } finally {
      setSpecSaving(false);
    }
  }, [specApp, specAppText, specAgentText, load, runLint]);

  useEffect(() => {
    if (visible) load();
  }, [visible, load]);

  useEffect(() => {
    if (visible) loadHalt();
  }, [visible, loadHalt]);

  // Guards the one-shot consumer landing fall-through (see the effect below).
  const autoSwitchedRef = useRef(false);

  // Consumer mode: on (re)open, snap the filters back to this card's intent —
  // the requested kind and the default lens — since the same screen instance
  // may be reused across opens for different kinds. Each open earns a fresh
  // auto-switch (below).
  useEffect(() => {
    if (!visible || !isConsumer) return;
    setScope('mine');
    setKindFilter(initialKind || 'app');
    autoSwitchedRef.current = false;
  }, [visible, isConsumer, initialKind]);

  // A consumer's "My apps" is ALWAYS empty — publishing sets `audience`, never
  // `owner_id` — so landing them there strands them on a dead tab. If the loaded
  // 'mine' page comes back empty on a consumer card, fall through to
  // "Published to me", where their apps actually are. One-shot per open, so a
  // deliberate click back to "My apps" sticks. serverTotal!==null gates on a
  // COMPLETED load: it is null until one lands, which stops the initial empty
  // render from switching before 'mine' has been fetched at all.
  useEffect(() => {
    if (!visible || !isConsumer || autoSwitchedRef.current) return;
    if (loading || scope !== 'mine' || serverTotal === null) return;
    if (apps.length === 0) {
      autoSwitchedRef.current = true;
      setScope('shared');
    }
  }, [visible, isConsumer, loading, scope, serverTotal, apps]);

  // Load per-app self-learning (auto-run) state for grounded apps so the toggle
  // shows the right label. Best-effort; failures leave the app's state unknown.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const a of (apps || [])) {
        if (!a.grounded) continue;
        try {
          const r = await SmartAppService.getSelfLearning(a.slug);
          if (!cancelled) setSelfLearn((s) => ({ ...s, [a.slug]: !!r.auto_refresh }));
        } catch (_e) { /* leave unknown */ }
        try {
          const gs = await SmartAppService.getGroundingStatus(a.slug);
          if (!cancelled) setGroundingStatus((s) => ({ ...s, [a.slug]: gs }));
        } catch (e) {
          // Per-app enrichment for the card badge — degrade (don't crash the
          // list) but LOG so a real failure isn't invisible.
          console.warn(`grounding status fetch failed for ${a.slug}:`, e && e.message);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [apps]);

  // Flip per-app auto-learning ON/OFF. OFF (default) = manual refresh only.
  const toggleSelfLearning = useCallback(async (item) => {
    try {
      const target = !selfLearn[item.slug];
      const resp = await SmartAppService.setSelfLearning(item.slug, target);
      setSelfLearn((s) => ({ ...s, [item.slug]: !!resp.auto_refresh }));
      Alert.alert(
        'Self-learning',
        resp.auto_refresh
          ? 'Auto-learning ON — validated outcomes fold into memory automatically.'
          : 'Manual only — use "Refresh grounding" to learn.'
      );
    } catch (e) {
      Alert.alert('Self-learning', (e && e.message) || 'Could not change self-learning.');
    }
  }, [selfLearn]);

  // Open the per-app self-learning metrics modal.
  const openMetrics = useCallback(async (item) => {
    setMetricsApp(item); setMetrics(null); setMetricsLoading(true);
    try {
      const m = await SmartAppService.getLoopMetrics(item.slug, 90);
      setMetrics(m);
    } catch (e) {
      setMetrics({ error: (e && e.message) || 'Could not load metrics.' });
    } finally {
      setMetricsLoading(false);
    }
  }, []);
  const fmtPct = (r) => (r === null || r === undefined ? '—' : `${Math.round(r * 100)}%`);

  // Pause is a kill switch — open a confirm modal (resume is safe → no confirm).
  // NB: RN Alert.alert is a no-op on web, so these use real <Modal>s below.
  const confirmPause = useCallback((item, paused) => {
    if (paused) { togglePause(item, true); return; }  // resume: no confirm needed
    setPauseConfirmApp(item);
  }, [togglePause]);

  // Open the plain-language auto-learn explainer + toggle modal.
  const explainAutoLearn = useCallback((item) => { setAutoLearnApp(item); }, []);

  // Headless (decision-API) apps: copy the API URL + view the contract.
  const copyDecisionApiUrl = useCallback(async (item) => {
    const url = SmartAppService.decisionApiUrl(item.slug);
    try {
      await Clipboard.setStringAsync(url);
      Alert.alert('Copied', 'Decision API URL copied. POST a case here for a recommendation, then call approve.');
    } catch {
      Alert.alert('Decision API URL', url);
    }
  }, []);

  // Embeddable cards: hand the developer the snippet.
  //
  // The snippet is BUILT SERVER-SIDE, not templated here — only the runtime
  // knows its own public origin, which bundle version it serves, and whether
  // the app actually has an embed page. Templating it in the UI would let all
  // three drift from reality, and the failure mode is a developer pasting a
  // snippet that renders an empty card in production and blaming their own
  // integration.
  const exportEmbedSnippet = useCallback(async (item) => {
    try {
      const r = await SmartAppService.getEmbedSnippet(item.slug);
      await Clipboard.setStringAsync(r.snippet);
      const envNote = r.environment === 'test'
        ? '\n\nThis is the TEST key — it reads test data. Promote the app to get the live key.'
        : '';
      Alert.alert(
        'Embed snippet copied',
        `Paste it into the page you want the card on.\n\n` +
        `Key: ${r.embed_key}\nScript: ${r.script_url}` + envNote,
      );
    } catch (e) {
      // 409 is the useful one: the app has no embed page, or predates embed
      // keys. Surface the server's message — it names the fix.
      Alert.alert('Cannot export', String(e?.message || e));
    }
  }, []);

  const openContract = useCallback(async (item) => {
    try {
      const c = await SmartAppService.getDecisionContract(item.slug);
      const ep = c.endpoints || {};
      Alert.alert(
        'Decision API contract',
        `Recommend: POST ${ep.recommend?.path}\n` +
        `Approve:   POST ${ep.approve?.path}\n\n` +
        `Auth: ${c.auth}\n\n` +
        `Rule: ${c.governance}`
      );
    } catch (e) {
      Alert.alert('Contract', (e && e.message) || 'Could not load contract.');
    }
  }, []);

  // Test playground: load contract → run a case → approve/override/reject.
  const openTest = useCallback(async (item) => {
    setTestApp(item); setTestContract(null); setTestResult(null); setTestCid(null);
    setTestCommitted(null); setTestError(''); setTestOverrides('[]');
    try {
      const c = await SmartAppService.getDecisionContract(item.slug);
      setTestContract(c);
      setTestAction((c.run_actions && c.run_actions[0]) || '');
      const ex = (c.example && c.example.recommend_request && c.example.recommend_request.inputs) || {};
      setTestInputs(JSON.stringify(ex, null, 2));
    } catch (e) {
      setTestError((e && e.message) || 'Could not load contract.');
    }
  }, []);

  const runTest = useCallback(async () => {
    setTestError(''); setTestResult(null); setTestCid(null); setTestCommitted(null);
    let inputs;
    try { inputs = JSON.parse(testInputs || '{}'); }
    catch { setTestError('Inputs must be valid JSON.'); return; }
    setTestRunning(true);
    try {
      const r = await SmartAppService.runDecision(testApp.slug, { action: testAction, inputs });
      setTestResult(r); setTestCid(r.correlation_id);
    } catch (e) {
      setTestError((e && e.message) || 'Run failed.');
    } finally { setTestRunning(false); }
  }, [testApp, testAction, testInputs]);

  const approveTest = useCallback(async (decision) => {
    if (!testCid) return;
    setTestError(''); setTestBusy(true);
    let overrides = [];
    if (decision === 'approve') {
      try { overrides = JSON.parse(testOverrides || '[]'); }
      catch { setTestError('Overrides must be valid JSON (array).'); setTestBusy(false); return; }
    }
    try {
      const r = await SmartAppService.approveDecision(testApp.slug, testCid, { decision, overrides, note: '' });
      setTestCommitted(r);
    } catch (e) {
      setTestError((e && e.message) || 'Action failed.');
    } finally { setTestBusy(false); }
  }, [testApp, testCid, testOverrides]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try { await load(); } finally { setRefreshing(false); }
  }, [load]);

  // ── derived ────────────────────────────────────────────────────────
  const counts = useMemo(() => {
    // "All" uses the server's total (the page is capped at 500 rows); the
    // per-kind splits are derived from the page, which matches total in
    // practice — the cap exists so a huge tenant degrades to a right total
    // with approximate splits rather than a wrong total.
    // Every displayKind MUST have a key here. A kind that is missing is
    // silently skipped by the guard below, so its apps count toward nothing —
    // and because the chip row hides a zero-count tab, they become unreachable
    // in the list entirely. That is exactly what happened when 'embed' was
    // added to displayKind but not here.
    const out = {
      all: serverTotal ?? apps.length, app: 0, embed: 0, api: 0, dashboard: 0,
    };
    for (const a of apps) {
      const k = displayKind(a);
      if (out[k] != null) out[k] += 1;
    }
    return out;
  }, [apps, serverTotal]);

  const filteredSorted = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rows = apps;
    if (kindFilter !== 'all') rows = rows.filter((a) => displayKind(a) === kindFilter);
    if (q) {
      rows = rows.filter((a) =>
        [a.title, a.description, a.slug]
          .filter(Boolean)
          .some((s) => String(s).toLowerCase().includes(q))
      );
    }
    rows = [...rows];
    if (sortBy === 'name') {
      rows.sort((x, y) => String(x.title || x.slug).localeCompare(String(y.title || y.slug)));
    } else if (sortBy === 'status') {
      rows.sort((x, y) => String(x.status || '').localeCompare(String(y.status || '')));
    } else {
      rows.sort((x, y) => {
        const a = x.deployed_at ? new Date(x.deployed_at).getTime() : 0;
        const b = y.deployed_at ? new Date(y.deployed_at).getTime() : 0;
        return b - a;
      });
    }
    return rows;
  }, [apps, kindFilter, search, sortBy]);

  // ── actions ────────────────────────────────────────────────────────
  // Launch strategy: desktop web → new browser tab (familiar, multi-task
  // friendly). Mobile web + installed PWA → in-app iframe modal so the
  // user stays inside the Citra shell (a `_blank` would escape the PWA
  // into the system browser and lose the home/back affordances).
  //
  // Auth handoff: the runtime lives on its OWN origin (different from
  // Citra-UI's origin), so cookies / localStorage don't cross over. We
  // append the current user's JWT as `?_t=<token>` on the URL; the
  // runtime's lib/userToken.ts captures it into sessionStorage on first
  // load and strips it from the visible URL, so browser-side panel
  // fetches inside the runtime can forward a real user identity to
  // smart-app-service → dept-MCP. Without this, the MCP sees the runtime
  // service token (no user context) and 403s with "org/dept denied".
  const openApp = useCallback(async (slugOrItem) => {
    const item = typeof slugOrItem === 'string' ? null : slugOrItem;
    const slug = item ? (item.slug || item.app_id) : slugOrItem;
    const baseUrl = SmartAppService.appUrl(slug);
    let url = baseUrl;
    try {
      const token = await authService.getToken();
      if (token) {
        const sep = baseUrl.includes('?') ? '&' : '?';
        url = `${baseUrl}${sep}_t=${encodeURIComponent(token)}`;
      }
    } catch {
      // Token unavailable — fall back to the bare URL. Runtime will
      // hit smart-app-service with its scope=citra-app-runtime fallback
      // token and panels relying on user identity will 403; that's a
      // diagnosable surface, not a crash.
    }
    if (Platform.OS === 'web') {
      if (isMobileWeb) {
        setLaunching({ slug, url, title: item?.title || slug });
      } else {
        window.open(url, '_blank', 'noopener,noreferrer');
      }
    } else {
      // Native (not yet supported by the in-app launcher) — fall back to
      // the system browser via Linking until the WebView path lands.
      Linking.openURL(url).catch(() => {});
    }
  }, [isMobileWeb]);

  const archiveApp = useCallback(async (slug) => {
    const proceed = Platform.OS === 'web'
      ? window.confirm(`Archive "${slug}"? It can be restored later.`)
      : await new Promise((resolve) => Alert.alert(
          'Archive app',
          `Archive "${slug}"? It can be restored later.`,
          [
            { text: 'Cancel', onPress: () => resolve(false), style: 'cancel' },
            { text: 'Archive', onPress: () => resolve(true), style: 'destructive' },
          ],
        ));
    if (!proceed) return;
    try {
      await SmartAppService.archiveApp(slug);
      load();
    } catch (err) {
      setError(err.message || 'Archive failed');
    }
  }, [load]);

  const editApp = useCallback(async (slug) => {
    try {
      const resp = await SmartAppService.editApp(slug);
      setActiveBuildSession({
        session_id: resp.session_id,
        builder_url: resp.builder_url,
        mode: 'edit',
        slug,
        goal: `Edit ${slug}`,
      });
    } catch (err) {
      setError(err.message || 'Failed to open editor');
    }
  }, []);

  // Manually rebuild a grounded app's few-shot-from-history samples. Starts
  // the async server job, then polls its status so the BA sees live progress
  // (phase + %) and the completion event in a modal. The guard leaves live
  // samples intact on a degraded pull (surfaced as a failure here).
  // Attach the progress modal to an ALREADY-STARTED grounding run (by run_id)
  // and poll it to a terminal state. Shared by the manual "Refresh grounding"
  // button and the promote-with-refresh flow (the server enqueues the run as
  // part of promote and returns its run_id).
  const attachGroundingJob = useCallback(async (slug, title, runId) => {
    setGroundingJob({ slug, title, run_id: runId, status: 'running', phase: 'queued', progress: 0 });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      let st;
      try {
        st = await SmartAppService.getGroundingRefreshStatus(slug, runId);
      } catch (e) {
        setGroundingJob((j) => (j && j.run_id === runId
          ? { ...j, status: 'failed', phase: 'error', error: { message: e.message } } : j));
        break;
      }
      // Only update if the user hasn't dismissed this run's modal — the
      // backend job runs to completion regardless of the UI.
      setGroundingJob((j) => (j && j.run_id === runId ? {
        slug, title, run_id: runId,
        status: st.status, phase: st.phase, progress: st.progress || 0,
        counts: st.counts, result: st.result, error: st.error,
      } : j));
      if (st.status === 'complete' || st.status === 'failed') break;
    }
    // Refresh the durable freshness so the card badge/info update immediately.
    try {
      const gs = await SmartAppService.getGroundingStatus(slug);
      setGroundingStatus((s) => ({ ...s, [slug]: gs }));
    } catch (e) {
      console.warn(`grounding status refetch failed for ${slug}:`, e && e.message);
    }
  }, []);

  const refreshGrounding = useCallback(async (item) => {
    const title = item.title || item.slug;
    let runId;
    try {
      const started = await SmartAppService.startGroundingRefresh(item.slug);
      runId = started.run_id;
    } catch (err) {
      Alert.alert('Grounding refresh', err.message || 'Could not start refresh');
      return;
    }
    await attachGroundingJob(item.slug, title, runId);
  }, [attachGroundingJob]);

  // Open the grounding freshness / refresh modal (RN Alert is a no-op on web).
  const showGroundingInfo = useCallback((item) => { setGroundingInfoApp(item); }, []);

  // L3 fraud calibration — open the modal, run the (read-only) report.
  const calibrateFraud = useCallback(async (item) => {
    const title = item.title || item.slug;
    setFraudReport({ slug: item.slug, title, loading: true });
    try {
      const report = await SmartAppService.calibrateFraud(item.slug);
      setFraudReport({ slug: item.slug, title, report });
    } catch (e) {
      setFraudReport({ slug: item.slug, title, error: e.message || 'Calibration failed' });
    }
  }, []);

  const GROUNDING_PHASE_LABEL = {
    queued: 'Queued', pulling: 'Pulling your history', packaging: 'Packaging records',
    selecting: 'Selecting examples', guarding: 'Validating quality',
    embedding: 'Embedding', swapping: 'Swapping in', done: 'Complete', error: 'Failed',
  };

  // Spawned by the builder modal when the BA sends their first chat message
  // (no goal — the message itself is the first turn, replayed once the pod
  // is up). We flip the existing draft session in place to a live one so the
  // same modal instance keeps the pending first message.
  //
  // The surface levers (headless / primaryPageKind) were seeded on the draft
  // session by the build-kind picker; we forward them to the backend so the
  // builder pod runs the right phases (headless skips UI design). They are a
  // seed, not a lock — the agent can still switch surface in conversation.
  const startBuild = useCallback(async () => {
    setBuildBusy(true);
    setBuildError('');
    try {
      const buildKinds = activeBuildSession?.buildKinds || ['app'];
      const headless = !!activeBuildSession?.headless;
      const primaryPageKind = activeBuildSession?.primaryPageKind || null;
      const resp = await SmartAppService.startBuild({ buildKinds, headless, primaryPageKind });
      setActiveBuildSession((prev) => ({
        ...(prev || {}),
        draft: false,
        session_id: resp.session_id,
        builder_url: resp.builder_url,
        mode: 'new',
        buildKinds,
      }));
    } catch (err) {
      setBuildError(err.message || 'Failed to start build');
    } finally {
      setBuildBusy(false);
    }
  }, [activeBuildSession]);

  // "Build new" first asks WHAT surface to build (app / API / dashboard /
  // combo / conversational) so the offering is explicit and the BA is never
  // locked into a UI app. The picker's choice then opens the builder in draft.
  const openNewBuild = useCallback(() => {
    setBuildError('');
    setShowBuildPicker(true);
  }, []);

  // A surface tile was chosen — open the builder in draft, seeding the surface
  // levers so startBuild forwards them. 'conversational' seeds nothing
  // (headless=false, primaryPageKind=null) and the first chat turn decides.
  const onPickBuildKind = useCallback((choice) => {
    setShowBuildPicker(false);
    setBuildError('');
    setActiveBuildSession({
      draft: true,
      mode: 'new',
      buildKind: choice?.id || 'conversational',
      buildKinds: choice?.buildKinds || ['app'],
      headless: !!choice?.headless,
      primaryPageKind: choice?.primaryPageKind || null,
    });
  }, []);

  const dismissBuildSession = useCallback(async () => {
    if (!activeBuildSession?.session_id) return setActiveBuildSession(null);
    try {
      await SmartAppService.stopBuildSession(activeBuildSession.session_id);
    } catch { /* noop */ }
    setActiveBuildSession(null);
    load();
  }, [activeBuildSession, load]);

  // ── render helpers ─────────────────────────────────────────────────
  if (!visible) return null;

  // Consumer mode trims the surface: only Apps + Dashboards kind tabs (no All /
  // APIs — APIs are a builder→developer handoff, never shown to consumers), and
  // a per-surface brand.
  // A consumer sees the surfaces they can OPEN. 'embed' belongs here: an
  // embedded card is published to officers like any other app, and omitting it
  // made such apps invisible — not merely unfiltered, but absent, since they
  // are classified 'embed' and so counted under neither 'app' nor 'dashboard'.
  const visibleKindTabs = isConsumer
    ? KIND_TABS.filter((t) => ['app', 'embed', 'dashboard'].includes(t.id))
    : KIND_TABS;
  const brandTitle = isConsumer
    ? (initialKind === 'dashboard' ? 'My Dashboards' : 'My Decision Apps')
    : 'Self-Improving Decision Apps & APIs';
  const brandSub = isConsumer
    ? (initialKind === 'dashboard' ? 'Dashboards you can open' : 'Apps you can open')
    : 'Apps, APIs & dashboards';
  const scopeHint = isConsumer
    ? consumerScopeHint(scope, initialKind)
    : SCOPE_HINTS[scope];

  const renderRail = () => (
    <LinearGradient
      colors={RAIL_GRADIENT}
      start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }}
      style={[styles.rail, { borderRightColor: colors.border }]}
    >
      <View style={{ padding: 20 }}>
        <View style={styles.brandRow}>
          <OpsMark size={36} gradient={BRAND_GRADIENT} style={styles.brandMarkShadow} />
          <View style={{ flex: 1 }}>
            <Text style={[styles.brand, { color: colors.text }]}>{brandTitle}</Text>
            <Text style={[styles.brandSub, { color: colors.textSecondary }]}>
              {brandSub}
            </Text>
          </View>
        </View>
      </View>

      {!isConsumer && scope === 'mine' && (
        <GradientButton
          colors={BRAND_GRADIENT}
          onPress={openNewBuild}
          style={{ marginHorizontal: 16, marginBottom: 8, paddingVertical: 11 }}
          icon="add"
          label="Build new"
        />
      )}

      <Text style={[styles.railSection, { color: colors.textSecondary }]}>BROWSE</Text>
      {visibleKindTabs.map((t) => {
        const active = kindFilter === t.id;
        const count = counts[t.id] ?? 0;
        const tok = t.id !== 'all' ? KIND_TOKENS[t.id] : null;
        return (
          <Pressable
            key={t.id}
            onPress={() => setKindFilter(t.id)}
            style={({ hovered }) => [
              styles.railRow,
              active && {
                backgroundColor: tok ? tok.bg : '#E0E7FF',
              },
              hovered && !active && { backgroundColor: colors.surfaceAlt },
            ]}
          >
            <Ionicons
              name={tok?.icon || 'grid-outline'}
              size={16}
              color={active ? (tok?.fg || '#3730A3') : colors.textSecondary}
            />
            <Text style={[
              styles.railRowText,
              {
                color: active ? (tok?.fg || '#3730A3') : colors.textSecondary,
                fontWeight: active ? '600' : '500',
              },
            ]}>
              {t.label}
            </Text>
            <View style={[
              styles.railCount,
              { backgroundColor: active ? '#FFFFFF' : 'transparent' },
            ]}>
              <Text style={{
                color: active ? (tok?.fg || '#3730A3') : colors.textSecondary,
                fontSize: 11,
                fontWeight: '700',
              }}>{count}</Text>
            </View>
          </Pressable>
        );
      })}

      {!isConsumer && (
        <>
          <Text style={[styles.railSection, { color: colors.textSecondary }]}>STATUS</Text>
          <Pressable
            onPress={() => setIncludeArchived((v) => !v)}
            style={({ hovered }) => [
              styles.railRow,
              hovered && { backgroundColor: colors.surfaceAlt },
            ]}
          >
            <Ionicons
              name={includeArchived ? 'checkbox' : 'square-outline'}
              size={16}
              color={includeArchived ? colors.primary : colors.textSecondary}
            />
            <Text style={[styles.railRowText, { color: colors.textSecondary }]}>
              Show archived
            </Text>
          </Pressable>
        </>
      )}
    </LinearGradient>
  );

  const renderHeader = () => (
    <LinearGradient
      colors={HEADER_GRADIENT}
      start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }}
      style={[styles.headerWrap, { borderBottomColor: colors.border }]}
    >
      <View style={styles.headerTop}>
        {!showRail && (
          <View style={styles.brandRow}>
            <OpsMark size={36} gradient={BRAND_GRADIENT} style={styles.brandMarkShadow} />
            <View style={{ flex: 1 }}>
              <Text style={[styles.title, { color: colors.text }]}>{brandTitle}</Text>
              <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
                {brandSub}
              </Text>
            </View>
          </View>
        )}
        {showRail && <View style={{ flex: 1 }} />}
        <TouchableOpacity onPress={onClose} hitSlop={10} style={styles.closeBtn}>
          <Ionicons name="close" size={22} color={colors.text} />
        </TouchableOpacity>
      </View>

      {/* Scope tabs + a one-line plain hint for the active tab. Drives the
          backend ?scope= query; independent of the kind chips below. Shown in
          both modes — the consumer cards need Mine/Shared/Admin too, else an
          owner cannot see their own apps from them. */}
      <View style={[styles.scopeTabBar, { borderColor: colors.border }]}>
        {SCOPE_TABS.map((t) => {
          const active = scope === t.id;
          return (
            <TouchableOpacity
              key={t.id}
              onPress={() => setScope(t.id)}
              style={[
                styles.scopeTab,
                active && { borderBottomColor: colors.primary || '#6366f1', borderBottomWidth: 2 },
              ]}
            >
              <Text style={{
                color: active ? (colors.primary || '#6366f1') : colors.textSecondary,
                fontSize: 13,
                fontWeight: active ? '600' : '500',
              }}>
                {t.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>

      {!!scopeHint && (
        <Text style={[styles.scopeHint, { color: colors.textSecondary }]}>
          {scopeHint}
        </Text>
      )}

      <View style={styles.headerControls}>
        <View style={[styles.searchBox, { borderColor: colors.border, backgroundColor: '#FFFFFF' }]}>
          <Ionicons name="search" size={16} color={colors.textSecondary} />
          <TextInput
            value={search}
            onChangeText={setSearch}
            placeholder="Search by title, description or slug…"
            placeholderTextColor={colors.textSecondary}
            style={[styles.searchInput, { color: colors.text }]}
          />
          {!!search && (
            <TouchableOpacity onPress={() => setSearch('')} hitSlop={8}>
              <Ionicons name="close-circle" size={16} color={colors.textSecondary} />
            </TouchableOpacity>
          )}
        </View>

        <SortDropdown
          value={sortBy}
          onChange={setSortBy}
          colors={colors}
        />

        {!isConsumer && !showRail && scope === 'mine' && (
          <GradientButton
            colors={BRAND_GRADIENT}
            onPress={openNewBuild}
            icon="add"
            label="Build new"
          />
        )}

        <TouchableOpacity onPress={load} hitSlop={10} style={styles.iconBtnGhost}>
          <Ionicons name="refresh" size={18} color={colors.textSecondary} />
        </TouchableOpacity>

        {/* Automation control — opens the scoped halt modal (org/dept). Red
            when a halt is active. Any admin (org/dept) may open it. */}
        {!isConsumer && !!adminScope && (
          <TouchableOpacity
            onPress={() => setShowHaltModal(true)}
            style={{
              flexDirection: 'row', alignItems: 'center', gap: 6,
              paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8,
              backgroundColor: scopeHalt ? '#DC2626' : 'transparent',
              borderWidth: scopeHalt ? 0 : 1, borderColor: '#FCA5A5',
            }}
          >
            <Ionicons
              name={scopeHalt ? 'warning' : 'stop-circle-outline'}
              size={14}
              color={scopeHalt ? '#FFFFFF' : '#DC2626'}
            />
            <Text style={{ color: scopeHalt ? '#FFFFFF' : '#DC2626', fontWeight: '700', fontSize: 12 }}>
              {scopeHalt ? 'Halt active' : 'Automation'}
            </Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Halt banner — front and centre whenever automation is frozen at any
          scope affecting this surface. */}
      {scopeHalt && (
        <View style={{
          flexDirection: 'row', alignItems: 'center', gap: 8,
          backgroundColor: '#FEF2F2', borderTopWidth: 1, borderTopColor: '#FECACA',
          paddingHorizontal: 16, paddingVertical: 10,
        }}>
          <Ionicons name="warning" size={18} color="#B91C1C" />
          <Text style={{ color: '#B91C1C', fontSize: 13, fontWeight: '600', flex: 1 }}>
            {scopeHalt.scope_type === 'global'
              ? 'GLOBAL HALT ACTIVE'
              : `${String(scopeHalt.scope_type).toUpperCase()} HALT ACTIVE`}
            {' — automation frozen'}
            {scopeHalt.actor ? ` by ${scopeHalt.actor}` : ''}
            {scopeHalt.reason ? ` · ${scopeHalt.reason}` : ''}
            {'. Reads & audit still work.'}
          </Text>
        </View>
      )}

      {!showRail && (
        <View style={styles.tabRow}>
          {visibleKindTabs.map((t) => {
            const active = kindFilter === t.id;
            const count = counts[t.id] ?? 0;
            const tok = t.id !== 'all' ? KIND_TOKENS[t.id] : null;
            const tabContent = (
              <Text style={{
                color: active ? '#FFFFFF' : colors.textSecondary,
                fontSize: 12,
                fontWeight: active ? '600' : '500',
              }}>{t.label} · {count}</Text>
            );
            if (active) {
              return (
                <TouchableOpacity key={t.id} onPress={() => setKindFilter(t.id)}>
                  <LinearGradient
                    colors={tok?.gradient || BRAND_GRADIENT}
                    start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                    style={[styles.tab, { borderColor: 'transparent' }]}
                  >
                    {tabContent}
                  </LinearGradient>
                </TouchableOpacity>
              );
            }
            return (
              <TouchableOpacity
                key={t.id}
                onPress={() => setKindFilter(t.id)}
                style={[styles.tab, { borderColor: colors.border }]}
              >
                {tabContent}
              </TouchableOpacity>
            );
          })}
        </View>
      )}
    </LinearGradient>
  );

  const renderCard = ({ item }) => {
    const kind = displayKind(item);
    const tok = KIND_TOKENS[kind] || KIND_TOKENS.app;
    const archived = item.status === 'archived';
    // Only show editing actions the caller can actually perform — the backend
    // resolves this per app via _can_edit_app and returns it as can_edit.
    // Consumer mode force-hides ALL builder chrome (view/edit/publish/audit/…):
    // a consumer can only Open what was published to them.
    const canEdit = !isConsumer && item.can_edit !== false;
    const paused = pausedSlugs.has(item.slug);

    return (
      <View style={[
        styles.card,
        {
          flexBasis: numColumns === 1 ? '100%' : `${100 / numColumns}%`,
        },
      ]}>
        <View style={[
          styles.cardInner,
          {
            backgroundColor: colors.background,
            borderColor: colors.border,
          },
        ]}>
          <View style={styles.cardHeader}>
            <View style={styles.kindMark}>
              <LinearGradient
                colors={tok.gradient}
                start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                style={styles.kindTile}
              >
                <Ionicons name={tok.icon} size={14} color="#FFFFFF" />
              </LinearGradient>
              <Text style={[styles.kindLabel, { color: tok.fg }]}>{tok.label}</Text>
            </View>
            <View style={[
              styles.statusChip,
              {
                borderColor: archived ? colors.border : '#D1FAE5',
                backgroundColor: archived ? colors.surfaceAlt : '#F0FDF4',
              },
            ]}>
              <View style={[
                styles.statusDot,
                { backgroundColor: archived ? '#9CA3AF' : '#10B981' },
              ]} />
              <Text style={{
                color: archived ? colors.textSecondary : '#047857',
                fontSize: 10.5,
                fontWeight: '600',
              }}>
                {(item.status || 'published').replace(/^\w/, (c) => c.toUpperCase())}
              </Text>
            </View>
          </View>

          <Text style={[styles.cardTitle, { color: colors.text }]} numberOfLines={1}>
            {item.title || item.slug}
          </Text>
          {!!item.description && (
            <Text
              style={[styles.cardDesc, { color: colors.textSecondary }]}
              numberOfLines={2}
            >
              {item.description}
            </Text>
          )}

          <View style={styles.cardMetaRow}>
            <Text style={[styles.cardMeta, { color: colors.textSecondary }]} numberOfLines={1}>
              {item.slug}
            </Text>
            <Text style={[styles.cardMeta, { color: colors.textSecondary, marginLeft: 8 }]}>
              · v{item.version}
            </Text>
            <AudienceBadge audience={item.audience} colors={colors} />
            {item.headless && (
              <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, borderWidth: 1, borderColor: colors.border, marginLeft: 6 }}>
                <Text style={{ fontSize: 10, color: colors.textSecondary, fontWeight: '600' }}>API · headless</Text>
              </View>
            )}
            {/* Same treatment as "API · headless": say plainly that this app is
                not opened from Citra, so nobody hunts for a URL that has no
                meaning for it. */}
            {item.has_embed_page && (
              <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, borderWidth: 1, borderColor: colors.border, marginLeft: 6 }}>
                <Text style={{ fontSize: 10, color: colors.textSecondary, fontWeight: '600' }}>Embedded · in your app</Text>
              </View>
            )}
            {/* Autonomous-writes badge — front and centre, never hidden. */}
            {item.automation_mode === 'auto_process' && (
              <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEE2E2', marginLeft: 6 }}>
                <Text style={{ fontSize: 10, color: '#B91C1C', fontWeight: '700' }}>🔴 Autonomous</Text>
              </View>
            )}
            {paused && (
              <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEF3C7', marginLeft: 6 }}>
                <Text style={{ fontSize: 10, color: '#92400E', fontWeight: '700' }}>Paused</Text>
              </View>
            )}
          </View>

          <View style={[styles.cardFooter, { borderTopColor: colors.border }]}>
            {(!isConsumer && item.headless) ? (
              <>
                <GradientButton
                  colors={tok.gradient}
                  onPress={() => copyDecisionApiUrl(item)}
                  icon="link-outline"
                  label="Copy API URL"
                  small
                />
                <TouchableOpacity
                  style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                  onPress={() => openTest(item)}
                  hitSlop={6}
                >
                  <Ionicons name="flask-outline" size={14} color={colors.text} />
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Test</Text>
                </TouchableOpacity>
              </>
            ) : (!isConsumer && item.has_embed_page) ? (
              /* An EMBED app has no Citra screen to open. Its surface is a page
                 in the CUSTOMER's application, so the deliverable is the
                 snippet — exactly as a headless app's deliverable is its API
                 URL, above. Opening the app URL renders the panels in the Citra
                 shell with no host record id, which shows an empty queue and
                 reads as broken. Preview is kept, demoted and renamed so it is
                 not mistaken for how officers reach this app. */
              <>
                <GradientButton
                  colors={tok.gradient}
                  onPress={() => exportEmbedSnippet(item)}
                  icon="code-download-outline"
                  label="Copy embed script"
                  small
                />
                <TouchableOpacity
                  style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                  onPress={() => openApp(item)}
                  hitSlop={6}
                >
                  <Ionicons name="eye-outline" size={14} color={colors.text} />
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Preview</Text>
                </TouchableOpacity>
              </>
            ) : (
              <GradientButton
                colors={tok.gradient}
                onPress={() => openApp(item)}
                icon="open-outline"
                label="Open"
                small
              />
            )}
            {/* Pause/Resume — per-app kill switch. Shown only when the app has
                scheduled/automated jobs (or is already paused); an on-demand-only
                app has no background automation to pause. */}
            {!isConsumer && canEdit && (item.has_automation || paused) && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: paused ? '#16A34A' : '#DC2626' }]}
                onPress={() => confirmPause(item, paused)}
                hitSlop={6}
              >
                <Ionicons
                  name={paused ? 'play-outline' : 'pause-outline'}
                  size={14}
                  color={paused ? '#16A34A' : '#DC2626'}
                />
                <Text style={[styles.footerBtnGhostText, { color: paused ? '#16A34A' : '#DC2626' }]}>
                  {paused ? 'Resume' : 'Pause'}
                </Text>
              </TouchableOpacity>
            )}
            {scope === 'test' && canEdit && (
              <GradientButton
                colors={BRAND_GRADIENT}
                onPress={() => setPromoting(item)}
                icon="rocket-outline"
                label="Promote to Prod"
                small
              />
            )}
            {!isConsumer && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => openSpec(item)}
              >
                <Ionicons name="document-text-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>View</Text>
              </TouchableOpacity>
            )}
            {canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => editApp(item.slug)}
              >
                <Ionicons name="create-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Edit</Text>
              </TouchableOpacity>
            )}
            {scope !== 'test' && canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => setPublishing(item)}
                hitSlop={6}
              >
                <Ionicons name="globe-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>
                  Publish to…
                </Text>
              </TouchableOpacity>
            )}
            {canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => setAiTriggersApp(item)}
                hitSlop={6}
              >
                <Ionicons name="sparkles-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>
                  {item.automation_mode === 'auto_process' ? 'Auto-Process' : 'Auto-Recommend'}
                </Text>
              </TouchableOpacity>
            )}
            {canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => setAuditApp(item)}
                hitSlop={6}
              >
                <Ionicons name="receipt-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>
                  Audit
                </Text>
              </TouchableOpacity>
            )}
            {canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => setVersionsApp(item)}
                hitSlop={6}
              >
                <Ionicons name="git-branch-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>
                  Versions
                </Text>
              </TouchableOpacity>
            )}
            {item.grounded && canEdit && (() => {
              const running = groundingJob
                && groundingJob.slug === item.slug
                && groundingJob.status === 'running';
              const gs = groundingStatus[item.slug];
              const pending = !running && gs && gs.never_refreshed;
              return (
                <TouchableOpacity
                  style={[styles.footerBtnGhost, {
                    borderColor: pending ? '#D97706' : colors.border,
                  }]}
                  onPress={() => showGroundingInfo(item)}
                  disabled={running}
                  hitSlop={6}
                >
                  <Ionicons
                    name={running ? 'hourglass-outline' : (pending ? 'alert-circle-outline' : 'refresh-outline')}
                    size={14}
                    color={pending ? '#D97706' : colors.text}
                  />
                  <Text style={[styles.footerBtnGhostText, { color: pending ? '#D97706' : colors.text }]}>
                    {running
                      ? `Refreshing… ${groundingJob.progress || 0}%`
                      : (pending ? 'Grounding: pending' : 'Refresh grounding')}
                  </Text>
                </TouchableOpacity>
              );
            })()}
            {item.grounded && canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => explainAutoLearn(item)}
                hitSlop={6}
              >
                <Ionicons
                  name={selfLearn[item.slug] ? 'sync-circle' : 'sync-circle-outline'}
                  size={14}
                  color={colors.text}
                />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>
                  {selfLearn[item.slug] === true ? 'Auto-learn: on'
                    : selfLearn[item.slug] === false ? 'Auto-learn: off'
                    : 'Auto-learn'}
                </Text>
              </TouchableOpacity>
            )}
            {!isConsumer && item.grounded && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => openMetrics(item)}
                hitSlop={6}
              >
                <Ionicons name="trending-up-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Learning</Text>
              </TouchableOpacity>
            )}
            {item.fraud_enabled && canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                onPress={() => calibrateFraud(item)}
                hitSlop={6}
              >
                <Ionicons name="shield-checkmark-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Calibrate fraud</Text>
              </TouchableOpacity>
            )}
            {scope === 'admin' && adminScope && (
              <TouchableOpacity
                style={[styles.footerBtnIcon, { borderColor: colors.border }]}
                onPress={() => setTransferring(item)}
                hitSlop={6}
              >
                <Ionicons name="swap-horizontal-outline" size={14} color={colors.textSecondary} />
              </TouchableOpacity>
            )}
            {!archived && canEdit && (
              <TouchableOpacity
                style={[styles.footerBtnIcon, { borderColor: colors.border }]}
                onPress={() => archiveApp(item.slug)}
                hitSlop={6}
              >
                <Ionicons name="archive-outline" size={14} color={colors.textSecondary} />
              </TouchableOpacity>
            )}
          </View>
        </View>
      </View>
    );
  };

  // Empty copy for the ACTIVE lens. Scope decides first — the tab asked the
  // question, so it should answer it ("Nothing shared with you"). Only the
  // scope==='mine' fall-through splits by mode: the consumer can't build, so it
  // points at the other tab instead of pitching the Build CTA below.
  const emptyCopy = () => {
    const noun = initialKind === 'dashboard' ? 'Dashboards' : 'Decision Apps';
    if (scope === 'shared') return ['Nothing published to you', 'Apps published to your team, department or the whole organisation — or on a Service Account you were added to — will appear here.'];
    if (scope === 'admin')  return ['No apps in your admin scope', 'Apps owned by users in your admin scope will appear here.'];
    if (scope === 'test')   return ['Nothing awaiting promotion', 'Apps published to test but not yet promoted to production will appear here.'];
    if (isConsumer) {
      return [
        initialKind === 'dashboard' ? 'No dashboards yet' : 'No Decision Apps yet',
        `${noun} you own will appear here — check “Published to me” for the ones published to you.`,
      ];
    }
    const title =
      kindFilter === 'all' ? 'No apps yet' :
      kindFilter === 'api' ? 'No APIs yet' :
      `No ${kindFilter}s yet`;
    return [title, 'Describe a goal in plain language and the agent assembles a Decision App, a Decision Agent API, or a dashboard page for you.'];
  };

  const renderEmpty = () => {
    const [emptyTitle, emptyBody] = emptyCopy();
    return (
    <View style={styles.emptyWrap}>
      <OpsMark size={96} radius={28} gradient={BRAND_GRADIENT} style={styles.emptyIcon} />
      <Text style={[styles.emptyTitle, { color: colors.text }]}>{emptyTitle}</Text>
      <Text style={[styles.emptyBody, { color: colors.textSecondary }]}>{emptyBody}</Text>
      {!isConsumer && scope === 'mine' && (
        <View style={{ marginTop: 16 }}>
          <GradientButton
            colors={BRAND_GRADIENT}
            onPress={openNewBuild}
            icon="add"
            label="Build new"
          />
        </View>
      )}
    </View>
    );
  };

  const renderSkeleton = () => (
    <View style={styles.skeletonGrid}>
      {Array.from({ length: numColumns * 2 }).map((_, i) => (
        <View
          key={i}
          style={[
            styles.skeletonCard,
            {
              borderColor: colors.border,
              backgroundColor: colors.surface,
              flexBasis: numColumns === 1 ? '100%' : `${100 / numColumns}%`,
            },
          ]}
        >
          <View style={[styles.skeletonLine, { backgroundColor: colors.surfaceAlt, width: '40%' }]} />
          <View style={[styles.skeletonLine, { backgroundColor: colors.surfaceAlt, width: '80%', height: 14, marginTop: 12 }]} />
          <View style={[styles.skeletonLine, { backgroundColor: colors.surfaceAlt, width: '95%' }]} />
          <View style={[styles.skeletonLine, { backgroundColor: colors.surfaceAlt, width: '60%' }]} />
        </View>
      ))}
    </View>
  );

  // ── render ─────────────────────────────────────────────────────────
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      {showRail && renderRail()}

      <View style={styles.main}>
        {renderHeader()}

        {activeBuildSession && (
          <SmartAppBuilderScreen
            visible
            session={activeBuildSession}
            buildKinds={activeBuildSession.buildKinds || ['app']}
            theme={theme}
            onClose={dismissBuildSession}
            // Non-destructive: refresh the catalog behind the modal when a
            // build goes LIVE. The session stays open (the BA may keep
            // tweaking, promoting, or adding a workflow); closing is the X.
            onPublished={() => load()}
            // Chat-first: the BA's first message spawns the session. No goal.
            onStartBuild={() => startBuild()}
            startBusy={buildBusy}
            startError={buildError}
          />
        )}

        {!!error && (
          <View style={[styles.errorBox, { backgroundColor: '#FEF2F2', borderColor: '#FECACA' }]}>
            <Ionicons name="alert-circle" size={16} color="#B91C1C" />
            <Text style={{ color: '#B91C1C', fontSize: 13, flex: 1, marginLeft: 8 }}>{error}</Text>
            <TouchableOpacity
              onPress={load}
              style={[styles.footerBtnGhost, { borderColor: '#FECACA' }]}
            >
              <Text style={{ color: '#B91C1C', fontSize: 12, fontWeight: '600' }}>Retry</Text>
            </TouchableOpacity>
          </View>
        )}

        {loading ? (
          renderSkeleton()
        ) : filteredSorted.length === 0 ? (
          renderEmpty()
        ) : (
          <FlatList
            key={`grid-${numColumns}`}
            data={filteredSorted}
            keyExtractor={(item) => item.app_id || item.slug}
            renderItem={renderCard}
            numColumns={numColumns}
            columnWrapperStyle={numColumns > 1 ? { gap: 16 } : undefined}
            contentContainerStyle={styles.gridContent}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          />
        )}
      </View>

      {/* Surface picker — shown by "Build new" before the conversational
          builder so the App / API / Dashboard offering is explicit. */}
      <BuildKindPickerModal
        visible={showBuildPicker}
        theme={colors}
        onPick={onPickBuildKind}
        onClose={() => setShowBuildPicker(false)}
      />

      <AudiencePickerModal
        visible={!!publishing}
        app={publishing}
        theme={colors}
        onClose={() => setPublishing(null)}
        onApplied={async () => { setPublishing(null); await load(); }}
      />

      <AudiencePickerModal
        visible={!!promoting}
        app={promoting}
        theme={colors}
        promote
        onClose={() => setPromoting(null)}
        onApplied={async (result) => {
          const it = promoting;
          setPromoting(null);
          await load();
          // Promote kicked off a grounding refresh — surface its live progress
          // in the shared grounding-job modal so the BA sees prod get grounded.
          if (result?.grounding_refresh_run_id && it) {
            attachGroundingJob(it.slug, it.title || it.slug, result.grounding_refresh_run_id);
          }
        }}
      />

      {/* Full-screen automation console (kill switches + schedules). */}
      <OperationsControlScreen
        visible={showHaltModal}
        theme={colors}
        onClose={() => { setShowHaltModal(false); loadHalt(); }}
      />

      {/* Spec viewer / editor — View → Copy → Edit → Save (review the built app/API) */}
      <Modal
        visible={!!specApp}
        transparent
        animationType="fade"
        onRequestClose={() => setSpecApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 720, maxHeight: '90%' }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Spec · {specApp?.title || specApp?.slug}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  {specEditing ? 'Editing — saves are validated before they apply.' : 'Review the app + agent/API spec. Copy, or edit and save.'}
                </Text>
              </View>
              <TouchableOpacity onPress={() => setSpecApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingTop: 12 }}>
              <TouchableOpacity
                onPress={() => setSpecTab('app')}
                style={[styles.footerBtnGhost, { borderColor: specTab === 'app' ? '#2563eb' : colors.border }]}
              >
                <Text style={[styles.footerBtnGhostText, { color: specTab === 'app' ? '#2563eb' : colors.text }]}>App spec</Text>
              </TouchableOpacity>
              {!!specAgentText && (
                <TouchableOpacity
                  onPress={() => setSpecTab('agent')}
                  style={[styles.footerBtnGhost, { borderColor: specTab === 'agent' ? '#2563eb' : colors.border }]}
                >
                  <Text style={[styles.footerBtnGhostText, { color: specTab === 'agent' ? '#2563eb' : colors.text }]}>
                    {specApp?.headless ? 'API / agent spec' : 'Agent spec'}
                  </Text>
                </TouchableOpacity>
              )}
            </View>
            {/* Advisory review band — sits ABOVE the spec; never alters the JSON. */}
            <View style={{ marginHorizontal: 16, marginTop: 12, borderWidth: 1, borderColor: colors.border, borderRadius: 8, overflow: 'hidden' }}>
              <TouchableOpacity onPress={() => setLintOpen((v) => !v)} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 10 }}>
                <Ionicons name={lintOpen ? 'chevron-down' : 'chevron-forward'} size={16} color={colors.textSecondary} />
                <Ionicons name="shield-checkmark-outline" size={15} color={colors.text} />
                <Text style={{ color: colors.text, fontWeight: '600' }}>Review</Text>
                {lintLoading && <Text style={{ color: colors.textSecondary, fontSize: 12 }}>· checking…</Text>}
                {!!lint && !lintLoading && (() => {
                  const c = lint.counts || {}; const e = c.error || 0, w = c.warning || 0, i = c.info || 0;
                  if (!e && !w && !i) return <Text style={{ color: '#16a34a', fontSize: 12 }}>· no issues</Text>;
                  return (
                    <View style={{ flexDirection: 'row', gap: 8 }}>
                      {!!e && <Text style={{ color: '#dc2626', fontSize: 12 }}>{e} error{e > 1 ? 's' : ''}</Text>}
                      {!!w && <Text style={{ color: '#d97706', fontSize: 12 }}>{w} warning{w > 1 ? 's' : ''}</Text>}
                      {!!i && <Text style={{ color: '#2563eb', fontSize: 12 }}>{i} note{i > 1 ? 's' : ''}</Text>}
                    </View>
                  );
                })()}
                <View style={{ flex: 1 }} />
                {!!specApp && (
                  <TouchableOpacity onPress={() => runLint(specApp.slug, true)} disabled={lintLoading} style={[styles.footerBtnGhost, { borderColor: colors.border, opacity: lintLoading ? 0.6 : 1 }]}>
                    <Ionicons name="pulse-outline" size={13} color={colors.text} />
                    <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Run live checks</Text>
                  </TouchableOpacity>
                )}
              </TouchableOpacity>
              {lintOpen && (
                <ScrollView style={{ maxHeight: 150 }} contentContainerStyle={{ padding: 10, paddingTop: 0, gap: 8 }}>
                  {!!(lint && lint.live_error) && <Text style={{ color: '#d97706', fontSize: 12 }}>Live checks unavailable: {lint.live_error}</Text>}
                  {!!(lint && lint.lint_error) && <Text style={{ color: '#dc2626', fontSize: 12 }}>{lint.lint_error}</Text>}
                  {!!lint && (lint.findings || []).length === 0 && !lintLoading && !lint.lint_error && (
                    <Text style={{ color: colors.textSecondary, fontSize: 12 }}>No correctness or performance issues found.</Text>
                  )}
                  {(lint?.findings || []).map((f, idx) => {
                    const sc = f.severity === 'error' ? '#dc2626' : f.severity === 'warning' ? '#d97706' : '#2563eb';
                    return (
                      <View key={idx} style={{ flexDirection: 'row', gap: 8 }}>
                        <View style={{ width: 6, height: 6, borderRadius: 3, marginTop: 5, backgroundColor: sc }} />
                        <View style={{ flex: 1 }}>
                          <Text style={{ color: colors.text, fontSize: 12 }}>{f.message}</Text>
                          {!!f.hint && <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 1 }}>{f.hint}</Text>}
                          <Text style={{ color: colors.textSecondary, fontSize: 11, fontFamily: 'monospace', marginTop: 1 }}>{f.path}</Text>
                        </View>
                      </View>
                    );
                  })}
                </ScrollView>
              )}
            </View>
            {!!caseSig && (
              <View style={{ borderTopWidth: 1, borderTopColor: colors.border }}>
                <TouchableOpacity
                  onPress={() => setSigOpen((v) => !v)}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 10 }}
                >
                  <Ionicons name={sigOpen ? 'chevron-down' : 'chevron-forward'} size={14} color={colors.textSecondary} />
                  <Text style={{ color: colors.text, fontSize: 13, fontWeight: '600' }}>
                    What this app learns by ({caseSig.families.length})
                  </Text>
                  <View style={{ flex: 1 }} />
                  {caseSig.confirmedBy && !caseSig.stale ? (
                    <Text style={{ color: '#059669', fontSize: 11 }} numberOfLines={1}>
                      confirmed by {caseSig.confirmedBy}
                    </Text>
                  ) : (
                    <Text style={{ color: '#d97706', fontSize: 11 }}>
                      {caseSig.stale ? 'changed since confirmed' : 'not confirmed'}
                    </Text>
                  )}
                </TouchableOpacity>
                {sigOpen && (
                  <View style={{ paddingHorizontal: 10, paddingBottom: 10, gap: 8 }}>
                    <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
                      A lesson your team teaches on one case is re-used on other cases with the
                      same signature — and only those. Check this is how you group them.
                    </Text>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                      {caseSig.facets.map((f, i) => (
                        <View
                          key={i}
                          style={{
                            borderWidth: 1, borderColor: colors.border, borderRadius: 6,
                            paddingHorizontal: 8, paddingVertical: 4,
                          }}
                        >
                          <Text style={{ color: colors.text, fontSize: 12 }}>
                            {String(f.family || '').replace(/_/g, ' ')}
                          </Text>
                          <Text style={{ color: colors.textSecondary, fontSize: 10 }}>
                            {f.kind}{f.from_column ? ` · ${f.from_column}` : ''}
                          </Text>
                        </View>
                      ))}
                    </View>
                    {caseSig.stale && (
                      <Text style={{ color: '#d97706', fontSize: 11 }}>
                        {caseSig.confirmedBy} confirmed a different list. Publishing will be
                        rejected (CS-04) until someone confirms the current one.
                      </Text>
                    )}
                    {!!specApp?.can_edit && (
                      <TouchableOpacity
                        onPress={confirmSig}
                        disabled={sigBusy}
                        style={[styles.footerBtnGhost, {
                          borderColor: '#2563eb', alignSelf: 'flex-start', opacity: sigBusy ? 0.6 : 1,
                        }]}
                      >
                        <Ionicons name="checkmark-circle-outline" size={14} color="#2563eb" />
                        <Text style={[styles.footerBtnGhostText, { color: '#2563eb' }]}>
                          {sigBusy ? 'Recording…'
                            : caseSig.confirmedBy && !caseSig.stale ? 'Re-confirm' : 'Confirm these'}
                        </Text>
                      </TouchableOpacity>
                    )}
                  </View>
                )}
              </View>
            )}
            <ScrollView style={{ maxHeight: 460 }} contentContainerStyle={{ padding: 16, gap: 10 }}>
              {!!specError && <Text style={{ color: '#dc2626' }}>{specError}</Text>}
              {specLoading ? (
                <Text style={{ color: colors.textSecondary }}>Loading…</Text>
              ) : specEditing ? (
                <TextInput
                  value={specTab === 'app' ? specAppText : specAgentText}
                  onChangeText={(t) => (specTab === 'app' ? setSpecAppText(t) : setSpecAgentText(t))}
                  multiline
                  autoCapitalize="none"
                  autoCorrect={false}
                  style={{ color: colors.text, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 10, fontFamily: 'monospace', fontSize: 12, minHeight: 340, textAlignVertical: 'top' }}
                />
              ) : (
                <Text selectable style={{ color: colors.text, fontFamily: 'monospace', fontSize: 12 }}>
                  {(specTab === 'app' ? specAppText : specAgentText) || '—'}
                </Text>
              )}
            </ScrollView>
            <View style={[styles.triggersHeader, { borderBottomWidth: 0, borderTopWidth: 1, borderTopColor: colors.border, gap: 8 }]}>
              <TouchableOpacity
                onPress={async () => { try { await Clipboard.setStringAsync(specTab === 'app' ? specAppText : specAgentText); Alert.alert('Copied', 'Spec JSON copied.'); } catch {} }}
                style={[styles.footerBtnGhost, { borderColor: colors.border }]}
              >
                <Ionicons name="copy-outline" size={14} color={colors.text} />
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Copy</Text>
              </TouchableOpacity>
              <View style={{ flex: 1 }} />
              {!!specApp?.can_edit && !specEditing && (
                <TouchableOpacity onPress={() => setSpecEditing(true)} style={[styles.footerBtnGhost, { borderColor: colors.border }]}>
                  <Ionicons name="create-outline" size={14} color={colors.text} />
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Edit</Text>
                </TouchableOpacity>
              )}
              {specEditing && (
                <TouchableOpacity onPress={saveSpec} disabled={specSaving} style={[styles.footerBtnGhost, { borderColor: '#2563eb', opacity: specSaving ? 0.6 : 1 }]}>
                  <Ionicons name="save-outline" size={14} color="#2563eb" />
                  <Text style={[styles.footerBtnGhostText, { color: '#2563eb' }]}>{specSaving ? 'Saving…' : 'Save'}</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>
        </View>
      </Modal>

      <Modal
        visible={!!testApp}
        transparent
        animationType="fade"
        onRequestClose={() => setTestApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 640, maxHeight: '88%' }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Test decision API · {testApp?.title || testApp?.slug}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  Run a case → see the recommendation → approve / override / reject.
                </Text>
              </View>
              <TouchableOpacity onPress={() => setTestApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 540 }} contentContainerStyle={{ padding: 16, gap: 10 }}>
              {!!testError && <Text style={{ color: '#dc2626' }}>{testError}</Text>}
              {/* API URL + full contract — what the developer integrates against */}
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Text style={{ color: colors.textSecondary, fontSize: 11, flex: 1 }} numberOfLines={1}>
                  POST {testApp ? SmartAppService.decisionApiUrl(testApp.slug) : ''}
                </Text>
                <TouchableOpacity
                  onPress={async () => { try { await Clipboard.setStringAsync(SmartAppService.decisionApiUrl(testApp.slug)); Alert.alert('Copied', 'API URL copied.'); } catch {} }}
                  hitSlop={6}
                  style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                >
                  <Ionicons name="link-outline" size={13} color={colors.text} />
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>URL</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={async () => { try { await Clipboard.setStringAsync(JSON.stringify(testContract || {}, null, 2)); Alert.alert('Copied', 'Full contract JSON copied.'); } catch {} }}
                  disabled={!testContract}
                  hitSlop={6}
                  style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                >
                  <Ionicons name="document-text-outline" size={13} color={colors.text} />
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Contract</Text>
                </TouchableOpacity>
              </View>
              {testContract?.write_actions?.length > 0 && (
                <View style={{ gap: 2 }}>
                  <Text style={{ color: colors.textSecondary, fontSize: 11 }}>Writes (table · action · editable)</Text>
                  {testContract.write_actions.map((w) => (
                    <Text key={w.action_id || w.name} style={{ color: colors.text, fontSize: 12 }}>
                      {w.dataset_id} · {w.action_id} · editable: {(w.editable_fields || []).join(', ') || '—'}
                    </Text>
                  ))}
                </View>
              )}
              <Text style={{ color: colors.textSecondary, fontSize: 11 }}>action</Text>
              <TextInput
                value={testAction}
                onChangeText={setTestAction}
                placeholder="action"
                placeholderTextColor={colors.textSecondary}
                style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 6, padding: 8, color: colors.text }}
              />
              <Text style={{ color: colors.textSecondary, fontSize: 11 }}>inputs (JSON)</Text>
              <TextInput
                value={testInputs}
                onChangeText={setTestInputs}
                multiline
                autoCapitalize="none"
                autoCorrect={false}
                style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 6, padding: 8, color: colors.text, minHeight: 96, fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace' }}
              />
              <TouchableOpacity
                onPress={runTest}
                disabled={testRunning}
                style={[styles.footerBtnGhost, { borderColor: colors.border, alignSelf: 'flex-start' }]}
              >
                {testRunning
                  ? <ActivityIndicator size="small" color={colors.text} />
                  : <Ionicons name="play" size={14} color={colors.text} />}
                <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>{testRunning ? 'Running…' : 'Run'}</Text>
              </TouchableOpacity>

              {testResult && (
                <View style={{ gap: 4, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 8 }}>
                  <Text style={{ color: colors.text, fontWeight: '600' }}>Recommendation ({testResult.status})</Text>
                  {/* /run explains every refusal in `error` — e.g. the read-before-write
                      guard's "anchor record complaint_id='…' not found". Dropping it left
                      the operator staring at "(failed)" with no reason while the answer sat
                      in the payload (and only reached us via GlitchTip). */}
                  {!!testResult.error && (
                    <Text style={{ color: colors.danger, fontSize: 12 }}>{testResult.error}</Text>
                  )}
                  <Text style={{ color: colors.text, fontSize: 12 }}>decision: {String(testResult.decision)}</Text>
                  {!!testResult.reasoning && (
                    <Text style={{ color: colors.textSecondary, fontSize: 12 }}>{testResult.reasoning}</Text>
                  )}
                  <Text style={{ color: colors.textSecondary, fontSize: 11 }}>planned_writes</Text>
                  <Text style={{ color: colors.text, fontSize: 11, fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace' }}>
                    {JSON.stringify(testResult.planned_writes || [], null, 2)}
                  </Text>
                  {/* A failed run staged NOTHING — there is no plan to approve,
                      override or reject, and /approve would only 4xx. Offering the
                      decision verbs there invites committing a plan that does not
                      exist. Show why it stopped instead. */}
                  {testResult.status === 'failed' ? (
                    <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 6 }}>
                      Nothing was staged, so there is nothing to approve. Fix the inputs above and run again.
                    </Text>
                  ) : (
                    <>
                      <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 6 }}>overrides (JSON array, optional)</Text>
                      <TextInput
                        value={testOverrides}
                        onChangeText={setTestOverrides}
                        multiline
                        autoCapitalize="none"
                        autoCorrect={false}
                        style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 6, padding: 8, color: colors.text, minHeight: 44, fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace' }}
                      />
                      <Text style={{ color: colors.textSecondary, fontSize: 10 }}>
                        Run is read-only (plan). Approve COMMITS the write to the system of record.
                      </Text>
                      <View style={{ flexDirection: 'row', gap: 8 }}>
                        {['approve', 'reject', 'cancel'].map((d) => (
                          <TouchableOpacity
                            key={d}
                            onPress={() => approveTest(d)}
                            disabled={testBusy}
                            style={[styles.footerBtnGhost, { borderColor: colors.border }]}
                          >
                            <Text style={[styles.footerBtnGhostText, { color: colors.text, textTransform: 'capitalize' }]}>{d}</Text>
                          </TouchableOpacity>
                        ))}
                      </View>
                    </>
                  )}
                </View>
              )}

              {testCommitted && (
                <View style={{ gap: 2, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 8 }}>
                  <Text style={{ color: colors.text, fontWeight: '600' }}>Result ({testCommitted.status})</Text>
                  <Text style={{ color: colors.text, fontSize: 11, fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace' }}>
                    {JSON.stringify(testCommitted.write_events || testCommitted.outputs || {}, null, 2)}
                  </Text>
                </View>
              )}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* Grounding freshness / refresh modal (RN Alert is a no-op on web) */}
      <Modal
        visible={!!groundingInfoApp}
        transparent
        animationType="fade"
        onRequestClose={() => setGroundingInfoApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 480 }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Refresh grounding · {groundingInfoApp?.title || groundingInfoApp?.slug}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  Few-shot memory — the historical examples the agent matches against.
                </Text>
              </View>
              <TouchableOpacity onPress={() => setGroundingInfoApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <View style={{ padding: 16, gap: 12 }}>
              {(() => {
                const item = groundingInfoApp;
                if (!item) return null;
                const gs = groundingStatus[item.slug];
                const running = groundingJob && groundingJob.slug === item.slug && groundingJob.status === 'running';
                if (running) {
                  return <Text style={{ color: colors.text, lineHeight: 20 }}>A refresh is in progress ({groundingJob.progress || 0}%). It runs in the background — you can close this.</Text>;
                }
                if (!gs || gs.never_refreshed) {
                  return <Text style={{ color: colors.text, lineHeight: 20 }}>This app’s few-shot memory has <Text style={{ fontWeight: '700' }}>never been refreshed</Text> — the agent has no historical examples yet. Refreshing pulls resolved historical records from your source system into the agent’s example memory.</Text>;
                }
                const when = gs.last_refreshed_at ? new Date(gs.last_refreshed_at).toLocaleString() : 'unknown';
                return (
                  <View style={{ gap: 6 }}>
                    <Text style={{ color: colors.text }}>Last refreshed: <Text style={{ fontWeight: '700' }}>{when}</Text></Text>
                    <Text style={{ color: colors.textSecondary }}>{gs.sample_count ?? '—'} samples (canonical {gs.canonical_samples ?? '—'} · neighbours {gs.neighbor_samples ?? '—'}).</Text>
                    <Text style={{ color: colors.textSecondary, fontSize: 12 }}>Refreshing re-pulls resolved historical records into the agent’s example memory.</Text>
                  </View>
                );
              })()}
              <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
                <TouchableOpacity onPress={() => setGroundingInfoApp(null)} style={[styles.footerBtnGhost, { borderColor: colors.border }]}>
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Close</Text>
                </TouchableOpacity>
                {!(groundingInfoApp && groundingJob && groundingJob.slug === groundingInfoApp.slug && groundingJob.status === 'running') && (
                  <GradientButton
                    colors={BRAND_GRADIENT}
                    onPress={() => { const it = groundingInfoApp; setGroundingInfoApp(null); if (it) refreshGrounding(it); }}
                    icon="refresh-outline"
                    label="Refresh now"
                    small
                  />
                )}
              </View>
            </View>
          </View>
        </View>
      </Modal>

      {/* L3 fraud calibration report modal */}
      <Modal
        visible={!!fraudReport}
        transparent
        animationType="fade"
        onRequestClose={() => setFraudReport(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 560, maxHeight: '85%' }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Calibrate fraud · {fraudReport?.title}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  How often each fraud signal actually led your officers to reject.
                </Text>
              </View>
              <TouchableOpacity onPress={() => setFraudReport(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 480 }} contentContainerStyle={{ padding: 16, gap: 12 }}>
              <Text style={{ color: colors.textSecondary, fontSize: 12.5, lineHeight: 18 }}>
                This looks back at how often each fraud signal (duplicate photo, recycled report, name mismatch…) actually led your officers to reject. Signals that rarely matter can be dialled down so the app flags less noise. <Text style={{ fontWeight: '700' }}>This only reads past decisions — it changes nothing automatically.</Text>
              </Text>
              {fraudReport?.loading ? (
                <ActivityIndicator color={colors.primary} style={{ marginVertical: 16 }} />
              ) : fraudReport?.error ? (
                <Text style={{ color: colors.danger || '#dc2626' }}>{fraudReport.error}</Text>
              ) : fraudReport?.report ? (() => {
                const r = fraudReport.report;
                const rows = Object.entries(r.per_signal_hit_rate || {});
                return (
                  <View style={{ gap: 8 }}>
                    <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                      {r.screenings_considered ?? 0} screenings · {r.matched_to_decisions ?? 0} matched to decisions
                    </Text>
                    {rows.length === 0 ? (
                      <Text style={{ color: colors.textSecondary }}>No screenings with matched decisions yet — run some cases (and disposition them) first, then calibrate.</Text>
                    ) : (
                      <View style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 8, overflow: 'hidden' }}>
                        <View style={{ flexDirection: 'row', backgroundColor: colors.backgroundSecondary || '#f3f4f6', paddingVertical: 6, paddingHorizontal: 8 }}>
                          <Text style={{ flex: 2, fontSize: 11, fontWeight: '700', color: colors.textSecondary }}>Signal</Text>
                          <Text style={{ flex: 1, fontSize: 11, fontWeight: '700', color: colors.textSecondary, textAlign: 'right' }}>Cases</Text>
                          <Text style={{ flex: 1, fontSize: 11, fontWeight: '700', color: colors.textSecondary, textAlign: 'right' }}>Reject rate</Text>
                        </View>
                        {rows.map(([sig, v]) => {
                          const rate = v.rejection_rate;
                          const rateColor = rate == null ? colors.textSecondary : rate >= 0.5 ? '#16a34a' : rate >= 0.2 ? '#d97706' : '#dc2626';
                          return (
                            <View key={sig} style={{ flexDirection: 'row', paddingVertical: 6, paddingHorizontal: 8, borderTopWidth: 1, borderTopColor: colors.border }}>
                              <Text style={{ flex: 2, fontSize: 12, color: colors.text }}>{String(sig).replace(/_/g, ' ')}</Text>
                              <Text style={{ flex: 1, fontSize: 12, color: colors.textSecondary, textAlign: 'right' }}>{v.cases}</Text>
                              <Text style={{ flex: 1, fontSize: 12, fontWeight: '600', color: rateColor, textAlign: 'right' }}>{rate == null ? '—' : `${Math.round(rate * 100)}%`}</Text>
                            </View>
                          );
                        })}
                      </View>
                    )}
                    <Text style={{ color: colors.textSecondary, fontSize: 11, lineHeight: 16 }}>
                      A <Text style={{ color: '#dc2626' }}>low</Text> reject-rate over many cases = a noisy signal (candidate to dial down); a <Text style={{ color: '#16a34a' }}>high</Text> rate = a signal worth keeping. Applying any change stays your call.
                    </Text>
                  </View>
                );
              })() : null}
              <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: 4 }}>
                <TouchableOpacity onPress={() => setFraudReport(null)} style={[styles.footerBtnGhost, { borderColor: colors.border }]}>
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Close</Text>
                </TouchableOpacity>
              </View>
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* Auto-learn explainer + toggle modal */}
      <Modal
        visible={!!autoLearnApp}
        transparent
        animationType="fade"
        onRequestClose={() => setAutoLearnApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 480 }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Auto-learn · {autoLearnApp?.title || autoLearnApp?.slug}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  Currently {autoLearnApp && selfLearn[autoLearnApp.slug] === true ? 'ON' : autoLearnApp && selfLearn[autoLearnApp.slug] === false ? 'OFF' : 'unknown'}.
                </Text>
              </View>
              <TouchableOpacity onPress={() => setAutoLearnApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <View style={{ padding: 16, gap: 12 }}>
              <Text style={{ color: colors.text, lineHeight: 20 }}>
                When <Text style={{ fontWeight: '700' }}>ON</Text>, once a decision’s real outcome is known (the record’s status back in the source system), the app learns from it automatically — good outcomes become examples to repeat, bad ones patterns to avoid.
              </Text>
              <Text style={{ color: colors.text, lineHeight: 20 }}>
                When <Text style={{ fontWeight: '700' }}>OFF</Text>, outcomes are still tracked, but the app only updates its examples when you click “Refresh grounding”.
              </Text>
              <Text style={{ color: colors.textSecondary, fontSize: 12, lineHeight: 18 }}>
                Either way, the app always learns from your own approve/reject decisions — this setting only controls learning from source-system outcomes.
              </Text>
              <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
                <TouchableOpacity onPress={() => setAutoLearnApp(null)} style={[styles.footerBtnGhost, { borderColor: colors.border }]}>
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Close</Text>
                </TouchableOpacity>
                <GradientButton
                  colors={BRAND_GRADIENT}
                  onPress={() => { const it = autoLearnApp; setAutoLearnApp(null); if (it) toggleSelfLearning(it); }}
                  label={autoLearnApp && selfLearn[autoLearnApp.slug] === true ? 'Turn OFF' : 'Turn ON'}
                  small
                />
              </View>
            </View>
          </View>
        </View>
      </Modal>

      {/* Pause confirm modal */}
      <Modal
        visible={!!pauseConfirmApp}
        transparent
        animationType="fade"
        onRequestClose={() => setPauseConfirmApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 460 }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>Pause this app?</Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]} numberOfLines={1}>{pauseConfirmApp?.title || pauseConfirmApp?.slug}</Text>
              </View>
              <TouchableOpacity onPress={() => setPauseConfirmApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <View style={{ padding: 16, gap: 12 }}>
              <Text style={{ color: colors.text, lineHeight: 20 }}>
                Pausing halts all runs, writes, approvals and scheduled jobs for this app. Reads and the audit trail stay available. You can resume anytime.
              </Text>
              <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'flex-end' }}>
                <TouchableOpacity onPress={() => setPauseConfirmApp(null)} style={[styles.footerBtnGhost, { borderColor: colors.border }]}>
                  <Text style={[styles.footerBtnGhostText, { color: colors.text }]}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={() => { const it = pauseConfirmApp; setPauseConfirmApp(null); if (it) togglePause(it, false); }}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8, backgroundColor: '#DC2626' }}
                >
                  <Ionicons name="pause" size={14} color="#fff" />
                  <Text style={{ color: '#fff', fontWeight: '700', fontSize: 12 }}>Pause</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </View>
      </Modal>

      <Modal
        visible={!!metricsApp}
        transparent
        animationType="fade"
        onRequestClose={() => setMetricsApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 520 }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Self-learning · {metricsApp?.title || metricsApp?.slug}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  Is it learning? Override-rate should fall; good-outcome rate should rise.
                </Text>
              </View>
              <TouchableOpacity onPress={() => setMetricsApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <View style={{ padding: 16, gap: 12 }}>
              {metricsLoading && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <ActivityIndicator size="small" color={colors.text} />
                  <Text style={{ color: colors.text }}>Loading…</Text>
                </View>
              )}
              {!metricsLoading && metrics?.error && (
                <Text style={{ color: '#dc2626' }}>{metrics.error}</Text>
              )}
              {!metricsLoading && metrics && !metrics.error && (
                <>
                  <View style={{ flexDirection: 'row', gap: 18, flexWrap: 'wrap' }}>
                    <View>
                      <Text style={{ color: colors.textSecondary, fontSize: 11 }}>Override rate ↓</Text>
                      <Text style={{ color: colors.text, fontSize: 20, fontWeight: '700' }}>{fmtPct(metrics.override_rate)}</Text>
                    </View>
                    <View>
                      <Text style={{ color: colors.textSecondary, fontSize: 11 }}>Good outcome ↑</Text>
                      <Text style={{ color: colors.text, fontSize: 20, fontWeight: '700' }}>{fmtPct(metrics.good_rate)}</Text>
                    </View>
                    <View>
                      <Text style={{ color: colors.textSecondary, fontSize: 11 }}>Automation</Text>
                      <Text style={{ color: colors.text, fontSize: 20, fontWeight: '700' }}>{fmtPct(metrics.automation_rate)}</Text>
                    </View>
                    <View>
                      <Text style={{ color: colors.textSecondary, fontSize: 11 }}>Decisions</Text>
                      <Text style={{ color: colors.text, fontSize: 20, fontWeight: '700' }}>{metrics.total ?? 0}</Text>
                    </View>
                  </View>
                  {Array.isArray(metrics.trend_weekly) && metrics.trend_weekly.length > 0 && (
                    <View style={{ gap: 4 }}>
                      <Text style={{ color: colors.textSecondary, fontSize: 11 }}>Weekly trend</Text>
                      {metrics.trend_weekly.slice(-8).map((t) => (
                        <View key={t.week} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                          <Text style={{ color: colors.text, fontSize: 12 }}>{t.week}</Text>
                          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                            ovr {fmtPct(t.override_rate)} · good {fmtPct(t.good_rate)} · n={t.n}
                          </Text>
                        </View>
                      ))}
                    </View>
                  )}
                  {metrics.by_model && Object.keys(metrics.by_model).length > 0 && (
                    <View style={{ gap: 4 }}>
                      <Text style={{ color: colors.textSecondary, fontSize: 11 }}>Good-rate by model (swap view)</Text>
                      {Object.entries(metrics.by_model).map(([k, v]) => (
                        <View key={k} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                          <Text style={{ color: colors.text, fontSize: 12 }} numberOfLines={1}>{k}</Text>
                          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>good {fmtPct(v.good_rate)} · n={v.n}</Text>
                        </View>
                      ))}
                    </View>
                  )}
                  {(metrics.total ?? 0) === 0 && (
                    <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                      No decisions recorded yet — metrics appear once the app makes and settles decisions.
                    </Text>
                  )}
                  {metrics.memory && (
                    <View style={{ gap: 4, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 }}>
                      <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
                        Memory — what the team has taught this app
                      </Text>
                      {(metrics.memory.rubrics || []).map((r) => (
                        <View key={`${r.modality}-${r.task_type}`} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                          <Text style={{ color: colors.text, fontSize: 12 }} numberOfLines={1}>
                            {r.modality} · {r.task_type} rubric {r.version}
                          </Text>
                          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                            {r.corrections} correction{r.corrections === 1 ? '' : 's'} folded
                          </Text>
                        </View>
                      ))}
                      {(metrics.memory.items?.total ?? 0) > 0 && (
                        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                          <Text style={{ color: colors.text, fontSize: 12 }}>Item ledger (images/docs)</Text>
                          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                            {metrics.memory.items.total} item{metrics.memory.items.total === 1 ? '' : 's'}
                            {metrics.memory.items.by_disposition?.accept ? ` · ${metrics.memory.items.by_disposition.accept} accepted` : ''}
                            {metrics.memory.items.by_disposition?.reject ? ` · ${metrics.memory.items.by_disposition.reject} rejected` : ''}
                          </Text>
                        </View>
                      )}
                      {(metrics.memory.items?.precedent_grounded ?? 0) > 0 && (
                        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                          <Text style={{ color: colors.text, fontSize: 12 }}>Analyses grounded by precedents</Text>
                          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>{metrics.memory.items.precedent_grounded}</Text>
                        </View>
                      )}
                      {metrics.memory.grounding?.total_samples != null && (
                        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                          <Text style={{ color: colors.text, fontSize: 12 }}>Few-shot samples (last refresh)</Text>
                          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>{metrics.memory.grounding.total_samples}</Text>
                        </View>
                      )}
                      {(metrics.memory.rubrics || []).length === 0 && (metrics.memory.items?.total ?? 0) === 0 && (
                        <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                          No memory yet — rubrics and the item ledger fill as officers review and give reasons.
                        </Text>
                      )}
                      {onOpenMemory && (
                        <TouchableOpacity
                          onPress={() => { const s = metricsApp?.slug; setMetricsApp(null); onOpenMemory(s); }}
                          style={{ flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start', paddingTop: 4 }}
                        >
                          <Text style={{ color: colors.primary, fontSize: 13, fontWeight: '600' }}>View full memory</Text>
                          <Ionicons name="arrow-forward" size={14} color={colors.primary} />
                        </TouchableOpacity>
                      )}
                    </View>
                  )}
                </>
              )}
            </View>
          </View>
        </View>
      </Modal>

      <Modal
        visible={!!aiTriggersApp}
        transparent
        animationType="fade"
        onRequestClose={() => setAiTriggersApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Auto-Recommend · {aiTriggersApp?.title || aiTriggersApp?.slug}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  Run this app's AI agent on a schedule or webhook — recommendations ready before the click
                </Text>
              </View>
              <TouchableOpacity onPress={() => setAiTriggersApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            {aiTriggersApp ? (
              <AITriggersPanel slug={aiTriggersApp.slug} />
            ) : null}
          </View>
        </View>
      </Modal>

      <Modal
        visible={!!versionsApp}
        transparent
        animationType="fade"
        onRequestClose={() => setVersionsApp(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Version history · {versionsApp?.title || versionsApp?.slug}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  Roll back this app to an earlier published version — spec and agent together
                </Text>
              </View>
              <TouchableOpacity onPress={() => setVersionsApp(null)} hitSlop={8}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            {versionsApp ? (
              <AppVersionsPanel slug={versionsApp.slug} />
            ) : null}
          </View>
        </View>
      </Modal>

      {/* Audit trail for the selected app — owner/editor accessible (the
          backend gates /apps/{slug}/runs by app access, not admin-only).
          SmartAppAuditScreen renders its own Modal. */}
      <SmartAppAuditScreen
        visible={!!auditApp}
        slug={auditApp ? auditApp.slug : null}
        appName={auditApp ? (auditApp.title || auditApp.slug) : null}
        theme={colors}
        onClose={() => setAuditApp(null)}
      />

      <Modal
        visible={!!groundingJob}
        transparent
        animationType="fade"
        onRequestClose={() => setGroundingJob(null)}
      >
        <View style={styles.triggersOverlay}>
          <View style={[styles.triggersCard, { backgroundColor: colors.background, borderColor: colors.border, maxWidth: 460 }]}>
            <View style={[styles.triggersHeader, { borderBottomColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.triggersTitle, { color: colors.text }]} numberOfLines={1}>
                  Refresh grounding · {groundingJob?.title}
                </Text>
                <Text style={[styles.triggersSub, { color: colors.textSecondary }]}>
                  Grounding this app on your historical decisions
                </Text>
              </View>
              {(groundingJob?.status === 'complete' || groundingJob?.status === 'failed') && (
                <TouchableOpacity onPress={() => setGroundingJob(null)} hitSlop={8}>
                  <Ionicons name="close" size={22} color={colors.textSecondary} />
                </TouchableOpacity>
              )}
            </View>

            <View style={{ padding: 16, gap: 12 }}>
              {/* Progress bar */}
              <View style={{ height: 8, borderRadius: 4, backgroundColor: colors.border, overflow: 'hidden' }}>
                <View style={{
                  height: 8,
                  width: `${groundingJob?.progress || 0}%`,
                  backgroundColor: groundingJob?.status === 'failed' ? '#dc2626'
                    : groundingJob?.status === 'complete' ? '#16a34a' : '#2563eb',
                }} />
              </View>

              {groundingJob?.status === 'running' && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <ActivityIndicator size="small" color={colors.text} />
                  <Text style={{ color: colors.text }}>
                    {GROUNDING_PHASE_LABEL[groundingJob?.phase] || groundingJob?.phase} · {groundingJob?.progress || 0}%
                  </Text>
                </View>
              )}

              {groundingJob?.status === 'complete' && (
                <Text style={{ color: colors.text }}>
                  ✅ Loaded {groundingJob?.result?.total_samples ?? 0} samples
                  {' '}({groundingJob?.result?.canonical_samples ?? 0} canonical) across
                  {' '}{(groundingJob?.result?.decision_classes || []).length} decision classes.
                  The app now decides using your past decisions.
                </Text>
              )}

              {groundingJob?.status === 'failed' && (
                <Text style={{ color: '#dc2626' }}>
                  ❌ {groundingJob?.error?.message || 'Refresh failed.'}
                  {groundingJob?.error?.code === 'guard_rejected'
                    ? ' Your live samples were left unchanged.' : ''}
                </Text>
              )}
            </View>
          </View>
        </View>
      </Modal>

      <SmartAppLauncherModal
        visible={!!launching}
        slug={launching?.slug}
        url={launching?.url}
        title={launching?.title}
        theme={colors}
        onClose={() => setLaunching(null)}
      />

      <TransferResourceModal
        visible={!!transferring}
        resource={transferring && {
          kind: 'app',
          id: transferring.slug,
          name: transferring.title || transferring.slug,
          owner_type: transferring.owner_type,
          owner_id: transferring.owner_id,
          org_id: transferring.org_id,
          dept_ids: transferring.dept_ids,
        }}
        theme={colors}
        onClose={() => setTransferring(null)}
        onSubmit={async ({ target_owner_type, target_owner_id, reason }) => {
          await SmartAppService.transferApp(transferring.slug, {
            targetOwnerType: target_owner_type,
            targetOwnerId: target_owner_id,
            reason,
          });
          await load();
        }}
      />

    </View>
  );
}

// ── Gradient button ───────────────────────────────────────────────────────
function GradientButton({
  colors, onPress, icon, label, small = false, busy = false, disabled = false, style,
}) {
  const padV = small ? 7 : 9;
  const padH = small ? 12 : 14;
  const fontSize = small ? 12 : 13;
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled || busy}
      style={[{ borderRadius: small ? 6 : 8, opacity: disabled ? 0.6 : 1 }, style]}
      activeOpacity={0.85}
    >
      <LinearGradient
        colors={colors}
        start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
        style={{
          flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
          gap: 6, paddingHorizontal: padH, paddingVertical: padV,
          borderRadius: small ? 6 : 8,
        }}
      >
        {busy
          ? <ActivityIndicator color="#FFFFFF" size="small" />
          : (icon ? <Ionicons name={icon} size={small ? 14 : 16} color="#FFFFFF" /> : null)}
        {!!label && (
          <Text style={{ color: '#FFFFFF', fontSize, fontWeight: '600' }}>{label}</Text>
        )}
      </LinearGradient>
    </TouchableOpacity>
  );
}

// ── Sort dropdown ────────────────────────────────────────────────────
function SortDropdown({ value, onChange, colors }) {
  const [open, setOpen] = useState(false);
  const current = SORT_OPTIONS.find((o) => o.id === value) || SORT_OPTIONS[0];
  return (
    <View style={{ position: 'relative' }}>
      <TouchableOpacity
        onPress={() => setOpen((v) => !v)}
        style={[styles.sortBtn, { borderColor: colors.border, backgroundColor: colors.surface }]}
      >
        <Ionicons name="swap-vertical" size={14} color={colors.textSecondary} />
        <Text style={{ color: colors.text, fontSize: 12, fontWeight: '500' }}>{current.label}</Text>
        <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={14} color={colors.textSecondary} />
      </TouchableOpacity>
      {open && (
        <View style={[styles.sortMenu, { borderColor: colors.border, backgroundColor: colors.background }]}>
          {SORT_OPTIONS.map((o) => (
            <TouchableOpacity
              key={o.id}
              onPress={() => { onChange(o.id); setOpen(false); }}
              style={[styles.sortItem, value === o.id && { backgroundColor: colors.surface }]}
            >
              <Text style={{
                color: colors.text,
                fontSize: 12,
                fontWeight: value === o.id ? '600' : '500',
              }}>{o.label}</Text>
              {value === o.id && (
                <Ionicons name="checkmark" size={14} color={colors.primary} />
              )}
            </TouchableOpacity>
          ))}
        </View>
      )}
    </View>
  );
}

// ── Audience badge + Publish picker ──────────────────────────────────
// The badge shows where each app is currently published (Owner / Team /
// Department / Organization). The picker calls /publish-options to get
// the options the current user can pick, greys out the rest, and POSTs
// /audience on apply.

function _audienceShortLabel(audience) {
  if (!audience || audience === 'owner') return 'Owner only';
  if (audience === 'org') return 'Organization';
  if (audience.startsWith('team:')) return `Team · ${audience.slice(5)}`;
  if (audience.startsWith('dept:')) return `Dept · ${audience.slice(5)}`;
  return audience;
}

function _audienceColor(audience, colors) {
  if (!audience || audience === 'owner') return colors.textSecondary;
  if (audience === 'org') return '#7C3AED';
  if (audience.startsWith('team:')) return '#3B82F6';
  if (audience.startsWith('dept:')) return '#16A34A';
  return colors.textSecondary;
}

function AudienceBadge({ audience, colors }) {
  const label = _audienceShortLabel(audience);
  const color = _audienceColor(audience, colors);
  return (
    <View style={{
      flexDirection: 'row', alignItems: 'center', gap: 4,
      marginLeft: 8, paddingHorizontal: 6, paddingVertical: 1,
      borderRadius: 8, borderWidth: 1, borderColor: color,
    }}>
      <Ionicons name="globe-outline" size={10} color={color} />
      <Text style={{ color, fontSize: 10, fontWeight: '600' }} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

function AudiencePickerModal({ visible, app, theme, onClose, onApplied, promote = false }) {
  const colors = theme || DEFAULT_THEME;
  const [loading, setLoading] = useState(false);
  const [options, setOptions] = useState([]);
  const [current, setCurrent] = useState('owner');
  const [picked, setPicked] = useState(null);
  const [reason, setReason] = useState('');
  const [applyBusy, setApplyBusy] = useState(false);
  const [err, setErr] = useState('');
  // "Publish to a specific user" sub-panel: org user search → publish to that
  // user's Work SA (audience team:<work_sa>). Only they see the app.
  const [uMode, setUMode] = useState(false);
  const [uq, setUq] = useState('');
  const [uResults, setUResults] = useState([]);
  const [uLoading, setULoading] = useState(false);
  const [uErr, setUErr] = useState('');
  // The term of the last COMPLETED search (null = none yet). Separates "no
  // matches" from "not searched yet", so a zero-result search says so instead
  // of rendering nothing. The message quotes THIS, not the live input — reading
  // the input during render relabels the message with a query never run.
  const [uSearchedFor, setUSearchedFor] = useState(null);
  const [pickedUser, setPickedUser] = useState(null);
  // Grounding gate (promote mode, grounded apps): the BA chooses to refresh the
  // few-shot memory as part of promote (default) or ship without it.
  const grounded = !!(promote && app?.grounded);
  const [groundingChoice, setGroundingChoice] = useState('refresh'); // 'refresh' | 'skip'
  const [freshness, setFreshness] = useState(null); // {never_refreshed, last_refreshed_at, sample_count}

  useEffect(() => {
    if (!visible || !app?.slug) return;
    let cancelled = false;
    setLoading(true); setErr(''); setPicked(null); setReason('');
    setUMode(false); setUq(''); setUResults([]); setUErr(''); setUSearchedFor(null); setPickedUser(null);
    setGroundingChoice('refresh'); setFreshness(null);
    if (grounded) {
      SmartAppService.getGroundingStatus(app.slug)
        .then((gs) => { if (!cancelled) setFreshness(gs); })
        .catch(() => { if (!cancelled) setFreshness(null); });
    }
    SmartAppService.getPublishOptions(app.slug)
      .then((data) => {
        if (cancelled) return;
        const cur = data?.current || 'owner';
        setCurrent(cur);
        setOptions(Array.isArray(data?.options) ? data.options : []);
        // Promote: do NOT preselect. The test app is audience="owner", so
        // preselecting it would silently NARROW a re-promoted prod app's
        // audience to owner. Force the BA to explicitly choose who sees it in
        // production (the prod audience), which is the right question anyway.
      })
      .catch((e) => { if (!cancelled) setErr(e.message || 'Failed to load options'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [visible, app?.slug, promote]);

  // Teams are no longer used, so the generic "team SA" audiences are hidden
  // from the picker (publishing to a person happens via that user's SA through
  // a dedicated flow, not the raw team list). Owner / dept / org remain.
  const visibleOptions = options.filter((o) => o.level !== 'team');

  // In promote mode any picked value is valid (including === current — the
  // action is "ship to prod"); in audience mode only a CHANGE applies.
  const canApply = !!picked && (promote || picked !== current);

  const apply = async () => {
    if (!canApply) return;
    setApplyBusy(true); setErr('');
    try {
      if (promote) {
        const result = await SmartAppService.promoteToProd(app.slug, {
          audience: picked,
          refresh_grounding: grounded && groundingChoice === 'refresh',
          promote_ungrounded: grounded && groundingChoice === 'skip',
        });
        await onApplied?.(result);
      } else {
        await SmartAppService.setAudience(app.slug, { audience: picked, reason });
        await onApplied?.();
      }
    } catch (e) {
      setErr(e.message || (promote ? 'Failed to promote' : 'Failed to apply'));
    } finally {
      setApplyBusy(false);
    }
  };

  // Org user search (builder/admin) for the "Publish to a user" flow.
  const searchUsers = async () => {
    // An empty q makes the backend skip its email filter and return the first
    // page of the whole directory — ask for a term instead of dumping a roster.
    const term = uq.trim();
    if (!term) {
      setUErr('Enter an email (or part of one) to search.');
      setUResults([]); setUSearchedFor(null);
      return;
    }
    setULoading(true); setUErr(''); setUSearchedFor(null);
    try {
      // listUsers already unwraps the envelope and returns the users ARRAY —
      // reading .users off it again yielded undefined and silently blanked every
      // search. Match the other call sites (AdminUsersScreen, Impersonate), and
      // throw on an unexpected shape rather than coercing it to [] — coercion is
      // what made the original bug invisible.
      const users = await AdminUserService.listUsers({ q: term, limit: 25 });
      if (!Array.isArray(users)) {
        throw new Error(`user search returned ${typeof users}, expected an array`);
      }
      setUResults(users);
      setUSearchedFor(term);
    } catch (e) {
      console.error('[publish-to-user] search failed', e);
      setUErr(e?.message || 'Search failed');
      setUResults([]);
    } finally {
      setULoading(false);
    }
  };
  // Pick a user → publish to their Work SA. Users without a Work SA can't be a
  // target (fail loud: the row is disabled with an explanation).
  const pickUser = (usr) => {
    if (!usr?.work_sa_id) return;
    setPickedUser(usr);
    setPicked('team:' + usr.work_sa_id);
  };

  if (!visible) return null;
  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <Pressable
        style={{
          flex: 1, backgroundColor: 'rgba(0,0,0,0.45)',
          justifyContent: 'center', alignItems: 'center',
        }}
        onPress={onClose}
      >
        <Pressable
          onPress={() => {}}
          style={{
            width: '90%', maxWidth: 520, maxHeight: '85%',
            backgroundColor: colors.background, borderRadius: 12,
            borderWidth: 1, borderColor: colors.border, overflow: 'hidden',
          }}
        >
          <View style={{
            paddingHorizontal: 16, paddingVertical: 14,
            borderBottomWidth: 1, borderBottomColor: colors.border,
            flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 16, fontWeight: '700', color: colors.text }}>
                {promote ? 'Promote to Production' : 'Publish to…'}
              </Text>
              <Text style={{ fontSize: 12, color: colors.textSecondary, marginTop: 2 }} numberOfLines={1}>
                {promote
                  ? `${app?.title || app?.slug} · choose who can see it in production`
                  : `${app?.title || app?.slug} · currently ${_audienceShortLabel(current)}`}
              </Text>
            </View>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={20} color={colors.textSecondary} />
            </TouchableOpacity>
          </View>

          <ScrollView style={{ maxHeight: 360 }} contentContainerStyle={{ padding: 12 }}>
            {loading ? (
              <ActivityIndicator color={colors.primary} style={{ marginVertical: 20 }} />
            ) : err ? (
              <Text style={{ color: colors.danger, fontSize: 13 }}>{err}</Text>
            ) : visibleOptions.length === 0 ? (
              <Text style={{ color: colors.textSecondary, fontSize: 13 }}>
                No audience options available.
              </Text>
            ) : (
              visibleOptions.map((opt) => {
                const isCurrent = opt.value === current;
                const isPicked = opt.value === picked;
                const disabled = !opt.allowed;
                return (
                  <TouchableOpacity
                    key={opt.value}
                    disabled={disabled}
                    onPress={() => setPicked(opt.value)}
                    style={{
                      flexDirection: 'row', alignItems: 'center', gap: 10,
                      padding: 12, marginBottom: 8, borderRadius: 8,
                      borderWidth: 1,
                      borderColor: isPicked ? colors.primary : colors.border,
                      backgroundColor: isPicked ? '#EFF6FF' : colors.background,
                      opacity: disabled ? 0.5 : 1,
                    }}
                  >
                    <Ionicons
                      name={
                        opt.level === 'org' ? 'globe-outline'
                        : opt.level === 'dept' ? 'business-outline'
                        : opt.level === 'team' ? 'people-outline'
                        : 'person-outline'
                      }
                      size={18}
                      color={_audienceColor(opt.value, colors)}
                    />
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: colors.text, fontWeight: '600', fontSize: 13 }}>
                        {opt.label}
                        {isCurrent ? '  · current' : ''}
                      </Text>
                      {disabled && opt.reason && (
                        <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 2 }}>
                          {opt.reason.replace(/_/g, ' ')}
                        </Text>
                      )}
                    </View>
                    {isPicked && (
                      <Ionicons name="checkmark-circle" size={18} color={colors.primary} />
                    )}
                  </TouchableOpacity>
                );
              })
            )}

            {/* Publish to a specific user — search the org directory and share
                the app to that person's Work SA (only they see it). */}
            {!promote && !loading && (
              <View style={{ marginTop: 4, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 }}>
                {!uMode ? (
                  <TouchableOpacity
                    onPress={() => setUMode(true)}
                    style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}
                  >
                    <Ionicons name="person-add-outline" size={16} color={colors.primary} />
                    <Text style={{ color: colors.primary, fontWeight: '600', fontSize: 13 }}>
                      Publish to a specific user…
                    </Text>
                  </TouchableOpacity>
                ) : (
                  <View>
                    <Text style={{ fontSize: 11, color: colors.textSecondary, marginBottom: 6 }}>
                      Search a user by email — the app is shared to their Work SA, so only they see it. Check the org on each result: an org admin sees only their own, a super admin searches across every org.
                    </Text>
                    <View style={{ flexDirection: 'row', gap: 8 }}>
                      <TextInput
                        value={uq}
                        onChangeText={setUq}
                        onSubmitEditing={searchUsers}
                        placeholder="Search by email…"
                        placeholderTextColor={colors.textSecondary}
                        style={{
                          flex: 1, borderWidth: 1, borderColor: colors.border, borderRadius: 8,
                          paddingHorizontal: 10, paddingVertical: 8, color: colors.text, fontSize: 13,
                        }}
                      />
                      <TouchableOpacity
                        onPress={searchUsers}
                        style={{ paddingHorizontal: 14, justifyContent: 'center', borderRadius: 8, backgroundColor: colors.primary }}
                      >
                        <Text style={{ color: '#FFFFFF', fontWeight: '600', fontSize: 13 }}>Search</Text>
                      </TouchableOpacity>
                    </View>
                    {uLoading && <ActivityIndicator color={colors.primary} style={{ marginVertical: 10 }} />}
                    {!!uErr && <Text style={{ color: colors.danger, fontSize: 12, marginTop: 6 }}>{uErr}</Text>}
                    {!!uSearchedFor && !uLoading && !uErr && uResults.length === 0 && (
                      <Text style={{ color: colors.textSecondary, fontSize: 12, marginTop: 8 }}>
                        No users match “{uSearchedFor}” — search matches the email address.
                      </Text>
                    )}
                    {uResults.map((usr) => {
                      const disabled = !usr.work_sa_id;
                      const sel = pickedUser?.email === usr.email;
                      return (
                        <TouchableOpacity
                          key={usr.email}
                          disabled={disabled}
                          onPress={() => pickUser(usr)}
                          style={{
                            flexDirection: 'row', alignItems: 'center', gap: 8,
                            padding: 10, marginTop: 6, borderRadius: 8, borderWidth: 1,
                            borderColor: sel ? colors.primary : colors.border,
                            backgroundColor: sel ? '#EFF6FF' : colors.background,
                            opacity: disabled ? 0.5 : 1,
                          }}
                        >
                          <Ionicons name="person-outline" size={16} color={colors.textSecondary} />
                          <View style={{ flex: 1 }}>
                            <Text style={{ color: colors.text, fontSize: 13, fontWeight: '600' }}>{usr.email}</Text>
                            {/* Org is the only thing separating two same-named
                                users when a super_admin searches every org —
                                publishing to the wrong one exposes this app to
                                another tenant, and the server won't stop it. */}
                            {!!usr.org_id && (
                              <Text style={{ color: colors.textSecondary, fontSize: 11 }}>{usr.org_id}</Text>
                            )}
                            {disabled && (
                              <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
                                No Work SA — can't publish to this user
                              </Text>
                            )}
                          </View>
                          {sel && <Ionicons name="checkmark-circle" size={18} color={colors.primary} />}
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                )}
              </View>
            )}

            {!promote && picked && picked !== current && (
              <View style={{ marginTop: 8 }}>
                <Text style={{ fontSize: 11, color: colors.textSecondary, marginBottom: 4 }}>
                  {pickedUser
                    ? `Publishing to ${pickedUser.email} · Reason (optional, recorded in audit)`
                    : 'Reason (optional, recorded in audit)'}
                </Text>
                <TextInput
                  value={reason}
                  onChangeText={setReason}
                  placeholder="e.g. requested by JE Patna via email"
                  placeholderTextColor={colors.textSecondary}
                  style={{
                    borderWidth: 1, borderColor: colors.border, borderRadius: 8,
                    paddingHorizontal: 10, paddingVertical: 8,
                    color: colors.text, fontSize: 13,
                  }}
                />
              </View>
            )}

            {/* Grounding gate — a grounded app must have fresh few-shot memory
                before it reasons from precedent in production. */}
            {grounded && !loading && (
              <View style={{
                marginTop: 8, borderWidth: 1, borderColor: '#D97706',
                borderRadius: 8, padding: 12, backgroundColor: '#FFFBEB',
              }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Ionicons name="school-outline" size={16} color="#B45309" />
                  <Text style={{ fontWeight: '700', fontSize: 13, color: '#92400E' }}>
                    This app learns from your history
                  </Text>
                </View>
                <Text style={{ fontSize: 12, color: '#92400E', marginBottom: 10 }}>
                  {freshness?.never_refreshed
                    ? 'Its few-shot memory has never been refreshed — until it is, the app runs on its base prompt in production, with no precedent to reason from.'
                    : (freshness?.last_refreshed_at
                        ? `Last refreshed ${new Date(freshness.last_refreshed_at).toLocaleDateString()} · ${freshness.sample_count ?? 0} samples. A promote refresh pulls your latest decisions.`
                        : 'Refresh its few-shot memory so production reasons from your real past decisions.')}
                </Text>
                {[
                  { key: 'refresh', title: 'Refresh grounding for production', sub: 'Recommended — pulls your latest decisions before it goes live.' },
                  { key: 'skip', title: 'Promote without grounding', sub: 'Ships now; runs on the base prompt until you refresh it.' },
                ].map((opt) => {
                  const sel = groundingChoice === opt.key;
                  return (
                    <TouchableOpacity
                      key={opt.key}
                      onPress={() => setGroundingChoice(opt.key)}
                      style={{
                        flexDirection: 'row', alignItems: 'flex-start', gap: 8,
                        padding: 10, marginBottom: 6, borderRadius: 8, borderWidth: 1,
                        borderColor: sel ? '#B45309' : colors.border,
                        backgroundColor: sel ? '#FEF3C7' : colors.background,
                      }}
                    >
                      <Ionicons
                        name={sel ? 'radio-button-on' : 'radio-button-off'}
                        size={16}
                        color={sel ? '#B45309' : colors.textSecondary}
                        style={{ marginTop: 1 }}
                      />
                      <View style={{ flex: 1 }}>
                        <Text style={{ color: colors.text, fontWeight: '600', fontSize: 13 }}>{opt.title}</Text>
                        <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 1 }}>{opt.sub}</Text>
                      </View>
                    </TouchableOpacity>
                  );
                })}
              </View>
            )}
          </ScrollView>

          <View style={{
            flexDirection: 'row', gap: 10,
            padding: 12, borderTopWidth: 1, borderTopColor: colors.border,
          }}>
            <TouchableOpacity
              onPress={onClose}
              style={{
                flex: 1, paddingVertical: 10, borderRadius: 8,
                borderWidth: 1, borderColor: colors.border,
                alignItems: 'center',
              }}
            >
              <Text style={{ color: colors.textSecondary, fontWeight: '600', fontSize: 13 }}>
                Cancel
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={apply}
              disabled={!canApply || applyBusy}
              style={{
                flex: 1, paddingVertical: 10, borderRadius: 8,
                backgroundColor: colors.primary,
                alignItems: 'center',
                opacity: !canApply || applyBusy ? 0.6 : 1,
              }}
            >
              {applyBusy ? (
                <ActivityIndicator color="#FFFFFF" />
              ) : (
                <Text style={{ color: '#FFFFFF', fontWeight: '600', fontSize: 13 }}>
                  {promote
                    ? (grounded && groundingChoice === 'refresh' ? 'Promote & refresh' : 'Promote to Prod')
                    : 'Apply'}
                </Text>
              )}
            </TouchableOpacity>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}


// ── styles ───────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  root: { flex: 1, flexDirection: 'row' },
  main: { flex: 1 },

  // Left rail
  rail: {
    width: 260, borderRightWidth: 1, paddingBottom: 24,
  },
  brandRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1,
  },
  // Soft drop shadow applied to the OpsMark brand tile in the rail / header.
  brandMarkShadow: {
    shadowColor: '#1E3A8A', shadowOpacity: 0.28,
    shadowRadius: 8, shadowOffset: { width: 0, height: 4 }, elevation: 4,
  },
  brand: { fontSize: 17, fontWeight: '700' },
  brandSub: { fontSize: 11, marginTop: 1 },
  railCta: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, marginHorizontal: 16, marginBottom: 8, paddingVertical: 10,
    borderRadius: 8,
  },
  railCtaText: { color: '#FFFFFF', fontSize: 13, fontWeight: '600' },
  railSection: {
    fontSize: 11, fontWeight: '700', letterSpacing: 0.6,
    paddingHorizontal: 20, paddingTop: 16, paddingBottom: 6,
  },
  railRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 16, paddingVertical: 8, marginHorizontal: 8,
    borderRadius: 6,
  },
  railRowText: { flex: 1, fontSize: 13 },
  railCount: {
    minWidth: 24, paddingHorizontal: 6, paddingVertical: 2,
    borderRadius: 10, alignItems: 'center',
  },

  // Header
  headerWrap: { borderBottomWidth: 1, paddingHorizontal: 24, paddingTop: 20 },
  headerTop: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    marginBottom: 16,
  },
  title: { fontSize: 22, fontWeight: '700' },
  subtitle: { fontSize: 13, marginTop: 4 },
  closeBtn: { padding: 6 },

  headerControls: {
    flexDirection: 'row', alignItems: 'center', gap: 10, paddingBottom: 12,
    flexWrap: 'wrap',
  },
  searchBox: {
    flex: 1, minWidth: 240,
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 12, height: 36,
    borderWidth: 1, borderRadius: 8,
  },
  searchInput: { flex: 1, fontSize: 13, outlineStyle: 'none', height: '100%' },
  primaryBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingHorizontal: 14, height: 36, borderRadius: 8,
  },
  primaryBtnText: { color: '#FFFFFF', fontSize: 13, fontWeight: '600' },
  iconBtnGhost: {
    width: 36, height: 36, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },

  // Scope tabs (Mine / Shared / Admin) — drives backend ?scope=
  scopeTabBar: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    marginBottom: 12,
  },
  scopeTab: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    marginRight: 4,
  },
  scopeHint: {
    fontSize: 12,
    marginTop: -4,
    marginBottom: 12,
    paddingHorizontal: 2,
  },

  // Tabs (narrow only) — kind chips (app/dashboard/workflow)
  tabRow: {
    flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap',
    paddingBottom: 12,
  },
  tab: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, borderWidth: 1,
  },

  // Sort
  sortBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 12, height: 36, borderWidth: 1, borderRadius: 8,
  },
  sortMenu: {
    position: 'absolute', top: 42, right: 0, minWidth: 200,
    borderWidth: 1, borderRadius: 8, paddingVertical: 4,
    shadowColor: '#000', shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08, shadowRadius: 12, elevation: 6,
    zIndex: 50,
  },
  sortItem: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 12, paddingVertical: 10,
  },

  // Banner / error
  banner: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    margin: 24, marginBottom: 0, padding: 12, borderRadius: 8, borderWidth: 1,
  },
  errorBox: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    margin: 24, marginBottom: 0, padding: 12, borderRadius: 8, borderWidth: 1,
  },

  // Grid
  gridContent: {
    padding: 24, gap: 16,
  },

  // Card
  card: { padding: 8 },
  cardInner: {
    borderWidth: 1, borderRadius: 14, padding: 18, gap: 8,
    overflow: 'hidden', position: 'relative',
    minHeight: 200,
    shadowColor: '#0F172A', shadowOpacity: 0.07, shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 }, elevation: 2,
  },
  cardHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  kindMark: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  kindTile: {
    width: 30, height: 30, borderRadius: 9,
    alignItems: 'center', justifyContent: 'center',
  },
  kindLabel: {
    fontSize: 11, fontWeight: '700', letterSpacing: 0.4, textTransform: 'uppercase',
  },
  statusChip: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 9, paddingVertical: 3, borderRadius: 6, borderWidth: 1,
  },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  cardTitle: { fontSize: 15, fontWeight: '700', marginTop: 4 },
  cardDesc: { fontSize: 12, lineHeight: 17 },
  cardMetaRow: {
    flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 4,
  },
  cardMeta: { fontSize: 11 },
  cardStatusLine: { fontSize: 11, fontStyle: 'italic', marginTop: 4 },

  execList: {
    marginTop: 8, padding: 8, borderRadius: 6, borderWidth: 1, gap: 6,
  },
  execRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
  },

  cardFooter: {
    flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap',
    paddingTop: 12, marginTop: 'auto', borderTopWidth: 1,
  },
  footerBtnPrimary: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 5, paddingHorizontal: 12, paddingVertical: 7, borderRadius: 6,
  },
  footerBtnPrimaryText: { color: '#FFFFFF', fontSize: 12, fontWeight: '600' },
  footerBtnGhost: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 5, paddingHorizontal: 12, paddingVertical: 7, borderRadius: 6, borderWidth: 1,
  },
  footerBtnGhostText: { fontSize: 12, fontWeight: '500' },
  footerBtnIcon: {
    width: 30, height: 30, borderRadius: 6, borderWidth: 1,
    alignItems: 'center', justifyContent: 'center',
  },

  // Workflow triggers modal
  triggersOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.5)',
    alignItems: 'center', justifyContent: 'center', padding: 16,
  },
  triggersCard: {
    width: '100%', maxWidth: 560, maxHeight: '85%',
    borderRadius: 14, borderWidth: 1, overflow: 'hidden',
  },
  triggersHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 18, paddingVertical: 14, borderBottomWidth: 1,
  },
  triggersTitle: { fontSize: 16, fontWeight: '700' },
  triggersSub: { fontSize: 12, marginTop: 2 },

  // Empty
  emptyWrap: {
    flex: 1, alignItems: 'center', justifyContent: 'center', padding: 40,
  },
  // OpsMark sets its own 96px tile + radius; this only carries margin + shadow.
  emptyIcon: {
    marginBottom: 18,
    shadowColor: '#1E3A8A', shadowOpacity: 0.32,
    shadowRadius: 18, shadowOffset: { width: 0, height: 8 }, elevation: 8,
  },
  emptyTitle: { fontSize: 17, fontWeight: '700', marginBottom: 6 },
  emptyBody: { fontSize: 13, lineHeight: 19, textAlign: 'center', maxWidth: 420 },

  // Skeleton
  skeletonGrid: {
    flexDirection: 'row', flexWrap: 'wrap', padding: 24, gap: 16,
  },
  skeletonCard: {
    borderWidth: 1, borderRadius: 12, padding: 16, gap: 8, minHeight: 180,
  },
  skeletonLine: { height: 10, borderRadius: 4, marginTop: 8 },

  // Drawer
  drawerScrim: {
    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: 'rgba(15,23,42,0.4)', zIndex: 90,
  },
  drawer: {
    position: 'absolute', borderLeftWidth: 1,
    shadowColor: '#000', shadowOffset: { width: -4, height: 0 },
    shadowOpacity: 0.1, shadowRadius: 16, elevation: 12, zIndex: 100,
  },
  drawerHeader: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 12,
    padding: 20, borderBottomWidth: 1,
  },
  drawerMark: {
    width: 38, height: 38, borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.2)',
    alignItems: 'center', justifyContent: 'center',
  },
  drawerTitle: { fontSize: 17, fontWeight: '700' },
  drawerSub: { fontSize: 12, marginTop: 4 },
  drawerFooter: {
    flexDirection: 'row', gap: 10, padding: 16, borderTopWidth: 1,
  },
  fieldLabel: { fontSize: 13, fontWeight: '600', marginBottom: 4 },
  fieldHint: { fontSize: 12, marginBottom: 8 },
  textarea: {
    minHeight: 96, borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10, fontSize: 14,
    textAlignVertical: 'top',
  },
  kindChoice: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 8, borderWidth: 1,
  },
});
