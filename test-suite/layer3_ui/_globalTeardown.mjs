// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/** Fold ../.coverage-cells/ui/*.json into ../.coverage-cells/ui.json. */
import { aggregate } from "./_coverage.mjs";

export default async function globalTeardown() {
  const n = aggregate();
  // eslint-disable-next-line no-console
  console.log(`[coverage] wrote ui.json with ${n} cells`);
}
