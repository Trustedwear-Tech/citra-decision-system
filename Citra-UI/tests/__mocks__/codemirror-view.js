// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
