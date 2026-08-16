// AITriggersPanel — "Trigger the AI agent on a schedule or webhook" surface.
//
// Lists the app's AI triggers (app_spec.triggers[]) — the agent runs AHEAD of
// the click and stages a recommendation into the same inbox (no workflow
// engine). An editor can:
//   • turn a trigger ON/OFF (they publish DEACTIVATED — this is where the
//     officer activates them),
//   • edit a schedule (cron / interval),
//   • copy a webhook URL to wire into an external system.
// Backed by:
//   GET   /apps/{slug}/ai-triggers
//   PATCH /apps/{slug}/ai-triggers/{trigger_id}

import React, { useEffect, useState, useCallback } from 'react';
import {
  View, Text, TextInput, Switch, TouchableOpacity, ActivityIndicator,
  StyleSheet, Alert, ScrollView, Modal,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import SmartAppService from '../services/SmartAppService';

const TYPE_LABEL = {
  webhook: 'On webhook (external event)',
  'schedule.cron': 'On a schedule (cron)',
  'schedule.interval': 'Every N seconds',
  poll: 'Poll for new records',
};

export default function AITriggersPanel({ slug }) {
  const [triggers, setTriggers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [savingId, setSavingId] = useState(null);
  const [runningId, setRunningId] = useState(null);
  const [runMsg, setRunMsg] = useState({}); // triggerId -> last-run message
  const [histRefresh, setHistRefresh] = useState({}); // triggerId -> reload counter
  // Arming ritual for autonomous (auto-process) triggers: type the app slug to
  // confirm before turning ON writes-with-no-approval.
  const [arming, setArming] = useState(null);   // the trigger being armed
  const [armText, setArmText] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await SmartAppService.getAiTriggers(slug);
      setTriggers(Array.isArray(r?.triggers) ? r.triggers : []);
    } catch (e) {
      setError(e?.message || 'Failed to load AI triggers');
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  const patch = async (t, body) => {
    setSavingId(t.id);
    setError(null);
    try {
      const r = await SmartAppService.updateAiTrigger(slug, t.id, body);
      if (r?.trigger) {
        setTriggers((arr) => arr.map((x) => (x.id === t.id ? r.trigger : x)));
      } else {
        await load();
      }
    } catch (e) {
      setError(e?.message || 'Update failed');
      await load();
    } finally {
      setSavingId(null);
    }
  };

  const runNow = async (t) => {
    setRunningId(t.id);
    setRunMsg((m) => ({ ...m, [t.id]: null }));
    try {
      const r = await SmartAppService.runAiTriggerNow(slug, t.id);
      if (r?.started) {
        // Async run: the backend fires the agent in the background and returns
        // immediately, so a 20-40s run can't make the browser fetch abort. Show
        // progress and auto-refresh Run history a few times so the outcome shows.
        setRunMsg((m) => ({
          ...m,
          [t.id]: r.message || 'Run started — the result appears in Run history in a few seconds.',
        }));
        [3000, 8000, 15000, 25000].forEach((d) =>
          setTimeout(() => setHistRefresh((m) => ({ ...m, [t.id]: (m[t.id] || 0) + 1 })), d));
      } else {
        // Back-compat: a synchronous response (older backend).
        const n = r?.fired || 0;
        setRunMsg((m) => ({
          ...m,
          [t.id]: n > 0
            ? `Ran once — ${n} recommendation staged in the inbox. Run again for the next case.`
            : (t.type === 'poll'
                ? 'No new rows to process right now.'
                : 'Ran — no recommendation produced (the action proposed no write).'),
        }));
      }
    } catch (e) {
      setRunMsg((m) => ({ ...m, [t.id]: `Run failed: ${e?.message || 'error'}` }));
    } finally {
      setRunningId(null);
      // Refresh the run-history view (if expanded) so the firing shows up.
      setHistRefresh((m) => ({ ...m, [t.id]: (m[t.id] || 0) + 1 }));
    }
  };

  const copy = async (url) => {
    try {
      await Clipboard.setStringAsync(url);
      Alert.alert('Copied', 'Webhook URL copied to clipboard.');
    } catch {
      /* noop */
    }
  };

  if (loading) {
    return <View style={s.center}><ActivityIndicator color="#3B82F6" /></View>;
  }
  if (error) {
    return (
      <View style={s.center}>
        <Text style={s.err}>{error}</Text>
        <TouchableOpacity onPress={load} style={s.retry}><Text style={s.retryTxt}>Retry</Text></TouchableOpacity>
      </View>
    );
  }
  if (!triggers.length) {
    return (
      <View style={s.center}>
        <Text style={s.muted}>
          This app has no AI triggers. Ask the builder to “trigger the agent on a schedule
          or webhook” so recommendations are ready before the officer opens the inbox.
        </Text>
      </View>
    );
  }

  const anyAutonomous = triggers.some((t) => t.autonomous || t.execution_mode === 'auto_process');
  return (
    <ScrollView style={s.wrap} contentContainerStyle={{ paddingBottom: 24 }}>
      <Text style={s.h}>Automation</Text>
      <Text style={s.sub}>
        Run the app’s AI agent automatically — ahead of the click.
        {anyAutonomous
          ? ' ⚠ One or more triggers are AUTO-PROCESS — they COMMIT to your source systems with NO human approval when their policy passes. Disable to stop autonomous writes.'
          : ' Each run stages a recommendation for an officer to approve; nothing is committed automatically.'}
      </Text>
      {triggers.map((t) => {
        const saving = savingId === t.id;
        return (
          <View key={t.id} style={s.card}>
            <View style={s.titleRow}>
              <Text style={s.name}>{t.id}</Text>
              <View style={s.badge}><Text style={s.badgeTxt}>{TYPE_LABEL[t.type] || t.type}</Text></View>
              {(t.autonomous || t.execution_mode === 'auto_process') && (
                <View style={{ marginLeft: 6, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEE2E2' }}>
                  <Text style={{ fontSize: 10, color: '#B91C1C', fontWeight: '700' }}>🔴 Autonomous writes</Text>
                </View>
              )}
            </View>
            <Text style={s.desc}>Runs action: {t.action}</Text>
            {(t.autonomous || t.execution_mode === 'auto_process') && (
              <Text style={{ color: '#B91C1C', fontSize: 12, marginTop: 2 }}>
                Commits to source systems with no approval when policy passes
                {t.auto_process_policy && t.auto_process_policy.rate_limit_per_hour
                  ? ` · ceiling ${t.auto_process_policy.rate_limit_per_hour}/hr`
                  : ' · ⚠ no hourly ceiling set — cannot be enabled'}.
              </Text>
            )}

            <View style={s.row}>
              <Text style={s.label}>Active</Text>
              <Switch
                value={!!t.enabled}
                onValueChange={(v) => {
                  // Arming an autonomous trigger requires the typed-confirm ritual.
                  if (v && (t.autonomous || t.execution_mode === 'auto_process')) {
                    setArmText(''); setArming(t);
                  } else {
                    patch(t, { enabled: v });
                  }
                }}
                disabled={saving}
              />
            </View>

            <View style={s.runRow}>
              <TouchableOpacity
                style={[s.runBtn, runningId === t.id && s.runBtnOff]}
                disabled={runningId === t.id}
                onPress={() => runNow(t)}
              >
                {runningId === t.id
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Text style={s.runTxt}>Run now (one case)</Text>}
              </TouchableOpacity>
            </View>
            {!!runMsg[t.id] && <Text style={s.runMsg}>{runMsg[t.id]}</Text>}

            <TriggerRunHistory slug={slug} triggerId={t.id} refreshKey={histRefresh[t.id] || 0} />

            {t.type === 'schedule.cron' && t.enabled && (
              <CronEditor
                value={t.cron || ''}
                disabled={saving}
                onSave={(c) => patch(t, { cron: c })}
              />
            )}

            {t.type === 'schedule.interval' && t.enabled && (
              <IntervalEditor
                value={t.every_seconds || 60}
                disabled={saving}
                onSave={(n) => patch(t, { every_seconds: n })}
              />
            )}

            {t.type === 'poll' && (
              <Text style={s.hint}>
                Polls “{t.tool}” every {t.every_seconds || 60}s, dedups by “{t.dedup_key}”,
                and runs the agent once per new record.
              </Text>
            )}

            {t.type === 'webhook' && (
              <>
                {!t.has_secret && (
                  <Text style={s.warn}>
                    Webhook secret not configured (secret_ref: {t.secret_ref || '—'}) — set it
                    in the deployment before exposing this URL.
                  </Text>
                )}
                {!!t.webhook_url && (
                  <View style={s.webhookBox}>
                    <Text style={s.webhookUrl} numberOfLines={1}>{t.webhook_url}</Text>
                    <TouchableOpacity style={s.copyBtn} onPress={() => copy(t.webhook_url)}>
                      <Text style={s.copyTxt}>Copy</Text>
                    </TouchableOpacity>
                  </View>
                )}
                <Text style={s.secret}>POST here HMAC-signed (X-Citra-Signature). One call = one case.</Text>
              </>
            )}
          </View>
        );
      })}

      {/* Arming ritual — typed confirm before enabling autonomous writes. */}
      {arming && (
        <Modal visible transparent animationType="fade" onRequestClose={() => setArming(null)}>
          <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', alignItems: 'center', padding: 24 }}>
            <View style={{ backgroundColor: '#fff', borderRadius: 16, width: '100%', maxWidth: 460, padding: 20 }}>
              <Text style={{ fontSize: 16, fontWeight: '800', color: '#B91C1C' }}>⚠ Arm autonomous writes</Text>
              <Text style={{ fontSize: 13, color: '#374151', marginTop: 8 }}>
                “{arming.id}” will COMMIT to your source systems with NO human approval when its policy passes:
              </Text>
              <View style={{ backgroundColor: '#F9FAFB', borderRadius: 8, padding: 10, marginTop: 8 }}>
                <Text style={{ fontSize: 12, color: '#374151' }}>Action: {arming.action || '—'}</Text>
                {arming.auto_process_policy && arming.auto_process_policy.confidence_min != null && (
                  <Text style={{ fontSize: 12, color: '#374151' }}>Confidence ≥ {arming.auto_process_policy.confidence_min}</Text>
                )}
                {arming.auto_process_policy && arming.auto_process_policy.value_cap && (
                  <Text style={{ fontSize: 12, color: '#374151' }}>
                    Value cap: {arming.auto_process_policy.value_cap.field} ≤ {arming.auto_process_policy.value_cap.max}
                  </Text>
                )}
                <Text style={{ fontSize: 12, color: (arming.auto_process_policy && arming.auto_process_policy.rate_limit_per_hour) ? '#374151' : '#B91C1C' }}>
                  Hourly ceiling: {(arming.auto_process_policy && arming.auto_process_policy.rate_limit_per_hour) ? `${arming.auto_process_policy.rate_limit_per_hour}/hr` : 'NOT SET — enabling will be rejected'}
                </Text>
              </View>
              <Text style={{ fontSize: 12, color: '#6B7280', marginTop: 12 }}>
                Type the app id <Text style={{ fontWeight: '700', color: '#111827' }}>{slug}</Text> to confirm:
              </Text>
              <TextInput
                value={armText}
                onChangeText={setArmText}
                placeholder={slug}
                placeholderTextColor="#9CA3AF"
                autoCapitalize="none"
                style={{ borderWidth: 1, borderColor: '#D1D5DB', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, marginTop: 6, color: '#111827' }}
              />
              <View style={{ flexDirection: 'row', gap: 10, marginTop: 16 }}>
                <TouchableOpacity onPress={() => setArming(null)} style={{ flex: 1, paddingVertical: 12, borderRadius: 8, borderWidth: 1, borderColor: '#D1D5DB', alignItems: 'center' }}>
                  <Text style={{ color: '#6B7280', fontWeight: '600' }}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  disabled={armText.trim() !== slug}
                  onPress={() => { const tr = arming; setArming(null); patch(tr, { enabled: true }); }}
                  style={{ flex: 1, paddingVertical: 12, borderRadius: 8, backgroundColor: armText.trim() === slug ? '#DC2626' : '#FCA5A5', alignItems: 'center' }}
                >
                  <Text style={{ color: '#fff', fontWeight: '700' }}>Arm autonomous writes</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </Modal>
      )}
    </ScrollView>
  );
}

function CronEditor({ value, onSave, disabled }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => { setDraft(value); }, [value]);
  return (
    <View style={s.cronRow}>
      <TextInput
        style={s.cronInput}
        value={draft}
        onChangeText={setDraft}
        placeholder="0 9 * * 1   (min hour dom mon dow)"
        placeholderTextColor="#9ca3af"
        autoCapitalize="none"
        autoCorrect={false}
        editable={!disabled}
      />
      <TouchableOpacity
        style={[s.saveBtn, (disabled || draft === value) && s.saveBtnOff]}
        disabled={disabled || draft === value}
        onPress={() => onSave(draft.trim())}
      >
        <Text style={s.saveTxt}>Save</Text>
      </TouchableOpacity>
    </View>
  );
}

function _fmtTime(v) {
  if (!v) return '—';
  try {
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    return d.toLocaleString();
  } catch {
    return String(v);
  }
}

function _statusLabel(r) {
  if (!r.fired) return 'Failed';
  if (r.status === 'pending_approval') return 'Recommended';
  if (r.status === 'completed') return 'Completed';
  if (r.status === 'failed') return 'Failed';
  return r.status || 'Ran';
}

function _dotStyle(r) {
  if (!r.fired || r.status === 'failed') return { backgroundColor: '#dc2626' };
  if (r.status === 'pending_approval') return { backgroundColor: '#16a34a' };
  return { backgroundColor: '#3B82F6' };
}

// Collapsible per-trigger run history. Loads on first expand (and on
// refreshKey bump after a Run-now). Every firing — scheduler, webhook, poll,
// manual — is recorded server-side, including failures, so a trigger that has
// silently stopped producing recommendations is visible here.
function TriggerRunHistory({ slug, triggerId, refreshKey }) {
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await SmartAppService.listTriggerRuns(slug, triggerId, { limit: 25 });
      setRuns(Array.isArray(r?.runs) ? r.runs : []);
    } catch (e) {
      setErr(e?.message || 'Failed to load run history');
    } finally {
      setLoading(false);
    }
  }, [slug, triggerId]);

  useEffect(() => {
    if (open) fetchRuns();
  }, [open, refreshKey, fetchRuns]);

  return (
    <View style={s.histWrap}>
      <TouchableOpacity onPress={() => setOpen((v) => !v)}>
        <Text style={s.histToggle}>{open ? '▾ Run history' : '▸ Run history'}</Text>
      </TouchableOpacity>
      {open && (
        loading ? (
          <ActivityIndicator color="#3B82F6" style={{ marginTop: 8 }} />
        ) : err ? (
          <Text style={s.err}>{err}</Text>
        ) : !runs || !runs.length ? (
          <Text style={s.histEmpty}>No firings recorded yet.</Text>
        ) : (
          runs.map((r, i) => (
            <View key={r.trigger_run_id || r.correlation_id || `${r.created_at}-${i}`} style={s.histRow}>
              <View style={[s.histDot, _dotStyle(r)]} />
              <View style={{ flex: 1 }}>
                <Text style={s.histLine}>
                  {_fmtTime(r.created_at || r.started_at)} · {_statusLabel(r)} · {r.fired_via || 'scheduler'}
                </Text>
                {!!r.error && <Text style={s.histErr}>{r.error}</Text>}
                {!r.error && r.fired && (
                  <Text style={s.histMeta}>
                    {r.write_count ? `${r.write_count} proposed write(s)` : 'no write proposed'}
                    {r.duration_ms != null ? ` · ${Math.round(r.duration_ms)}ms` : ''}
                  </Text>
                )}
              </View>
            </View>
          ))
        )
      )}
    </View>
  );
}

