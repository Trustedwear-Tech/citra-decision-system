// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Multi-spec-safe UI coverage emission.
 *
 * Every test records the cell it exercised by writing ONE tiny file to
 * ../.coverage-cells/ui/<panel>__<state>__<interaction>.json. Distinct filenames
 * mean parallel workers AND multiple spec files never clobber each other (the
 * old in-process array + afterAll wrote ui.json once per worker/spec and the last
 * writer won). `globalTeardown` (playwright.config) globs the dir into ui.json.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const CELLS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", ".coverage-cells");
const UI_DIR = path.join(CELLS_DIR, "ui");

/** Record one exercised (panel, state, interaction) cell. interaction defaults
 *  to "-" (a pure render cell). */
export function emitCell(panel, state, interaction = "-") {
  fs.mkdirSync(UI_DIR, { recursive: true });
  const key = `${panel}__${state}__${interaction}`.replace(/[^a-z0-9_]/gi, "_");
  fs.writeFileSync(path.join(UI_DIR, `${key}.json`), JSON.stringify([panel, state, interaction]));
}

/** Fold ../.coverage-cells/ui/*.json into ../.coverage-cells/ui.json. Called
 *  once from globalTeardown after every spec has finished. */
export function aggregate() {
  const cells = [];
  if (fs.existsSync(UI_DIR)) {
    for (const f of fs.readdirSync(UI_DIR)) {
      if (f.endsWith(".json")) cells.push(JSON.parse(fs.readFileSync(path.join(UI_DIR, f), "utf8")));
    }
  }
  fs.mkdirSync(CELLS_DIR, { recursive: true });
  fs.writeFileSync(path.join(CELLS_DIR, "ui.json"),
    JSON.stringify({ layer: "ui", cells_hit: cells }, null, 2));
  return cells.length;
}
