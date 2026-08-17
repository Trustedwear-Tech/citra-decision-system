// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Isolated build dir for test harnesses (test-suite/layer3_ui) so a matrix
  // run can `next dev` beside an already-running dev server without clobbering
  // its .next. Defaults to .next — no effect on normal dev/prod builds.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  experimental: {
    typedRoutes: false
  },
  async headers() {
    // The embeddable decision UI bundle (public/v1/, built by
    // `npm run build:embed`). Three URLs, three caching stories:
    //
    //   /v1/citra.js          the stable pointer every snippet uses. Short TTL
    //                         so a bundle fix reaches customers without them
    //                         touching their code.
    //   /v1/citra-<hash>.js   an immutable build, named by CONTENT hash.
    //                         Customers who need byte-stability pin here.
    //   /v1/manifest.json     resolves the current hash. Must never be cached
    //                         hard — it is the thing that changes.
    //
    // The immutable name was once `citra-<package version>.js`, and that was
    // unsafe: the version does not change between builds, so each deploy
    // rewrote the bytes of a URL browsers cache for a year and never
    // revalidate. Content-hashing makes `immutable` literally true.
    //
    // NOTE: a CDN can override these. Cloudflare's "Browser Cache TTL" replaced
    // the 300s below with 14400s in front of this deployment until it was set
    // to respect origin headers — so if a bundle fix is not reaching browsers,
    // check the edge before the code.
    return [
      {
        source: "/v1/citra.js",
        headers: [
          { key: "Cache-Control", value: "public, max-age=300, must-revalidate" }
        ]
      },
      {
        source: "/v1/manifest.json",
        headers: [{ key: "Cache-Control", value: "no-cache" }]
      },
      {
        source: "/v1/citra-:hash.js",
        headers: [
          { key: "Cache-Control", value: "public, max-age=31536000, immutable" }
        ]
      }
    ];
  }
};

module.exports = nextConfig;
