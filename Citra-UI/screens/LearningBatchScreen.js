// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * LearningBatchScreen — admin control for clause consolidation
 * (docs/clause-memory-graph-plan.md §9.1, §19).
 *
 * The job this panel controls REPLACED a synchronous summarizer that reran an
 * LLM rewrite of the whole rubric inside every officer's approve/reject
 * request. Moving that work into a batch is the reason it needs an operator
 * surface at all: work that is no longer visible in a request trace has to be
 * visible somewhere.
 *
 * Two honesty rules mirrored from the server, never fudged in render:
 *   - Pausing is LOSSLESS. Corrections keep queuing (consumed_by: null) and
 *     fold on the next unpaused pass. This is NOT a kill switch: nothing an
 *     officer does changes, so it must never be worded like an outage.
 *   - A failed queue read renders as "couldn't read the queue", never as an
 *     empty queue. "Nothing pending" and "we don't know" are different facts.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator, ScrollView, Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import SmartAppService from '../services/SmartAppService';

const GREEN = '#16A34A';
const AMBER = '#D97706';
const RED = '#DC2626';

const fmtWhen = (v) => {
  if (!v) return 'never';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
};

export default function LearningBatchScreen({ visible, onClose, theme }) {
  const colors = theme || {};
  const bg = colors.background || '#FFFFFF';
  const surface = colors.surface || '#F9FAFB';
  const text = colors.text || '#111827';
  const sub = colors.textSecondary || '#6B7280';
  const border = colors.border || '#E5E7EB';
  const primary = colors.primary || '#3B82F6';

  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState(null);
  // Inline result line — NOT Alert.alert. RN-web maps Alert to window.alert,
  // which embedded browsers suppress entirely, so a completed pass looked
  // like a dead button there (found live, same failure class as the
  // impersonation picker's window.confirm).
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await SmartAppService.consolidationStatus());
    } catch (e) {
      setError(e?.message || 'Could not load the learning batch status.');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (visible) load(); }, [visible, load]);

  const togglePause = useCallback(async () => {
    if (!data) return;
    const next = !data.paused;
    setBusy(true);
    try {
      await SmartAppService.setConsolidationPaused(
        next, next ? 'Paused from the admin console' : '');
      setNotice(null);
      await load();
    } catch (e) {
      setNotice({ tone: 'error',
        text: `Could not change the pause state: ${e?.message || 'unknown error'}` });
    } finally {
      setBusy(false);
    }
  }, [data, load]);

  const runNow = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await SmartAppService.runConsolidationNow();
      const r = res?.result || {};
      if (r.paused) {
        setNotice({ tone: 'warn',
          text: 'Batch is paused — resume it first; running while paused would make the pause meaningless.' });
      } else {
        setNotice({ tone: r.errors ? 'warn' : 'ok',
          text: `Pass complete: ${r.buckets || 0} bucket(s) · ${r.created || 0} new judgement(s) · ` +
            `${r.reinforced || 0} reinforced · ${r.skipped || 0} skipped` +
            (r.errors ? ` · ${r.errors} error(s) — check the service logs` : '') });
      }
      await load();
    } catch (e) {
      setNotice({ tone: 'error', text: `Run failed: ${e?.message || 'unknown error'}` });
    } finally {
      setBusy(false);
    }
  }, [load]);

  const paused = !!data?.paused;
  const queueUnknown = !!data?.queue_read_failed;

  const Tile = ({ label, value, color }) => (
    <View style={{
      flex: 1, minWidth: 120, backgroundColor: surface, borderRadius: 12,
      borderWidth: 1, borderColor: border, padding: 14, margin: 4,
    }}>
      <Text style={{ fontSize: 22, fontWeight: '700', color: color || text }}>{value}</Text>
      <Text style={{ fontSize: 12, color: sub, marginTop: 2 }}>{label}</Text>
    </View>
  );

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: bg }}>
        <View style={{
          flexDirection: 'row', alignItems: 'center', padding: 16,
          borderBottomWidth: 1, borderBottomColor: border,
        }}>
          <Ionicons name="school-outline" size={22} color={primary} />
          <Text style={{ fontSize: 18, fontWeight: '700', color: text, marginLeft: 10, flex: 1 }}>
            Learning Batch
          </Text>
          <TouchableOpacity onPress={onClose} accessibilityLabel="Close">
            <Ionicons name="close" size={24} color={sub} />
          </TouchableOpacity>
        </View>

        <ScrollView contentContainerStyle={{ padding: 12 }}>
          <Text style={{ fontSize: 13, color: sub, paddingHorizontal: 4, marginBottom: 12 }}>
            Folds your officers' corrections into the judgements your apps
            apply — your SOP stays the rules, and always wins. It runs in the
            background so it never slows down an approval.
          </Text>

          {loading && <ActivityIndicator color={primary} style={{ marginTop: 24 }} />}

          {!!error && (
            <View style={{
              backgroundColor: '#FEF2F2', borderRadius: 12, borderWidth: 1,
              borderColor: '#FECACA', padding: 14, marginBottom: 12,
            }}>
              <Text style={{ color: RED, fontSize: 13 }}>{error}</Text>
            </View>
          )}

          {!loading && data && (
            <>
              <View style={{
                flexDirection: 'row', alignItems: 'center', backgroundColor: surface,
                borderRadius: 12, borderWidth: 1,
                borderColor: paused ? '#FCD34D' : border, padding: 14, marginBottom: 8,
              }}>
                <Ionicons
                  name={paused ? 'pause-circle-outline' : 'play-circle-outline'}
                  size={22} color={paused ? AMBER : GREEN} />
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={{ fontSize: 15, fontWeight: '600', color: text }}>
                    {paused ? 'Paused' : 'Running'}
                  </Text>
                  <Text style={{ fontSize: 12, color: sub, marginTop: 2 }}>
                    {paused
                      ? 'Feedback is still being collected — it will be folded in when you resume. Nothing else is affected.'
                      : `Checks every ${Math.round((data.interval_seconds || 900) / 60)} min · last ran ${fmtWhen(data.last_pass_at)}`}
                  </Text>
                </View>
                <TouchableOpacity
                  onPress={togglePause} disabled={busy}
                  style={{
                    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8,
                    borderWidth: 1, borderColor: border, opacity: busy ? 0.5 : 1,
                  }}>
                  <Text style={{ color: paused ? GREEN : AMBER, fontWeight: '600', fontSize: 13 }}>
                    {paused ? 'Resume' : 'Pause'}
                  </Text>
                </TouchableOpacity>
              </View>

              <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 8 }}>
                <Tile
                  label="Feedback waiting"
                  value={queueUnknown ? '—' : (data.pending_total ?? 0)} />
                <Tile
                  label="Ready to fold"
                  value={queueUnknown ? '—' : (data.due_buckets ?? 0)}
                  color={(data.due_buckets || 0) > 0 ? AMBER : undefined} />
                <Tile
                  label="Judgements added last run"
                  value={data.last_pass?.created ?? 0} color={GREEN} />
                <Tile
                  label="Reinforced last run"
                  value={data.last_pass?.reinforced ?? 0} />
              </View>

              {queueUnknown && (
                <View style={{
                  backgroundColor: '#FEF2F2', borderRadius: 12, borderWidth: 1,
                  borderColor: '#FECACA', padding: 14, marginBottom: 8,
                }}>
                  <Text style={{ color: RED, fontSize: 13 }}>
                    Couldn't read the queue, so these counts are unknown — this is
                    not the same as "nothing is waiting". Check the service logs.
                  </Text>
                </View>
              )}

              {!queueUnknown && data.queue_partial && (
                <View style={{
                  backgroundColor: '#FFFBEB', borderRadius: 12, borderWidth: 1,
                  borderColor: '#FDE68A', padding: 14, marginBottom: 8,
                }}>
                  <Text style={{ color: AMBER, fontSize: 13 }}>
                    One environment didn't answer, so the totals above are a
                    partial count — the real backlog may be larger.
                  </Text>
                </View>
              )}

              {!!notice && (
                <View style={{
                  backgroundColor: notice.tone === 'ok' ? '#F0FDF4'
                    : notice.tone === 'warn' ? '#FFFBEB' : '#FEF2F2',
                  borderRadius: 12, borderWidth: 1,
                  borderColor: notice.tone === 'ok' ? '#BBF7D0'
                    : notice.tone === 'warn' ? '#FDE68A' : '#FECACA',
                  padding: 14, marginBottom: 8,
                }}>
                  <Text style={{
                    color: notice.tone === 'ok' ? GREEN
                      : notice.tone === 'warn' ? AMBER : RED,
                    fontSize: 13,
                  }}>{notice.text}</Text>
                </View>
              )}

              <TouchableOpacity
                onPress={runNow} disabled={busy || paused}
                style={{
                  backgroundColor: (busy || paused) ? border : primary,
                  borderRadius: 10, padding: 14, alignItems: 'center', marginBottom: 16,
                }}>
                <Text style={{ color: '#FFFFFF', fontWeight: '700', fontSize: 14 }}>
                  {busy ? 'Working…' : 'Fold feedback now'}
                </Text>
              </TouchableOpacity>

              <Text style={{
                fontSize: 13, fontWeight: '700', color: text,
                paddingHorizontal: 4, marginBottom: 6,
              }}>
                Waiting by app
              </Text>
              {(data.buckets || []).length === 0 && !queueUnknown && (
                <Text style={{ fontSize: 13, color: sub, padding: 14 }}>
                  Nothing waiting — every correction has been folded in.
                </Text>
              )}
              {(data.buckets || []).map((b, i) => (
                <View key={`${b.app_slug}-${b.modality}-${b.task_type}-${i}`} style={{
                  flexDirection: 'row', alignItems: 'center', backgroundColor: surface,
                  borderRadius: 10, borderWidth: 1, borderColor: border,
                  padding: 12, marginBottom: 6,
                }}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontSize: 14, fontWeight: '600', color: text }}>
                      {b.app_slug}
                    </Text>
                    <Text style={{ fontSize: 12, color: sub, marginTop: 2 }}>
                      {b.env === 'test' ? 'Test · ' : ''}
                      {b.modality} · {b.task_type} · oldest {fmtWhen(b.oldest)}
                    </Text>
                  </View>
                  <Text style={{
                    fontSize: 15, fontWeight: '700',
                    color: b.due ? AMBER : sub, marginRight: 8,
                  }}>
                    {b.pending}
                  </Text>
                  {b.due && <Ionicons name="ellipse" size={8} color={AMBER} />}
                </View>
              ))}
            </>
          )}
        </ScrollView>
      </View>
    </Modal>
  );
}
