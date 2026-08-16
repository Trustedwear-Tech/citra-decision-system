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
