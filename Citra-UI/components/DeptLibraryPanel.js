// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * DeptLibraryPanel — the department SOP Library admin surface (HomePanel Admin
 * section). Manage dept-owned libraries of reference documents (SOPs, policies,
 * manuals) that the dept-MCP queries for RAG.
 *
 * This panel is slice 3: library lifecycle (create / list / delete), backed by
 * Citra-Service dept_library.py. Reads are dept-scoped server-side; create and
 * delete are curator-only (dept_admin/org_admin/super_admin) — the server is
 * the gate, `canManage` only hides the controls.
 *
 * Document UPLOAD + ingestion into the per-dept Milvus collection is the
 * remaining ingestion work (a deeper change — the platform's document pipeline
 * writes one shared collection today, while the MCP queries a per-source
 * collection). The "Upload SOPs" affordance is surfaced but gated on that.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator, TextInput,
  StyleSheet, ScrollView, Alert, Platform, Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import DeptLibraryService from '../services/DeptLibraryService';

const DEFAULT_THEME = {
  background: '#FFFFFF', surface: '#F9FAFB', surfaceAlt: '#F3F4F6',
  text: '#111827', textSecondary: '#6B7280', primary: '#3B82F6',
  border: '#E5E7EB', danger: '#DC2626',
};

// Document categories a Decision App's document panel — and a rag tool — filter
// on (`doc_types`). A document uploaded WITHOUT one is stored with doc_type=""
// and is then invisible to every filtered panel: retrievable by chat, but the
// panel renders "No documents found" with no clue why. (That is exactly how the
// acme Policy Library sat blank over 12 healthy documents.) So we always send a
// category — derived from the filename, curator-confirmable.
// Keep in sync with demo-data/tenants/*/scripts/ingest_docs.py DOC_TYPE_RULES.
const DOC_TYPES = [
  'sop', 'charter', 'circular', 'tariff_order', 'guideline',
  'template', 'rules', 'protocol', 'policy',
];
const DOC_TYPE_RULES = [
  ['sop', 'sop'], ['tariff_order', 'tariff_order'], ['circular', 'circular'],
  ['charter', 'charter'], ['guidelines', 'guideline'], ['guideline', 'guideline'],
  ['template', 'template'], ['protocol', 'protocol'], ['rules', 'rules'],
];

/** Suggest a category from the filename (first keyword wins; 'policy' fallback) —
 *  the same rule the server-side ingest uses, so a re-upload keeps its category. */
export function docTypeForFilename(filename) {
  const stem = String(filename || '').replace(/\.[^.]+$/, '').toLowerCase();
  const hit = DOC_TYPE_RULES.find(([kw]) => stem.includes(kw));
  return hit ? hit[1] : 'policy';
}

