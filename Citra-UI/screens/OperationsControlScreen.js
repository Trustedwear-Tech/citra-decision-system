// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * OperationsControlScreen — full-screen "Decision App & API Automation Control".
 *
 * ONE console, launched from the Decision Apps surface AND the HomePanel Admin
 * section. Two tabs:
 *   1. Kill switches — scoped halt (deployment / org / dept). super_admin sees
 *      every org; org_admin their org + depts; dept_admin their dept(s).
 *   2. Schedules — every scheduled/triggered app in scope: mode, when it runs
 *      (next + last run), deploy status, and inline Start/Stop + edit-schedule.
 *
 * Backend: /admin/halt, /admin/automation, PATCH /apps/{slug}/ai-triggers/{id}.
 * No browser dialogs; all confirms are in-UI.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator, ScrollView, Modal, TextInput,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import SmartAppService from '../services/SmartAppService';
import AdminUserService from '../services/AdminUserService';
import authService from '../services/authService';

const RED = '#DC2626';
const GREEN = '#16A34A';
const KIND = {
  app: { label: 'Decision App', color: '#2563EB', icon: 'apps-outline' },
  api: { label: 'Decision Agent API', color: '#059669', icon: 'code-slash-outline' },
  dashboard: { label: 'Dashboard', color: '#6366F1', icon: 'bar-chart-outline' },
};

function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return '—'; }
}

