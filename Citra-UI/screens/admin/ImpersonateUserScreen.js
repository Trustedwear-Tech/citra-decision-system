// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * ImpersonateUserScreen — super_admin "Login as demo persona" picker.
 *
 * This is the demo workflow. Citra is sold as a platform; we provision a
 * demo tenant (acme-cement, etc.) per prospective customer with realistic
 * personas + data seeded by demo-data/. A Citra super_admin opens this
 * screen, picks a persona inside the prospect's tenant, and walks them
 * through their own company's experience.
 *
 * Two browsing modes:
 *   Mode "demo"  (default) — calls /impersonation-candidates which
 *                            returns only is_demo=true tenants, grouped
 *                            by org. The natural demo flow.
 *   Mode "all"             — calls /users which lists every user in the
 *                            caller's scope. Used for debugging real
 *                            tenants on request.
 *
 * Gating: super_admin upstream + backend re-checks. The mint endpoint
 * (POST /:userId/impersonation-token) returns 403 otherwise.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
  StyleSheet,
  Alert,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import AdminUserService from '../../services/AdminUserService';
import { useUser } from '../../components/UserProvider';


const ImpersonateUserScreen = ({ onClose }) => {
  const { impersonate, user: currentUser } = useUser();

  // Demo orgs (grouped). Used in mode='demo'.
  const [orgs, setOrgs] = useState([]);
  // Flat user list. Used in mode='all'.
  const [users, setUsers] = useState([]);
  const [mode, setMode] = useState('demo');

  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [busyEmail, setBusyEmail] = useState(null);
  // Web two-step confirm: which row is ARMED awaiting its second click.
  // Native dialogs (window.confirm / window.alert) are suppressed in embedded
  // browsers (webviews, kiosk shells, automation panes) — confirm() returns
  // false there, so the old dialog flow silently aborted and "Login as"
  // appeared dead. An in-DOM confirm cannot be suppressed by any host.
  const [armedEmail, setArmedEmail] = useState(null);
  const armTimer = React.useRef(null);
  useEffect(() => () => armTimer.current && clearTimeout(armTimer.current), []);

  const load = useCallback(async () => {
    try {
      if (mode === 'demo') {
        const list = await AdminUserService.listImpersonationCandidates();
        setOrgs(list);
      } else {
        const list = await AdminUserService.listUsers({ q, limit: 200 });
        setUsers(list);
      }
      setError(null);
    } catch (err) {
      setError(err.message || 'failed to load users');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [q, mode]);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefreshing(true); load(); };

  // Flatten + filter the org-grouped demo list into rows the same FlatList
  // can render, interleaving section headers between tenant groups.
  const rows = useMemo(() => {
    if (mode !== 'demo') return null;
    const needle = q.trim().toLowerCase();
    const out = [];
    for (const org of orgs) {
      const matched = needle
        ? org.users.filter(u =>
            (u.email || '').toLowerCase().includes(needle) ||
            (u.name || '').toLowerCase().includes(needle))
        : org.users;
      if (!matched.length) continue;
      out.push({ kind: 'header', key: `h-${org.id}`, org });
      for (const u of matched) out.push({ kind: 'user', key: u.email, user: u });
    }
    return out;
  }, [orgs, q, mode]);

  const handleLoginAs = async (target) => {
    if (currentUser && target.email === currentUser.email) {
      if (Platform.OS === 'web') {
        setError('Cannot impersonate yourself — pick a different user.');
      } else {
        Alert.alert('Cannot impersonate yourself', 'Pick a different user.');
      }
      return;
    }
    if (target.deletion_state === 'deleted') {
      if (Platform.OS === 'web') {
        setError('Account deleted — cannot impersonate a deleted user.');
      } else {
        Alert.alert('Account deleted', 'Cannot impersonate a deleted user.');
      }
      return;
    }

    // Confirmation. On web this is a TWO-STEP INLINE confirm (first click arms
    // the row, second click proceeds, auto-disarms after 6s). Deliberately NOT
    // window.confirm: native dialogs are suppressed in embedded browsers
    // (webviews, kiosk shells, automation panes) where confirm() returns false
    // — the old flow silently aborted there and the button looked dead. On
    // native, RN's Alert with buttons works and stays.
    if (Platform.OS === 'web') {
      if (armedEmail !== target.email) {
        setArmedEmail(target.email);
        if (armTimer.current) clearTimeout(armTimer.current);
        armTimer.current = setTimeout(() => setArmedEmail(null), 6000);
        return;
      }
      if (armTimer.current) clearTimeout(armTimer.current);
      setArmedEmail(null);
    } else {
      const proceed = await new Promise((resolve) => {
        Alert.alert(
          `Login as ${target.email}?`,
          'You will be acting on this user\'s behalf for the next hour. The ' +
          'session is audited; downstream services see your email as the actor.',
          [
            { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
            { text: 'Continue', style: 'default', onPress: () => resolve(true) },
          ],
          { cancelable: true, onDismiss: () => resolve(false) },
        );
      });
      if (!proceed) return;
    }

    setBusyEmail(target.email);
    let result;
    try {
      result = await impersonate(target.email, 'admin picker');
    } catch (err) {
      result = { success: false, error: err?.message || String(err) };
    }
    setBusyEmail(null);

    if (!result || !result.success) {
      const msg = (result && result.error) || 'unknown error';
      if (Platform.OS === 'web') {
        // In-DOM banner, not window.alert — suppressed in embedded browsers,
        // and a failure the admin never sees reads as a dead button.
        setError(`Impersonation failed: ${msg}`);
      } else {
        Alert.alert('Impersonation failed', msg);
      }
      return;
    }
    // Close the picker — banner takes over from here.
    if (typeof onClose === 'function') onClose();
  };

  const renderUserRow = (item) => {
    const isSelf = currentUser && item.email === currentUser.email;
    const isBusy = busyEmail === item.email;
    const isDisabled = isSelf || item.deletion_state === 'deleted' || isBusy;
    const isArmed = armedEmail === item.email;
    return (
      <View style={styles.row}>
        <View style={{ flex: 1 }}>
          <Text style={styles.email}>{item.name ? `${item.name} — ${item.email}` : item.email}</Text>
          <Text style={styles.meta}>
            {(item.roles || []).join(', ') || 'user'}
            {item.dept_ids?.length ? ` · dept: ${item.dept_ids.join(', ')}` : ''}
            {item.org_id ? ` · org: ${item.org_id}` : ''}
          </Text>
          {isSelf && <Text style={styles.selfBadge}>you</Text>}
          {item.deletion_state && item.deletion_state !== 'active' && (
            <Text style={styles.stateBadge}>{item.deletion_state}</Text>
          )}
          {isArmed && (
            <Text style={styles.armedNote}>
              1-hour audited session as this user — click again to confirm.
            </Text>
          )}
        </View>
        <TouchableOpacity
          style={[styles.loginAsBtn, isArmed && styles.loginAsBtnArmed,
                  isDisabled && styles.loginAsBtnDisabled]}
          onPress={() => handleLoginAs(item)}
          disabled={isDisabled}
        >
          {isBusy ? (
            <ActivityIndicator size="small" color="#2563EB" />
          ) : (
            <>
              <Ionicons
                name={isArmed ? 'checkmark-circle-outline' : 'enter-outline'}
                size={18}
                color={isDisabled ? '#999' : isArmed ? '#B45309' : '#2563EB'} />
              <Text style={[styles.loginAsBtnText, isArmed && styles.loginAsBtnTextArmed,
                            isDisabled && { color: '#999' }]}>
                {isArmed ? 'Confirm' : 'Login as'}
              </Text>
            </>
          )}
        </TouchableOpacity>
      </View>
    );
  };

  const renderRow = ({ item }) => {
    if (mode === 'demo' && item.kind === 'header') {
      return (
        <View style={styles.orgHeader}>
          <Ionicons name="business" size={16} color="#444" />
          <Text style={styles.orgHeaderText}>
            {item.org.name || item.org.id}
          </Text>
          <Text style={styles.orgHeaderMeta}>
            {item.org.domain || item.org.id} · {item.org.users.length} persona{item.org.users.length === 1 ? '' : 's'}
          </Text>
        </View>
      );
    }
    if (mode === 'demo') return renderUserRow(item.user);
    return renderUserRow(item);
  };

  const flatData = mode === 'demo' ? (rows || []) : users;
  const keyExtractor = (item) =>
    mode === 'demo' ? item.key : item.email;

  return (
    <View style={styles.container}>
      <View style={styles.notice}>
        <Ionicons name="information-circle-outline" size={16} color="#7C2D12" />
        <Text style={styles.noticeText}>
          Impersonation sessions last 1 hour and are audited. Downstream
          services see your email as the actor.
        </Text>
      </View>

      <View style={styles.tabsRow}>
        <TouchableOpacity
          onPress={() => setMode('demo')}
          style={[styles.tab, mode === 'demo' && styles.tabActive]}
        >
          <Text style={[styles.tabText, mode === 'demo' && styles.tabTextActive]}>
            Demo personas
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => setMode('all')}
          style={[styles.tab, mode === 'all' && styles.tabActive]}
        >
          <Text style={[styles.tabText, mode === 'all' && styles.tabTextActive]}>
            All users
          </Text>
        </TouchableOpacity>
      </View>

      <View style={styles.searchRow}>
        <Ionicons name="search" size={16} color="#888" />
        <TextInput
          style={styles.searchInput}
          placeholder={mode === 'demo' ? 'Search demo personas…' : 'Search all users…'}
          value={q}
          onChangeText={setQ}
          onSubmitEditing={load}
          autoCapitalize="none"
        />
      </View>
      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} />
      ) : error ? (
        <Text style={styles.error}>{error}</Text>
      ) : (
        <FlatList
          data={flatData}
          keyExtractor={keyExtractor}
          renderItem={renderRow}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>
              {mode === 'demo'
                ? 'No demo tenants seeded yet. Run demo-data/scripts/seed_demo_users.py.'
                : 'No users in your scope.'}
            </Text>
          }
        />
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  notice: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 10,
    backgroundColor: '#FEF3C7',
    borderBottomWidth: 1,
    borderColor: '#FCD34D',
  },
  noticeText: { color: '#7C2D12', fontSize: 12, marginLeft: 6, flex: 1 },
  tabsRow: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderColor: '#eee',
  },
  tab: {
    flex: 1,
    paddingVertical: 10,
    alignItems: 'center',
    borderBottomWidth: 2,
    borderColor: 'transparent',
  },
  tabActive: { borderColor: '#2563EB' },
  tabText: { color: '#555', fontSize: 13, fontWeight: '500' },
  tabTextActive: { color: '#2563EB' },
  orgHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: '#F3F4F6',
    borderBottomWidth: 1,
    borderColor: '#E5E7EB',
  },
  orgHeaderText: { fontSize: 13, fontWeight: '600', color: '#222', marginLeft: 6 },
  orgHeaderMeta: { marginLeft: 8, fontSize: 11, color: '#777' },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    borderBottomWidth: 1,
    borderColor: '#eee',
  },
  searchInput: { flex: 1, marginLeft: 8, fontSize: 14 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderBottomWidth: 1,
    borderColor: '#f0f0f0',
  },
  email: { fontSize: 15, fontWeight: '500', color: '#222' },
  meta: { fontSize: 12, color: '#666', marginTop: 2 },
  selfBadge: {
    marginTop: 4,
    alignSelf: 'flex-start',
    fontSize: 11,
    backgroundColor: '#eee',
    color: '#666',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  stateBadge: {
    marginTop: 4,
    alignSelf: 'flex-start',
    fontSize: 11,
    backgroundColor: '#eef',
    color: '#226',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  loginAsBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: '#2563EB',
    borderRadius: 6,
    minWidth: 110,
    justifyContent: 'center',
  },
  loginAsBtnDisabled: { borderColor: '#ccc' },
  loginAsBtnArmed: { borderColor: '#B45309', backgroundColor: '#FFFBEB' },
  loginAsBtnText: { color: '#2563EB', marginLeft: 4, fontSize: 13, fontWeight: '500' },
  loginAsBtnTextArmed: { color: '#B45309' },
  armedNote: { color: '#B45309', fontSize: 12, marginTop: 4 },
  empty: { textAlign: 'center', marginTop: 40, color: '#888' },
  error: { color: '#c44', padding: 16 },
});

export default ImpersonateUserScreen;
