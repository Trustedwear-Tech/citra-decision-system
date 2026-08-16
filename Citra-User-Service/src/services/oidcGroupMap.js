// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

'use strict';
/**
 * OIDC group → dept/role mapping.
 *
 * WHY THIS EXISTS
 * ---------------
 * `oidcAuthService` JIT-provisions a first-time SSO user with ZERO access
 * (`dept_ids: []`, roles `['user']`) so an admin must explicitly grant them a
 * department. That is the right default for a deployment with a handful of
 * users, and completely unworkable for a customer embedding Decision Apps in
 * their own application: every app authorises through the app's audience
 * (`dept:<id>` / `team:<sa>` / `org`), so a zero-access user 404s on
 * `/apps/{slug}/run`. With 200 officers, that is 200 manual grants before
 * anybody can use the product.
 *
 * When a group map is configured, the customer's IdP becomes the source of
 * truth for `dept_ids` (and optionally roles): the officer's directory groups
 * are mapped to Citra depts on EVERY login. Access is then granted and revoked
 * in the customer's own directory, which is also what their infosec team wants
 * — an offboarded officer loses access at the IdP, not when someone remembers
 * to edit a Citra admin screen.
 *
 * CONFIG (`config/oidc-group-map.json`, override with OIDC_GROUP_MAP_PATH)
 * ----------------------------------------------------------------------
 *   {
 *     "claim": "groups",            // ID-token claim holding group names.
 *                                   // Dotted paths supported ("realm_access.roles").
 *     "case_insensitive": true,     // match group names ignoring case
 *     "groups": {
 *       "Credit-Ops-Mumbai": { "dept_ids": ["collections"] },
 *       "Credit-Risk":       { "dept_ids": ["credit"], "roles": ["decision-app-builder"] }
 *     }
 *   }
 *
 * ENABLED / DISABLED
 * ------------------
 * Enabled iff the map file exists. An explicitly-set OIDC_GROUP_MAP_PATH that
 * does not resolve is a hard error — a typo there would silently drop every
 * officer back to zero access, which looks exactly like "the product is
 * broken" and is miserable to diagnose. When no map is configured at all the
 * zero-access JIT behaviour is unchanged.
 *
 * FAIL LOUD
 * ---------
 * Every malformed-config path throws. A group map that half-loads would grant
 * the wrong departments, and a dept is a data-access boundary — the dept-MCP
 * scopes what rows an officer can read by it. Silence here is a security bug,
 * not a degraded feature.
 */

const fs = require('fs');
const path = require('path');
const { ROLES, ALL_ROLES } = require('../constants/roles');

const DEFAULT_MAP_PATH = './config/oidc-group-map.json';

/**
 * Roles a customer IdP group may NEVER grant.
 *
 * super_admin is the break-glass LOCAL account, IdP-independent BY DESIGN
 * (see the local-account guard in oidcAuthService). Letting a directory group
 * confer it would hand over the platform to anyone who can create a group in
 * the customer's AD — precisely the takeover that guard exists to prevent.
 */
const UNGRANTABLE_ROLES = Object.freeze([ROLES.SUPER_ADMIN]);

let _cache = null; // { enabled, absPath, claim, caseInsensitive, groups: Map }

function _resolvePath() {
  const configured = (process.env.OIDC_GROUP_MAP_PATH || '').trim();
  const raw = configured || DEFAULT_MAP_PATH;
  const absPath = path.isAbsolute(raw) ? raw : path.join(process.cwd(), raw);
  return { absPath, explicit: Boolean(configured) };
}

/**
 * Read + validate the group map. Memoized per process (the file is read at
 * boot and on first login; a changed map needs a restart, same as depts.seed).
 *
 * @returns {{enabled: boolean, claim: string, caseInsensitive: boolean,
 *            groups: Map<string, {dept_ids: string[], roles: string[]}>,
 *            absPath: string|null}}
 * @throws {Error} on a missing explicit path or any malformed entry
 */