export default function OperationsControlScreen({ visible, onClose, theme }) {
  const colors = theme || {};
  const bg = colors.background || '#FFFFFF';
  const surface = colors.surface || '#F9FAFB';
  const text = colors.text || '#111827';
  const sub = colors.textSecondary || '#6B7280';
  const border = colors.border || '#E5E7EB';
  const primary = colors.primary || '#3B82F6';

  const me = useMemo(() => authService.getCurrentUser?.() || {}, []);
  const roles = useMemo(
    () => (Array.isArray(me.roles) ? me.roles : (me.roles ? [me.roles] : [])), [me],
  );
  const isSuper = roles.includes('super_admin');
  const isOrg = roles.includes('org_admin') || isSuper;
  const myDeptIds = useMemo(() => (Array.isArray(me.dept_ids) ? me.dept_ids : []), [me]);

  const [tab, setTab] = useState('kill');

  // ── kill-switch state ──────────────────────────────────────────────────
  const [controls, setControls] = useState([]);
  const [orgs, setOrgs] = useState([]);
  const [depts, setDepts] = useState([]);
  const [haltNote, setHaltNote] = useState('');
  const [haltErr, setHaltErr] = useState('');
  const [haltBusy, setHaltBusy] = useState(null);
  const [pending, setPending] = useState(null);
  // super_admin org drill-down → that org's departments (lazy-loaded).
  const [expandedOrgs, setExpandedOrgs] = useState(() => new Set());
  const [orgDepts, setOrgDepts] = useState({});      // orgId -> depts[]
  const [orgDeptsBusy, setOrgDeptsBusy] = useState(() => new Set());

  const toggleOrgExpand = useCallback(async (orgId) => {
    setExpandedOrgs((prev) => {
      const next = new Set(prev);
      if (next.has(orgId)) next.delete(orgId); else next.add(orgId);
      return next;
    });
    // Lazy-load that org's depts the first time it's expanded.
    if (!orgDepts[orgId]) {
      setOrgDeptsBusy((s) => new Set(s).add(orgId));
      try {
        const list = await AdminUserService.listDepts(orgId);
        setOrgDepts((m) => ({ ...m, [orgId]: Array.isArray(list) ? list : [] }));
      } catch {
        setOrgDepts((m) => ({ ...m, [orgId]: [] }));
      } finally {
        setOrgDeptsBusy((s) => { const n = new Set(s); n.delete(orgId); return n; });
      }
    }
  }, [orgDepts]);

  const loadHalt = useCallback(async () => {
    setHaltErr(''); setHaltNote(''); setPending(null);
    try {
      const r = await SmartAppService.listHalt();
      setControls(Array.isArray(r?.controls) ? r.controls : []);
    } catch {
      setControls([]);
      setHaltNote('Could not load current halt status (is smart-app-service running?). You can still act below.');
    }
    if (isSuper) {
      try { const o = await AdminUserService.listOrgs(); setOrgs(Array.isArray(o) ? o : []); }
      catch { setOrgs([]); }
    } else {
      try {
        const all = await AdminUserService.listDepts();
        const list = Array.isArray(all) ? all : [];
        setDepts(isOrg ? list : list.filter((d) => myDeptIds.includes(d.id)));
      } catch { setDepts(isOrg ? [] : myDeptIds.map((id) => ({ id, name: id }))); }
    }
  }, [isSuper, isOrg, myDeptIds]);

  const isHalted = (scopeType, scopeId) =>
    controls.some((c) => c.scope_type === scopeType && (scopeType === 'global' || c.scope_id === scopeId));

  const applyHalt = useCallback(async (row) => {
    setHaltBusy(row.key); setHaltErr('');
    try {
      await SmartAppService.setHalt({
        scopeType: row.scopeType, scopeId: row.scopeId, enabled: row.enabled,
        reason: row.enabled ? `Halted via console (${row.label})` : '',
      });
      await loadHalt();
    } catch (e) {
      setHaltErr(e?.message || 'Action failed — is smart-app-service running with the latest build?');
    } finally { setHaltBusy(null); setPending(null); }
  }, [loadHalt]);

  const haltRows = useMemo(() => {
    const rows = [];
    const org = me.org_id;
    if (isSuper) {
      rows.push({ key: 'global', scopeType: 'global', scopeId: null, label: 'Entire deployment', icon: 'earth-outline',
        hint: 'Freezes EVERY organization — all apps, all departments, autonomous writes, approvals and triggers.' });
      // Each org row expands to its departments (drill-down).
      for (const o of orgs) rows.push({ key: `org:${o.id}`, scopeType: 'org', scopeId: o.id, orgId: o.id, expandable: true, label: o.name || o.id, icon: 'business-outline', hint: `Freezes all apps in ${o.name || o.id}. Expand to halt a single department.` });
    } else if (isOrg) {
      rows.push({ key: `org:${org}`, scopeType: 'org', scopeId: org || null, label: 'Entire organization', icon: 'business-outline', hint: 'Freezes every app in your organization — all departments.' });
      for (const d of depts) rows.push({ key: `dept:${org}:${d.id}`, scopeType: 'dept', scopeId: `${org}:${d.id}`, label: d.name || d.id, icon: 'people-outline', hint: `Freezes all apps in ${d.name || d.id}.` });
    } else {
      for (const d of depts) rows.push({ key: `dept:${org}:${d.id}`, scopeType: 'dept', scopeId: `${org}:${d.id}`, label: d.name || d.id, icon: 'people-outline', hint: `Freezes all apps in ${d.name || d.id}.` });
    }
    return rows;
  }, [isSuper, isOrg, orgs, depts, me.org_id]);

  // ── schedules state ────────────────────────────────────────────────────
  const [apps, setApps] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [systemJobs, setSystemJobs] = useState([]);  // fleet-level learning loops
  const [schedLoading, setSchedLoading] = useState(false);
  const [schedErr, setSchedErr] = useState('');
  const [rowBusy, setRowBusy] = useState(null);   // `${slug}:${tid}`
  const [editing, setEditing] = useState(null);   // {slug, tid, type, value}
  const [query, setQuery] = useState('');
  const [onlyScheduled, setOnlyScheduled] = useState(false);

  const loadAutomation = useCallback(async () => {
    setSchedLoading(true); setSchedErr('');
    try {
      const r = await SmartAppService.listAutomation();
      setApps(Array.isArray(r?.apps) ? r.apps : []);
      setIncidents(Array.isArray(r?.incidents) ? r.incidents : []);
      setSystemJobs(Array.isArray(r?.system_jobs) ? r.system_jobs : []);
    } catch (e) {
      setApps([]); setIncidents([]); setSystemJobs([]);
      setSchedErr(e?.message || 'Could not load automation (is smart-app-service running with the latest build?).');
    } finally { setSchedLoading(false); }
  }, []);

  const toggleTrigger = useCallback(async (slug, t) => {
    const key = `${slug}:${t.id}`;
    setRowBusy(key); setSchedErr('');
    try {
      await SmartAppService.setTrigger(slug, t.id, { enabled: !t.enabled });
      await loadAutomation();
    } catch (e) { setSchedErr(e?.message || 'Could not change trigger.'); }
    finally { setRowBusy(null); }
  }, [loadAutomation]);

  const saveSchedule = useCallback(async () => {
    if (!editing) return;
    const key = `${editing.slug}:${editing.tid}`;
    setRowBusy(key); setSchedErr('');
    try {
      const patch = editing.type === 'schedule.cron'
        ? { cron: String(editing.value || '').trim() }
        : { every_seconds: Math.max(30, parseInt(editing.value, 10) || 0) };
      await SmartAppService.setTrigger(editing.slug, editing.tid, patch);
      setEditing(null);
      await loadAutomation();
    } catch (e) { setSchedErr(e?.message || 'Could not save schedule.'); }
    finally { setRowBusy(null); }
  }, [editing, loadAutomation]);

  useEffect(() => {
    if (!visible) return;
    loadHalt();
    loadAutomation();
  }, [visible, loadHalt, loadAutomation]);

  const filteredApps = useMemo(() => {
    const q = query.trim().toLowerCase();
    return apps
      .map((a) => ({
        ...a,
        triggers: (a.triggers || []).filter((t) => !onlyScheduled || t.type !== 'webhook'),
      }))
      .filter((a) => (a.triggers.length > 0 || !onlyScheduled))
      .filter((a) => !q || String(a.title).toLowerCase().includes(q) || String(a.slug).toLowerCase().includes(q));
  }, [apps, query, onlyScheduled]);

  if (!visible) return null;

  const TabBtn = ({ id, label }) => (
    <TouchableOpacity
      onPress={() => setTab(id)}
      style={{ paddingVertical: 12, paddingHorizontal: 16, borderBottomWidth: 2, borderBottomColor: tab === id ? primary : 'transparent' }}
    >
      <Text style={{ color: tab === id ? primary : sub, fontWeight: tab === id ? '700' : '500', fontSize: 14 }}>{label}</Text>
    </TouchableOpacity>
  );

  const renderHaltCard = (row, indent = false) => {
    const halted = isHalted(row.scopeType, row.scopeId);
    const busy = haltBusy === row.key;
    const confirming = pending && pending.key === row.key;
    const expanded = row.expandable && expandedOrgs.has(row.orgId);
    return (
      <View key={row.key} style={{ borderWidth: 1, borderColor: halted ? RED : border, borderRadius: 10, padding: 12, marginBottom: 10, marginLeft: indent ? 20 : 0, backgroundColor: halted ? '#FEF2F2' : bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Ionicons name={row.icon || 'business-outline'} size={16} color={halted ? RED : sub} />
          <Text style={{ flex: 1, fontSize: 14, fontWeight: '700', color: text }}>{row.label}</Text>
          <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 12, backgroundColor: halted ? '#FEE2E2' : '#F0FDF4' }}>
            <Text style={{ fontSize: 11, fontWeight: '700', color: halted ? RED : GREEN }}>{halted ? 'HALTED' : 'Active'}</Text>
          </View>
          {row.expandable && (
            <TouchableOpacity onPress={() => toggleOrgExpand(row.orgId)} hitSlop={8}>
              <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={18} color={sub} />
            </TouchableOpacity>
          )}
        </View>
        <Text style={{ fontSize: 12, color: sub, marginTop: 4 }}>{row.hint}</Text>
        {confirming ? (
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 10, alignItems: 'center' }}>
            <Text style={{ flex: 1, fontSize: 12, color: RED, fontWeight: '600' }}>
              {pending.enabled ? `Halt ${pending.label}? This stops all its automation now.` : `Resume ${pending.label}?`}
            </Text>
            <TouchableOpacity onPress={() => setPending(null)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: border }}>
              <Text style={{ color: sub, fontSize: 12, fontWeight: '600' }}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity disabled={busy} onPress={() => applyHalt(pending)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: pending.enabled ? RED : GREEN, opacity: busy ? 0.6 : 1 }}>
              {busy ? <ActivityIndicator color="#fff" size="small" /> : <Text style={{ color: '#fff', fontSize: 12, fontWeight: '700' }}>{pending.enabled ? 'Confirm halt' : 'Confirm resume'}</Text>}
            </TouchableOpacity>
          </View>
        ) : (
          <TouchableOpacity onPress={() => setPending({ ...row, enabled: !halted })} style={{ alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: halted ? GREEN : RED }}>
            <Ionicons name={halted ? 'play' : 'stop'} size={14} color="#fff" />
            <Text style={{ color: '#fff', fontSize: 12, fontWeight: '700' }}>{halted ? 'Resume' : 'Halt'}</Text>
          </TouchableOpacity>
        )}
      </View>
    );
  };

  return (
    <Modal visible transparent={false} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: bg }}>
        {/* header */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: border }}>
          <Ionicons name="stop-circle-outline" size={22} color={RED} />
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 18, fontWeight: '800', color: text }}>Decision App &amp; API Automation Control</Text>
            <Text style={{ fontSize: 12, color: sub }}>Freeze scope, and see/control what runs and when. Reads &amp; audit stay available.</Text>
          </View>
          <TouchableOpacity onPress={onClose} hitSlop={12}><Ionicons name="close" size={24} color={text} /></TouchableOpacity>
        </View>

        {/* tabs */}
        <View style={{ flexDirection: 'row', paddingHorizontal: 12, borderBottomWidth: 1, borderBottomColor: border }}>
          <TabBtn id="kill" label="Kill switches" />
          <TabBtn id="sched" label="Schedules & triggers" />
        </View>

        {tab === 'kill' ? (
          <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 16, maxWidth: 720, alignSelf: 'center', width: '100%' }}>
            {!!haltNote && <Text style={{ color: '#92400E', backgroundColor: '#FFFBEB', fontSize: 12, padding: 10, borderRadius: 8, marginBottom: 10 }}>{haltNote}</Text>}
            {!!haltErr && <Text style={{ color: RED, backgroundColor: '#FEF2F2', fontSize: 12, padding: 10, borderRadius: 8, marginBottom: 10 }}>{haltErr}</Text>}
            {haltRows.map((row) => (
              <View key={row.key}>
                {renderHaltCard(row)}
                {isSuper && row.expandable && expandedOrgs.has(row.orgId) && (
                  <View>
                    {orgDeptsBusy.has(row.orgId) && <ActivityIndicator color={primary} style={{ marginBottom: 10 }} />}
                    {(orgDepts[row.orgId] || []).map((d) => renderHaltCard({
                      key: `dept:${row.orgId}:${d.id}`, scopeType: 'dept', scopeId: `${row.orgId}:${d.id}`,
                      label: d.name || d.id, icon: 'people-outline', hint: `Freezes all apps in ${d.name || d.id} (${row.label}).`,
                    }, true))}
                    {!orgDeptsBusy.has(row.orgId) && (orgDepts[row.orgId] || []).length === 0 && (
                      <Text style={{ fontSize: 12, color: sub, marginLeft: 20, marginBottom: 10 }}>No departments in this org.</Text>
                    )}
                  </View>
                )}
              </View>
            ))}
          </ScrollView>
        ) : (
          <View style={{ flex: 1 }}>
            {/* filter bar */}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderBottomWidth: 1, borderBottomColor: border }}>
              <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: 1, borderColor: border, borderRadius: 8, paddingHorizontal: 10 }}>
                <Ionicons name="search" size={14} color={sub} />
                <TextInput value={query} onChangeText={setQuery} placeholder="Search app / API…" placeholderTextColor={sub} style={{ flex: 1, paddingVertical: 8, color: text, fontSize: 13 }} />
              </View>
              <TouchableOpacity onPress={() => setOnlyScheduled((v) => !v)} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: onlyScheduled ? primary : border }}>
                <Ionicons name={onlyScheduled ? 'checkbox' : 'square-outline'} size={16} color={onlyScheduled ? primary : sub} />
                <Text style={{ fontSize: 12, color: onlyScheduled ? primary : sub, fontWeight: '600' }}>Scheduled only</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={loadAutomation} hitSlop={8}><Ionicons name="refresh" size={18} color={sub} /></TouchableOpacity>
            </View>

            {!!schedErr && <Text style={{ color: RED, backgroundColor: '#FEF2F2', fontSize: 12, padding: 10 }}>{schedErr}</Text>}

            {incidents.length > 0 && (
              <View style={{ backgroundColor: '#FEF2F2', borderBottomWidth: 1, borderBottomColor: '#FECACA', paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: '#B91C1C', fontSize: 12, fontWeight: '700', marginBottom: 4 }}>Recent failures ({incidents.length})</Text>
                {incidents.slice(0, 6).map((inc, i) => (
                  <Text key={`${inc.slug}:${inc.trigger_id}:${i}`} style={{ color: '#991B1B', fontSize: 11 }} numberOfLines={1}>
                    • {inc.title} · {inc.trigger_id} · {inc.status} · {fmtTime(inc.at)}{inc.error ? ` — ${inc.error}` : ''}
                  </Text>
                ))}
              </View>
            )}

            {schedLoading ? (
              <ActivityIndicator color={primary} style={{ marginTop: 32 }} />
            ) : (
              <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 12 }}>
                {systemJobs.length > 0 && (
                  <View style={{ borderWidth: 1, borderColor: border, borderRadius: 10, marginBottom: 12, overflow: 'hidden' }}>
                    <View style={{ padding: 12, backgroundColor: surface, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                      <Ionicons name="hardware-chip-outline" size={16} color={primary} />
                      <Text style={{ fontWeight: '700', color: text, fontSize: 14 }}>Learning scheduler (fleet)</Text>
                    </View>
                    {systemJobs.map((j) => (
                      <View key={j.type} style={{ padding: 12, borderTopWidth: 1, borderTopColor: border }}>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <Text style={{ fontWeight: '600', color: text, fontSize: 13 }}>
                            {j.type === 'outcome_poll_scheduler' ? 'Outcome-poll scheduler'
                              : j.type === 'grounding_rebuild' ? 'Grounding-rebuild scheduler' : j.type}
                          </Text>
                          <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: j.enabled ? '#F0FDF4' : '#FEF3C7' }}>
                            <Text style={{ fontSize: 10, fontWeight: '700', color: j.enabled ? GREEN : '#92400E' }}>{j.enabled ? 'RUNNING' : 'STOPPED'}</Text>
                          </View>
                        </View>
                        <Text style={{ fontSize: 12, color: sub, marginTop: 4 }}>
                          {j.schedule_human}{j.enabled && j.last_tick_at ? `  ·  last tick: ${fmtTime(j.last_tick_at)}` : ''}
                        </Text>
                        {!!j.note && <Text style={{ fontSize: 11, color: sub, marginTop: 2 }}>{j.note}</Text>}
                        {!j.enabled && (
                          <Text style={{ fontSize: 11, color: '#92400E', marginTop: 2 }}>
                            Stopped — no app is polled/rebuilt while this is off, regardless of each app's auto-learn setting below.
                          </Text>
                        )}
                      </View>
                    ))}
                  </View>
                )}
                {filteredApps.length === 0 ? (
                  <Text style={{ color: sub, fontSize: 13, padding: 12 }}>No apps with triggers in your scope.</Text>
                ) : filteredApps.map((a) => {
                  const k = KIND[a.kind] || KIND.app;
                  return (
                    <View key={a.slug} style={{ borderWidth: 1, borderColor: border, borderRadius: 10, marginBottom: 12, overflow: 'hidden' }}>
                      {/* app header */}
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, backgroundColor: surface }}>
                        <Ionicons name={k.icon} size={16} color={k.color} />
                        <Text style={{ fontWeight: '700', color: text, fontSize: 14 }}>{a.title}</Text>
                        <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: k.color + '18' }}>
                          <Text style={{ fontSize: 10, color: k.color, fontWeight: '700' }}>{k.label}</Text>
                        </View>
                        {a.paused && (
                          <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEF3C7' }}>
                            <Text style={{ fontSize: 10, color: '#92400E', fontWeight: '700' }}>Paused</Text>
                          </View>
                        )}
                        {a.stats && a.stats.failures > 0 && (
                          <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEE2E2' }}>
                            <Text style={{ fontSize: 10, color: RED, fontWeight: '700' }}>{a.stats.failures} failed</Text>
                          </View>
                        )}
                        <View style={{ flex: 1 }} />
                        <Text style={{ fontSize: 11, color: sub }}>{a.org_id}</Text>
                      </View>
                      {/* triggers */}
                      {(a.triggers || []).length === 0 ? (
                        <Text style={{ color: sub, fontSize: 12, padding: 12 }}>No triggers.</Text>
                      ) : (a.triggers || []).map((t) => {
                        const key = `${a.slug}:${t.id}`;
                        const busy = rowBusy === key;
                        const isEditing = editing && editing.slug === a.slug && editing.tid === t.id;
                        const canEditSchedule = t.type === 'schedule.cron' || t.type === 'schedule.interval' || t.type === 'poll';
                        return (
                          <View key={t.id} style={{ padding: 12, borderTopWidth: 1, borderTopColor: border }}>
                            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                              <Text style={{ fontWeight: '600', color: text, fontSize: 13 }}>{t.id}</Text>
                              <Text style={{ fontSize: 11, color: sub }}>· {t.type}</Text>
                              {t.autonomous && (
                                <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEE2E2' }}>
                                  <Text style={{ fontSize: 10, color: RED, fontWeight: '700' }}>🔴 Autonomous</Text>
                                </View>
                              )}
                              <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: t.enabled ? '#F0FDF4' : '#F3F4F6' }}>
                                <Text style={{ fontSize: 10, fontWeight: '700', color: t.enabled ? GREEN : sub }}>{t.enabled ? 'ON' : 'OFF'}</Text>
                              </View>
                            </View>
                            <Text style={{ fontSize: 12, color: sub, marginTop: 4 }}>
                              {t.schedule_human}  ·  next: {fmtTime(t.next_run)}  ·  last: {fmtTime(t.last_run?.at)}{t.last_run?.status ? ` (${t.last_run.status})` : ''}
                            </Text>

                            {isEditing ? (
                              <View style={{ flexDirection: 'row', gap: 8, marginTop: 8, alignItems: 'center' }}>
                                <TextInput
                                  value={String(editing.value ?? '')}
                                  onChangeText={(v) => setEditing((e) => ({ ...e, value: v }))}
                                  placeholder={editing.type === 'schedule.cron' ? '0 9 * * *' : 'seconds (≥30)'}
                                  placeholderTextColor={sub}
                                  keyboardType={editing.type === 'schedule.cron' ? 'default' : 'numeric'}
                                  style={{ flex: 1, borderWidth: 1, borderColor: border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, color: text, fontSize: 13 }}
                                />
                                <TouchableOpacity onPress={() => setEditing(null)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: border }}>
                                  <Text style={{ color: sub, fontSize: 12, fontWeight: '600' }}>Cancel</Text>
                                </TouchableOpacity>
                                <TouchableOpacity disabled={busy} onPress={saveSchedule} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: primary, opacity: busy ? 0.6 : 1 }}>
                                  {busy ? <ActivityIndicator color="#fff" size="small" /> : <Text style={{ color: '#fff', fontSize: 12, fontWeight: '700' }}>Save</Text>}
                                </TouchableOpacity>
                              </View>
                            ) : (
                              <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
                                <TouchableOpacity disabled={busy} onPress={() => toggleTrigger(a.slug, t)} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: t.enabled ? RED : GREEN, opacity: busy ? 0.6 : 1 }}>
                                  <Ionicons name={t.enabled ? 'stop' : 'play'} size={13} color="#fff" />
                                  <Text style={{ color: '#fff', fontSize: 12, fontWeight: '700' }}>{t.enabled ? 'Stop' : 'Start'}</Text>
                                </TouchableOpacity>
                                {canEditSchedule && (
                                  <TouchableOpacity onPress={() => setEditing({ slug: a.slug, tid: t.id, type: t.type, value: t.type === 'schedule.cron' ? (t.cron || '') : (t.every_seconds || 60) })} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: border }}>
                                    <Ionicons name="time-outline" size={13} color={text} />
                                    <Text style={{ color: text, fontSize: 12, fontWeight: '600' }}>Edit schedule</Text>
                                  </TouchableOpacity>
                                )}
                              </View>
                            )}
                          </View>
                        );
                      })}
                      {/* LEARNING jobs (outcome poll + grounding refresh) — from agent_spec.
                          These are the app's SETTINGS; the fleet scheduler above is the engine
                          that actually runs them. When it's stopped, auto-learn is inert. */}
                      {(a.learning || []).map((l) => {
                        const schedOn = !!(systemJobs.find((x) => x.type === 'outcome_poll_scheduler') || {}).enabled;
                        const inert = l.auto_learn && !schedOn;  // wants auto-learn but engine stopped
                        return (
                        <View key={`${a.slug}:learn:${l.type}`} style={{ padding: 12, borderTopWidth: 1, borderTopColor: border, backgroundColor: surface }}>
                          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <Ionicons name={l.type === 'outcome_poll' ? 'pulse-outline' : 'sparkles-outline'} size={13} color={primary} />
                            <Text style={{ fontWeight: '600', color: text, fontSize: 13 }}>
                              {l.type === 'outcome_poll' ? 'Outcome poll (learning)' : 'Grounding refresh (few-shot)'}
                            </Text>
                            {l.type === 'grounding_refresh' && l.never_refreshed && (
                              <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEF3C7' }}>
                                <Text style={{ fontSize: 10, color: '#92400E', fontWeight: '700' }}>Never refreshed</Text>
                              </View>
                            )}
                            <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: l.auto_learn ? '#F0FDF4' : '#F3F4F6' }}>
                              <Text style={{ fontSize: 10, fontWeight: '700', color: l.auto_learn ? GREEN : sub }}>{l.auto_learn ? 'auto-learn ON' : 'manual'}</Text>
                            </View>
                            {inert && (
                              <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: '#FEF3C7' }}>
                                <Text style={{ fontSize: 10, fontWeight: '700', color: '#92400E' }}>paused — scheduler stopped</Text>
                              </View>
                            )}
                          </View>
                          <Text style={{ fontSize: 12, color: sub, marginTop: 4 }}>
                            {l.schedule_human}
                            {l.type === 'outcome_poll' && l.table ? `  ·  reads: ${l.table}` : ''}
                            {l.type === 'grounding_refresh' && l.last_refreshed_at ? `  ·  last: ${fmtTime(l.last_refreshed_at)} (${l.sample_count ?? '—'} samples)` : ''}
                          </Text>
                        </View>
                        );
                      })}
                    </View>
                  );
                })}
              </ScrollView>
            )}
          </View>
        )}
      </View>
    </Modal>
  );
}
