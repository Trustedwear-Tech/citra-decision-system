// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * validateDeploymentOrg — fail-loud boot guard for the deployment's ORG_ID.
 *
 * Single-customer posture: every deployment is pinned to exactly one org via
 * the ORG_ID env. That value is the source of truth that UI-login users are
 * stamped with (deploymentDefaults.js), that depts are seeded under, and that
 * downstream services (data-discovery crawler, dept-MCPs) must agree on. A
 * typo'd or missing ORG_ID silently creates a phantom org — users land in an
 * org with no depts/sources and nothing errors. This guard turns that silent
 * misconfig into a hard boot failure.
 *
 * Runs AFTER orgSeed so a seed-created org counts. If ORG_ID is set but no
 * matching `orgs` row exists we throw, and the caller aborts startup (RULE:
 * fail loud, fail early). If ORG_ID is unset we warn — leaving it empty is a
 * deliberate multi-tenant/dev choice (deploymentDefaults no-ops), not a
 * failure, so we don't block boot on it.
 */

const Org = require('../models/Org');

async function validateDeploymentOrg() {
  const orgId = (process.env.ORG_ID || '').trim();
  if (!orgId) {
    console.warn(
      '[deploymentOrg] ⚠️ ORG_ID is not set — UI-login users will NOT be ' +
      'stamped with an org (deploymentDefaults no-op). Set ORG_ID for a ' +
      'single-customer deployment.'
    );
    return;
  }
  const org = await Org.findOne({ id: orgId }).lean();
  if (!org) {
    throw new Error(
      `[deploymentOrg] ORG_ID=${orgId} does not match any row in the 'orgs' ` +
      `collection. This deployment would stamp every user into a phantom org. ` +
      `Fix ORG_ID or seed the org (config/orgs.seed.json) before starting.`
    );
  }
  console.log(`[deploymentOrg] ✅ ORG_ID=${orgId} resolves to org '${org.name}'`);
}

module.exports = { validateDeploymentOrg };
