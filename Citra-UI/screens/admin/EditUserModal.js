/**
 * EditUserModal — super_admin / org_admin / dept_admin edits a user's roles,
 * dept_ids, and (super_admin only) org_id.
 *
 * Backend: PATCH /api/admin/users/:userId — see Citra-User-Service
 * src/routes/userAdminRoutes.js. The server enforces:
 *   - dept_admin can only assign their own depts
 *   - org_id change requires super_admin
 *   - role rank cannot exceed the caller's rank (no privilege escalation)
 *
 * Why dept catalog comes from GET /api/depts: single-tenant deployments seed
 * depts from config/depts.seed.json. Until SAML group-mapping lands, the
 * admin picks from this fixed list rather than typing free-text dept_ids.
 */

import React, { useEffect, useMemo, useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, ActivityIndicator,
  Modal, StyleSheet, ScrollView, Switch, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import AdminUserService from '../../services/AdminUserService';
import authService from '../../services/authService';

const ASSIGNABLE_ROLES = [
  { value: 'user',                 label: 'User' },
  // Non-admin builder persona — may build & publish Decision Apps/APIs. Any
  // admin can grant it. Mirrors citra-auth Roles.DECISION_APP_BUILDER.
  { value: 'decision-app-builder', label: 'Decision App Builder' },
  { value: 'dept_admin',           label: 'Dept Admin' },
  { value: 'org_admin',            label: 'Org Admin' },
  { value: 'super_admin',          label: 'Super Admin' },
];

export default function EditUserModal({ visible, user, onClose, onSaved }) {
  // Caller's identity — used to gate which controls show (e.g., org_id
  // input only visible to super_admin since backend rejects non-super edits).
  const caller = useMemo(() => authService.getCurrentUser() || {}, []);
  const callerRoles = Array.isArray(caller.roles) ? caller.roles : [];
  const isSuper = callerRoles.includes('super_admin');
  const isOrgAdmin = callerRoles.includes('org_admin');

  const [roles, setRoles] = useState([]);
  const [deptIds, setDeptIds] = useState([]);
  const [orgId, setOrgId] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [personalSaId, setPersonalSaId] = useState('');
  const [workSaId, setWorkSaId] = useState('');
  const [deptCatalog, setDeptCatalog] = useState([]);
  const [loadingDepts, setLoadingDepts] = useState(false);
  const [saving, setSaving] = useState(false);
  const [fixingSAs, setFixingSAs] = useState(false);
  const [fixResult, setFixResult] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible || !user) return;
    setRoles(Array.isArray(user.roles) ? [...user.roles] : ['user']);
    setDeptIds(Array.isArray(user.dept_ids) ? [...user.dept_ids] : []);
    setOrgId(user.org_id || '');
    setIsActive(user.isActive !== false);
    setPersonalSaId(user.personal_sa_id || '');
    setWorkSaId(user.work_sa_id || '');
    setFixResult('');
    setError('');
  }, [visible, user]);

  useEffect(() => {
    if (!visible) return;
    setLoadingDepts(true);
    AdminUserService.listDepts()
      .then((d) => setDeptCatalog(Array.isArray(d) ? d : []))
      .catch((e) => setDeptCatalog([]))
      .finally(() => setLoadingDepts(false));
  }, [visible]);

  if (!user) return null;

  const toggleRole = (r) => {
    setRoles((prev) => {
      const next = new Set(prev);
      if (next.has(r)) next.delete(r);
      else next.add(r);
      // Server always re-adds 'user' on save, but keep it visible too.
      if (r !== 'user') next.add('user');
      return Array.from(next);
    });
  };

  const toggleDept = (id) => {
    setDeptIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const onSave = async () => {
    setSaving(true);
    setError('');
    try {
      const patch = {
        roles,
        dept_ids: deptIds,
        isActive,
      };
      // Only super_admin can change org_id (backend will 403 anyone else).
      if (isSuper && orgId !== (user.org_id || '')) {
        patch.org_id = orgId || null;
      }
      // SA-id edits are super_admin-only on the server too.
      if (isSuper && personalSaId !== (user.personal_sa_id || '')) {
        patch.personal_sa_id = personalSaId || null;
      }
      if (isSuper && workSaId !== (user.work_sa_id || '')) {
        patch.work_sa_id = workSaId || null;
      }
      const updated = await AdminUserService.updateUser(user.email, patch);
      if (typeof onSaved === 'function') onSaved(updated);
      onClose();
    } catch (e) {
      setError(e?.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const onFixSAs = async () => {
    setFixingSAs(true);
    setError('');
    setFixResult('');
    try {
      const res = await AdminUserService.ensureServiceAccounts(user.email);
      const u = res.user || {};
      setPersonalSaId(u.personal_sa_id || '');
      setWorkSaId(u.work_sa_id || '');
      const provisioned = res.provisioned || {};
      const parts = [];
      if (provisioned.personal) parts.push('personal');
      if (provisioned.work)     parts.push('work');
      setFixResult(parts.length
        // Tell the admin the user must re-login — their existing JWT still
        // carries the old empty SA claims until they sign back in.
        ? `Provisioned: ${parts.join(' + ')}. The user must sign out and back in for the new Service Accounts to take effect.`
        : 'Both SAs already exist. If the user still sees the warning banner, ask them to sign out and back in.');
      if (typeof onSaved === 'function') onSaved(u);
    } catch (e) {
      setError(e?.message || 'Fix Service Accounts failed');
    } finally {
      setFixingSAs(false);
    }
  };

  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>Edit user</Text>
            <TouchableOpacity onPress={onClose}>
              <Ionicons name="close" size={22} color="#666" />
            </TouchableOpacity>
          </View>

          <ScrollView style={{ maxHeight: 520 }} contentContainerStyle={{ padding: 16 }}>
            <Text style={styles.label}>Email</Text>
            <Text style={styles.readOnly}>{user.email}</Text>

            <Text style={[styles.label, { marginTop: 16 }]}>Roles</Text>
            <View style={styles.chipRow}>
              {ASSIGNABLE_ROLES.map((r) => {
                const selected = roles.includes(r.value);
                // dept_admin can't assign org_admin or super_admin.
                const disabled = !isSuper && (r.value === 'super_admin'
                  || (!isOrgAdmin && r.value === 'org_admin'));
                return (
                  <TouchableOpacity
                    key={r.value}
                    style={[styles.chip, selected && styles.chipOn, disabled && styles.chipDisabled]}
                    onPress={() => !disabled && toggleRole(r.value)}
                    disabled={disabled}
                  >
                    <Text style={[styles.chipText, selected && styles.chipTextOn]}>
                      {r.label}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            <Text style={[styles.label, { marginTop: 16 }]}>Departments</Text>
            {loadingDepts ? (
              <ActivityIndicator style={{ marginTop: 8 }} />
            ) : deptCatalog.length === 0 ? (
              <Text style={styles.hint}>No depts configured for this org. Add them to config/depts.seed.json.</Text>
            ) : (
              <View style={styles.chipRow}>
                {deptCatalog.map((d) => {
                  const selected = deptIds.includes(d.id);
                  return (
                    <TouchableOpacity
                      key={d.id}
                      style={[styles.chip, selected && styles.chipOn]}
                      onPress={() => toggleDept(d.id)}
                    >
                      <Text style={[styles.chipText, selected && styles.chipTextOn]}>
                        {d.name}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
            )}

            <Text style={[styles.label, { marginTop: 16 }]}>Org ID</Text>
            {isSuper ? (
              <TextInput
                style={styles.input}
                value={orgId}
                onChangeText={setOrgId}
                placeholder="e.g. acme-cement"
                autoCapitalize="none"
              />
            ) : (
              <Text style={styles.readOnly}>{orgId || '—'}  (super_admin only)</Text>
            )}

            <View style={styles.switchRow}>
              <Text style={styles.label}>Active</Text>
              <Switch value={isActive} onValueChange={setIsActive} />
            </View>

            {/* Service Accounts — edit (super_admin) + Fix button */}
            <View style={{ marginTop: 20, padding: 12, borderRadius: 8, borderWidth: 1, borderColor: '#E5E7EB', backgroundColor: '#F9FAFB' }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text style={[styles.label, { color: '#374151' }]}>Service Accounts</Text>
                <TouchableOpacity
                  onPress={onFixSAs}
                  disabled={fixingSAs || !orgId}
                  style={{
                    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6,
                    backgroundColor: '#10B981', opacity: (fixingSAs || !orgId) ? 0.5 : 1,
                  }}
                >
                  {fixingSAs
                    ? <ActivityIndicator color="#fff" size="small" />
                    : <Text style={{ color: '#fff', fontSize: 12, fontWeight: '600' }}>Fix Service Accounts</Text>}
                </TouchableOpacity>
              </View>
              {!orgId ? (
                <Text style={[styles.hint, { color: '#B45309', marginTop: 6 }]}>
                  Assign an org_id first — SA ids are deterministic on org_id.
                </Text>
              ) : null}
              {fixResult ? (
                <Text style={[styles.hint, { color: '#047857', marginTop: 6 }]}>{fixResult}</Text>
              ) : null}

              <Text style={[styles.label, { marginTop: 10 }]}>Personal SA id</Text>
              {isSuper ? (
                <TextInput
                  style={styles.input}
                  value={personalSaId}
                  onChangeText={setPersonalSaId}
                  placeholder="svc:personal-<user>@<org>.citra.ai"
                  autoCapitalize="none"
                />
              ) : (
                <Text style={styles.readOnly}>{personalSaId || '—'}  (super_admin only)</Text>
              )}

              <Text style={[styles.label, { marginTop: 10 }]}>Work SA id</Text>
              {isSuper ? (
                <TextInput
                  style={styles.input}
                  value={workSaId}
                  onChangeText={setWorkSaId}
                  placeholder="svc:work-<user>@<org>.citra.ai"
                  autoCapitalize="none"
                />
              ) : (
                <Text style={styles.readOnly}>{workSaId || '—'}  (super_admin only)</Text>
              )}
            </View>

            {!!error && <Text style={styles.error}>{error}</Text>}
          </ScrollView>

          <View style={styles.footer}>
            <TouchableOpacity style={styles.btnGhost} onPress={onClose} disabled={saving}>
              <Text style={styles.btnGhostText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.btnPrimary} onPress={onSave} disabled={saving}>
              {saving
                ? <ActivityIndicator color="#fff" />
                : <Text style={styles.btnPrimaryText}>Save</Text>}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  sheet: {
    backgroundColor: '#fff',
    borderRadius: 16,
    width: '100%',
    maxWidth: 560,
    maxHeight: '90%',
    overflow: 'hidden',
    ...Platform.select({
      web: { boxShadow: '0 20px 60px rgba(0,0,0,0.3)' },
      default: {
        elevation: 10,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 10 },
        shadowOpacity: 0.3,
        shadowRadius: 20,
      },
    }),
  },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    padding: 16, borderBottomWidth: 1, borderColor: '#eee',
  },
  title: { fontSize: 17, fontWeight: '600' },
  label: { fontSize: 12, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5 },
  readOnly: { fontSize: 14, color: '#222', marginTop: 4 },
  input: {
    marginTop: 6, borderWidth: 1, borderColor: '#ddd', borderRadius: 6,
    paddingHorizontal: 10, paddingVertical: 8, fontSize: 14,
  },
  hint: { fontSize: 12, color: '#888', marginTop: 6 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 6 },
  chip: {
    borderWidth: 1, borderColor: '#ccc', borderRadius: 16,
    paddingHorizontal: 12, paddingVertical: 6, marginRight: 6, marginTop: 6,
  },
  chipOn: { backgroundColor: '#2563EB', borderColor: '#2563EB' },
  chipDisabled: { opacity: 0.4 },
  chipText: { fontSize: 12, color: '#333' },
  chipTextOn: { color: '#fff', fontWeight: '500' },
  switchRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    marginTop: 20,
  },
  error: { color: '#c44', marginTop: 12, fontSize: 13 },
  footer: {
    flexDirection: 'row', justifyContent: 'flex-end',
    padding: 12, borderTopWidth: 1, borderColor: '#eee',
  },
  btnGhost: { paddingHorizontal: 16, paddingVertical: 10, marginRight: 8 },
  btnGhostText: { color: '#555', fontSize: 14 },
  btnPrimary: {
    backgroundColor: '#2563EB', paddingHorizontal: 18, paddingVertical: 10,
    borderRadius: 6, minWidth: 80, alignItems: 'center',
  },
  btnPrimaryText: { color: '#fff', fontSize: 14, fontWeight: '600' },
});
