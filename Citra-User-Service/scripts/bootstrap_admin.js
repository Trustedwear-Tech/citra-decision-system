// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Bootstrap a BREAK-GLASS local super_admin (Wave 3, slice A).
 *
 * Every deployment needs a day-0 admin that does NOT depend on the customer IdP
 * — to seed the platform before anyone can log in, and to recover if the IdP is
 * ever unreachable. This creates a LOCAL email+password user with roles
 * [user, super_admin], so it works across ALL services (the role is carried in
 * the JWT, unlike ADMIN_EMAILS which only elevates inside the user-service).
 *
 * IdP-independent by design: sign in at POST /api/auth/local/login (keep the
 * 'local' provider in AUTH_PROVIDERS even on OIDC deployments, exactly for this).
 *
 * Idempotent — re-run to reset the password or re-assert the role.
 *
 * Usage:
 *   node scripts/bootstrap_admin.js <email> <password> [org_id]
 *   # or env: BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD / ORG_ID
 */
require('dotenv').config();
const mongoose = require('mongoose');
const bcrypt = require('bcryptjs');
const CitraAIUser = require('../src/models/CitraAIUser');
const { ensurePersonalSA } = require('../src/services/personalSAService');
const { ensureWorkSA } = require('../src/services/workSAService');
const { applyToUserData } = require('../src/services/deploymentDefaults');

const SALT_ROUNDS = 10;

async function main() {
  const email = (process.argv[2] || process.env.BOOTSTRAP_ADMIN_EMAIL || '').trim().toLowerCase();
  const password = process.argv[3] || process.env.BOOTSTRAP_ADMIN_PASSWORD || '';
  const orgId = process.argv[4] || process.env.ORG_ID || process.env.BOOTSTRAP_ORG_ID || null;

  if (!email || !password) {
    console.error('Usage: node scripts/bootstrap_admin.js <email> <password> [org_id]');
    process.exit(2);
  }
  if (password.length < 8) {
    console.error('Refusing: break-glass password must be at least 8 characters.');
    process.exit(2);
  }

  const conn = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI;
  if (!conn) { console.error('MONGODB_CONNECTION_STRING is not set.'); process.exit(2); }
  // Default DB name MUST match the service (environment.js: MONGODB_DATABASE ||
  // 'citra-ai'); a mismatch would write the admin to a DB the service never reads,
  // silently defeating break-glass recovery.
  await mongoose.connect(conn, { dbName: process.env.MONGODB_DATABASE || 'citra-ai' });

  const passwordHash = await bcrypt.hash(password, SALT_ROUNDS);
  let user = await CitraAIUser.findOne({ email });
  const isNew = !user;

  if (isNew) {
    const userData = {
      email,
      name: 'Break-glass Admin',
      passwordHash,
      authProvider: 'local',
      emailVerified: true,       // bootstrap admin skips the email-verification loop
      isActive: true,
      lastLogin: new Date(),
      roles: ['user', 'super_admin'],
    };
    applyToUserData(userData, null, true);   // org_id / entity_type deployment defaults
    if (orgId) userData.org_id = orgId;
    user = await CitraAIUser.create(userData);
  } else {
    // Idempotent recovery: reset the password + re-assert super_admin. Keep an
    // existing IdP linkage (google/oidc) by promoting authProvider to 'both'.
    user.passwordHash = passwordHash;
    if (user.authProvider && user.authProvider !== 'local') user.authProvider = 'both';
    const roles = new Set(user.roles || []);
    roles.add('user'); roles.add('super_admin');
    user.roles = Array.from(roles);
    user.emailVerified = true;
    user.isActive = true;
    if (orgId && !user.org_id) user.org_id = orgId;
    await user.save();
  }

  try { await ensurePersonalSA(user); } catch (e) { console.warn('ensurePersonalSA (continuing):', e.message); }
  try { await ensureWorkSA(user); } catch (e) { console.warn('ensureWorkSA (continuing):', e.message); }

  console.log(`✓ break-glass super_admin ${isNew ? 'created' : 'updated'}: ${email}`);
  console.log(`  org_id       : ${user.org_id}`);
  console.log(`  roles        : ${JSON.stringify(user.roles)}`);
  console.log(`  authProvider : ${user.authProvider}`);
  console.log('  Sign in at POST /api/auth/local/login — works with the IdP down.');
  await mongoose.disconnect();
}

main().catch(e => { console.error('fatal:', e); process.exit(1); });
