// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Integration check for oidcAuthService's department assignment.
 *
 * Stubs every Mongo-touching dependency so the real branch logic in
 * authenticateWithOidc runs standalone. Guards the two behaviours a customer
 * deployment depends on: officers arrive already entitled when a group map is
 * configured, and the zero-access JIT is untouched when one is not.
 *
 *   node scripts/test-oidc-group-map-integration.js
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'oidc-'));
process.env.ORG_ID = 'acme-bank';

let pass = 0, fail = 0;
function check(name, fn) {
  return fn().then(
    () => { console.log(`  ok   ${name}`); pass++; },
    e => { console.log(`  FAIL ${name}\n       ${e.message}`); fail++; });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

/** Load a fresh oidcAuthService with stubbed deps. `existing` = current DB doc. */
function freshService({ mapDoc, existing }) {
  const srcDir = path.join(ROOT, 'src');
  for (const k of Object.keys(require.cache)) {
    if (k.startsWith(srcDir)) delete require.cache[k];
  }
  if (mapDoc) {
    const p = path.join(tmp, `m-${Math.random().toString(36).slice(2)}.json`);
    fs.writeFileSync(p, JSON.stringify(mapDoc));
    process.env.OIDC_GROUP_MAP_PATH = p;
  } else {
    process.env.OIDC_GROUP_MAP_PATH = path.join(tmp, 'absent-by-design');
  }

  const stub = (rel, value) => {
    const resolved = require.resolve(path.join(ROOT, rel));
    require.cache[resolved] = { id: resolved, filename: resolved, loaded: true, exports: value };
  };

  const captured = {};
  stub('src/models/CitraAIUser', { findOne: async () => existing || null });
  stub('src/services/tokenService', { generateToken: async () => 'tok' });
  stub('src/services/personalSAService', { ensurePersonalSA: async () => {} });
  stub('src/services/workSAService', { ensureWorkSA: async () => {} });
  stub('src/services/usageTrackingService', { initializeNewUser: async () => {} });
  stub('src/services/googleAuthService', {
    createOrUpdateUser: async (userData) => {
      captured.userData = JSON.parse(JSON.stringify(userData));
      return { ...userData, toJSON: () => userData };
    },
  });

  // No group map configured → the service must treat it as disabled, not throw.
  // (Path is deliberately absent but NOT explicit-set in that case.)
  if (!mapDoc) delete process.env.OIDC_GROUP_MAP_PATH;

  const svc = require(path.join(ROOT, 'src/services/oidcAuthService'));
  return { svc, captured };
}

const MAP = {
  claim: 'groups',
  groups: {
    'Credit-Ops-Mumbai': { dept_ids: ['collections'] },
    'Credit-Risk': { dept_ids: ['lending'], roles: ['decision-app-builder'] },
  },
};

async function run() {
  console.log('\n— dept assignment through authenticateWithOidc —');

  await check('NEW user + group map → arrives already entitled', async () => {
    const { svc, captured } = freshService({ mapDoc: MAP, existing: null });
    svc.verifyIdToken = async () => ({
      email: 'officer@bank.com', sub: 'o1', groups: ['Credit-Ops-Mumbai'],
    });
    await svc.authenticateWithOidc('fake');
    assert(JSON.stringify(captured.userData.dept_ids) === '["collections"]',
      `got ${JSON.stringify(captured.userData.dept_ids)}`);
  });

  await check('NEW user + NO group map → zero access (unchanged behaviour)', async () => {
    const { svc, captured } = freshService({ mapDoc: null, existing: null });
    svc.verifyIdToken = async () => ({
      email: 'officer@bank.com', sub: 'o1', groups: ['Credit-Ops-Mumbai'],
    });
    await svc.authenticateWithOidc('fake');
    assert(JSON.stringify(captured.userData.dept_ids) === '[]',
      `got ${JSON.stringify(captured.userData.dept_ids)}`);
  });

  await check('EXISTING user re-evaluated on every login (dept transfer follows)', async () => {
    const { svc, captured } = freshService({
      mapDoc: MAP,
      existing: { email: 'officer@bank.com', authProvider: 'oidc',
                  dept_ids: ['collections'], roles: ['user'], org_id: 'acme-bank',
                  save: async function () { return this; } },
    });
    svc.verifyIdToken = async () => ({
      email: 'officer@bank.com', sub: 'o1', groups: ['Credit-Risk'],
    });
    await svc.authenticateWithOidc('fake');
    assert(JSON.stringify(captured.userData.dept_ids) === '["lending"]',
      `expected the NEW dept only, got ${JSON.stringify(captured.userData.dept_ids)}`);
  });

  await check('removal from all groups → access revoked', async () => {
    const { svc, captured } = freshService({
      mapDoc: MAP,
      existing: { email: 'officer@bank.com', authProvider: 'oidc',
                  dept_ids: ['collections'], roles: ['user'], org_id: 'acme-bank',
                  save: async function () { return this; } },
    });
    svc.verifyIdToken = async () => ({
      email: 'officer@bank.com', sub: 'o1', groups: ['VPN-Users'],
    });
    await svc.authenticateWithOidc('fake');
    assert(JSON.stringify(captured.userData.dept_ids) === '[]',
      `got ${JSON.stringify(captured.userData.dept_ids)}`);
  });

  await check('a role granted in Citra is NOT stripped by the map', async () => {
    const { svc, captured } = freshService({
      mapDoc: MAP,
      existing: { email: 'boss@bank.com', authProvider: 'oidc',
                  dept_ids: ['collections'], roles: ['user', 'org_admin'],
                  org_id: 'acme-bank', save: async function () { return this; } },
    });
    svc.verifyIdToken = async () => ({
      email: 'boss@bank.com', sub: 'o2', groups: ['Credit-Risk'],
    });
    await svc.authenticateWithOidc('fake');
    const roles = captured.userData.roles.sort().join(',');
    assert(roles === 'decision-app-builder,org_admin,user', roles);
  });

  await check('local break-glass account still refuses SSO assumption', async () => {
    const { svc } = freshService({
      mapDoc: MAP,
      existing: { email: 'root@bank.com', authProvider: 'local', roles: ['super_admin'] },
    });
    svc.verifyIdToken = async () => ({
      email: 'root@bank.com', sub: 'x', groups: ['Credit-Risk'],
    });
    let threw = null;
    try { await svc.authenticateWithOidc('fake'); } catch (e) { threw = e; }
    assert(threw && threw.code === 'LOCAL_ACCOUNT_NO_SSO', `got ${threw && threw.message}`);
  });

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
}

run();
