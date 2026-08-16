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
