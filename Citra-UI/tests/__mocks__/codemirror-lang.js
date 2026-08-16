// Stub for @codemirror/lang-python and @codemirror/lang-json language packs.
// Each returns an empty extension so CodeEditorField renders without the real
// (untransformed, ESM-only) CodeMirror language modules during tests.
module.exports = {
  python: () => [],
  json: () => [],
  html: () => [],
  sql: () => [],
};
