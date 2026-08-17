// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * MoneyImpactScreen — admin "Money impact" panel
 * (docs/money-saved-roi-plan.md V4).
 *
 * One question, answered defensibly: how much money did decisions made
 * through Citra recover / prevent, by the customer's OWN pre-agreed
 * definition?
 *
 * Honesty rules mirrored from the server (never fudge them in render):
 *   - Every number is a sum of poller-stamped outcome.value amounts computed
 *     by the ontology's FROZEN value_semantics definition — the UI never
 *     recomputes money.
 *   - The definition versions + attribution rules that produced the numbers
 *     are shown in the footer — the headline is traceable.
 *   - value_errors (decisions whose value could NOT be stamped) are surfaced,
 *     not hidden — a zero always means "genuinely nothing", never "it broke".
 *   - No invented baselines: with no stamped values yet the server's note is
 *     shown verbatim.
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

const PERIODS = [
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'all', label: 'All time' },
];

const KIND_META = {
  recovered: { label: 'Recovered', icon: 'cash-outline', accent: GREEN, hint: 'money actually collected after a decision' },
  prevented_loss: { label: 'Loss prevented', icon: 'shield-checkmark-outline', accent: GREEN, hint: 'exposure blocked, frozen at decision time' },
  sanctioned: { label: 'Sanctioned', icon: 'checkmark-circle-outline', accent: AMBER, hint: 'amounts approved for disbursal' },
  settled: { label: 'Settled', icon: 'document-text-outline', accent: AMBER, hint: 'amounts paid out on settled cases' },
};

const ATTRIBUTION_LABEL = {
  approved_recommendation: 'officer-approved AI recommendations only',
  approved_within_window: 'approved recommendations within the agreed window',
  any_citra_touched: 'every decision recorded through Citra',
};

function fmtMoney(amount, currency) {
  if (amount === null || amount === undefined) return '—';
  try {
    return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
      style: 'currency', currency: currency || 'INR',
      notation: Math.abs(amount) >= 100000 ? 'compact' : 'standard',
      maximumFractionDigits: Math.abs(amount) >= 100000 ? 2 : 0,
    }).format(amount);
  } catch {
    return `${currency || ''} ${Math.round(amount).toLocaleString()}`;
  }
}

export default function MoneyImpactScreen({ visible, onClose, theme }) {
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

  const load = useCallback(async (p) => {
    setLoading(true); setError('');
    try {
      setStats(await DecisionStatsService.getOrgValueStats(p));
    } catch (e) {
      setStats(null);
      setError(e?.status === 403
        ? 'Money impact is admin-only.'
        : (e?.message || 'Could not load money impact.'));
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (visible) load(period);
  }, [visible, period, load]);

  if (!visible) return null;

  const totals = stats?.totals || [];
  const byApp = stats?.by_app || [];
  const attributions = stats?.attributions || [];
  const versions = stats?.definition_versions || [];

  return (
    <Modal visible transparent={false} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: border }}>
          <Ionicons name="cash-outline" size={22} color={primary} />
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 18, fontWeight: '800', color: text }}>Money impact</Text>
            <Text style={{ fontSize: 12, color: sub }}>
              Value recovered or protected through decisions made here — by your own agreed definition.
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
          {!loading && !error && stats && (
            <>
              {totals.length === 0 && (
                <Text style={{ fontSize: 13, color: sub, marginTop: 16, lineHeight: 19 }}>
                  {stats.note || 'No stamped values in this period.'}
                </Text>
              )}

              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
                {totals.map((t) => {
                  const meta = KIND_META[t.kind] || { label: t.kind, icon: 'cash-outline', accent: text, hint: '' };
                  return (
                    <View key={`${t.kind}:${t.currency}`} style={{ flexGrow: 1, flexBasis: 150, borderWidth: 1, borderColor: border, borderRadius: 12, padding: 12, backgroundColor: surface }}>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                        <Ionicons name={meta.icon} size={14} color={meta.accent} />
                        <Text style={{ fontSize: 12, fontWeight: '700', color: text }}>{meta.label}</Text>
                      </View>
                      <Text style={{ fontSize: 22, fontWeight: '800', color: meta.accent, marginTop: 4 }}>
                        {fmtMoney(t.amount, t.currency)}
                      </Text>
                      <Text style={{ fontSize: 10, color: sub, marginTop: 2 }}>
                        {t.decisions} decision{t.decisions === 1 ? '' : 's'}{meta.hint ? ` · ${meta.hint}` : ''}
                      </Text>
                    </View>
                  );
                })}
              </View>

              {/* Unstampable values are shown, never hidden — a zero means
                  "genuinely nothing", not "the read failed". */}
              {stats.value_errors ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: 1, borderColor: `${AMBER}66`, backgroundColor: `${AMBER}0D`, borderRadius: 10, padding: 10, marginTop: 12 }}>
                  <Ionicons name="warning-outline" size={15} color={AMBER} />
                  <Text style={{ flex: 1, fontSize: 12, color: text }}>
                    {stats.value_errors} decision{stats.value_errors === 1 ? '' : 's'} could not be valued
                    (source read failed or amount unparseable) — excluded from every total above.
                  </Text>
                </View>
              ) : null}

              {byApp.length > 0 && (
                <>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 18, marginBottom: 8 }}>
                    <Ionicons name="apps-outline" size={15} color={sub} />
                    <Text style={{ fontSize: 13, fontWeight: '800', color: text }}>By app</Text>
                  </View>
                  {byApp.map((a) => {
                    const meta = KIND_META[a.kind] || { label: a.kind, accent: text };
                    return (
                      <View key={`${a.slug}:${a.kind}:${a.currency}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: 1, borderColor: border, borderRadius: 10, padding: 12, marginBottom: 8, backgroundColor: bg }}>
                        <Text style={{ flex: 1, fontSize: 13, fontWeight: '700', color: text }} numberOfLines={1}>{a.slug}</Text>
                        <Text style={{ fontSize: 11, color: sub }}>{meta.label}</Text>
                        <Text style={{ fontSize: 13, fontWeight: '800', color: meta.accent }}>{fmtMoney(a.amount, a.currency)}</Text>
                        <Text style={{ fontSize: 11, color: sub }}>{a.decisions}×</Text>
                      </View>
                    );
                  })}
                </>
              )}

              {/* The defensibility footer — which rules and definitions
                  produced the numbers above. */}
              {(attributions.length > 0 || versions.length > 0) && (
                <View style={{ borderWidth: 1, borderColor: `${primary}55`, backgroundColor: `${primary}0D`, borderRadius: 10, padding: 12, marginTop: 14 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                    <Ionicons name="ribbon-outline" size={15} color={primary} />
                    <Text style={{ fontSize: 13, fontWeight: '800', color: text }}>How these numbers are counted</Text>
                  </View>
                  {attributions.map((a) => (
                    <Text key={a} style={{ fontSize: 12, color: text, marginTop: 6 }}>
                      • Counts {ATTRIBUTION_LABEL[a] || a}.
                    </Text>
                  ))}
                  {versions.length > 0 && (
                    <Text style={{ fontSize: 10.5, color: sub, marginTop: 6 }}>
                      Definition version{versions.length === 1 ? '' : 's'}: {versions.join(', ')} — frozen in the
                      data ontology on day zero; any change produces a new version, so the metric can’t silently move.
                    </Text>
                  )}
                </View>
              )}
            </>
          )}
        </ScrollView>
      </View>
    </Modal>
  );
}
