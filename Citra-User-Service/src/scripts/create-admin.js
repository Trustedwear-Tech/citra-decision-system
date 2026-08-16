#!/usr/bin/env node
// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * create-admin.js — Seed an admin user with email/password credentials,
 * an enterprise role, and optional org/dept membership.
 *
 * Usage:
 *   node src/scripts/create-admin.js <email> <password> [name] \
 *        [--role=super_admin|org_admin|dept_admin] \
 *        [--org=<org_id>] [--dept=<dept_id>[,<dept_id>...]]
 *
 * Defaults:
 *   role = super_admin   (this script is for bootstrap; super_admin is
 *                         the platform-wide tier — only do this once)
 *   org  = (unset)        but if --role=org_admin or dept_admin is used,
 *                         --org is REQUIRED so the role has a scope
 *   dept = (unset)
 *
 * Examples:
 *   # First platform admin
 *   node src/scripts/create-admin.js founder@citra.ai 'pw' 'Founder'
 *
 *   # Org admin for ACME
 *   node src/scripts/create-admin.js admin@acme.com 'pw' 'ACME Admin' \
 *        --role=org_admin --org=acme
 *
 *   # Dept admin for plant_ops dept inside ACME
 *   node src/scripts/create-admin.js lead@acme.com 'pw' 'Plant Lead' \
 *        --role=dept_admin --org=acme --dept=plant_ops
 *
 * The user's authProvider is set to 'local' (or 'both' if they already
 * signed in with Google). emailVerified is set to true so no verification
 * email is needed.
 */

const bcrypt = require('bcryptjs');
const mongoose = require('mongoose');

function parseFlags(argv) {
  const flags = {};
  const positional = [];
  for (const a of argv) {
    const m = a.match(/^--([^=]+)=(.*)$/);
    if (m) flags[m[1]] = m[2];
    else positional.push(a);
  }
  return { flags, positional };
}

const VALID_ROLES = new Set(['user', 'dept_admin', 'org_admin', 'super_admin', 'IT-workflow', 'decision-app-builder']);

async function main() {
  const { flags, positional } = parseFlags(process.argv.slice(2));
  const [email, password, name] = positional;

  if (!email || !password) {
    console.error(
      'Usage: node src/scripts/create-admin.js <email> <password> [name]\n' +
      '       [--role=super_admin|org_admin|dept_admin] [--org=<id>] [--dept=<id,id>]'
    );
    process.exit(1);
  }

  const role = (flags.role || 'super_admin').toLowerCase();
  if (!VALID_ROLES.has(role)) {
    console.error(`--role must be one of ${[...VALID_ROLES].join(', ')}`);
    process.exit(1);
  }
  const org_id = (flags.org || '').trim() || null;
  const dept_ids = (flags.dept || '')
    .split(',').map(s => s.trim()).filter(Boolean);

  // Scoped roles require a scope.
  if ((role === 'org_admin' || role === 'dept_admin') && !org_id) {
    console.error(`--org=<id> is required when --role=${role}`);
    process.exit(1);
  }
  if (role === 'dept_admin' && dept_ids.length === 0) {
    console.error('--dept=<id> is required when --role=dept_admin');
    process.exit(1);
  }

  // Load vault secrets (if configured)
  try {
    const { loadVaultSecrets } = require('../config/vault-env-loader');
    await loadVaultSecrets();
  } catch { /* vault may not be configured locally */ }

  const connString = process.env.MONGODB_CONNECTION_STRING;
  if (!connString) {
    console.error('MONGODB_CONNECTION_STRING is not set. Export it or add to .env');
    process.exit(1);
  }

  await mongoose.connect(connString, { dbName: process.env.MONGODB_DATABASE || 'citra-ai' });
  console.log('Connected to MongoDB');

  const CitraAIUser = require('../models/CitraAIUser');

  const passwordHash = await bcrypt.hash(password, 12);
  const normalizedEmail = email.trim().toLowerCase();

  let user = await CitraAIUser.findOne({ email: normalizedEmail });

  // Merge the new role/scope into the user doc.
  const rolesToSet = (existing) => {
    const have = new Set(existing?.roles || []);
    have.add('user');   // always carries the base role
    have.add(role);
    return [...have];
  };

  if (user) {
    // Upgrade existing user
    user.passwordHash = passwordHash;
    user.emailVerified = true;
    user.authProvider = user.authProvider === 'google' ? 'both' : (user.authProvider || 'local');
    if (name) user.name = name;
    user.roles = rolesToSet(user);
    if (org_id) user.org_id = org_id;
    if (dept_ids.length) {
      user.dept_ids = [...new Set([...(user.dept_ids || []), ...dept_ids])];
    }
    user.isActive = true;
    user.deletion_state = 'active';
    await user.save();
    console.log(`✅ Updated existing user ${normalizedEmail} (authProvider: ${user.authProvider}, roles: ${user.roles.join(',')}${user.org_id ? ', org=' + user.org_id : ''}${user.dept_ids?.length ? ', depts=' + user.dept_ids.join(',') : ''})`);
  } else {
    // Create new user
    user = await CitraAIUser.create({
      email: normalizedEmail,
      name: name || '',
      passwordHash,
      authProvider: 'local',
      emailVerified: true,
      isActive: true,
      roles: rolesToSet(null),
      org_id: org_id || null,
      dept_ids: dept_ids,
      lastLogin: new Date(),
    });
    console.log(`✅ Created admin user: ${normalizedEmail} (roles: ${user.roles.join(',')}${org_id ? ', org=' + org_id : ''}${dept_ids.length ? ', depts=' + dept_ids.join(',') : ''})`);
  }

  // Ensure ADMIN_EMAILS includes this user (legacy gate)
  const adminEmails = (process.env.ADMIN_EMAILS || '').split(',').map(e => e.trim().toLowerCase()).filter(Boolean);
  if (!adminEmails.includes(normalizedEmail)) {
    console.log(`⚠️  ALSO add "${normalizedEmail}" to ADMIN_EMAILS in your .env (legacy gate — used by some routes still).`);
  }

  if (role === 'super_admin') {
    console.log(`\nℹ️  This user is now a super_admin. Use them to:`);
    console.log(`    1. Invite org admins:  POST /api/admin/users { email, org_id, roles:['org_admin'] }`);
    console.log(`    2. Let org admins create their own dept admins via the same endpoint.`);
  }

  await mongoose.disconnect();
  console.log('Done.');
}

main().catch(err => {
  console.error('Failed:', err);
  process.exit(1);
});
