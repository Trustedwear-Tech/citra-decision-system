/**
 * DeptSourcesScreen.js — Operational Data Flow view (MCP fleet + audit)
 *
 * Two read-only tabs:
 *   1. MCP Fleet — live registry from discovery-service (heartbeat status)
 *   2. Audit     — the dept-MCP data-plane trail (dept_query_audit)
 *
 * The former "Sources" CRUD tab was removed (2026-07-10): data sources are now
 * defined in the MCP's SOURCES_FILE + published to the discovery registry, NOT
 * managed from the UI. Mounted at /api/dept-sources for route stability.
 * Designed to sit inside a Modal in App.js (mirrors WorkspaceManagementScreen).
 *
 * A "Run History" tab (runs of a source's ingestion workflow) went with it
 * (2026-07-17). It reached the workflow via ``dept_sources.workflow_id``, and
 * nothing replaced that link: SOURCES_FILE still accepts an informational
 * ``workflow_id`` but registration.py never publishes it, so a fleet row cannot
 * reach a workflow. Re-adding the tab means publishing workflow_id from the
 * registration payload first.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, FlatList, ActivityIndicator,
  TextInput, StyleSheet, RefreshControl,
} from 'react-native';
import DeptSourceService from '../services/DeptSourceService';
import DiscoveryService from '../services/DiscoveryService';

const TABS = [
  { key: 'fleet', label: 'MCP Fleet' },
  { key: 'audit', label: 'Audit' },
];

const AUDIT_OPS = ['all', 'query', 'run_query', 'execute_action'];

const DEFAULT_THEME = {
  background: '#FFFFFF',
  surface:    '#F9FAFB',
  text:       '#1F2937',
  textSecondary: '#6B7280',
  primary:    '#3B82F6',
  border:     '#E5E7EB',
};

export default function DeptSourcesScreen({
  visible,
  onClose,
  theme = DEFAULT_THEME,
  initialOrgId,
  initialDeptId,
}) {
  const [tab, setTab] = useState('fleet');

  // Filters (shared by Fleet/Audit)
  const [orgId, setOrgId]   = useState(initialOrgId || '');
  const [deptId, setDeptId] = useState(initialDeptId || '');

  // Data
  const [fleet, setFleet] = useState([]);

  // Audit tab — MCP data-plane trail (dept_query_audit)
  const [auditRows, setAuditRows]     = useState([]);
  const [auditOp, setAuditOp]         = useState('all');
  const [deniedOnly, setDeniedOnly]   = useState(false);
  const [auditSource, setAuditSource] = useState(null); // deep-link filter

  // UI state
  const [loading, setLoading]       = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError]           = useState('');

  // Monotonic request sequence shared by all tab loaders. loading/error are
  // shared state — without this, a slow response from a tab the user already
  // left clobbers the current tab's spinner/error/data.
  const reqSeqRef = useRef(0);

  const colors = theme;

  // ── Data loaders ────────────────────────────────────────────────────

  const loadFleet = useCallback(async () => {
    const seq = ++reqSeqRef.current;
    setError(''); setLoading(true);
    try {
      const data = await DiscoveryService.listTools({ activeOnly: false });
      if (seq !== reqSeqRef.current) return;
      setFleet(Array.isArray(data?.tools) ? data.tools : []);
    } catch (err) {
      if (seq !== reqSeqRef.current) return;
      setError(err.message || 'Failed to load MCP fleet');
      setFleet([]);
    } finally { if (seq === reqSeqRef.current) setLoading(false); }
  }, []);

  const loadAudit = useCallback(async () => {
    const seq = ++reqSeqRef.current;
    setError(''); setLoading(true);
    try {
      const data = await DeptSourceService.listQueryAudit({
        orgId: orgId || undefined,
        deptId: deptId || undefined,
        sourceId: auditSource?.source_id || undefined,
        op: auditOp,
        deniedOnly,
        limit: 200,
      });
      if (seq !== reqSeqRef.current) return;
      setAuditRows(Array.isArray(data?.records) ? data.records : []);
    } catch (err) {
      if (seq !== reqSeqRef.current) return;
      setError(err.message || 'Failed to load audit trail');
      setAuditRows([]);
    } finally { if (seq === reqSeqRef.current) setLoading(false); }
  }, [orgId, deptId, auditSource, auditOp, deniedOnly]);

  // ── Tab effects ────────────────────────────────────────────────────
  //
  // Fleet loads immediately on tab entry. Audit loads through ONE debounced
  // path — it depends on the Org/Dept text inputs, and a separate immediate
  // effect would double-fetch on every keystroke.

  useEffect(() => {
    if (!visible) return;
    if (tab === 'fleet') loadFleet();
  }, [visible, tab, loadFleet]);

  useEffect(() => {
    if (!visible || tab !== 'audit') return;
    const handle = setTimeout(() => { loadAudit(); }, 350);
    return () => clearTimeout(handle);
  }, [visible, tab, loadAudit]);

  const handleRefresh = async () => {
    setRefreshing(true);
    if (tab === 'fleet') await loadFleet();
    else if (tab === 'audit') await loadAudit();
    setRefreshing(false);
  };

  // ── Renderers ──────────────────────────────────────────────────────

  const renderFleetRow = ({ item }) => {
    const lastHb = item.last_heartbeat ? new Date(item.last_heartbeat) : null;
    const stale = lastHb ? (Date.now() - lastHb.getTime() > 5 * 60 * 1000) : true;
    return (
      <View style={[styles.row, { borderColor: colors.border, backgroundColor: colors.surface }]}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: colors.text, fontWeight: '600' }}>{item.tool_id}</Text>
          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
            {item.source_type || 'unknown'} · {(item.dept_ids || []).join(',') || '-'}
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 11 }}>
            {item.query_endpoint}
          </Text>
        </View>
        <View>
          <Text style={{ color: stale ? '#DC2626' : '#16A34A', fontSize: 12 }}>
            {stale ? '● stale' : '● healthy'}
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 10 }}>
            {lastHb ? _formatTs(lastHb) : 'never'}
          </Text>
        </View>
      </View>
    );
  };

  const renderAuditRow = ({ item }) => {
    const denied = item.allowed === false;
    const failed = !denied && item.ok === false;
    const isWrite = item.op === 'execute_action';
    // `ts` is a float epoch in SECONDS (time.time() on the MCP side).
    const when = item.ts ? _formatTs(item.ts * 1000) : '';
    const statusColor = denied ? '#DC2626' : failed ? '#D97706' : '#16A34A';
    const statusLabel = denied ? '✗ denied' : failed ? '⚠ failed' : '✓';
    return (
      <View style={[styles.row, { borderColor: colors.border, backgroundColor: colors.surface }]}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: colors.text, fontWeight: '600', fontSize: 13 }}>
            {isWrite ? `✍ ${item.action_id || 'write'}` : item.op || 'query'}
            {item.dry_run ? ' · dry-run' : ''}
            <Text style={{ color: statusColor }}>  {statusLabel}</Text>
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
            {item.user_email || item.user_id || 'unknown user'} → {item.source_id}
            {item.source_dept_id ? ` (${item.source_dept_id})` : ''}
            {item.dataset_id ? ` · ${item.dataset_id}` : ''}
          </Text>
          {(denied || item.error) ? (
            <Text style={{ color: '#DC2626', fontSize: 11 }} numberOfLines={2}>
              {String(item.reason || item.error || '').slice(0, 200)}
            </Text>
          ) : item.query ? (
            <Text style={{ color: colors.textSecondary, fontSize: 11 }} numberOfLines={1}>
              {item.query}
            </Text>
          ) : null}
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Text style={{ color: colors.textSecondary, fontSize: 10 }}>{when}</Text>
          {typeof item.result_count === 'number' && !denied ? (
            <Text style={{ color: colors.textSecondary, fontSize: 10 }}>{item.result_count} rows</Text>
          ) : null}
          {item.act ? (
            <Text style={{ color: '#B45309', fontSize: 10 }}>impersonated</Text>
          ) : null}
        </View>
      </View>
    );
  };

  // ── Tab content ────────────────────────────────────────────────────

  const renderFleetTab = () => {
    if (loading) return <ActivityIndicator style={{ marginTop: 30 }} color={colors.primary} />;
    // A failed load must not claim the fleet is empty — "0 MCP instances
    // registered" beside an error reads as "there are no MCPs". It still needs
    // a way back: the list (and its pull-to-refresh) is unmounted here, and on
    // web a RefreshControl isn't reachable with a mouse anyway, so offer an
    // explicit retry. The message itself renders above.
    if (error) {
      return (
        <View style={{ marginTop: 30, alignItems: 'center' }}>
          <Text style={{ color: colors.textSecondary, fontSize: 12, marginBottom: 10 }}>
            Couldn't load the MCP fleet.
          </Text>
          <TouchableOpacity
            onPress={loadFleet}
            style={[styles.chip, { backgroundColor: colors.primary, borderColor: colors.primary }]}
          >
            <Text style={{ color: '#fff', fontSize: 12 }}>Retry</Text>
          </TouchableOpacity>
        </View>
      );
    }
    // "visible", not "registered": discovery returns only the tools THIS caller
    // may see (org/dept/roles_allowed), so the response cannot speak for the
    // whole registry. A dept_admin filtered down to nothing would otherwise be
    // told the fleet is empty when it isn't.
    return (
      <>
        <Text style={{ color: colors.textSecondary, marginBottom: 6, fontSize: 12 }}>
          {fleet.length} MCP {fleet.length === 1 ? 'instance' : 'instances'} visible
        </Text>
        <FlatList
          data={fleet}
          keyExtractor={(it) => it.tool_id}
          renderItem={renderFleetRow}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
          ListEmptyComponent={
            <Text style={{ color: colors.textSecondary, textAlign: 'center', marginTop: 30 }}>
              No MCP instances visible to you.
            </Text>
          }
        />
      </>
    );
  };

  const renderAuditTab = () => (
    <>
      <View style={styles.toolbar}>
        <TextInput
          style={[styles.smallInput, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surface }]}
          placeholder="Org ID" placeholderTextColor={colors.textSecondary}
          value={orgId} onChangeText={setOrgId}
        />
        <TextInput
          style={[styles.smallInput, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surface }]}
          placeholder="Dept ID (data owner)" placeholderTextColor={colors.textSecondary}
          value={deptId} onChangeText={setDeptId}
        />
      </View>
      <View style={styles.chipsRow}>
        {AUDIT_OPS.map((o) => (
          <TouchableOpacity
            key={o}
            onPress={() => setAuditOp(o)}
            style={[styles.chip, {
              backgroundColor: auditOp === o ? colors.primary : colors.surface,
              borderColor: colors.border,
            }]}
          >
            <Text style={{ color: auditOp === o ? '#fff' : colors.text, fontSize: 11 }}>
              {o === 'execute_action' ? 'writes' : o}
            </Text>
          </TouchableOpacity>
        ))}
        <TouchableOpacity
          onPress={() => setDeniedOnly((v) => !v)}
          style={[styles.chip, {
            backgroundColor: deniedOnly ? '#DC2626' : colors.surface,
            borderColor: deniedOnly ? '#DC2626' : colors.border,
          }]}
        >
          <Text style={{ color: deniedOnly ? '#fff' : colors.text, fontSize: 11 }}>denied only</Text>
        </TouchableOpacity>
        {auditSource ? (
          <TouchableOpacity
            onPress={() => setAuditSource(null)}
            style={[styles.chip, { backgroundColor: colors.primary, borderColor: colors.primary }]}
          >
            <Text style={{ color: '#fff', fontSize: 11 }}>{auditSource.source_id} ✕</Text>
          </TouchableOpacity>
        ) : null}
      </View>
      {loading
        ? <ActivityIndicator style={{ marginTop: 30 }} color={colors.primary} />
        : <FlatList
            data={auditRows}
            keyExtractor={(it, idx) => `${it.ts || ''}_${it.user_id || ''}_${idx}`}
            renderItem={renderAuditRow}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
            ListEmptyComponent={
              <Text style={{ color: colors.textSecondary, textAlign: 'center', marginTop: 30 }}>
                No audit records match the current filter.
              </Text>
            }
          />
      }
    </>
  );

  // ── Layout ─────────────────────────────────────────────────────────

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <View style={[styles.topBar, { borderColor: colors.border }]}>
        <Text style={{ color: colors.text, fontSize: 16, fontWeight: '600' }}>
          Operational Data Flow Audit
        </Text>
        <TouchableOpacity onPress={onClose}>
          <Text style={{ color: colors.textSecondary, fontSize: 18 }}>✕</Text>
        </TouchableOpacity>
      </View>

      <View style={[styles.tabBar, { borderColor: colors.border }]}>
        {TABS.map((t) => (
          <TouchableOpacity
            key={t.key}
            onPress={() => setTab(t.key)}
            style={[styles.tab, tab === t.key && { borderBottomColor: colors.primary, borderBottomWidth: 2 }]}
          >
            <Text style={{ color: tab === t.key ? colors.primary : colors.textSecondary, fontWeight: '500' }}>
              {t.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {error ? (
        <Text style={{ color: '#DC2626', padding: 8, fontSize: 12 }}>{error}</Text>
      ) : null}

      <View style={{ flex: 1, padding: 12 }}>
        {tab === 'fleet' && renderFleetTab()}
        {tab === 'audit' && renderAuditTab()}
      </View>
    </View>
  );
}

// ── Atoms ──────────────────────────────────────────────────────────────

function _formatTs(value) {
  try {
    const d = new Date(value);
    if (isNaN(d.getTime())) return String(value);
    const now = Date.now();
    const diff = now - d.getTime();
    if (diff < 60_000) return 'just now';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return d.toLocaleString();
  } catch {
    return String(value);
  }
}

const styles = StyleSheet.create({
  topBar: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: 1,
  },
  tabBar: { flexDirection: 'row', borderBottomWidth: 1 },
  tab: { paddingHorizontal: 16, paddingVertical: 10 },
  toolbar: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  smallInput: {
    borderWidth: 1, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 6,
    fontSize: 12, marginRight: 6, minWidth: 90,
  },
  chipsRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12, borderWidth: 1, marginRight: 6, marginBottom: 4 },
  row: {
    flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: 8,
    padding: 10, marginBottom: 6,
  },
});
