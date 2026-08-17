// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// Stub for @uiw/react-codemirror so tests don't load the real CodeMirror
// (ESM-only @codemirror/* core packages aren't transformed by jest).
const React = require('react');

const CodeMirror = (props) =>
  React.createElement('textarea', {
    'data-testid': 'codemirror',
    value: props.value || '',
    onChange: (e) => props.onChange && props.onChange(e.target.value),
  });

const EditorView = { lineWrapping: [] };

module.exports = CodeMirror;
module.exports.default = CodeMirror;
module.exports.EditorView = EditorView;
