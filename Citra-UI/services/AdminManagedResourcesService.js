/**
 * AdminManagedResourcesService — admin-page client for managed resources.
 *
 * Surfaces every workflow / smart-app the caller (dept_admin /
 * org_admin / super_admin) is entitled to see. Powers AdminManagedResourcesScreen.
 *
 * Backend endpoints:
 *   GET /api/admin/workflows   (citra-workflow)
 *   GET /admin/apps            (smart-app-service)
 */

import { CONFIG } from '../config/config';
import authService from './authService';


function _baseUrl(serviceKey, fallback) {
  return (CONFIG.urls?.[serviceKey] || fallback || '').replace(/\/$/, '');
}


async function _get(url) {
  const resp = await authService.authenticatedFetch(url, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!resp.ok) {
    const text = await resp.text();
    const err = new Error(`${url} → ${resp.status}: ${text}`);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}


export async function listAdminWorkflows(limit = 200) {
  const base = _baseUrl('workflowService', CONFIG.WORKFLOW_SERVICE_URL);
  if (!base) return [];
  const data = await _get(`${base}/api/admin/workflows?limit=${limit}`);
  return data.workflows || [];
}


export async function listAdminSmartApps(limit = 200) {
  const base = _baseUrl('smartAppService', CONFIG.SMART_APP_SERVICE_URL);
  if (!base) return [];
  const data = await _get(`${base}/admin/apps?limit=${limit}`);
  return data.apps || [];
}


export default {
  listAdminWorkflows,
  listAdminSmartApps,
};