function IntervalEditor({ value, onSave, disabled }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => { setDraft(String(value)); }, [value]);
  const n = parseInt(draft, 10);
  const valid = Number.isFinite(n) && n >= 30 && n <= 86400;
  return (
    <View style={s.cronRow}>
      <TextInput
        style={s.cronInput}
        value={draft}
        onChangeText={setDraft}
        placeholder="seconds (30–86400)"
        placeholderTextColor="#9ca3af"
        keyboardType="number-pad"
        editable={!disabled}
      />
      <TouchableOpacity
        style={[s.saveBtn, (disabled || !valid || String(value) === draft) && s.saveBtnOff]}
        disabled={disabled || !valid || String(value) === draft}
        onPress={() => onSave(n)}
      >
        <Text style={s.saveTxt}>Save</Text>
      </TouchableOpacity>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, padding: 16 },
  center: { padding: 24, alignItems: 'center', justifyContent: 'center' },
  h: { fontSize: 18, fontWeight: '600', color: '#0f172a', marginBottom: 4 },
  sub: { fontSize: 12, color: '#6b7280', marginBottom: 12 },
  card: { borderWidth: 1, borderColor: '#e5e7eb', borderRadius: 10, padding: 14, marginBottom: 12, backgroundColor: '#fff' },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  name: { fontSize: 15, fontWeight: '600', color: '#111827' },
  badge: { backgroundColor: '#eef2ff', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  badgeTxt: { fontSize: 11, color: '#4338ca', fontWeight: '600' },
  desc: { fontSize: 13, color: '#6b7280', marginTop: 2, marginBottom: 4 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 10 },
  label: { fontSize: 14, color: '#374151', fontWeight: '500' },
  hint: { fontSize: 12, color: '#6b7280', marginTop: 8 },
  warn: { fontSize: 11, color: '#b45309', marginTop: 8 },
  runRow: { flexDirection: 'row', marginTop: 10 },
  runBtn: { backgroundColor: '#4338ca', borderRadius: 6, paddingHorizontal: 14, paddingVertical: 9, minWidth: 150, alignItems: 'center' },
  runBtnOff: { opacity: 0.6 },
  runTxt: { color: '#fff', fontWeight: '600', fontSize: 13 },
  runMsg: { fontSize: 12, color: '#374151', marginTop: 6 },
  histWrap: { marginTop: 10, borderTopWidth: 1, borderTopColor: '#f1f5f9', paddingTop: 8 },
  histToggle: { fontSize: 12, color: '#4338ca', fontWeight: '600' },
  histEmpty: { fontSize: 12, color: '#9ca3af', marginTop: 6 },
  histRow: { flexDirection: 'row', alignItems: 'flex-start', marginTop: 8 },
  histDot: { width: 8, height: 8, borderRadius: 4, marginTop: 4, marginRight: 8 },
  histLine: { fontSize: 12, color: '#374151', fontWeight: '500' },
  histErr: { fontSize: 11, color: '#b91c1c', marginTop: 1 },
  histMeta: { fontSize: 11, color: '#6b7280', marginTop: 1 },
  cronRow: { flexDirection: 'row', alignItems: 'center', marginTop: 8 },
  cronInput: { flex: 1, borderWidth: 1, borderColor: '#d1d5db', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 8, fontSize: 13, color: '#111827', fontFamily: 'monospace' },
  saveBtn: { marginLeft: 8, backgroundColor: '#3B82F6', borderRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  saveBtnOff: { backgroundColor: '#cbd5e1' },
  saveTxt: { color: '#fff', fontWeight: '600', fontSize: 13 },
  webhookBox: { flexDirection: 'row', alignItems: 'center', marginTop: 10, backgroundColor: '#f8fafc', borderWidth: 1, borderColor: '#e5e7eb', borderRadius: 6, paddingLeft: 10 },
  webhookUrl: { flex: 1, fontSize: 12, color: '#334155', fontFamily: 'monospace' },
  copyBtn: { backgroundColor: '#0f172a', borderTopRightRadius: 6, borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  copyTxt: { color: '#fff', fontWeight: '600', fontSize: 13 },
  secret: { fontSize: 11, color: '#9ca3af', marginTop: 6 },
  muted: { fontSize: 14, color: '#6b7280', textAlign: 'center', lineHeight: 20 },
  err: { fontSize: 13, color: '#b91c1c' },
  retry: { marginTop: 10, paddingHorizontal: 14, paddingVertical: 8, borderWidth: 1, borderColor: '#d1d5db', borderRadius: 6 },
  retryTxt: { color: '#374151', fontWeight: '600' },
});
