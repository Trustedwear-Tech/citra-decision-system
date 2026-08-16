// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Checks for oidcGroupMap — the OIDC group→dept mapping that lets a customer's
 * directory drive Citra department membership.
 *
 * No Mongo and no server: everything here is the pure config/resolution layer.
 * The DB-touching half (validateDeptsExist) is exercised at boot by server.js.
 *
 *   node scripts/test-oidc-group-map.js
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const SVC = path.join(ROOT, 'src/services/oidcGroupMap');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gmap-'));

let pass = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); pass++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); fail++; }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function throws(fn, needle) {
  let threw = null;
  try { fn(); } catch (e) { threw = e; }
  assert(threw, 'expected a throw, got none');
  assert(threw.message.includes(needle),
    `expected message containing "${needle}", got: ${threw.message}`);
}

function withMap(doc, fn, { explicit = true } = {}) {
  const p = path.join(tmp, `map-${Math.random().toString(36).slice(2)}.json`);
  if (doc !== null) fs.writeFileSync(p, JSON.stringify(doc));
  process.env.OIDC_GROUP_MAP_PATH = explicit ? p : '';
  delete require.cache[require.resolve(SVC)];
  const m = require(SVC);
  m.resetCache();
  return fn(m);
}

const VALID = {
  claim: 'groups',
  groups: {
    'Credit-Ops-Mumbai': { dept_ids: ['collections'] },
    'Credit-Risk': { dept_ids: ['lending'], roles: ['decision-app-builder'] },
    'Dept-Heads': { dept_ids: ['collections', 'claims'], roles: ['dept_admin'] },
  },
};

console.log('\n— config loading —');

check('missing file at an EXPLICIT path throws', () => {
  process.env.OIDC_GROUP_MAP_PATH = path.join(tmp, 'nope.json');
  delete require.cache[require.resolve(SVC)];
  const m = require(SVC);
  m.resetCache();
  throws(() => m.loadGroupMap(), 'no file exists there');
});

check('no path + no default file → disabled (zero-access JIT preserved)', () => {
  const cwd = process.cwd();
  process.chdir(tmp);                 // no ./config/oidc-group-map.json here
  try {
    process.env.OIDC_GROUP_MAP_PATH = '';
    delete require.cache[require.resolve(SVC)];
    const m = require(SVC);
    m.resetCache();
    assert(m.isEnabled() === false, 'should be disabled');
    assert(m.resolveFromClaims({ groups: ['x'] }) === null, 'resolve should be null');
  } finally { process.chdir(cwd); }
});

check('valid map loads', () => withMap(VALID, m => {
  const map = m.loadGroupMap();
  assert(map.enabled, 'enabled');
  assert(map.groups.size === 3, `3 groups, got ${map.groups.size}`);
}));

check('super_admin from a group is refused', () => withMap(
  { groups: { G: { roles: ['super_admin'] } } },
  m => throws(() => m.loadGroupMap(), 'break-glass')));

check('unknown role is refused', () => withMap(
  { groups: { G: { roles: ['wizard'] } } },
  m => throws(() => m.loadGroupMap(), 'unknown role')));

check('empty groups object is refused', () => withMap(
  { groups: {} },
  m => throws(() => m.loadGroupMap(), 'empty "groups"')));

check('group granting nothing is refused', () => withMap(
  { groups: { G: {} } },
  m => throws(() => m.loadGroupMap(), 'neither dept_ids nor roles')));

check('non-string dept_ids refused', () => withMap(
  { groups: { G: { dept_ids: [7] } } },
  m => throws(() => m.loadGroupMap(), 'non-empty strings')));

check('case-insensitive duplicate refused', () => withMap(
  { groups: { Ops: { dept_ids: ['a'] }, ops: { dept_ids: ['b'] } } },
  m => throws(() => m.loadGroupMap(), 'duplicate group')));

check('malformed JSON refused', () => {
  const p = path.join(tmp, 'bad.json');
  fs.writeFileSync(p, '{ not json');
  process.env.OIDC_GROUP_MAP_PATH = p;
  delete require.cache[require.resolve(SVC)];
  const m = require(SVC);
  m.resetCache();
  throws(() => m.loadGroupMap(), 'could not parse');
});

console.log('\n— claim extraction —');

