/**
 * SmartAppAuditScreen — the "Changes" ledger for a Smart App.
 *
 * This is an EFFECT-centric audit: it records WHAT CHANGED and WHO caused it —
 * not what the AI recommended. A recommendation no one acted on changed nothing
 * and is deliberately excluded. Two kinds of change appear:
 *
 *   • 🤖 AI auto-process — a write the AI committed automatically by policy, with
 *     no human approval (the highest-scrutiny path).
 *   • 👤 a person — a change a user caused: approved a recommendation, fired a
 *     queue action, or edited/assigned a record (shown with old → new values).
 *
 * Backed by GET /apps/{slug}/changes (merges auto_process_decisions + write-
 * bearing app_run_audit rows). See docs/smart-app-change-ledger-plan.md.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, TouchableOpacity, FlatList, ActivityIndicator, Modal, StyleSheet,
} from 'react-native';
import SmartAppService from '../services/SmartAppService';


const DEFAULT_THEME = {
  background:    '#FFFFFF',
  surface:       '#F9FAFB',
  text:          '#111827',
  textSecondary: '#6B7280',
  primary:       '#3B82F6',
  border:        '#E5E7EB',
  danger:        '#DC2626',
  success:       '#16A34A',
  warning:       '#D97706',
};

// SmartApp has no "workflows" anymore (the modes are auto-process / auto-recommend
// / on-demand recommend). The stored action strings still carry the legacy
// "workflow_staging_*" names from the recommendation/approve path; relabel for
// display only — the stored value is unchanged.
const ACTION_LABELS = {
  workflow_staging_apply: 'Recommendation applied',
  workflow_staging_review: 'Recommendation reviewed',
};
function actionLabel(a) {
  if (!a) return 'Change';
  return ACTION_LABELS[a] || String(a).replace(/_/g, ' ');
}

const WRITE_STATUS_LABELS = { ok: 'applied', blocked: 'blocked', error: 'failed' };
function writeStatusLabel(s) { return WRITE_STATUS_LABELS[s] || s || '—'; }

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}
function fmtVal(v) {
  if (v === null || v === undefined || v === '') return '∅';
  if (typeof v === 'object') { try { return JSON.stringify(v); } catch { return String(v); } }
  return String(v);
}
function pretty(obj) {
  try { return JSON.stringify(obj, null, 2); } catch { return String(obj); }
}

function Pill({ label, color, theme }) {
  return (
    <View style={[styles.pill, { borderColor: color || theme.border }]}>
      <Text style={{ fontSize: 10, color: color || theme.textSecondary }}>{label}</Text>
    </View>
  );
}

/** old → new lines for a delta map ({ field: { from, to } }). */
function DeltaRows({ delta, theme }) {
  const keys = delta && typeof delta === 'object' ? Object.keys(delta) : [];
  if (!keys.length) return null;
  return (
    <View style={{ marginTop: 4 }}>
      {keys.map((k) => {
        const d = delta[k] || {};
        return (
          <Text key={`d:${k}`} style={{ color: theme.text, fontSize: 11, marginTop: 1 }}>
            <Text style={{ color: theme.textSecondary }}>{k}: </Text>
            {fmtVal(d.from)} → {fmtVal(d.to)}
          </Text>
        );
      })}
    </View>
  );
}

