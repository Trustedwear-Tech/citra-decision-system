/**
 * TransferResourceModal — admin-only modal to re-home a resource
 * (workflow / smart-app) to a different SA / dept / org.
 *
 * Used from the per-row Transfer button in the Admin tab of:
 *   components/WorkflowBuilder/WorkflowListScreen.js
 *   screens/PowerAppsScreen.js
 *
 * The caller passes:
 *   resource: { kind, id, name, owner_type, owner_id, org_id, dept_ids }
 *   onSubmit({ target_owner_type, target_owner_id, reason })
 *
 * RBAC + actual transfer happens server-side. This modal is a thin
 * dispatcher — it just collects the new owner and calls back.
 */

import React, { useEffect, useState } from 'react';
import {
  Modal, View, Text, TextInput, TouchableOpacity, ActivityIndicator,
  StyleSheet, ScrollView, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const TARGETS = [
  { id: 'service_account', label: 'Service Account', hint: 'Move to another SA (the new SA admins control it)' },
  { id: 'dept',            label: 'Department',      hint: 'Move to a dept (the whole dept inherits ownership)' },
  { id: 'org',             label: 'Organization',    hint: 'Org-wide ownership (visible across the org)' },
];

export default function TransferResourceModal({
  visible,
  resource,
  onClose,
  onSubmit,
  theme = {},
}) {
  const colors = {
    bg:      theme.background    || '#FFFFFF',
    surface: theme.surface       || '#F9FAFB',
    text:    theme.text          || '#1F2937',
    sub:     theme.textSecondary || '#6B7280',
    primary: theme.primary       || '#6366F1',
    border:  theme.border        || '#E5E7EB',
    danger:  '#DC2626',
    warning: '#F59E0B',
  };

  const [targetOwnerType, setTargetOwnerType] = useState('service_account');
  const [targetOwnerId, setTargetOwnerId]     = useState('');
  const [reason, setReason]                   = useState('');
  const [submitting, setSubmitting]           = useState(false);
  const [error, setError]                     = useState('');

  useEffect(() => {
    if (!visible) return;
    setTargetOwnerType('service_account');
    setTargetOwnerId('');
    setReason('');
    setSubmitting(false);
    setError('');
  }, [visible]);

  if (!resource) return null;

  const handleSubmit = async () => {
    setError('');
    if (!targetOwnerId.trim()) {
      setError('Enter the new owner id (SA id, dept id, or org id)');
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({
        target_owner_type: targetOwnerType,
        target_owner_id:   targetOwnerId.trim(),
        reason:            reason.trim() || undefined,
      });
      onClose && onClose();
    } catch (e) {
      setError(e.message || 'Transfer failed');
    } finally {
      setSubmitting(false);
    }
  };

  const placeholder =
    targetOwnerType === 'service_account' ? 'svc:work-<user>@<org>.citra.ai  OR  svc:<dept>-team@<org>.citra.ai' :
    targetOwnerType === 'dept'            ? 'dept-id (e.g. dept-eng)' :
                                            'org-id (e.g. acme)';

  return (
    <Modal transparent animationType="fade" visible={visible} onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={[styles.card, { backgroundColor: colors.bg, borderColor: colors.border }]}>
          <View style={[styles.header, { borderColor: colors.border }]}>
            <Text style={[styles.title, { color: colors.text }]}>
              Transfer {resource.kind || 'resource'}
            </Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={20} color={colors.sub} />
            </TouchableOpacity>
          </View>

          <ScrollView contentContainerStyle={{ padding: 16 }} keyboardShouldPersistTaps="handled">
            {/* Source summary */}
            <View style={[styles.sourceBox, { borderColor: colors.border, backgroundColor: colors.surface }]}>
              <Text style={{ color: colors.sub, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
                Currently owned by
              </Text>
              <Text style={{ color: colors.text, fontSize: 13, fontWeight: '600' }}>
                {resource.name || resource.id}
              </Text>
              <Text style={{ color: colors.sub, fontSize: 12, marginTop: 2 }}>
                {resource.owner_type} · {resource.owner_id}
              </Text>
              {resource.org_id ? (
                <Text style={{ color: colors.sub, fontSize: 11, marginTop: 2 }}>
                  org: {resource.org_id}{resource.dept_ids && resource.dept_ids.length ? `  ·  dept: ${resource.dept_ids.join(', ')}` : ''}
                </Text>
              ) : null}
            </View>

            {/* Target type chips */}
            <Text style={[styles.label, { color: colors.sub }]}>New owner type</Text>
            <View style={styles.typeRow}>
              {TARGETS.map((t) => {
                const active = targetOwnerType === t.id;
                return (
                  <TouchableOpacity
                    key={t.id}
                    onPress={() => setTargetOwnerType(t.id)}
                    style={[
                      styles.typeChip,
                      {
                        backgroundColor: active ? colors.primary : colors.surface,
                        borderColor: colors.border,
                      },
                    ]}
                  >
                    <Text style={{
                      color: active ? '#fff' : colors.text,
                      fontSize: 12,
                      fontWeight: active ? '600' : '500',
                    }}>
                      {t.label}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            <Text style={{ color: colors.sub, fontSize: 11, marginTop: 4, marginBottom: 8 }}>
              {TARGETS.find((t) => t.id === targetOwnerType)?.hint}
            </Text>

            <Text style={[styles.label, { color: colors.sub }]}>
              {targetOwnerType === 'service_account' ? 'Target Service Account id'
                : targetOwnerType === 'dept' ? 'Target dept id' : 'Target org id'}
            </Text>
            <TextInput
              style={[styles.input, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surface }]}
              value={targetOwnerId}
              onChangeText={setTargetOwnerId}
              placeholder={placeholder}
              placeholderTextColor={colors.sub}
              autoCapitalize="none"
            />

            <Text style={[styles.label, { color: colors.sub, marginTop: 12 }]}>Reason (audit trail)</Text>
            <TextInput
              style={[styles.input, { color: colors.text, borderColor: colors.border, backgroundColor: colors.surface, minHeight: 60 }]}
              value={reason}
              onChangeText={setReason}
              placeholder="e.g. user leaving, dept reorganisation, change of ownership"
              placeholderTextColor={colors.sub}
              multiline
            />

            {error ? (
              <View style={[styles.errorBox, { borderColor: colors.danger }]}>
                <Ionicons name="alert-circle" size={14} color={colors.danger} />
                <Text style={{ color: colors.danger, fontSize: 12, marginLeft: 6, flex: 1 }}>{error}</Text>
              </View>
            ) : null}
          </ScrollView>

          <View style={[styles.footer, { borderColor: colors.border }]}>
            <TouchableOpacity style={[styles.btn, { borderColor: colors.border }]} onPress={onClose} disabled={submitting}>
              <Text style={{ color: colors.sub, fontSize: 13 }}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.btn, { backgroundColor: colors.primary, opacity: submitting ? 0.6 : 1 }]}
              onPress={handleSubmit}
              disabled={submitting}
            >
              {submitting
                ? <ActivityIndicator color="#fff" size="small" />
                : <Text style={{ color: '#fff', fontSize: 13, fontWeight: '600' }}>Transfer</Text>}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}


const styles = StyleSheet.create({
  backdrop: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'center', alignItems: 'center', padding: 16,
  },
  card: {
    width: '100%', maxWidth: 560, maxHeight: '90%',
    borderRadius: 12, borderWidth: 1, overflow: 'hidden',
  },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    padding: 16, borderBottomWidth: 1,
  },
  title: { fontSize: 16, fontWeight: '600' },
  sourceBox: {
    borderWidth: 1, borderRadius: 8, padding: 12, marginBottom: 16,
  },
  label: { fontSize: 11, marginTop: 6, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 },
  input: {
    borderWidth: 1, borderRadius: 6, paddingHorizontal: 10, paddingVertical: Platform.OS === 'web' ? 8 : 10,
    fontSize: 13,
  },
  typeRow: { flexDirection: 'row', flexWrap: 'wrap' },
  typeChip: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, borderWidth: 1,
    marginRight: 6, marginBottom: 4,
  },
  errorBox: {
    flexDirection: 'row', alignItems: 'center',
    borderWidth: 1, borderRadius: 6, padding: 8, marginTop: 12,
  },
  footer: {
    flexDirection: 'row', justifyContent: 'flex-end',
    padding: 12, borderTopWidth: 1,
  },
  btn: {
    paddingHorizontal: 16, paddingVertical: 9, borderRadius: 6,
    marginLeft: 8, borderWidth: 1, borderColor: 'transparent',
    minWidth: 92, alignItems: 'center',
  },
});
