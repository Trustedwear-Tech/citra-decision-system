// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * AdminManagedResourcesScreen — single admin surface listing every
 * MANAGED resource (Workflow / SmartApp) the caller (dept_admin /
 * org_admin / super_admin) is entitled to see.
 *
 * Personal resources (presentations, reports, etc.) are intentionally
 * NOT shown here — they're admin-invisible by policy. See
 * services/resourceTaxonomy.js.
 *
 * Each row shows owner_type / owner_id / org_id / dept_ids + status.
 * Actions are deferred to the resource-specific surfaces (workflow
 * builder, smart-app builder) so this screen is the read-side admin
 * index.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, TouchableOpacity, FlatList, ActivityIndicator,
  TextInput, StyleSheet, Platform, RefreshControl,
} from 'react-native';
import {
  listAdminWorkflows, listAdminSmartApps,
} from '../../services/AdminManagedResourcesService';
import { authService } from '../../services/authService';
import { MANAGED_RESOURCES } from '../../services/resourceTaxonomy';
import SmartAppAuditScreen from '../SmartAppAuditScreen';


const DEFAULT_THEME = {
  background:    '#FFFFFF',
  surface:       '#F9FAFB',
  text:          '#111827',
  textSecondary: '#6B7280',
  primary:       '#3B82F6',
  border:        '#E5E7EB',
  danger:        '#DC2626',
};

const TABS = [
  // Workflow Automation PARKED (2026-07-28) — tab hidden, list/load code kept
  // wired; restore by re-adding the entry (plan:
  // docs/workflow-agent-and-connectivity-plan.md).
  // { key: 'workflow',  label: 'Workflows'  },
  { key: 'smart_app', label: 'Decision Apps' },
];

const KIND_LABEL = {
  workflow:  'Workflow',
  smart_app: 'Decision App',
};


