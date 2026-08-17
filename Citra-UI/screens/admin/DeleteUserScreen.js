// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * DeleteUserScreen — admin per-resource picker for user deletion.
 *
 * Flow:
 *   1. on mount: POST /api/admin/users/:userId/preflight-delete →
 *      gets the resource table + list of SAs the admin can pick as
 *      transfer targets.
 *   2. admin picks an action per row (Keep / Transfer to SA / Transfer
 *      to dept / Make me admin / Archive / Delete).
 *   3. "Apply all" submits via POST /delete and the screen shows a
 *      progress card that polls /deletion-status until status=applied.
 *   4. Final card links to the produced HandoffReport.
 *
 * The picker is intentionally compact rather than fancy — every action
 * is reversible from the lifecycle endpoints, so admins can correct
 * misclicks afterwards.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity, ActivityIndicator,
  StyleSheet, Alert, TextInput,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import AdminUserService from '../../services/AdminUserService';

const ACTIONS = [
  { id: 'keep', label: 'Keep' },
  { id: 'transfer_to_sa', label: 'Transfer to SA →' },
  { id: 'transfer_to_dept', label: 'Transfer to dept →' },
  { id: 'make_me_admin', label: 'Make me admin' },
  { id: 'archive', label: 'Archive' },
  { id: 'delete', label: 'Delete' },
];


