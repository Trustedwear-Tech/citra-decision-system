// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// Stub for @codemirror/lang-python and @codemirror/lang-json language packs.
// Each returns an empty extension so CodeEditorField renders without the real
// (untransformed, ESM-only) CodeMirror language modules during tests.
module.exports = {
  python: () => [],
  json: () => [],
  html: () => [],
  sql: () => [],
};