export default function DeptLibraryPanel({
  visible, onClose, theme, canManage = false, deptIds = [],
}) {
  const colors = useMemo(() => ({ ...DEFAULT_THEME, ...(theme || {}) }), [theme]);

  const [libraries, setLibraries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Create form (curators only). dept comes from the user's dept list; if the
  // user administers multiple depts they pick which one owns the library.
  const [creating, setCreating] = useState(false);
  const [newDept, setNewDept] = useState(deptIds[0] || '');
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  // Public-within-org: any user in the org can read/query it (not just the dept).
  const [newPublic, setNewPublic] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploadingId, setUploadingId] = useState(null);
  // Per-document in-flight action (`${document_id}:view|download|delete`) — so a
  // row's own button spins and a double-tap can't fire the action twice.
  const [busyDoc, setBusyDoc] = useState(null);
  // In-app document reader (View) — { url, name }. `url` is a blob object URL we
  // render in an <iframe> (web) and MUST revoke on close.
  const [reader, setReader] = useState(null);
  // Per-library document list (expand a library to see + manage its docs).
  const [expandedId, setExpandedId] = useState(null);
  const [docsByLib, setDocsByLib] = useState({});   // { [libId]: {loading, docs, error} }

  const loadDocs = useCallback(async (libId) => {
    setDocsByLib((s) => ({ ...s, [libId]: { ...(s[libId] || {}), loading: true, error: '' } }));
    try {
      const d = await DeptLibraryService.listDocuments(libId);
      setDocsByLib((s) => ({ ...s, [libId]: { loading: false, docs: d?.documents || [], error: '' } }));
    } catch (e) {
      setDocsByLib((s) => ({ ...s, [libId]: { loading: false, docs: [], error: e?.message || 'Could not load documents.' } }));
    }
  }, []);

  const toggleExpand = useCallback((libId) => {
    setExpandedId((cur) => {
      const next = cur === libId ? null : libId;
      if (next && !docsByLib[libId]) loadDocs(libId);
      return next;
    });
  }, [docsByLib, loadDocs]);

  const closeReader = useCallback(() => {
    setReader((r) => {
      if (r?.url) { try { URL.revokeObjectURL(r.url); } catch (_) { /* noop */ } }
      return null;
    });
  }, []);

  // View — open the document in the IN-APP reader: fetch the same-origin proxy
  // blob and render it in an <iframe> modal (like the folder panel's reader),
  // rather than spawning a browser tab.
  const viewDoc = useCallback(async (libId, doc) => {
    if (Platform.OS !== 'web') {
      Alert.alert('View', 'Opening documents is a web action for now. Open the SOP Library in a browser.');
      return;
    }
    setBusyDoc(`${doc.document_id}:view`);
    try {
      const url = await DeptLibraryService.getInlineBlobUrl(libId, doc.document_id);
      setReader((prev) => {
        if (prev?.url) { try { URL.revokeObjectURL(prev.url); } catch (_) { /* noop */ } }
        return { url, name: doc.filename };
      });
    } catch (e) {
      Alert.alert('View failed', e?.message || 'Could not open the document.');
    } finally {
      setBusyDoc(null);
    }
  }, []);

  // Download — fetch the same-origin proxy blob and save it via an anchor. A
  // same-origin blob URL honours the `download` attribute (filename), which a
  // cross-origin S3 URL would ignore.
  const downloadDoc = useCallback(async (libId, doc) => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') {
      Alert.alert('Download', 'Downloading is a web action for now. Open the SOP Library in a browser.');
      return;
    }
    setBusyDoc(`${doc.document_id}:download`);
    try {
      const url = await DeptLibraryService.getInlineBlobUrl(libId, doc.document_id);
      const a = document.createElement('a');
      a.href = url;
      a.download = doc.filename || 'document';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => { try { URL.revokeObjectURL(url); } catch (_) { /* noop */ } }, 10000);
    } catch (e) {
      Alert.alert('Download failed', e?.message || 'Could not download the document.');
    } finally {
      setBusyDoc(null);
    }
  }, []);

  const deleteDoc = useCallback(async (libId, doc) => {
    const go = async () => {
      setBusyDoc(`${doc.document_id}:delete`);
      try {
        await DeptLibraryService.deleteDocument(libId, doc.document_id);
        loadDocs(libId);
      } catch (e) {
        Alert.alert('Delete failed', e?.message || 'Could not delete the document.');
      } finally {
        setBusyDoc(null);
      }
    };
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      // eslint-disable-next-line no-alert
      if (window.confirm(`Delete "${doc.filename}" from this library?`)) go();
    } else {
      Alert.alert('Delete document', `Delete "${doc.filename}"?`,
        [{ text: 'Cancel', style: 'cancel' },
         { text: 'Delete', style: 'destructive', onPress: go }]);
    }
  }, [loadDocs]);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const d = await DeptLibraryService.listLibraries();
      setLibraries(d?.folders || []);
    } catch (e) {
      setError(e?.message || 'Could not load libraries.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (visible) load(); }, [visible, load]);

  const create = useCallback(async () => {
    const dept = (newDept || '').trim();
    const name = (newName || '').trim();
    if (!dept || !name) {
      Alert.alert('SOP Library', 'A department and a library name are both required.');
      return;
    }
    setSaving(true);
    try {
      const res = await DeptLibraryService.createLibrary(dept, { name, description: newDesc, public: newPublic });
      setCreating(false); setNewName(''); setNewDesc(''); setNewPublic(false);
      load();
      // The library may be created but not yet searchable if MCP registration
      // failed server-side — surface that so the admin retries, never silent.
      if (res && res.registered === false) {
        Alert.alert('Library created — action needed',
          res.warning || 'The library was created but is not yet searchable. Re-save it to retry registration.');
      }
    } catch (e) {
      Alert.alert('Create failed', e?.message || 'Could not create the library.');
    } finally {
      setSaving(false);
    }
  }, [newDept, newName, newDesc, load]);

  // Web file-picker upload. The uploaded file is extracted + embedded into the
  // dept's Milvus collection server-side (the one the dept-MCP queries).
  const uploadTo = useCallback((lib) => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') {
      Alert.alert('Upload', 'SOP upload is a web action for now. Open the SOP Library in a browser to upload.');
      return;
    }
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf,.doc,.docx,.txt,.md,.html';
    input.onchange = async () => {
      const f = input.files && input.files[0];
      if (!f) return;
      // Category is REQUIRED for the doc to appear in a filtered Decision-App
      // panel. Suggest from the filename, let the curator confirm/override.
      const suggested = docTypeForFilename(f.name);
      const answer = (typeof window !== 'undefined' && window.prompt)
        ? window.prompt(
            `Category for "${f.name}"\n\n`
            + 'Decision App document panels filter on this — a document with no '
            + 'category will not appear in them.\n\n'
            + `One of: ${DOC_TYPES.join(', ')}`,
            suggested)
        : suggested;
      if (answer === null) return;                      // curator cancelled
      const docType = (String(answer || suggested).trim().toLowerCase()) || suggested;
      setUploadingId(lib.id);
      try {
        const res = await DeptLibraryService.uploadDocument(lib.id, f, docType);
        Alert.alert('Uploaded', `"${f.name}" added to ${lib.name} as ${docType}${res?.chunks ? ` (${res.chunks} chunks)` : ''}.`);
        // reveal the new doc: expand + refresh the library's document list
        setExpandedId(lib.id);
        loadDocs(lib.id);
      } catch (e) {
        Alert.alert('Upload failed', e?.message || 'Could not upload the document.');
      } finally {
        setUploadingId(null);
      }
    };
    input.click();
  }, []);

  const remove = useCallback((lib) => {
    const doDelete = async () => {
      try {
        await DeptLibraryService.deleteLibrary(lib.id);
        setLibraries((prev) => prev.filter((l) => l.id !== lib.id));
      } catch (e) {
        Alert.alert('Delete failed', e?.message || 'Could not delete the library.');
      }
    };
    // RN Alert confirm on native; window.confirm on web.
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      // eslint-disable-next-line no-alert
      if (window.confirm(`Delete "${lib.name}" and its documents? This cannot be undone.`)) doDelete();
    } else {
      Alert.alert('Delete library', `Delete "${lib.name}" and its documents?`,
        [{ text: 'Cancel', style: 'cancel' },
         { text: 'Delete', style: 'destructive', onPress: doDelete }]);
    }
  }, []);

  if (!visible) return null;

  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <View style={{ flex: 1 }}>
          <Text style={[styles.title, { color: colors.text }]}>SOP Library</Text>
          <Text style={[styles.subtitle, { color: colors.textSecondary }]}>
            Department libraries of SOPs, policies and manuals — the reference knowledge your Decision Apps and chat draw on.
          </Text>
        </View>
        {canManage && (
          <TouchableOpacity onPress={() => setCreating((v) => !v)} style={[styles.newBtn, { backgroundColor: colors.primary }]}>
            <Ionicons name="add" size={18} color="#fff" />
            <Text style={{ color: '#fff', fontWeight: '700', fontSize: 14 }}>New library</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={onClose} hitSlop={10} style={{ marginLeft: 6 }}>
          <Ionicons name="close" size={26} color={colors.textSecondary} />
        </TouchableOpacity>
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 24, gap: 16, maxWidth: 1040, width: '100%', alignSelf: 'center' }}>
        {creating && canManage && (
          <View style={[styles.card, { borderColor: colors.border, backgroundColor: colors.background, gap: 12 }]}>
            <Text style={{ color: colors.text, fontWeight: '800', fontSize: 16 }}>New department library</Text>
            {deptIds.length > 1 ? (
              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                {deptIds.map((d) => (
                  <TouchableOpacity key={d} onPress={() => setNewDept(d)} style={[styles.chip, {
                    borderColor: newDept === d ? colors.primary : colors.border,
                    backgroundColor: newDept === d ? `${colors.primary}14` : colors.background,
                  }]}>
                    <Text style={{ color: newDept === d ? colors.primary : colors.text, fontSize: 13, fontWeight: '600' }}>{d}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            ) : (
              <TextInput
                value={newDept} onChangeText={setNewDept} placeholder="Department id (e.g. operations)"
                placeholderTextColor={colors.textSecondary}
                style={[styles.input, { borderColor: colors.border, color: colors.text }]}
              />
            )}
            <TextInput
              value={newName} onChangeText={setNewName} placeholder="Library name (e.g. Operations SOPs)"
              placeholderTextColor={colors.textSecondary}
              style={[styles.input, { borderColor: colors.border, color: colors.text }]}
            />
            <TextInput
              value={newDesc} onChangeText={setNewDesc} placeholder="Description (optional)"
              placeholderTextColor={colors.textSecondary}
              style={[styles.input, { borderColor: colors.border, color: colors.text }]}
            />
            <TouchableOpacity
              onPress={() => setNewPublic((v) => !v)}
              style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8, paddingVertical: 2 }}
              accessibilityRole="checkbox" accessibilityState={{ checked: newPublic }}>
              <Ionicons
                name={newPublic ? 'checkbox' : 'square-outline'}
                size={20} color={newPublic ? colors.primary : colors.textSecondary} />
              <View style={{ flex: 1 }}>
                <Text style={{ color: colors.text, fontSize: 14, fontWeight: '600' }}>Public within organization</Text>
                <Text style={{ color: colors.textSecondary, fontSize: 12, lineHeight: 17 }}>
                  {newPublic
                    ? `Any user in the org can query this library — not just ${(newDept || 'the').trim() || 'the'} dept.`
                    : `Only ${(newDept || 'this').trim() || 'this'} department’s members can query this library.`}
                </Text>
              </View>
            </TouchableOpacity>
            <View style={{ flexDirection: 'row', gap: 10, marginTop: 4 }}>
              <TouchableOpacity disabled={saving} onPress={create} style={[styles.btn, { backgroundColor: colors.primary }]}>
                <Text style={{ color: '#fff', fontWeight: '700', fontSize: 15 }}>{saving ? 'Creating…' : 'Create'}</Text>
              </TouchableOpacity>
              <TouchableOpacity disabled={saving} onPress={() => setCreating(false)} style={[styles.btn, { backgroundColor: colors.surfaceAlt }]}>
                <Text style={{ color: colors.text, fontSize: 15, fontWeight: '600' }}>Cancel</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}

        {loading && (
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <ActivityIndicator size="small" color={colors.text} />
            <Text style={{ color: colors.text }}>Loading…</Text>
          </View>
        )}
        {!!error && <Text style={{ color: colors.danger }}>{error}</Text>}

        {!loading && libraries.map((lib) => (
          <View key={lib.id} style={[styles.card, { borderColor: colors.border, backgroundColor: colors.background }]}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
              <Text style={{ color: colors.text, fontWeight: '700', fontSize: 18, letterSpacing: -0.2, flex: 1 }}>{lib.name}</Text>
              {lib.public_within_org && (
                <View style={[styles.badge, { backgroundColor: `${colors.primary}14` }]}>
                  <Ionicons name="globe-outline" size={13} color={colors.primary} />
                  <Text style={{ color: colors.primary, fontSize: 12, fontWeight: '700' }}>Public</Text>
                </View>
              )}
              <Text style={[styles.deptTag, { color: colors.textSecondary, backgroundColor: colors.surfaceAlt }]}>{lib.dept_id}</Text>
            </View>
            {!!lib.description && (
              <Text style={{ color: colors.text, fontSize: 14, lineHeight: 20 }}>{lib.description}</Text>
            )}
            <Text style={{ color: colors.textSecondary, fontSize: 12.5 }}>
              {lib.public_within_org
                ? 'Any user in the organization can query this library.'
                : `Everyone in ${lib.dept_id} can query this library.`}
            </Text>
            <View style={{ flexDirection: 'row', gap: 20, paddingTop: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <TouchableOpacity onPress={() => toggleExpand(lib.id)} style={styles.inlineBtn}>
                <Ionicons name={expandedId === lib.id ? 'chevron-down' : 'chevron-forward'} size={16} color={colors.text} />
                <Text style={{ color: colors.text, fontSize: 14, fontWeight: '600' }}>Documents</Text>
              </TouchableOpacity>
              {canManage && (
                <TouchableOpacity onPress={() => uploadTo(lib)} disabled={uploadingId === lib.id} style={styles.inlineBtn}>
                  <Ionicons name="cloud-upload-outline" size={16} color={colors.primary} />
                  <Text style={{ color: colors.primary, fontSize: 14, fontWeight: '600' }}>
                    {uploadingId === lib.id ? 'Uploading…' : 'Upload SOPs'}
                  </Text>
                </TouchableOpacity>
              )}
              {canManage && (
                <TouchableOpacity onPress={() => remove(lib)} style={styles.inlineBtn}>
                  <Ionicons name="trash-outline" size={16} color={colors.danger} />
                  <Text style={{ color: colors.danger, fontSize: 14, fontWeight: '600' }}>Delete library</Text>
                </TouchableOpacity>
              )}
            </View>

            {expandedId === lib.id && (
              <View style={{ marginTop: 10, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 12, gap: 10 }}>
                {docsByLib[lib.id]?.loading && (
                  <Text style={{ color: colors.textSecondary, fontSize: 13 }}>Loading documents…</Text>
                )}
                {!!docsByLib[lib.id]?.error && (
                  <Text style={{ color: colors.danger, fontSize: 13 }}>{docsByLib[lib.id].error}</Text>
                )}
                {(docsByLib[lib.id]?.docs || []).map((doc) => (
                  <View key={doc.document_id} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10, backgroundColor: colors.surface, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
                      <Ionicons name="document-text-outline" size={18} color={colors.textSecondary} />
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <Text style={{ color: colors.text, fontSize: 14, fontWeight: '600' }} numberOfLines={1}>{doc.filename}</Text>
                        <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                          {doc.chunks} chunk{doc.chunks === 1 ? '' : 's'}
                          {doc.uploaded_by ? ` · ${doc.uploaded_by}` : ''}
                        </Text>
                      </View>
                    </View>
                    <View style={{ flexDirection: 'row', gap: 16, alignItems: 'center' }}>
                      <TouchableOpacity
                        onPress={() => viewDoc(lib.id, doc)}
                        disabled={busyDoc === `${doc.document_id}:view`}
                        hitSlop={6} style={styles.inlineBtn} accessibilityLabel="View document">
                        {busyDoc === `${doc.document_id}:view`
                          ? <ActivityIndicator size="small" color={colors.primary} />
                          : <Ionicons name="eye-outline" size={18} color={colors.primary} />}
                      </TouchableOpacity>
                      <TouchableOpacity
                        onPress={() => downloadDoc(lib.id, doc)}
                        disabled={busyDoc === `${doc.document_id}:download`}
                        hitSlop={6} style={styles.inlineBtn} accessibilityLabel="Download document">
                        {busyDoc === `${doc.document_id}:download`
                          ? <ActivityIndicator size="small" color={colors.primary} />
                          : <Ionicons name="download-outline" size={18} color={colors.primary} />}
                      </TouchableOpacity>
                      {canManage && (
                        <TouchableOpacity
                          onPress={() => deleteDoc(lib.id, doc)}
                          disabled={busyDoc === `${doc.document_id}:delete`}
                          hitSlop={6} style={styles.inlineBtn} accessibilityLabel="Delete document">
                          {busyDoc === `${doc.document_id}:delete`
                            ? <ActivityIndicator size="small" color={colors.danger} />
                            : <Ionicons name="trash-outline" size={17} color={colors.danger} />}
                        </TouchableOpacity>
                      )}
                    </View>
                  </View>
                ))}
                {!docsByLib[lib.id]?.loading && (docsByLib[lib.id]?.docs || []).length === 0 && !docsByLib[lib.id]?.error && (
                  <Text style={{ color: colors.textSecondary, fontSize: 13 }}>
                    No documents yet.{canManage ? ' Use “Upload SOPs” to add one.' : ''}
                  </Text>
                )}
              </View>
            )}
          </View>
        ))}

        {!loading && libraries.length === 0 && !error && (
          <Text style={{ color: colors.textSecondary, fontSize: 14, lineHeight: 20 }}>
            {canManage
              ? 'No department libraries yet. Create one, then use “Upload SOPs” to add documents.'
              : 'No SOP libraries are shared with your department yet.'}
          </Text>
        )}
      </ScrollView>

      <View style={[styles.footer, { borderTopColor: colors.border }]}>
        <Text style={{ color: colors.textSecondary, fontSize: 12.5, lineHeight: 18 }}>
          Libraries belong to the department, not to you — they stay when people move on. Only dept/org admins can add or remove them.
        </Text>
      </View>

      {/* In-app document reader (View) — renders the fetched blob in an iframe on
          web, mirroring the folder panel's reader. */}
      <Modal visible={!!reader} transparent animationType="fade" onRequestClose={closeReader}>
        <View style={styles.readerOverlay}>
          <View style={[styles.readerCard, { backgroundColor: colors.background, borderColor: colors.border }]}>
            <View style={[styles.header, { borderBottomColor: colors.border, paddingVertical: 12 }]}>
              <Text style={[styles.title, { color: colors.text, fontSize: 15, flex: 1 }]} numberOfLines={1}>
                {reader?.name || 'Document'}
              </Text>
              <TouchableOpacity onPress={closeReader} hitSlop={10}>
                <Ionicons name="close" size={24} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            {Platform.OS === 'web' && reader?.url ? (
              <iframe
                src={reader.url}
                title={reader?.name || 'Document'}
                style={{ flex: 1, width: '100%', height: '100%', border: 'none', backgroundColor: '#fff' }}
              />
            ) : (
              <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 }}>
                <Text style={{ color: colors.textSecondary }}>Open the SOP Library in a browser to view documents.</Text>
              </View>
            )}
          </View>
        </View>
      </Modal>
    </View>
  );
}