const DeleteUserScreen = ({ route, navigation }) => {
  const userId = route?.params?.userId;
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [requestId, setRequestId] = useState(null);
  const [resources, setResources] = useState([]);
  const [saChoices, setSaChoices] = useState([]);
  const [resultReport, setResultReport] = useState(null);
  // Phase H — personal content (presentations, reports, notes, …)
  // contentBuckets[bucket] = { total, by_collection: {col: count} }
  // contentPolicies[bucket] = 'keep' | 'transfer_to_admin' | 'delete'
  const [contentBuckets, setContentBuckets] = useState(null);
  const [contentPolicies, setContentPolicies] = useState({});

  const load = useCallback(async () => {
    try {
      const [preflight, saList] = await Promise.all([
        AdminUserService.preflightDelete(userId),
        AdminUserService.listManageableServiceAccounts().catch(() => []),
      ]);
      setRequestId(preflight.request_id);
      setResources(preflight.resources || []);
      setSaChoices(saList || []);
      // personal_content is an object: { grand_total, buckets: {name: {total, by_collection}} }
      const pc = preflight.personal_content;
      if (pc && pc.buckets) {
        setContentBuckets(pc.buckets);
        // default every bucket to 'keep' so admins consciously opt-in to
        // transfer/delete.
        const init = {};
        for (const b of Object.keys(pc.buckets)) init[b] = 'keep';
        setContentPolicies(init);
      } else {
        setContentBuckets(null);
        setContentPolicies({});
      }
      setError(null);
    } catch (err) {
      setError(err.message || 'preflight failed');
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { load(); }, [load]);

  const updateRow = (idx, patch) => {
    setResources((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const submit = async () => {
    // Validate that every transfer_to_* row has a target
    const missing = resources.filter(
      (r) => ['transfer_to_sa', 'transfer_to_dept'].includes(r.action) && !r.action_target,
    );
    if (missing.length) {
      Alert.alert('Missing target', `Pick a transfer target for: ${missing.map((m) => m.name || m.resource_id).join(', ')}`);
      return;
    }
    setSubmitting(true);
    try {
      const res = await AdminUserService.submitDelete(userId, {
        requestId,
        resourceActions: resources.map((r) => ({
          kind: r.kind,
          resource_id: r.resource_id,
          action: r.action,
          action_target: r.action_target || null,
          name: r.name,
          current_sa: r.current_sa,
          sole_admin: r.sole_admin,
        })),
        personalContentPolicies: contentPolicies,
      });
      // Poll for applied state.
      const poll = async (attempt) => {
        if (attempt > 30) {
          setError('Timed out waiting for worker. The picker has been saved — refresh later.');
          setSubmitting(false);
          return;
        }
        try {
          const status = await AdminUserService.getDeletionStatus(userId, requestId);
          const dreq = status.deletion_request || {};
          if (dreq.status === 'applied') {
            setResultReport({
              handoff_report_id: dreq.applied_handoff_report_id,
              applied_at: dreq.applied_at,
              resources: dreq.resources,
            });
            setSubmitting(false);
            return;
          }
          if (dreq.status === 'failed') {
            setError(dreq.failure_reason || 'worker reported failure');
            setSubmitting(false);
            return;
          }
        } catch (e) {
          // tolerate transient errors during polling
        }
        setTimeout(() => poll(attempt + 1), 2000);
      };
      poll(0);
    } catch (err) {
      setError(err.message || 'submission failed');
      setSubmitting(false);
    }
  };

  if (loading) return <ActivityIndicator style={{ marginTop: 40 }} />;
  if (error) {
    return (
      <View style={styles.container}>
        <Text style={styles.error}>{error}</Text>
        <TouchableOpacity style={styles.primaryBtn} onPress={load}>
          <Text style={styles.primaryBtnText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (resultReport) {
    return (
      <View style={styles.container}>
        <Ionicons name="checkmark-circle" size={48} color="#2a8" style={{ alignSelf: 'center', marginTop: 24 }} />
        <Text style={styles.successHeader}>User deletion applied</Text>
        <Text style={styles.subText}>
          HandoffReport: {resultReport.handoff_report_id}
        </Text>
        <ScrollView style={{ marginTop: 16 }}>
          {(resultReport.resources || []).map((r) => (
            <Text key={`${r.kind}:${r.resource_id}`} style={styles.summaryLine}>
              [{r.kind}] {r.name || r.resource_id} → {r.action}
              {r.action_target ? ` (${r.action_target})` : ''}
            </Text>
          ))}
        </ScrollView>
        <TouchableOpacity
          style={styles.primaryBtn}
          onPress={() => navigation.navigate('DeparturesScreen')}
        >
          <Text style={styles.primaryBtnText}>View Departures</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.header}>Delete user: {userId}</Text>
      <Text style={styles.subText}>
        Choose what happens to each resource the user owns. SA-shared resources
        default to "Keep" (other admins continue); sole-admin resources need an
        explicit reassignment.
      </Text>

      <ScrollView style={{ marginTop: 12 }}>
        {resources.length === 0 && (
          <Text style={styles.empty}>
            This user does not own any workflows or smart-apps. The
            "Delete user" submission will simply remove their account.
          </Text>
        )}
        {resources.map((r, idx) => (
          <View key={`${r.kind}:${r.resource_id}`} style={styles.card}>
            <View style={styles.cardHeader}>
              <Text style={styles.cardTitle}>
                [{r.kind}] {r.name || r.resource_id}
              </Text>
              {r.sole_admin && (
                <Text style={styles.warnBadge}>SOLE ADMIN</Text>
              )}
            </View>
            <Text style={styles.cardMeta}>
              Owning SA: {r.current_sa || '—'}
            </Text>

            <View style={styles.actionsRow}>
              {ACTIONS.map((a) => (
                <TouchableOpacity
                  key={a.id}
                  style={[
                    styles.actionChip,
                    r.action === a.id && styles.actionChipActive,
                  ]}
                  onPress={() => updateRow(idx, { action: a.id, action_target: null })}
                >
                  <Text
                    style={[
                      styles.actionChipText,
                      r.action === a.id && styles.actionChipTextActive,
                    ]}
                  >
                    {a.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {r.action === 'transfer_to_sa' && (
              <View style={styles.targetPicker}>
                <Text style={styles.targetLabel}>Target SA:</Text>
                <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                  {saChoices.map((sa) => (
                    <TouchableOpacity
                      key={sa.service_account_id}
                      style={[
                        styles.targetChip,
                        r.action_target === sa.service_account_id && styles.targetChipActive,
                      ]}
                      onPress={() => updateRow(idx, { action_target: sa.service_account_id })}
                    >
                      <Text style={styles.targetChipText}>
                        {sa.display_name || sa.service_account_id}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
              </View>
            )}

            {r.action === 'transfer_to_dept' && (
              <View style={styles.targetPicker}>
                <Text style={styles.targetLabel}>Dept ID:</Text>
                <TextInput
                  style={styles.targetInput}
                  placeholder="e.g. plant_ops"
                  value={r.action_target || ''}
                  onChangeText={(v) => updateRow(idx, { action_target: v.trim() })}
                  autoCapitalize="none"
                />
              </View>
            )}
          </View>
        ))}

        {/* Phase H — Personal content (presentations, reports, notes,
            mindmaps, pages, etc.). These don't have an SA owner, so the
            admin picks a bulk policy per content bucket. */}
        {contentBuckets && Object.keys(contentBuckets).length > 0 && (
          <View style={{ marginTop: 18 }}>
            <Text style={styles.sectionHeader}>Personal content</Text>
            <Text style={styles.subText}>
              These are documents the user authored that don't belong to a
              service account (presentations, reports, notes, etc.).
              Choose a bulk action per category.
            </Text>
            {Object.entries(contentBuckets).map(([bucket, info]) => (
              <View key={bucket} style={styles.card}>
                <View style={styles.cardHeader}>
                  <Text style={styles.cardTitle}>
                    {bucket} · {info.total} item{info.total === 1 ? '' : 's'}
                  </Text>
                </View>
                {info.by_collection && (
                  <Text style={styles.cardMeta} numberOfLines={2}>
                    {Object.entries(info.by_collection)
                      .filter(([, n]) => n > 0)
                      .map(([col, n]) => `${col}:${n}`)
                      .join(', ') || '(empty)'}
                  </Text>
                )}
                <View style={styles.actionsRow}>
                  {[
                    { id: 'keep', label: 'Keep' },
                    { id: 'transfer_to_admin', label: 'Transfer to me' },
                    { id: 'delete', label: 'Delete' },
                  ].map((p) => (
                    <TouchableOpacity
                      key={p.id}
                      style={[
                        styles.actionChip,
                        contentPolicies[bucket] === p.id && styles.actionChipActive,
                      ]}
                      onPress={() =>
                        setContentPolicies((prev) => ({ ...prev, [bucket]: p.id }))
                      }
                    >
                      <Text
                        style={[
                          styles.actionChipText,
                          contentPolicies[bucket] === p.id && styles.actionChipTextActive,
                        ]}
                      >
                        {p.label}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            ))}
          </View>
        )}
      </ScrollView>

      <TouchableOpacity
        style={[styles.primaryBtn, submitting && { opacity: 0.6 }]}
        onPress={submit}
        disabled={submitting}
      >
        {submitting ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.primaryBtnText}>Apply &amp; delete user</Text>
        )}
      </TouchableOpacity>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16, backgroundColor: '#fff' },
  header: { fontSize: 18, fontWeight: '600', color: '#222' },
  sectionHeader: { fontSize: 16, fontWeight: '600', color: '#222', marginBottom: 4 },
  subText: { fontSize: 13, color: '#666', marginTop: 4 },
  successHeader: { fontSize: 20, fontWeight: '600', textAlign: 'center', marginTop: 8 },
  summaryLine: { fontSize: 13, color: '#444', marginVertical: 2 },
  empty: { color: '#888', textAlign: 'center', marginTop: 24 },
  card: {
    borderWidth: 1, borderColor: '#eee', borderRadius: 8,
    padding: 12, marginBottom: 12, backgroundColor: '#fafafa',
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center' },
  cardTitle: { flex: 1, fontWeight: '500', fontSize: 14 },
  cardMeta: { fontSize: 12, color: '#666', marginTop: 4 },
  warnBadge: {
    fontSize: 10, fontWeight: '600',
    color: '#a33', backgroundColor: '#fdd',
    paddingHorizontal: 6, paddingVertical: 2, borderRadius: 3,
  },
  actionsRow: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 8 },
  actionChip: {
    paddingHorizontal: 10, paddingVertical: 5,
    borderWidth: 1, borderColor: '#ccc', borderRadius: 4,
    marginRight: 6, marginTop: 6,
  },
  actionChipActive: { backgroundColor: '#226', borderColor: '#226' },
  actionChipText: { color: '#444', fontSize: 12 },
  actionChipTextActive: { color: '#fff' },
  targetPicker: { marginTop: 8 },
  targetLabel: { fontSize: 12, color: '#444', marginBottom: 4 },
  targetChip: {
    paddingHorizontal: 8, paddingVertical: 5,
    borderWidth: 1, borderColor: '#ccc', borderRadius: 4, marginRight: 6,
  },
  targetChipActive: { backgroundColor: '#226' },
  targetChipText: { color: '#444', fontSize: 11 },
  targetInput: {
    borderWidth: 1, borderColor: '#ccc', borderRadius: 4,
    padding: 6, fontSize: 13,
  },
  primaryBtn: {
    backgroundColor: '#c44', padding: 12, borderRadius: 6,
    alignItems: 'center', marginTop: 12,
  },
  primaryBtnText: { color: '#fff', fontWeight: '600' },
  error: { color: '#c44', marginTop: 24, textAlign: 'center' },
});

export default DeleteUserScreen;