function loadGroupMap() {
  if (_cache) return _cache;

  const { absPath, explicit } = _resolvePath();
  if (!fs.existsSync(absPath)) {
    if (explicit) {
      throw new Error(
        `[oidcGroupMap] OIDC_GROUP_MAP_PATH is set to "${absPath}" but no file ` +
        `exists there. Refusing to start: every SSO user would silently land ` +
        `with zero access. Fix the path or unset the variable.`
      );
    }
    _cache = { enabled: false, claim: null, caseInsensitive: false,
               groups: new Map(), absPath: null };
    return _cache;
  }

  let doc;
  try {
    doc = JSON.parse(fs.readFileSync(absPath, 'utf-8'));
  } catch (err) {
    throw new Error(`[oidcGroupMap] could not parse ${absPath}: ${err.message}`);
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    throw new Error(`[oidcGroupMap] ${absPath} must be a JSON object`);
  }

  const claim = typeof doc.claim === 'string' && doc.claim.trim()
    ? doc.claim.trim()
    : 'groups';
  const caseInsensitive = doc.case_insensitive !== false; // default ON
  const rawGroups = doc.groups;
  if (!rawGroups || typeof rawGroups !== 'object' || Array.isArray(rawGroups)) {
    throw new Error(`[oidcGroupMap] ${absPath} must have a "groups" object`);
  }
  const names = Object.keys(rawGroups);
  if (names.length === 0) {
    throw new Error(
      `[oidcGroupMap] ${absPath} has an empty "groups" object — that would map ` +
      `every officer to zero access. Delete the file to disable group mapping ` +
      `instead of shipping an empty one.`
    );
  }

  const groups = new Map();
  for (const name of names) {
    const entry = rawGroups[name] || {};
    if (typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`[oidcGroupMap] group "${name}": value must be an object`);
    }
    const deptIds = entry.dept_ids === undefined ? [] : entry.dept_ids;
    const roles = entry.roles === undefined ? [] : entry.roles;
    if (!Array.isArray(deptIds) || deptIds.some(d => typeof d !== 'string' || !d.trim())) {
      throw new Error(
        `[oidcGroupMap] group "${name}": dept_ids must be an array of non-empty strings`
      );
    }
    if (!Array.isArray(roles) || roles.some(r => typeof r !== 'string' || !r.trim())) {
      throw new Error(
        `[oidcGroupMap] group "${name}": roles must be an array of non-empty strings`
      );
    }
    for (const r of roles) {
      if (!ALL_ROLES.includes(r)) {
        throw new Error(
          `[oidcGroupMap] group "${name}": unknown role "${r}". Valid roles: ` +
          ALL_ROLES.join(', ')
        );
      }
      if (UNGRANTABLE_ROLES.includes(r)) {
        throw new Error(
          `[oidcGroupMap] group "${name}": role "${r}" cannot be granted from an ` +
          `IdP group — it is the break-glass local account role.`
        );
      }
    }
    if (deptIds.length === 0 && roles.length === 0) {
      throw new Error(
        `[oidcGroupMap] group "${name}": grants neither dept_ids nor roles. ` +
        `Remove it rather than mapping it to nothing.`
      );
    }
    const key = caseInsensitive ? name.toLowerCase() : name;
    if (groups.has(key)) {
      throw new Error(
        `[oidcGroupMap] duplicate group "${name}" (case-insensitive matching is ` +
        `on, so it collides with an earlier entry)`
      );
    }
    groups.set(key, {
      dept_ids: deptIds.map(d => d.trim()),
      roles: roles.map(r => r.trim()),
    });
  }

  _cache = { enabled: true, claim, caseInsensitive, groups, absPath };
  return _cache;
}

/** Test/boot hook — drop the memoized map so the next load re-reads the file. */
function resetCache() {
  _cache = null;
}

function isEnabled() {
  return loadGroupMap().enabled;
}

/**
 * Read a (possibly dotted) claim path out of the verified ID-token claims and
 * normalise it to a list of group names.
 *
 * Accepts the three shapes IdPs actually emit: an array of strings, a single
 * string, or a delimited string (space/comma — Keycloak and some SAML bridges).
 */
