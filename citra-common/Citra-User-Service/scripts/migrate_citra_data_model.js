// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Citra data-model migration — idempotent, safe to re-run.
 *
 * This consolidates every cleanup we need so the platform reflects the
 * intended architecture:
 *
 *   - Citra-AI is the home org for everyone who logs in via the UI.
 *     They get dept_ids=[citra-software] and entity_type=company.
 *   - Demo tenants (acme-cement, future companies) have their own org
 *     with is_demo=true. Their personas are seeded via demo-data/.
 *   - A Citra super_admin can impersonate any demo persona to walk a
 *     prospect through their own seeded data.
 *
 * The migration does:
 *   1. Seed citra-ai org + citra-software dept (idempotent upserts).
 *   2. Drop stale depts under citra-ai that came from when the demo
 *      depts.seed.json was being seeded under ORG_ID=citra-ai.
 *   3. Drop the orphan trustedweartech org (no users point at it).
 *   4. For every user with org_id=citra-ai or org_id=null:
 *        org_id  := citra-ai
 *        dept_ids := [citra-software]   (only if currently empty)
 *        entity_type := company         (only if currently general/empty)
 *      Then ensurePersonalSA + ensureWorkSA so the user has both SAs.
 *      Demo users (@<demo-org>.citra.ai with org_id matching a demo org)
 *      are skipped — they keep their seeded dept/entity_type.
 *   5. Delete orphan ServiceAccount docs whose admin email no longer has
 *      that SA stamped on their user doc (pre-prod cleanup; in prod we
 *      keep them as audit).
 *
 * Usage:
 *   node scripts/migrate_citra_data_model.js          # dry-run
 *   node scripts/migrate_citra_data_model.js --apply  # actually write
 */
require('dotenv').config();
const dns = require('dns');
dns.setServers(['8.8.8.8', '1.1.1.1']);
const mongoose = require('mongoose');

const CitraAIUser = require('../src/models/CitraAIUser');
const ServiceAccount = require('../src/models/ServiceAccount');
const Org = require('../src/models/Org');
const Dept = require('../src/models/Dept');
const { ensurePersonalSA } = require('../src/services/personalSAService');
const { ensureWorkSA } = require('../src/services/workSAService');
const { applyToUserDoc, DEFAULT_DEPT_ID } = require('../src/services/deploymentDefaults');

const APPLY = process.argv.includes('--apply');
// In prod we keep stale SAs (audit trail). Pre-prod we wipe them.
const WIPE_STALE_SAS = APPLY && process.argv.includes('--wipe-stale-sas');

const CITRA_ORG_ID = 'citra-ai';
const CITRA_ORG_NAME = 'Citra AI';
const CITRA_ORG_DOMAIN = 'citra-ai.com';

// Depts that were seeded under citra-ai by mistake (they're cement-only).
const STALE_CITRA_AI_DEPTS = ['plant_ops', 'quality', 'sales_dispatch'];


function log(label, payload) {
  if (payload === undefined) console.log(label);
  else console.log(label, JSON.stringify(payload));
}


async function step1_seedCitraOrgAndDept() {
  log('\n── STEP 1: seed citra-ai org + citra-software dept ──');
  const existingOrg = await Org.findOne({ id: CITRA_ORG_ID }).lean();
  if (!existingOrg) {
    log('  org=citra-ai missing — would insert', { id: CITRA_ORG_ID, name: CITRA_ORG_NAME });
    if (APPLY) {
      await Org.create({ id: CITRA_ORG_ID, name: CITRA_ORG_NAME, domain: CITRA_ORG_DOMAIN, is_demo: false });
    }
  } else {
    log('  org=citra-ai already present (skip)');
  }

  const existingDept = await Dept.findOne({ org_id: CITRA_ORG_ID, id: DEFAULT_DEPT_ID }).lean();
  if (!existingDept) {
    log('  dept=citra-software missing — would insert', { org_id: CITRA_ORG_ID, id: DEFAULT_DEPT_ID });
    if (APPLY) {
      await Dept.create({ org_id: CITRA_ORG_ID, id: DEFAULT_DEPT_ID, name: 'Citra Software', parent_id: null });
    }
  } else {
    log('  dept=citra-software already present (skip)');
  }
}


async function step2_dropStaleCitraAiDepts() {
  log('\n── STEP 2: drop stale demo-flavoured depts under citra-ai ──');
  const stale = await Dept.find({ org_id: CITRA_ORG_ID, id: { $in: STALE_CITRA_AI_DEPTS } }).lean();
  for (const d of stale) {
    log(`  would delete dept org=${d.org_id} id=${d.id} name=${d.name}`);
  }
  if (APPLY && stale.length) {
    const res = await Dept.deleteMany({ org_id: CITRA_ORG_ID, id: { $in: STALE_CITRA_AI_DEPTS } });
    log(`  deleted ${res.deletedCount} stale dept rows`);
  }
}


async function step3_dropOrphanOrgs() {
  log('\n── STEP 3: drop orgs that no user references ──');
  const orgs = await Org.find({}).lean();
  for (const o of orgs) {
    if (o.id === CITRA_ORG_ID) continue;
    const count = await CitraAIUser.countDocuments({ org_id: o.id });
    if (count === 0 && !o.is_demo) {
      log(`  would delete orphan non-demo org id=${o.id} name=${o.name}`);
      if (APPLY) {
        await Org.deleteOne({ id: o.id });
      }
    } else {
      log(`  keep org id=${o.id} users=${count} is_demo=${!!o.is_demo}`);
    }
  }
}


