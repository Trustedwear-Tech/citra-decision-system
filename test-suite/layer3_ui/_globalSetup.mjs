// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/** Clear stale per-cell coverage files so a run reflects only THIS run. */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const UI_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", ".coverage-cells", "ui");

export default async function globalSetup() {
  fs.rmSync(UI_DIR, { recursive: true, force: true });
}
