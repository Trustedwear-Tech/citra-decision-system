// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Smoke-test for Phase A.3 — impersonation token mint + audit + revoke.
 *
 * Requires a real demo target user to exist in `users`. Looks for
 * one of the acme-cement personas; if none exist, creates a minimal
 * one directly so the test is self-contained.
 *
 * Usage:  node scripts/test-impersonation.js [base_url]
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
  user_id: 'anita-test@acme-cement.citra.ai',
  email: 'anita-test@acme-cement.citra.ai',
  org_id: 'acme-cement',
  roles: ['org_admin', 'user'],
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
  if (cond) { pass++; console.log(`  ✅ ${label}`); }
  else { fail++; console.log(`  ❌ ${label}`, JSON.stringify(detail || {})); }
}

(async () => {
  console.log(`\n→ Base URL: ${BASE}\n`);

  // Ensure a target user exists by calling POST /api/admin/users
  // (existing admin API; idempotent upsert by email). super_admin token
  // bypasses the same-org scope check.
  const targetEmail = 'impersonation-target@acme-cement.citra.ai';
  {
    const r = await call('POST', '/api/admin/users', superToken, {
      email: targetEmail,
      name: 'Impersonation Target',
      org_id: 'acme-cement',
      dept_ids: ['plant_ops'],
      roles: ['user'],
    });
    if (r.status !== 200 && r.status !== 201) {
      console.error('Failed to provision target user:', r);
      process.exit(1);
    }
    console.log(`  (provisioned target ${targetEmail})\n`);
  }

  console.log('1. Auth gates on mint');
  {
    const r = await call('POST', `/api/admin/users/${targetEmail}/impersonation-token`, null);
    check('mint without token → 401', r.status === 401, r);
  }
  {
    const r = await call('POST', `/api/admin/users/${targetEmail}/impersonation-token`, orgAdminToken, {});
    check('mint as org_admin → 403', r.status === 403, r);
  }
  {
    const r = await call('POST', `/api/admin/users/${targetEmail}/impersonation-token`, superToken, {});
    // mint as super_admin against self would be 400, but rohit is different from target
    check('mint as super_admin (rohit→demo target) → 200', r.status === 200, r);
    if (r.status !== 200) {
      console.log('     server response body:', JSON.stringify(r.body));
    }
  }

  console.log('\n2. Mint shape + JWT claims');
  let firstMint;
  {
    const r = await call('POST', `/api/admin/users/${targetEmail}/impersonation-token`, superToken, {
      reason: 'A.3 smoke test',
    });
    firstMint = r.body;
    check('returns token + impersonation_id + expires_at',
      r.status === 200 && firstMint.token && firstMint.impersonation_id && firstMint.expires_at, r);

    if (firstMint.token) {
      const decoded = jwt.verify(firstMint.token, SECRET);
      check('decoded.email = target', decoded.email === targetEmail, decoded);
      check('decoded.org_id = target org', decoded.org_id === 'acme-cement', decoded);
      check('decoded.roles = target roles (not admin\'s)',
        Array.isArray(decoded.roles) && !decoded.roles.includes('super_admin'), decoded);
      check('decoded.act = admin email', decoded.act === 'rohit@trustedweartech.com', decoded);
      check('decoded.impersonation_id matches response',
        decoded.impersonation_id === firstMint.impersonation_id, decoded);
      // Expiry should be ~1 hour from now (allow 60s skew)
      const ttl = decoded.exp - Math.floor(Date.now() / 1000);
      check('TTL ≈ 1 hour (3540 < ttl ≤ 3600)', ttl > 3540 && ttl <= 3600, { ttl });
    }
  }

  console.log('\n3. Self-impersonation blocked');
  {
    const r = await call('POST', `/api/admin/users/rohit@trustedweartech.com/impersonation-token`, superToken, {});
    // depends on whether rohit user exists; if not, it'll be 404. Both prove the path works.
    check('mint targeting self → 400 (or 404 if user not in DB)',
      r.status === 400 || r.status === 404, r);
  }

  console.log('\n4. Unknown target');
  {
    const r = await call('POST', '/api/admin/users/nobody@nowhere.test/impersonation-token', superToken, {});
    check('mint unknown user → 404', r.status === 404, r);
  }

  console.log('\n5. Audit list (super_admin only)');
  {
    const r = await call('GET', '/api/admin/impersonations', superToken);
    check('list → 200 with recent rows', r.status === 200 && Array.isArray(r.body.impersonations), r);
    const found = r.body.impersonations.find(x => x.impersonation_id === firstMint.impersonation_id);
    check('audit row written for first mint', !!found, found);
    check('audit.target_email = target', found && found.target_email === targetEmail, found);
    check('audit.admin_email = rohit', found && found.admin_email === 'rohit@trustedweartech.com', found);
    check('audit.reason = "A.3 smoke test"', found && found.reason === 'A.3 smoke test', found);
  }
  {
    const r = await call('GET', '/api/admin/impersonations', orgAdminToken);
    check('list as org_admin → 403', r.status === 403, r);
  }

  console.log('\n6. Audit get-one');
  {
    const r = await call('GET', `/api/admin/impersonations/${firstMint.impersonation_id}`, superToken);
    check('get one → 200', r.status === 200 && r.body.impersonation.impersonation_id === firstMint.impersonation_id, r);
  }
  {
    const r = await call('GET', '/api/admin/impersonations/does-not-exist', superToken);
    check('get unknown → 404', r.status === 404, r);
  }

  console.log('\n7. Revoke');
  {
    const r = await call('POST', `/api/admin/impersonations/${firstMint.impersonation_id}/revoke`, superToken, {});
    check('revoke → 200 with revoked_at set', r.status === 200 && r.body.revoked_at, r);
  }
  {
    const r = await call('POST', `/api/admin/impersonations/${firstMint.impersonation_id}/revoke`, superToken, {});
    check('double-revoke → 409 with prior revoked_at', r.status === 409, r);
  }
  {
    const r = await call('POST', '/api/admin/impersonations/does-not-exist/revoke', superToken, {});
    check('revoke unknown → 404', r.status === 404, r);
  }
  {
    const r = await call('POST', `/api/admin/impersonations/${firstMint.impersonation_id}/revoke`, orgAdminToken, {});
    check('revoke as org_admin → 403', r.status === 403, r);
  }

  // Leave the test target user in DB — cleaning up via API would
  // require a hard-delete path we don't expose. The audit rows persist
  // for compliance review either way.

  console.log(`\n══ Results: ${pass} passed, ${fail} failed ══\n`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
