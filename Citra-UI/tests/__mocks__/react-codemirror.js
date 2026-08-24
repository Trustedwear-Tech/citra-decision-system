// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