function extractGroups(claims, claimPath) {
  let node = claims;
  for (const seg of String(claimPath).split('.')) {
    if (node === null || node === undefined || typeof node !== 'object') return [];
    node = node[seg];
  }
  if (node === null || node === undefined) return [];
  if (Array.isArray(node)) {
    return node.filter(g => typeof g === 'string' && g.trim()).map(g => g.trim());
  }
  if (typeof node === 'string') {
    return node.split(/[,\s]+/).map(g => g.trim()).filter(Boolean);
  }
  return [];
}

/**
 * Resolve an officer's dept_ids + roles from their verified ID-token claims.
 *
 * Returns null when group mapping is disabled (caller keeps the zero-access
 * JIT behaviour). Otherwise returns the union across every group that matched,
 * plus the diagnostic lists a support engineer needs when an officer says
 * "it says app not found": which of their groups we recognised, and which we
 * ignored.
 *
 * An officer in NO mapped group resolves to `dept_ids: []` — access revoked.
 * That is the intended semantic, not an error: it is how removing someone from
 * a directory group removes their access here.
 */
function resolveFromClaims(claims) {
  const map = loadGroupMap();
  if (!map.enabled) return null;

  const present = extractGroups(claims || {}, map.claim);
  const deptIds = new Set();
  const roles = new Set([ROLES.USER]);
  const matched = [];
  const ignored = [];

  for (const g of present) {
    const key = map.caseInsensitive ? g.toLowerCase() : g;
    const entry = map.groups.get(key);
    if (!entry) {
      ignored.push(g);
      continue;
    }
    matched.push(g);
    entry.dept_ids.forEach(d => deptIds.add(d));
    entry.roles.forEach(r => roles.add(r));
  }

  return {
    dept_ids: Array.from(deptIds),
    roles: Array.from(roles),
    matched,
    ignored,
    claim: map.claim,
    groups_in_token: present.length,
  };
}

/**
 * Assert every dept_id the map can grant exists in this deployment's dept
 * catalogue. Called once at startup (server.js, after seedDepts).
 *
 * A mapping onto a dept that does not exist is undetectable at login time —
 * the officer just gets an app they cannot open — so it is checked at boot
 * where it fails in front of whoever deployed it.
 *
 * @param {string} orgId - deployment ORG_ID
 * @throws {Error} when the map references depts the catalogue does not have
 */
async function validateDeptsExist(orgId) {
  const map = loadGroupMap();
  if (!map.enabled) return { enabled: false, checked: 0 };
  if (!orgId) {
    throw new Error(
      '[oidcGroupMap] a group map is configured but ORG_ID is not set — the ' +
      'dept catalogue is org-scoped, so mapped depts cannot be validated.'
    );
  }

  const Dept = require('../models/Dept');
  const wanted = new Set();
  for (const entry of map.groups.values()) {
    entry.dept_ids.forEach(d => wanted.add(d));
  }
  if (wanted.size === 0) return { enabled: true, checked: 0 };

  const rows = await Dept.find(
    { org_id: orgId, id: { $in: Array.from(wanted) } }, { id: 1 }
  ).lean();
  const known = new Set(rows.map(r => r.id));
  const missing = Array.from(wanted).filter(d => !known.has(d));
  if (missing.length) {
    throw new Error(
      `[oidcGroupMap] ${map.absPath} maps groups onto dept(s) that do not exist ` +
      `in org "${orgId}": ${missing.join(', ')}. Add them to config/depts.seed.json ` +
      `or correct the mapping — officers mapped there would get an app they ` +
      `cannot open.`
    );
  }
  return { enabled: true, checked: wanted.size };
}

module.exports = {
  DEFAULT_MAP_PATH,
  UNGRANTABLE_ROLES,
  loadGroupMap,
  resetCache,
  isEnabled,
  extractGroups,
  resolveFromClaims,
  validateDeptsExist,
};