/** One change in the ledger — who, what changed (old → new), result. */
function ChangeCard({ c, theme }) {
  const [open, setOpen] = useState(false);
  const isAuto = c.actor_type === 'ai_auto';
  const ok = c.outcome === 'applied';
  const outColor = ok ? theme.success : (c.outcome === 'pending' ? theme.warning : theme.danger);
  const wes = Array.isArray(c.write_events) ? c.write_events : [];
  const hasOverride = c.override && typeof c.override === 'object' && Object.keys(c.override).length > 0;
  const hasDetail = wes.length > 0 || c.payload;

  return (
    <View style={[
      styles.row,
      { borderColor: ok ? theme.border : theme.danger, backgroundColor: theme.surface },
    ]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap' }}>
        <Text style={{ color: theme.text, fontWeight: '600', fontSize: 13 }}>
          {isAuto ? '🤖 ' : '👤 '}{actionLabel(c.action)}
        </Text>
        <Pill label={c.outcome || '—'} color={outColor} theme={theme} />
        <Pill
          label={isAuto ? 'auto-process' : 'by person'}
          color={isAuto ? theme.warning : theme.primary}
          theme={theme}
        />
      </View>

      <Text style={{ color: theme.textSecondary, fontSize: 11, marginTop: 2 }} numberOfLines={1}>
        {fmtTime(c.ts)} · {isAuto ? 'AI auto-process' : c.actor_display}
        {c.target && c.target.dataset_id ? ` · ${c.target.dataset_id}` : ''}
      </Text>

      {isAuto && !!c.policy_reason && (
        <Text style={{ color: theme.text, fontSize: 11, marginTop: 4 }}>
          Allowed by rule: {c.policy_reason}
        </Text>
      )}

      {/* old → new: approval overrides + per-write deltas (e.g. an assignment) */}
      {hasOverride && <DeltaRows delta={c.override} theme={theme} />}
      {wes.map((w, i) => (w && w.delta ? <DeltaRows key={`wd:${i}`} delta={w.delta} theme={theme} /> : null))}

      {hasDetail && (
        <TouchableOpacity onPress={() => setOpen((v) => !v)}>
          <Text style={{ color: theme.primary, fontSize: 11, marginTop: 6 }}>
            {open ? '▾ hide details' : '▸ show details'}
          </Text>
        </TouchableOpacity>
      )}

      {open && (
        <View style={{ marginTop: 6 }}>
          {!!c.payload && (
            <>
              <Text style={{ color: theme.textSecondary, fontSize: 10, fontWeight: '600' }}>
                WHAT THE AI SUBMITTED
              </Text>
              <Text style={{ color: theme.text, fontSize: 11, fontFamily: 'monospace', marginTop: 2 }}>
                {pretty(c.payload)}
              </Text>
            </>
          )}
          {wes.map((w, i) => (
            <View key={`we:${i}`} style={{ marginTop: 6 }}>
              <Text style={{ color: theme.textSecondary, fontSize: 10, fontWeight: '600' }}>
                {(w.dataset_id || '—')}.{(w.action_id || '—')} · {writeStatusLabel(w.status)}
                {w.overlay ? ' · in-app' : ''}
              </Text>
              <Text style={{ color: theme.text, fontSize: 11, fontFamily: 'monospace', marginTop: 2 }}>
                {pretty(w.payload || {})}
              </Text>
            </View>
          ))}
          <Text style={{ color: theme.textSecondary, fontSize: 10, marginTop: 6 }} numberOfLines={1}>
            reference: {c.correlation_id || '—'}
          </Text>
        </View>
      )}
    </View>
  );
}


export default function SmartAppAuditScreen({
  visible,
  slug,
  appName,
  onClose,
  theme = DEFAULT_THEME,
}) {
  const colors = theme;
  const PAGE = 50;

  const [changes, setChanges] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [actorFilter, setActorFilter] = useState('all');     // all | ai_auto | user
  const [outcomeFilter, setOutcomeFilter] = useState('all'); // all | applied | failed

  const fetchPage = useCallback(async (nextOffset, append) => {
    const opts = { limit: PAGE, offset: nextOffset };
    if (actorFilter !== 'all') opts.actor = actorFilter;
    if (outcomeFilter !== 'all') opts.outcome = outcomeFilter;
    const data = await SmartAppService.listAppChanges(slug, opts);
    const list = Array.isArray(data.changes) ? data.changes : [];
    setTotal(data.total || 0);
    setChanges((prev) => (append ? prev.concat(list) : list));
    setOffset(nextOffset + list.length);
  }, [slug, actorFilter, outcomeFilter]);

  const load = useCallback(async () => {
    if (!slug) return;
    setLoading(true); setError('');
    try {
      await fetchPage(0, false);
    } catch (err) {
      setError(err.message || 'Failed to load changes');
      setChanges([]); setTotal(0); setOffset(0);
    } finally {
      setLoading(false);
    }
  }, [slug, fetchPage]);

  const loadMore = useCallback(async () => {
    if (loadingMore || loading || changes.length >= total) return;
    setLoadingMore(true); setError('');
    try {
      await fetchPage(offset, true);
    } catch (err) {
      setError(err.message || 'Failed to load more');
    } finally {
      setLoadingMore(false);
    }
  }, [fetchPage, offset, total, changes.length, loading, loadingMore]);

  useEffect(() => { if (visible) load(); }, [visible, load]);

  const ACTORS = [['all', 'all'], ['ai_auto', 'Auto-process'], ['user', 'People']];
  const OUTCOMES = [['all', 'all'], ['applied', 'applied'], ['failed', 'failed']];

  return (
    <Modal visible={!!visible} onRequestClose={onClose} animationType="slide" transparent={false}>
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        {/* Header */}
        <View style={[styles.topBar, { borderColor: colors.border }]}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.text, fontSize: 16, fontWeight: '600' }}>Changes</Text>
            <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 2 }} numberOfLines={1}>
              {appName || slug} · {total} change{total === 1 ? '' : 's'} · what changed & who
            </Text>
          </View>
          <TouchableOpacity onPress={load} style={{ paddingHorizontal: 10 }}>
            <Text style={{ color: colors.primary, fontSize: 12 }}>Refresh</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={onClose}>
            <Text style={{ color: colors.textSecondary, fontSize: 18 }}>✕</Text>
          </TouchableOpacity>
        </View>

        {/* Who changed it */}
        <View style={[styles.filterBar, { borderColor: colors.border }]}>
          <Text style={{ fontSize: 11, color: colors.textSecondary, alignSelf: 'center', marginRight: 4 }}>Who:</Text>
          {ACTORS.map(([f, label]) => (
            <TouchableOpacity
              key={f}
              onPress={() => setActorFilter(f)}
              style={[
                styles.chip,
                { borderColor: colors.border },
                actorFilter === f && { backgroundColor: colors.primary, borderColor: colors.primary },
              ]}
            >
              <Text style={{ fontSize: 11, color: actorFilter === f ? '#FFFFFF' : colors.textSecondary }}>
                {label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Result */}
        <View style={[styles.filterBar, { borderColor: colors.border }]}>
          <Text style={{ fontSize: 11, color: colors.textSecondary, alignSelf: 'center', marginRight: 4 }}>Result:</Text>
          {OUTCOMES.map(([f, label]) => (
            <TouchableOpacity
              key={f}
              onPress={() => setOutcomeFilter(f)}
              style={[
                styles.chip,
                { borderColor: f === 'failed' ? colors.danger : colors.border },
                outcomeFilter === f && {
                  backgroundColor: f === 'failed' ? colors.danger : colors.primary,
                  borderColor: f === 'failed' ? colors.danger : colors.primary,
                },
              ]}
            >
              <Text style={{ fontSize: 11, color: outcomeFilter === f ? '#FFFFFF' : colors.textSecondary }}>
                {label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {error ? (
          <Text style={{ color: colors.danger, padding: 8, fontSize: 12 }}>{error}</Text>
        ) : null}

        <View style={{ flex: 1, paddingHorizontal: 12, paddingTop: 8 }}>
          {loading ? (
            <ActivityIndicator style={{ marginTop: 30 }} color={colors.primary} />
          ) : (
            <FlatList
              data={changes}
              keyExtractor={(it, idx) => `${it.correlation_id || 'c'}:${it.actor_type}:${idx}`}
              renderItem={({ item }) => <ChangeCard c={item} theme={colors} />}
              onEndReachedThreshold={0.4}
              onEndReached={() => { if (changes.length < total) loadMore(); }}
              ListEmptyComponent={
                <Text style={{ color: colors.textSecondary, textAlign: 'center', marginTop: 30 }}>
                  No changes recorded yet — nothing in this app has been auto-applied
                  or changed by a person.
                </Text>
              }
              ListFooterComponent={
                changes.length === 0 ? null : changes.length < total ? (
                  <TouchableOpacity
                    onPress={loadMore}
                    disabled={loadingMore}
                    style={{ paddingVertical: 14, alignItems: 'center' }}
                  >
                    {loadingMore ? (
                      <ActivityIndicator color={colors.primary} />
                    ) : (
                      <Text style={{ color: colors.primary, fontSize: 12, fontWeight: '600' }}>
                        Load more — showing {changes.length} of {total}
                      </Text>
                    )}
                  </TouchableOpacity>
                ) : (
                  <Text style={{ color: colors.textSecondary, fontSize: 11, textAlign: 'center', paddingVertical: 14 }}>
                    Showing all {total} change{total === 1 ? '' : 's'}
                  </Text>
                )
              }
            />
          )}
        </View>
      </View>
    </Modal>
  );
}


const styles = StyleSheet.create({
  topBar: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: 1,
  },
  filterBar: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 6,
    paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: 1,
  },
  chip: {
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12, borderWidth: 1,
    marginRight: 6,
  },
  pill: {
    paddingHorizontal: 6, paddingVertical: 1, borderRadius: 8, borderWidth: 1,
    marginLeft: 6,
  },
  row: {
    borderWidth: 1, borderRadius: 8, padding: 10, marginBottom: 6,
  },
});
