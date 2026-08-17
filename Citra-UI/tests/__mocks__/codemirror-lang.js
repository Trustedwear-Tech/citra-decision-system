// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// Stub for @codemirror/lang-python and @codemirror/lang-json language packs.
// Each returns an empty extension so CodeEditorField renders without the real
// (untransformed, ESM-only) CodeMirror language modules during tests.
module.exports = {
  python: () => [],
  json: () => [],
  html: () => [],
  sql: () => [],
};
