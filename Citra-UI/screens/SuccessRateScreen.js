/**
 * SuccessRateScreen — admin "Success Rate" panel
 * (docs/adoption-metrics-precedent-citation-plan.md §1, wedge plan §A.1).
 *
 * One question, answered in plain words: across all our Decision Apps, how
 * many AI recommendations were accepted, and how many rejected?
 *
 * Exactness rules mirrored from the server (never fudge them in render):
 *   - "Accepted with changes" is its OWN bucket — never folded either way.
 *   - "Automated" is shown separately and never mixed into the human rate.
 *   - Memory lift renders only when the server published a number; below the
 *     cohort minimum it shows the server's "insufficient data" note instead.
 *
 * The per-app rows will become the Learning Report drill-down (plan step 4);
 * v1 renders them read-only.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator, ScrollView, Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import DecisionStatsService from '../services/DecisionStatsService';

const RED = '#DC2626';
const GREEN = '#16A34A';
const AMBER = '#D97706';
const GREY = '#6B7280';
// Per-app "needs attention" dot below this acceptance rate.
const ATTENTION_RATE = 0.6;

const PERIODS = [
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'all', label: 'All time' },
];

const pct = (r) => (r === null || r === undefined ? '—' : `${Math.round(r * 100)}%`);

export default function SuccessRateScreen({ visible, onClose, theme }) {
  const colors = theme || {};
  const bg = colors.background || '#FFFFFF';
  const surface = colors.surface || '#F9FAFB';
  const text = colors.text || '#111827';
  const sub = colors.textSecondary || '#6B7280';
  const border = colors.border || '#E5E7EB';
  const primary = colors.primary || '#3B82F6';

  const [period, setPeriod] = useState('month');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stats, setStats] = useState(null);
  // Learning Report drill-down — {slug, name} when an app row is open.
  const [app, setApp] = useState(null);
  const [report, setReport] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState('');

  const PERIOD_DAYS = { week: 7, month: 30, all: 3650 };

  const load = useCallback(async (p) => {
    setLoading(true); setError('');
    try {
      setStats(await DecisionStatsService.getOrgDecisionStats(p));
    } catch (e) {
      setStats(null);
      setError(e?.status === 403
        ? 'Decision stats are admin-only.'
        : (e?.message || 'Could not load decision stats.'));
    } finally { setLoading(false); }
  }, []);

  const loadReport = useCallback(async (slug, p) => {
    setReportLoading(true); setReportError(''); setReport(null);
    try {
      setReport(await DecisionStatsService.getAppLoopMetrics(slug, PERIOD_DAYS[p] || 30));
    } catch (e) {
      setReportError(e?.message || 'Could not load the learning report.');
    } finally { setReportLoading(false); }
  }, []);

  // Reset navigation only when the modal opens; a period change must never
  // eject the user from a drill-down (screening-panel review lesson).
  useEffect(() => {
    if (!visible) return;
    setApp(null); setReport(null);
  }, [visible]);

  useEffect(() => {
    if (!visible || app) return;
    load(period);
  }, [visible, app, period, load]);

  useEffect(() => {
    if (app?.slug) loadReport(app.slug, period);
  }, [app, period, loadReport]);

  if (!visible) return null;

  const totals = stats?.totals || {};
  const lift = stats?.memory_lift || {};
  const attention = (r) => r !== null && r !== undefined && r < ATTENTION_RATE;

  const StatTile = ({ label, value, hint, accent }) => (
    <View style={{ flexGrow: 1, flexBasis: 130, borderWidth: 1, borderColor: border, borderRadius: 12, padding: 12, backgroundColor: surface }}>
      <Text style={{ fontSize: 22, fontWeight: '800', color: accent || text }}>{value}</Text>
      <Text style={{ fontSize: 12, fontWeight: '700', color: text, marginTop: 2 }}>{label}</Text>
      {hint ? <Text style={{ fontSize: 10, color: sub, marginTop: 2 }}>{hint}</Text> : null}
    </View>
  );

  const SectionTitle = ({ icon, children }) => (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 18, marginBottom: 8 }}>
      <Ionicons name={icon} size={15} color={sub} />
      <Text style={{ fontSize: 13, fontWeight: '800', color: text }}>{children}</Text>
    </View>
  );

  const fmtDuration = (s) => {
    if (s === null || s === undefined) return '—';
    if (s < 90) return `${Math.round(s)}s`;
    if (s < 5400) return `${Math.round(s / 60)}m`;
    return `${(s / 3600).toFixed(1)}h`;
  };

  // ── Learning Report (per app) — adoption plan §3 ─────────────────────────
  const renderReport = () => {
    const tt = report?.time_to_decision || {};
    const rLift = report?.memory_lift || {};
    const absorb = report?.correction_absorption || {};
    const trend = report?.trend_weekly || [];
    return (
      <>
        <TouchableOpacity onPress={() => setApp(null)} style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 8 }}>
          <Ionicons name="arrow-back" size={16} color={primary} />
          <Text style={{ fontSize: 13, fontWeight: '700', color: primary }}>All apps</Text>
        </TouchableOpacity>
        <Text style={{ fontSize: 16, fontWeight: '800', color: text }}>{app.name} — Learning Report</Text>

        {reportLoading && <ActivityIndicator style={{ marginTop: 20 }} color={primary} />}
        {!!reportError && <Text style={{ fontSize: 13, color: RED, marginTop: 10 }}>{reportError}</Text>}

        {report && (
          <>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 12 }}>
              <StatTile label="Override rate" value={pct(report.override_rate)}
                hint="corrected by officers — trends DOWN as it learns"
                accent={report.override_rate > 0.4 ? AMBER : GREEN} />
              <StatTile label="Outcome good" value={pct(report.good_rate)}
                hint="decisions later observed good — trends UP" accent={GREEN} />
              <StatTile label="Time to decision" value={fmtDuration(tt.median_seconds)}
                hint={`median · p90 ${fmtDuration(tt.p90_seconds)} · ${tt.n ?? 0} cases`} />
              <StatTile label="Decisions" value={report.total ?? 0}
                hint={`window: ${report.window_days}d`} />
            </View>

            <SectionTitle icon="trending-up-outline">Learning curve (weekly)</SectionTitle>
            {trend.length === 0 && <Text style={{ fontSize: 12, color: sub }}>No weekly data yet.</Text>}
            {trend.map((w) => (
              <View key={w.week} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <Text style={{ width: 76, fontSize: 11, color: sub }}>{w.week}</Text>
                <View style={{ flex: 1, height: 8, backgroundColor: surface, borderRadius: 4, overflow: 'hidden' }}>
                  {/* override rate bar — SHRINKS as the app learns */}
                  <View style={{ width: `${Math.round((w.override_rate || 0) * 100)}%`, height: 8, backgroundColor: AMBER }} />
                </View>
                <Text style={{ width: 66, fontSize: 11, color: sub, textAlign: 'right' }}>
                  {pct(w.override_rate)} ovr · {w.n}
                </Text>
              </View>
            ))}
            <Text style={{ fontSize: 10, color: sub, marginTop: 2 }}>
              Bar = override rate per week (shorter is better); a shrinking bar on a fixed
              model means the learning lives in the memory, not the weights.
            </Text>

            <SectionTitle icon="school-outline">Memory impact</SectionTitle>
            {rLift.lift !== null && rLift.lift !== undefined ? (
              <Text style={{ fontSize: 12.5, color: text, lineHeight: 18 }}>
                Recommendations backed by past decisions are accepted{' '}
                <Text style={{ fontWeight: '800', color: rLift.lift >= 0 ? GREEN : RED }}>
                  {Math.abs(Math.round(rLift.lift * 100))} points {rLift.lift >= 0 ? 'more' : 'less'}
                </Text>{' '}
                often ({pct(rLift.with_memory?.acceptance_rate)} with memory vs {pct(rLift.cold?.acceptance_rate)} cold).
              </Text>
            ) : (
              <Text style={{ fontSize: 12, color: sub }}>{rLift.note || 'Not enough data yet.'}</Text>
            )}

            <SectionTitle icon="checkmark-done-outline">Corrections absorbed</SectionTitle>
            {absorb.taught ? (
              <>
                <Text style={{ fontSize: 12.5, color: text }}>
                  {absorb.taught} correction{absorb.taught === 1 ? '' : 's'} taught ·{' '}
                  <Text style={{ fontWeight: '800', color: GREEN }}>{absorb.stopped_recurring} never needed again</Text>
                </Text>
                {(absorb.fields || []).map((f) => (
                  <Text key={f.field} style={{ fontSize: 11, color: f.recurred ? AMBER : sub, marginTop: 2 }}>
                    {f.recurred ? '↻' : '✓'} {f.field}{f.recurred ? ' — still being corrected' : ''}
                  </Text>
                ))}
              </>
            ) : (
              <Text style={{ fontSize: 12, color: sub }}>No field corrections taught yet.</Text>
            )}

            <SectionTitle icon="create-outline">Most-corrected fields</SectionTitle>
            {(report.top_overridden_fields || []).length === 0 && (
              <Text style={{ fontSize: 12, color: sub }}>No overrides in this window.</Text>
            )}
            {(report.top_overridden_fields || []).map((f) => (
              <View key={f.field} style={{ flexDirection: 'row', gap: 8, marginBottom: 2 }}>
                <Text style={{ flex: 1, fontSize: 12, color: text }}>{f.field}</Text>
                <Text style={{ fontSize: 12, color: AMBER }}>{f.overrides}×</Text>
              </View>
            ))}
          </>
        )}
      </>
    );
  };

  return (
    <Modal visible transparent={false} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: border }}>
          <Ionicons name="trending-up-outline" size={22} color={primary} />
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 18, fontWeight: '800', color: text }}>Success Rate</Text>
            <Text style={{ fontSize: 12, color: sub }}>
              How your teams dispose AI recommendations — and whether memory is measurably helping.
            </Text>
          </View>
          <TouchableOpacity onPress={onClose} hitSlop={12}>
            <Ionicons name="close" size={24} color={text} />
          </TouchableOpacity>
        </View>

        <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 20, paddingVertical: 10 }}>
          {PERIODS.map((p) => (
            <TouchableOpacity
              key={p.id}
              onPress={() => setPeriod(p.id)}
              style={{ paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, borderWidth: 1, borderColor: period === p.id ? primary : border, backgroundColor: period === p.id ? `${primary}18` : bg }}
            >
              <Text style={{ fontSize: 12, fontWeight: '700', color: period === p.id ? primary : sub }}>{p.label}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 32 }}>
          {loading && <ActivityIndicator style={{ marginTop: 30 }} color={primary} />}
          {!!error && !loading && (
            <Text style={{ fontSize: 13, color: RED, marginTop: 16 }}>{error}</Text>
          )}
          {app ? renderReport() : null}
          {!app && !loading && !error && stats && (
            <>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
                <StatTile label="Accepted" value={totals.accepted ?? 0}
                  hint="approved exactly as recommended" accent={GREEN} />
                <StatTile label="Accepted with changes" value={totals.accepted_with_changes ?? 0}
                  hint="approved after officer edits" accent={AMBER} />
                <StatTile label="Rejected" value={totals.rejected ?? 0}
                  hint="officer said no" accent={totals.rejected ? RED : undefined} />
                <StatTile label="Pending" value={totals.pending ?? 0}
                  hint="awaiting an officer now" accent={GREY} />
                <StatTile label="Acceptance rate" value={pct(totals.acceptance_rate)}
                  hint="accepted ÷ all disposed"
                  accent={attention(totals.acceptance_rate) ? RED : GREEN} />
              </View>

              {totals.automated ? (
                <Text style={{ fontSize: 11, color: sub, marginTop: 8 }}>
                  Plus {totals.automated} auto-processed under configured policy — counted
                  separately, never mixed into the human acceptance rate.
                </Text>
              ) : null}

              {/* Memory lift — the "is it learning" sentence, or an honest
                  not-enough-data note. Never a fabricated number. */}
              <View style={{ borderWidth: 1, borderColor: `${primary}55`, backgroundColor: `${primary}0D`, borderRadius: 10, padding: 12, marginTop: 14 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Ionicons name="school-outline" size={15} color={primary} />
                  <Text style={{ fontSize: 13, fontWeight: '800', color: text }}>Memory impact</Text>
                </View>
                {lift.lift !== null && lift.lift !== undefined ? (
                  <Text style={{ fontSize: 12.5, color: text, marginTop: 6, lineHeight: 18 }}>
                    Recommendations backed by your own past decisions are accepted{' '}
                    <Text style={{ fontWeight: '800', color: lift.lift >= 0 ? GREEN : RED }}>
                      {Math.abs(Math.round(lift.lift * 100))} points {lift.lift >= 0 ? 'more' : 'less'}
                    </Text>{' '}
                    often ({pct(lift.with_memory?.acceptance_rate)} with memory vs{' '}
                    {pct(lift.cold?.acceptance_rate)} cold).
                  </Text>
                ) : (
                  <Text style={{ fontSize: 12, color: sub, marginTop: 6 }}>
                    {lift.note || 'Not enough judged decisions yet to measure memory impact.'}
                  </Text>
                )}
              </View>

              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 18, marginBottom: 8 }}>
                <Ionicons name="apps-outline" size={15} color={sub} />
                <Text style={{ fontSize: 13, fontWeight: '800', color: text }}>By app</Text>
              </View>
              {(stats.apps || []).length === 0 && (
                <Text style={{ fontSize: 13, color: sub, paddingVertical: 8 }}>
                  No disposed recommendations in this period.
                </Text>
              )}
              {(stats.apps || []).map((a) => (
                <TouchableOpacity
                  key={a.app_slug}
                  onPress={() => setApp({ slug: a.app_slug, name: a.name || a.app_slug })}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: 1, borderColor: border, borderRadius: 10, padding: 12, marginBottom: 8, backgroundColor: bg }}
                >
                  {attention(a.acceptance_rate) && (
                    <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: RED }} />
                  )}
                  <Text style={{ flex: 1, fontSize: 13, fontWeight: '700', color: text }} numberOfLines={1}>
                    {a.name || a.app_slug}
                  </Text>
                  <Text style={{ fontSize: 12, color: GREEN }}>{a.accepted} ✓</Text>
                  <Text style={{ fontSize: 12, color: AMBER }}>{a.accepted_with_changes} ✎</Text>
                  <Text style={{ fontSize: 12, color: RED }}>{a.rejected} ✗</Text>
                  {a.pending ? <Text style={{ fontSize: 12, color: sub }}>{a.pending} pending</Text> : null}
                  <Text style={{ fontSize: 12, fontWeight: '700', color: attention(a.acceptance_rate) ? RED : GREEN }}>
                    {pct(a.acceptance_rate)}
                  </Text>
                  <Ionicons name="chevron-forward" size={16} color={sub} />
                </TouchableOpacity>
              ))}
            </>
          )}
        </ScrollView>
      </View>
    </Modal>
  );
}
