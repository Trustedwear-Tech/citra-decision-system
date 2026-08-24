// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