async function step4_backfillCitraUsers() {
  log('\n── STEP 4: backfill citra-ai users (null or citra-ai) ──');

  // Set of demo org ids — users in these orgs are demo personas, leave alone.
  const demoOrgIds = (await Org.find({ is_demo: true }).select('id').lean()).map(o => o.id);
  log('  demo org ids (skip):', demoOrgIds);

  // Find all users that should be on citra-ai: either already there, or
  // currently org-less. Demo users in @demo-tenant.citra.ai already have
  // org_id=<demo-org-id> and will be skipped by this query.
  const candidates = await CitraAIUser.find({
    $or: [{ org_id: CITRA_ORG_ID }, { org_id: null }, { org_id: { $exists: false } }],
  });

  log(`  candidates: ${candidates.length}`);

  let touched = 0;
  for (const u of candidates) {
    if (u.org_id && demoOrgIds.includes(u.org_id)) {
      log(`    skip ${u.email} — belongs to demo org ${u.org_id}`);
      continue;
    }
    const before = {
      org_id: u.org_id,
      dept_ids: u.dept_ids,
      entity_type: u.entity_type,
    };
    // applyToUserDoc only changes empty fields, but for citra-ai users
    // we additionally force dept_ids := [citra-software] if it contains
    // the stale cement depts (left over from when seedDepts ran under
    // ORG_ID=citra-ai with the cement seed file).
    let changed = applyToUserDoc(u);
    if (Array.isArray(u.dept_ids) && u.dept_ids.some(d => STALE_CITRA_AI_DEPTS.includes(d))) {
      u.dept_ids = [DEFAULT_DEPT_ID];
      changed = true;
    }
    // user_type: every Citra user should be paid (we, the team, own the
    // platform — no point treating ourselves as free-tier).
    if (u.user_type !== 'paid') {
      u.user_type = 'paid';
      changed = true;
    }

    if (!changed && u.org_id === CITRA_ORG_ID && u.personal_sa_id && u.work_sa_id) {
      // Nothing to do for this user (already healthy).
      continue;
    }

    const after = {
      org_id: u.org_id,
      dept_ids: u.dept_ids,
      entity_type: u.entity_type,
    };
    log(`  ${APPLY ? '✓' : 'WOULD'} update ${u.email}`);
    log(`      before:`, before);
    log(`      after :`, after);

    if (APPLY) {
      await u.save();
      try { await ensurePersonalSA(u); } catch (e) { log(`    ensurePersonalSA failed: ${e.message}`); }
      try { await ensureWorkSA(u); }     catch (e) { log(`    ensureWorkSA failed: ${e.message}`); }
    }
    touched++;
  }
  log(`  total citra-ai users touched: ${touched}`);
}


async function step5_dropStaleSAs() {
  log('\n── STEP 5: stale SA cleanup ──');
  // An SA is "stale" if its admin email's user doc doesn't reference it
  // as either personal_sa_id or work_sa_id, i.e. nothing in the system
  // currently uses it. Common after an org-move: the user got a new SA
  // and the old one is unreferenced.
  const sas = await ServiceAccount.find({}).lean();
  const stale = [];
  for (const sa of sas) {
    const ownerEmail = (sa.admins && sa.admins[0]) || sa.created_by_user_id;
    if (!ownerEmail) continue;
    const user = await CitraAIUser.findOne({ email: ownerEmail }).select('personal_sa_id work_sa_id').lean();
    if (!user) {
      stale.push({ id: sa.service_account_id, reason: 'no user doc for owner', owner: ownerEmail });
      continue;
    }
    const used = user.personal_sa_id === sa.service_account_id || user.work_sa_id === sa.service_account_id;
    if (!used) {
      stale.push({ id: sa.service_account_id, reason: 'unreferenced by owner user doc', owner: ownerEmail });
    }
  }
  log(`  stale SAs: ${stale.length}`);
  for (const s of stale) log(`    ${s.id} (owner=${s.owner}, reason=${s.reason})`);

  if (WIPE_STALE_SAS && stale.length) {
    const ids = stale.map(s => s.id);
    const res = await ServiceAccount.deleteMany({ service_account_id: { $in: ids } });
    log(`  deleted ${res.deletedCount} stale SA docs (--wipe-stale-sas)`);
  } else if (stale.length) {
    log('  (pass --wipe-stale-sas to actually delete; default keeps for audit)');
  }
}


async function main() {
  await mongoose.connect(process.env.MONGODB_CONNECTION_STRING, {
    dbName: process.env.MONGODB_DATABASE || 'dev',
  });
  log(`connected to ${mongoose.connection.name} (mode=${APPLY ? 'APPLY' : 'DRY-RUN'}, wipeStaleSAs=${WIPE_STALE_SAS})`);

  await step1_seedCitraOrgAndDept();
  await step2_dropStaleCitraAiDepts();
  await step3_dropOrphanOrgs();
  await step4_backfillCitraUsers();
  await step5_dropStaleSAs();

  log('\nDONE');
  await mongoose.disconnect();
}

main().catch(e => { console.error('fatal:', e); process.exit(1); });