check('array claim', () => withMap(VALID, m =>
  assert(JSON.stringify(m.extractGroups({ groups: ['a', 'b'] }, 'groups')) === '["a","b"]')));

check('space-delimited string claim (Keycloak-style)', () => withMap(VALID, m =>
  assert(JSON.stringify(m.extractGroups({ groups: 'a b' }, 'groups')) === '["a","b"]')));

check('dotted path claim', () => withMap(VALID, m =>
  assert(JSON.stringify(
    m.extractGroups({ realm_access: { roles: ['r1'] } }, 'realm_access.roles')) === '["r1"]')));

check('absent claim → empty, no throw', () => withMap(VALID, m =>
  assert(m.extractGroups({}, 'groups').length === 0)));

check('non-string members filtered out', () => withMap(VALID, m =>
  assert(JSON.stringify(m.extractGroups({ groups: ['a', 5, null] }, 'groups')) === '["a"]')));

console.log('\n— resolution —');

check('single group → its dept', () => withMap(VALID, m => {
  const r = m.resolveFromClaims({ groups: ['Credit-Ops-Mumbai'] });
  assert(JSON.stringify(r.dept_ids) === '["collections"]', JSON.stringify(r.dept_ids));
  assert(JSON.stringify(r.roles) === '["user"]', JSON.stringify(r.roles));
}));

check('multiple groups union depts AND roles', () => withMap(VALID, m => {
  const r = m.resolveFromClaims({ groups: ['Credit-Ops-Mumbai', 'Credit-Risk'] });
  assert(r.dept_ids.sort().join(',') === 'collections,lending', r.dept_ids.join(','));
  assert(r.roles.sort().join(',') === 'decision-app-builder,user', r.roles.join(','));
}));

check('case-insensitive match (AD casing drift)', () => withMap(VALID, m => {
  const r = m.resolveFromClaims({ groups: ['CREDIT-ops-MUMBAI'] });
  assert(JSON.stringify(r.dept_ids) === '["collections"]', JSON.stringify(r.dept_ids));
}));

check('case_insensitive:false is honoured', () => withMap(
  { case_insensitive: false, groups: { Ops: { dept_ids: ['collections'] } } },
  m => {
    assert(m.resolveFromClaims({ groups: ['ops'] }).dept_ids.length === 0, 'lower should miss');
    assert(m.resolveFromClaims({ groups: ['Ops'] }).dept_ids.length === 1, 'exact should hit');
  }));

check('unmapped groups ignored, recorded for support', () => withMap(VALID, m => {
  const r = m.resolveFromClaims({ groups: ['VPN-Users', 'Credit-Risk', 'Printer-Access'] });
  assert(JSON.stringify(r.dept_ids) === '["lending"]', JSON.stringify(r.dept_ids));
  assert(r.ignored.length === 2, JSON.stringify(r.ignored));
  assert(r.groups_in_token === 3);
}));

check('NO matching group → zero access (revocation path)', () => withMap(VALID, m => {
  const r = m.resolveFromClaims({ groups: ['VPN-Users'] });
  assert(r.dept_ids.length === 0, 'depts must be empty');
  assert(JSON.stringify(r.roles) === '["user"]', 'still a plain user');
}));

check('claim entirely absent → zero access, no throw', () => withMap(VALID, m => {
  const r = m.resolveFromClaims({ email: 'a@b.com' });
  assert(r.dept_ids.length === 0);
  assert(r.groups_in_token === 0);
}));

check('dept-head group grants dept_admin over several depts', () => withMap(VALID, m => {
  const r = m.resolveFromClaims({ groups: ['Dept-Heads'] });
  assert(r.dept_ids.sort().join(',') === 'claims,collections', r.dept_ids.join(','));
  assert(r.roles.includes('dept_admin'));
}));

console.log('\n— shipped example file —');
check('config/oidc-group-map.example.json is valid', () => {
  const p = path.join(ROOT, 'config/oidc-group-map.example.json');
  process.env.OIDC_GROUP_MAP_PATH = p;
  delete require.cache[require.resolve(SVC)];
  const m = require(SVC);
  m.resetCache();
  const map = m.loadGroupMap();
  assert(map.enabled && map.groups.size === 7, `7 groups, got ${map.groups.size}`);
  const r = m.resolveFromClaims({ groups: ['Citra-App-Builders'] });
  assert(r.roles.includes('decision-app-builder'));
});

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
