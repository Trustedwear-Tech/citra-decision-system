// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import { API_CONFIG } from '../config/config';
import authService from './authService';

/**
 * DeptLibraryService — client for the department SOP Library (Citra-Service
 * dept_library.py). Dept-owned document folders: reads are dept-scoped
 * (server-enforced), writes are curator-only (dept_admin/org_admin/super_admin
 * — the server returns 403 otherwise; the UI also hides the controls).
 *
 * Mirrors FolderExplorerService's auth + base-URL pattern.
 */
class DeptLibraryService {
  constructor() {
    this.baseURL = API_CONFIG.CITRA_SERVICE_URL || 'http://localhost:8085/citra-ai';
  }

  async _headers() {
    const token = await authService.getToken();
    if (!token) throw new Error('No authentication token found. Please log in.');
    return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  }

  async _json(res, what) {
    const text = await res.text();
    const body = text ? JSON.parse(text) : null;
    if (!res.ok) {
      const detail = body && (body.detail || body.message);
      throw new Error(
        (typeof detail === 'object' ? detail.message : detail) ||
        `${what} failed (${res.status})`,
      );
    }
    return body;
  }

  /** List dept SOP libraries the caller may read. Omit deptId to get every
   *  library the caller can see (own dept for members; all for org admins). */
  async listLibraries(deptId = null) {
    const headers = await this._headers();
    const q = deptId ? `?dept_id=${encodeURIComponent(deptId)}` : '';
    const res = await fetch(`${this.baseURL}/api/dept-library/folders${q}`, { headers });
    return this._json(res, 'List libraries');
  }

  /** Create a dept SOP library (curator-only). `public` = readable/queryable by
   *  ANY user in the org (not just the owning dept); defaults to dept-scoped. */
  async createLibrary(deptId, { name, description = '', color = '#6b7280', public: isPublic = false }) {
    const headers = await this._headers();
    const res = await fetch(
      `${this.baseURL}/api/dept-library/folders?dept_id=${encodeURIComponent(deptId)}`,
      { method: 'POST', headers, body: JSON.stringify({ name, description, color, public: !!isPublic }) },
    );
    return this._json(res, 'Create library');
  }

  /** Upload one SOP/policy/manual into a library (curator-only). The file is
   *  extracted, chunked, embedded and ingested into the dept's Milvus
   *  collection — the one the dept-MCP queries. `file` is a browser File.
   *
   *  `docType` is the document's category (sop / charter / circular /
   *  tariff_order / guideline / template / rules / protocol). It is stamped on
   *  every chunk and is what a Decision App's document panel — and a rag tool —
   *  filter on. Upload WITHOUT it and the document is retrievable but invisible
   *  to every filtered panel (this silently blanked the acme Policy Library:
   *  12 untagged docs behind a panel filtering `charter`). */
  async uploadDocument(folderId, file, docType) {
    const token = await authService.getToken();
    if (!token) throw new Error('No authentication token found. Please log in.');
    const form = new FormData();
    form.append('file', file);
    if (docType) form.append('doc_type', docType);
    const res = await fetch(
      `${this.baseURL}/api/dept-library/folders/${encodeURIComponent(folderId)}/documents`,
      { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form },
    );
    return this._json(res, 'Upload document');
  }

  /** List the documents inside a library (name, chunk count, when, uploader).
   *  Dept-scoped read. */
  async listDocuments(folderId) {
    const headers = await this._headers();
    const res = await fetch(
      `${this.baseURL}/api/dept-library/folders/${encodeURIComponent(folderId)}/documents`,
      { headers },
    );
    return this._json(res, 'List documents');
  }

  /** Fetch a library document via the same-origin proxy stream and return a blob
   *  object URL — powers BOTH the in-app reader (View, rendered in an <iframe>)
   *  and Download (a same-origin blob URL so the `download` attribute reliably
   *  saves with the right filename; a cross-origin S3 URL would ignore it). The
   *  proxy needs the bearer token, so we fetch bytes rather than open the URL
   *  directly. Read-scoped (any dept member). Caller MUST URL.revokeObjectURL()
   *  the result when done. Fails loud on an empty body (the proxy yields nothing
   *  if the S3 fetch failed upstream). */
  async getInlineBlobUrl(folderId, documentId) {
    const token = await authService.getToken();
    if (!token) throw new Error('No authentication token found. Please log in.');
    const res = await fetch(
      `${this.baseURL}/api/dept-library/folders/${encodeURIComponent(folderId)}/documents/${encodeURIComponent(documentId)}/proxy`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(text || `View document failed (${res.status})`);
    }
    const blob = await res.blob();
    if (!blob || blob.size === 0) {
      throw new Error('The document could not be loaded (empty response from storage).');
    }
    return URL.createObjectURL(blob);
  }

  /** Delete one document from a library (its vectors + chunks). Curator-only. */
  async deleteDocument(folderId, documentId) {
    const headers = await this._headers();
    const res = await fetch(
      `${this.baseURL}/api/dept-library/folders/${encodeURIComponent(folderId)}/documents/${encodeURIComponent(documentId)}`,
      { method: 'DELETE', headers },
    );
    return this._json(res, 'Delete document');
  }

  /** Delete a dept SOP library + its documents (curator-only). */
  async deleteLibrary(folderId) {
    const headers = await this._headers();
    const res = await fetch(
      `${this.baseURL}/api/dept-library/folders/${encodeURIComponent(folderId)}`,
      { method: 'DELETE', headers },
    );
    return this._json(res, 'Delete library');
  }
}

export default new DeptLibraryService();
