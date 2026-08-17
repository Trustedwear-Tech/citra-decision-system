// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// Stub for @codemirror/view so tests don't load the real (ESM-only,
// untransformed) module. CodeEditorField only uses these for the optional
// {{template}} highlighter; returning no-ops disables it cleanly in tests.
module.exports = {
  Decoration: { mark: () => ({}) },
  ViewPlugin: { define: () => ({}) },
  MatchDecorator: function MatchDecorator() {
    return { createDeco: () => ({}), updateDeco: () => ({}) };
  },
  EditorView: { lineWrapping: [], contentAttributes: { of: () => [] } },
};