export default function AdminManagedResourcesScreen({
  visible,
  onClose,
  onOpenResource,
  theme = DEFAULT_THEME,
}) {
  const colors = theme;
  // getCurrentUser() decodes the JWT and returns a fresh object each call.
  // Memoize once per mount so dependency arrays (load, etc.) stay stable
  // and don't trigger render loops.
  const user = useMemo(() => authService.getCurrentUser() || {}, []);
  const roles = Array.isArray(user.roles) ? user.roles : [];
  const isAdmin = roles.includes('super_admin')
    || roles.includes('org_admin')
    || roles.includes('dept_admin');

  const [tab, setTab] = useState('smart_app');
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  // Selected Smart App for the Audit view: { slug, name } | null.
  const [auditApp, setAuditApp] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      let data = [];
      if (tab === 'workflow')        data = await listAdminWorkflows();
      else if (tab === 'smart_app')  data = await listAdminSmartApps();
      setRows(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || 'Failed to load');
      setRows([]);
    } finally { setLoading(false); }
  }, [tab]);

  useEffect(() => {
    if (visible && isAdmin) load();
  }, [visible, isAdmin, load]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const filtered = useMemo(() => {
    if (!search.trim()) return rows;
    const q = search.toLowerCase();
    return rows.filter((r) => {
      const id = (r.workflow_id || r.slug || '').toLowerCase();
      const name = (r.name || r.title || r.display_name || '').toLowerCase();
      const owner = (r.owner_id || '').toLowerCase();
      return id.includes(q) || name.includes(q) || owner.includes(q);
    });
  }, [rows, search]);

  const renderRow = ({ item }) => {
    const id = item.workflow_id || item.slug;
    const name = item.name || item.title || item.display_name || id;
    const ownerType = item.owner_type || '—';
    const ownerId = item.owner_id || '—';
    const orgId = item.org_id || '—';
    const deptIds = Array.isArray(item.dept_ids) ? item.dept_ids : [];
    const status = item.status || '';
    // Smart-app rows carry an `audience` field after the audience-model
    // breaking change. Surface it as a pill so admins can tell at a
    // glance whether an app is owner-only / team / dept / org-wide.
    const audience = item.audience || (tab === 'smart_app' ? 'owner' : null);
    const audienceColor =
      !audience || audience === 'owner' ? colors.textSecondary
      : audience === 'org' ? '#7C3AED'
      : audience.startsWith('dept:') ? '#16A34A'
      : audience.startsWith('team:') ? '#3B82F6'
      : colors.textSecondary;
    const audienceLabel =
      !audience ? null
      : audience === 'owner' ? 'owner only'
      : audience === 'org' ? 'org-wide'
      : audience.startsWith('dept:') ? `dept · ${audience.slice(5)}`
      : audience.startsWith('team:') ? `team · ${audience.slice(5)}`
      : audience;

    return (
      <View style={[styles.row, { borderColor: colors.border, backgroundColor: colors.surface }]}>
        <View style={{ flex: 1 }}>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', flexWrap: 'wrap' }}>
            <Text style={{ color: colors.text, fontWeight: '600' }} numberOfLines={1}>
              {name}
            </Text>
            {!!status && (
              <View style={[styles.pill, { borderColor: colors.border }]}>
                <Text style={{ fontSize: 10, color: colors.textSecondary }}>{status}</Text>
              </View>
            )}
            {audienceLabel && (
              <View style={[styles.pill, { borderColor: audienceColor }]}>
                <Text style={{ fontSize: 10, color: audienceColor, fontWeight: '600' }}>
                  {audienceLabel}
                </Text>
              </View>
            )}
          </View>
          <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 2 }} numberOfLines={1}>
            {id} · {ownerType}/{ownerId}
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 2 }} numberOfLines={1}>
            org={orgId}{deptIds.length ? ` · dept=${deptIds.join(',')}` : ''}
          </Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          {tab === 'smart_app' ? (
            <TouchableOpacity
              style={{ paddingHorizontal: 10, paddingVertical: 6 }}
              onPress={() => setAuditApp({ slug: id, name })}
            >
              <Text style={{ color: colors.primary, fontSize: 12 }}>Audit</Text>
            </TouchableOpacity>
          ) : null}
          {onOpenResource ? (
            <TouchableOpacity
              style={{ paddingHorizontal: 10, paddingVertical: 6 }}
              onPress={() => onOpenResource(tab, id, item)}
            >
              <Text style={{ color: colors.primary, fontSize: 12 }}>Open</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      </View>
    );
  };

  if (!isAdmin) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background, justifyContent: 'center', alignItems: 'center', padding: 24 }}>
        <Text style={{ color: colors.text, fontSize: 16, fontWeight: '600' }}>Admin access required</Text>
        <Text style={{ color: colors.textSecondary, fontSize: 12, marginTop: 8, textAlign: 'center' }}>
          This page lists managed resources (Decision Apps) across your dept / org.
          dept_admin, org_admin or super_admin role is required.
        </Text>
        {onClose ? (
          <TouchableOpacity onPress={onClose} style={{ marginTop: 16 }}>
            <Text style={{ color: colors.primary }}>Close</Text>
          </TouchableOpacity>
        ) : null}
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <View style={[styles.topBar, { borderColor: colors.border }]}>
        <View>
          <Text style={{ color: colors.text, fontSize: 16, fontWeight: '600' }}>
            Managed Resources
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 11, marginTop: 2 }}>
            {MANAGED_RESOURCES.join(' · ')} — dept/org admin view
          </Text>
        </View>
        {onClose ? (
          <TouchableOpacity onPress={onClose}>
            <Text style={{ color: colors.textSecondary, fontSize: 18 }}>✕</Text>
          </TouchableOpacity>
        ) : null}
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

      <View style={{ padding: 12 }}>
        <TextInput
          style={[styles.smallInput, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surface }]}
          placeholder={`Search ${KIND_LABEL[tab].toLowerCase()}s by id, name, owner`}
          placeholderTextColor={colors.textSecondary}
          value={search}
          onChangeText={setSearch}
        />
      </View>

      {error ? (
        <Text style={{ color: colors.danger, padding: 8, fontSize: 12 }}>{error}</Text>
      ) : null}

      <View style={{ flex: 1, paddingHorizontal: 12 }}>
        {loading ? <ActivityIndicator style={{ marginTop: 30 }} color={colors.primary} /> : (
          <FlatList
            data={filtered}
            keyExtractor={(it, idx) => `${tab}:${it.workflow_id || it.slug || idx}`}
            renderItem={renderRow}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
            ListEmptyComponent={
              <Text style={{ color: colors.textSecondary, textAlign: 'center', marginTop: 30 }}>
                {`No ${KIND_LABEL[tab].toLowerCase()}s in your scope.`}
              </Text>
            }
          />
        )}
      </View>

      {/* Audit view for the selected Smart App (renders its own Modal). */}
      <SmartAppAuditScreen
        visible={!!auditApp}
        slug={auditApp ? auditApp.slug : null}
        appName={auditApp ? auditApp.name : null}
        theme={colors}
        onClose={() => setAuditApp(null)}
      />
    </View>
  );
}


const styles = StyleSheet.create({
  topBar: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: 1,
  },
  tabBar: { flexDirection: 'row', borderBottomWidth: 1 },
  tab: { paddingHorizontal: 16, paddingVertical: 10 },
  smallInput: {
    borderWidth: 1, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 8,
    fontSize: 12,
  },
  pill: {
    paddingHorizontal: 6, paddingVertical: 1, borderRadius: 8, borderWidth: 1,
    marginLeft: 6,
  },
  row: {
    flexDirection: 'row', alignItems: 'flex-start', borderWidth: 1, borderRadius: 8,
    padding: 10, marginBottom: 6,
  },
});