const CARD_SHADOW = Platform.OS === 'web'
  ? { boxShadow: '0 1px 2px rgba(16,24,40,0.04), 0 4px 12px rgba(16,24,40,0.06)' }
  : {
      shadowColor: '#101828', shadowOpacity: 0.08, shadowRadius: 12,
      shadowOffset: { width: 0, height: 4 }, elevation: 2,
    };

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 14,
    paddingHorizontal: 28, paddingVertical: 22, borderBottomWidth: 1,
  },
  title: { fontSize: 24, fontWeight: '800', letterSpacing: -0.4, marginBottom: 4 },
  subtitle: { fontSize: 14, lineHeight: 20 },
  newBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 7,
    borderRadius: 999, paddingHorizontal: 18, paddingVertical: 11,
    ...(Platform.OS === 'web' ? { cursor: 'pointer' } : {}),
  },
  card: {
    borderWidth: 1, borderRadius: 16, padding: 20, gap: 10,
    ...CARD_SHADOW,
  },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 16, paddingVertical: 8 },
  input: {
    borderWidth: 1, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15,
    ...(Platform.OS === 'web' ? { outlineStyle: 'none' } : {}),
  },
  btn: {
    borderRadius: 10, paddingHorizontal: 20, paddingVertical: 12,
    ...(Platform.OS === 'web' ? { cursor: 'pointer' } : {}),
  },
  inlineBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start',
    ...(Platform.OS === 'web' ? { cursor: 'pointer' } : {}),
  },
  badge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4,
  },
  deptTag: {
    fontSize: 12, fontWeight: '600', borderRadius: 999,
    paddingHorizontal: 10, paddingVertical: 4, overflow: 'hidden',
  },
  footer: { borderTopWidth: 1, paddingHorizontal: 28, paddingVertical: 14 },
  readerOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)',
    padding: Platform.OS === 'web' ? 40 : 12,
  },
  readerCard: { flex: 1, borderRadius: 14, borderWidth: 1, overflow: 'hidden' },
});
