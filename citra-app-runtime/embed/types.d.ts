/**
 * Ambient declarations for the embed build.
 *
 * These describe things esbuild provides that tsc cannot infer on its own, so
 * `npm run typecheck:embed` checks the same reality the bundler builds.
 */

/**
 * Stylesheets are bundled with esbuild's `text` loader and injected into the
 * shadow root as a string — not linked, not processed. A `<link>` would need a
 * second request the bank's CSP may block, and a linked sheet cannot reach
 * inside a shadow root anyway.
 */
declare module "*.css" {
  const content: string;
  export default content;
}

/** Stamped from package.json at build time (esbuild `define`). */
declare const __EMBED_VERSION__: string;
