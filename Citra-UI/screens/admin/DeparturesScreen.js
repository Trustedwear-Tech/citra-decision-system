// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * DeparturesScreen — read-only HandoffReport browser.
 *
 * Admins can audit past user deletions here: who was deleted, by whom,
 * what happened to each of their resources, and whether anything
 * errored. Each row links to the detailed handoff doc.
 *
 * The /api/handoff-reports backend endpoint is a future surface (the
 * HandoffReports collection is currently populated by Citra-Worker but
 * not yet exposed via REST). When the endpoint is unavailable the
 * screen degrades to an empty state with a hint.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, FlatList, ActivityIndicator, RefreshControl,
  TouchableOpacity, StyleSheet,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import AdminUserService from '../../services/AdminUserService';


const DeparturesScreen = () => {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const list = await AdminUserService.listHandoffReports({});
      setReports(list || []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefreshing(true); load(); };

  const renderReport = ({ item }) => {
    const summary = item.summary || {};
    const errs = summary.errors || 0;
    return (
      <View style={[styles.card, errs > 0 && styles.cardWithErrors]}>
        <View style={styles.cardHeader}>
          <Ionicons
            name={errs > 0 ? 'warning-outline' : 'checkmark-circle-outline'}
            size={20}
            color={errs > 0 ? '#c80' : '#2a8'}
          />
          <Text style={styles.cardTitle}>
            {(item.deactivated_user && item.deactivated_user.email) || '(unknown user)'}
          </Text>
        </View>
        <Text style={styles.meta}>
          deleted by {item.deactivated_by || '?'} · {new Date(item.deactivated_at || item.created_at).toLocaleString()}
        </Text>
        <Text style={styles.summary}>
          {summary.total || 0} resources
          {summary.transferred_to_sa ? ` · ${summary.transferred_to_sa} → SA` : ''}
          {summary.transferred_to_dept ? ` · ${summary.transferred_to_dept} → dept` : ''}
          {summary.transferred_to_org ? ` · ${summary.transferred_to_org} → org` : ''}
          {summary.archived ? ` · ${summary.archived} archived` : ''}
          {summary.deleted ? ` · ${summary.deleted} deleted` : ''}
          {errs > 0 ? ` · ${errs} errors` : ''}
        </Text>
        <Text style={styles.idLine}>report: {item.report_id}</Text>
      </View>
    );
  };

  return (
    <View style={styles.container}>
      <Text style={styles.header}>Departures (handoff reports)</Text>
      <Text style={styles.subText}>
        Past user deletions and what happened to their workflows + smart-apps.
      </Text>
      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} />
      ) : error ? (
        <Text style={styles.error}>
          Couldn't load HandoffReports: {error}.
          {'\n'}(The /api/handoff-reports endpoint may not be live yet — see Phase F notes.)
        </Text>
      ) : (
        <FlatList
          data={reports}
          keyExtractor={(r) => r.report_id || `${r.deactivated_at}:${(r.deactivated_user && r.deactivated_user.email) || '?'}`}
          renderItem={renderReport}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          ListEmptyComponent={
            <Text style={styles.empty}>No departures recorded yet.</Text>
          }
        />
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16, backgroundColor: '#fff' },
  header: { fontSize: 18, fontWeight: '600', color: '#222' },
  subText: { fontSize: 12, color: '#666', marginTop: 4, marginBottom: 12 },
  card: {
    borderWidth: 1, borderColor: '#eee', borderRadius: 8,
    padding: 12, marginBottom: 10, backgroundColor: '#fafafa',
  },
  cardWithErrors: { borderColor: '#fc8' },
  cardHeader: { flexDirection: 'row', alignItems: 'center' },
  cardTitle: { marginLeft: 8, fontSize: 14, fontWeight: '500' },
  meta: { fontSize: 12, color: '#666', marginTop: 4 },
  summary: { fontSize: 12, color: '#333', marginTop: 6 },
  idLine: { fontSize: 11, color: '#aaa', marginTop: 4 },
  empty: { textAlign: 'center', color: '#888', marginTop: 40 },
  error: { color: '#c44', marginTop: 24, textAlign: 'center' },
});

export default DeparturesScreen;
