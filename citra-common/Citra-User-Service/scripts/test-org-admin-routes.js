// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Smoke-test for /api/admin/orgs (Phase A.2).
 *
 * Mints super_admin + org_admin + plain-user JWTs using the same secret
 * the running server uses, then exercises every endpoint. Exits non-zero
 * on the first assertion failure.
 *
 * Usage:  node scripts/test-org-admin-routes.js [base_url]
 *         (default base_url = http://localhost:7004)
 */

require('dotenv').config();
const jwt = require('jsonwebtoken');

const BASE = process.argv[2] || 'http://localhost:7004';
const SECRET = process.env.JWT_SECRET;
if (!SECRET) {
  console.error('JWT_SECRET not set in env');
  process.exit(1);
}

function mint(claims) {
  return jwt.sign(claims, SECRET, { expiresIn: '5m', issuer: 'Citra-AI' });
}

const superToken = mint({
  user_id: 'rohit@trustedweartech.com',
  email: 'rohit@trustedweartech.com',
  org_id: 'trustedweartech',
  roles: ['super_admin', 'user'],
});

const orgAdminToken = mint({
  user_id: 'anita@acme-cement.citra.ai',
  email: 'anita@acme-cement.citra.ai',
  org_id: 'acme-cement',
  roles: ['org_admin', 'user'],
});

const plainToken = mint({
  user_id: 'vikram@acme-cement.citra.ai',
  email: 'vikram@acme-cement.citra.ai',
  org_id: 'acme-cement',
  roles: ['user'],
});

async function call(method, path, token, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json;
  try { json = text ? JSON.parse(text) : null; } catch { json = { raw: text }; }
  return { status: res.status, body: json };
}

let pass = 0;
let fail = 0;
function check(label, cond, detail) {
  if (cond) {
    pass++;
    console.log(`  ✅ ${label}`);
  } else {
    fail++;
    console.log(`  ❌ ${label}`, detail || '');
  }
}

(async () => {
  console.log(`\n→ Base URL: ${BASE}\n`);

  console.log('1. Auth gates');
  {
    const r = await call('GET', '/api/admin/orgs', null);
    check('GET without token → 401', r.status === 401, r);
  }
  {
    const r = await call('POST', '/api/admin/orgs', plainToken, { id: 'foo', name: 'Foo' });
    check('POST as plain user → 403', r.status === 403, r);
  }
  {
    const r = await call('POST', '/api/admin/orgs', orgAdminToken, { id: 'foo', name: 'Foo' });
    check('POST as org_admin → 403 (super_admin only)', r.status === 403, r);
  }

  console.log('\n2. GET / list with filtering');
  {
    const r = await call('GET', '/api/admin/orgs', superToken);
    check('super_admin GET / → 200 with seeded orgs', r.status === 200 && Array.isArray(r.body.orgs), r);
    check('super_admin sees acme-cement + trustedweartech',
      r.body.orgs && r.body.orgs.find(o => o.id === 'acme-cement') && r.body.orgs.find(o => o.id === 'trustedweartech'),
      r.body && r.body.orgs);
  }
  {
    const r = await call('GET', '/api/admin/orgs', orgAdminToken);
    check('org_admin GET / → only their own org', r.status === 200 && r.body.orgs.length === 1 && r.body.orgs[0].id === 'acme-cement', r);
  }

  console.log('\n3. POST creation');
  const testOrgId = 'test-phase-a2-' + Math.floor(Math.random() * 1e6);
  {
    const r = await call('POST', '/api/admin/orgs', superToken, {
      id: testOrgId, name: 'Test Phase A2', is_demo: true,
    });
    check(`POST { id: ${testOrgId} } → 201`, r.status === 201 && r.body.org && r.body.org.id === testOrgId, r);
  }
  {
    const r = await call('POST', '/api/admin/orgs', superToken, {
      id: testOrgId, name: 'Duplicate', is_demo: true,
    });
    check('POST duplicate id → 409', r.status === 409, r);
  }
  {
    const r = await call('POST', '/api/admin/orgs', superToken, { id: 'INVALID UPPERCASE', name: 'x' });
    check('POST invalid id → 400', r.status === 400, r);
  }
  {
    const r = await call('POST', '/api/admin/orgs', superToken, { id: 'no-name', name: '' });
    check('POST missing name → 400', r.status === 400, r);
  }

  console.log('\n4. GET /:orgId');
  {
    const r = await call('GET', `/api/admin/orgs/${testOrgId}`, superToken);
    check('super_admin GET /:orgId → 200', r.status === 200 && r.body.org.id === testOrgId, r);
  }
  {
    const r = await call('GET', `/api/admin/orgs/${testOrgId}`, orgAdminToken);
    check('org_admin GET other org → 403', r.status === 403, r);
  }
  {
    const r = await call('GET', '/api/admin/orgs/acme-cement', orgAdminToken);
    check('org_admin GET own org → 200', r.status === 200, r);
  }
  {
    const r = await call('GET', '/api/admin/orgs/does-not-exist', superToken);
    check('GET non-existent → 404', r.status === 404, r);
  }

  console.log('\n5. PATCH /:orgId');
  {
    const r = await call('PATCH', `/api/admin/orgs/${testOrgId}`, superToken, { name: 'Updated Name' });
    check('PATCH name → 200 with new name', r.status === 200 && r.body.org.name === 'Updated Name', r);
  }
  {
    const r = await call('PATCH', `/api/admin/orgs/${testOrgId}`, superToken, {});
    check('PATCH empty body → 400', r.status === 400, r);
  }
  {
    const r = await call('PATCH', `/api/admin/orgs/${testOrgId}`, orgAdminToken, { name: 'Hijacked' });
    check('PATCH as org_admin → 403', r.status === 403, r);
  }

  console.log('\n6. DELETE /:orgId');
  {
    const r = await call('DELETE', '/api/admin/orgs/acme-cement', superToken);
    // acme-cement may or may not have users — accept 409 (has users) or 200 (no users yet)
    check('DELETE acme-cement → 409 if users exist, else 200',
      r.status === 409 || r.status === 200, r);
  }
  {
    const r = await call('DELETE', `/api/admin/orgs/${testOrgId}`, superToken);
    check(`DELETE ${testOrgId} (no users) → 200`, r.status === 200, r);
  }
  {
    const r = await call('GET', `/api/admin/orgs/${testOrgId}`, superToken);
    check('GET deleted → 404', r.status === 404, r);
  }

  console.log(`\n══ Results: ${pass} passed, ${fail} failed ══\n`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
