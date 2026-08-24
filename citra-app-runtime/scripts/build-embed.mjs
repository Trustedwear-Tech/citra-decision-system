// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Builds the embeddable decision-UI bundle (`public/v1/citra.js`).
 *
 * THE CENTRAL IDEA: every difference between the app build and the embed build
 * is expressed as a build-time ALIAS, never as an edit to the runtime's source.
 * `next build` therefore produces byte-identical output before and after this
 * script exists, so charts, maps and modals in the live app cannot regress —
 * there is no new code path for them to travel down. `npm run verify:embed`
 * enforces that claim.
 *
 * REACT, NOT PREACT. A preact/compat build was measured at 73.4 KB gzip against
 * React's 108.3 KB, passing the identical suite — but 35 KB is not worth a
 * second rendering library whose divergences from React only show up in the
 * panels nobody has written yet. React 18 is what the renderer is developed and
 * tested against every day. See docs/embeddable-decision-ui-plan.md §11.
 *
 *   node scripts/build-embed.mjs            # build
 *   node scripts/build-embed.mjs --analyze  # print the heaviest inputs
 */
import * as esbuild from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { gzipSync } from "node:zlib";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = path.join(ROOT, "public", "v1");
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));

const ANALYZE = process.argv.includes("--analyze");

const p = (...seg) => path.join(ROOT, ...seg);

/**
 * Module → replacement. Each entry states WHY, because a silent alias is how a
 * bundle quietly diverges from the app it is supposed to mirror.
 */
const ALIASES = [
  {
    // No Next router in a customer's page — and the panels use the URL as their
    // filter-state store, so this must be a real in-memory store, not a no-op.
    filter: /^next\/navigation$/,
    to: p("embed", "shims", "navigation.tsx"),
  },
  {
    // React.lazy + Suspense stands in for next/dynamic.
    filter: /^next\/dynamic$/,
    to: p("embed", "shims", "dynamic.tsx"),
  },
  {
    // Redirects createPortal(document.body) into the shadow root, so the
    // decision modal keeps its styles. See embed/shims/react-dom.ts.
    filter: /^react-dom$/,
    to: p("embed", "shims", "react-dom.ts"),
  },
  {
    // Same-origin /api + ?_t= token → absolute Citra origin + getToken().
    filter: /^@\/lib\/runtimeFetch$/,
    to: p("embed", "shims", "runtimeFetch.ts"),
  },
  {
    // echarts is ~7x the rest of the bundle and no embed surface charts.
    // Reached by TWO static imports (PanelRenderer.tsx:20, KpiSparkline.tsx:6)
    // so it cannot be tree-shaken — only aliased away.
    filter: /^echarts-for-react$/,
    to: p("embed", "stubs", "echarts.tsx"),
  },
  {
    // The THIRD echarts entry point, and the least obvious:
    // src/lib/executiveTheme.ts does `import * as echarts from "echarts"` for a
    // single registerTheme() call, while ALSO exporting the number/currency
    // formatters that ordinary panels use. Without this alias, formatting a
    // rupee value pulls in the whole charting library.
    filter: /^echarts$/,
    to: p("embed", "stubs", "echarts-core.ts"),
  },
  {
    // LeafletMap is behind next/dynamic, but IIFE output cannot code-split, so
    // the "lazy" module would be inlined and leaflet would ship anyway.
    filter: /^react-leaflet$/,
    to: p("embed", "stubs", "react-leaflet.tsx"),
  },
  {
    // Covers `leaflet` and `leaflet/dist/leaflet.css` (a side-effect import).
    filter: /^leaflet(\/.*)?$/,
    to: p("embed", "stubs", "empty.ts"),
  },
];

/** Files whose own imports must NOT be re-aliased, or they would resolve to
 *  themselves and recurse (the react-dom shim imports react-dom). */
const SHIM_FILES = new Set(ALIASES.map((a) => a.to));

const aliasPlugin = {
  name: "citra-embed-alias",
  setup(build) {
    for (const { filter, to } of ALIASES) {
      build.onResolve({ filter }, (args) => {
        // A shim importing the module it replaces must reach the REAL one, or
        // it would resolve to itself and recurse (the react-dom shim imports
        // react-dom to re-export it).
        if (SHIM_FILES.has(args.importer)) return null;
        return { path: to };
      });
    }
  },
};

async function run() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const outfile = path.join(OUT_DIR, "citra.js");

  const result = await esbuild.build({
    entryPoints: [p("embed", "entry.tsx")],
    bundle: true,
    outfile,
    format: "iife",
    globalName: "Citra",
    // The global must be the module's default export, not its namespace, so a
    // bank writes `Citra.mountSpec(...)` rather than `Citra.default.mountSpec`.
    footer: { js: "Citra = Citra.default || Citra;" },
    platform: "browser",
    target: ["es2020"],
    minify: true,
    sourcemap: true,
    metafile: true,
    jsx: "automatic",
    tsconfig: p("tsconfig.json"),
    loader: { ".css": "text", ".svg": "dataurl", ".png": "dataurl" },
    define: {
      "process.env.NODE_ENV": '"production"',
      __EMBED_VERSION__: JSON.stringify(pkg.version),
    },
    plugins: [aliasPlugin],
    logLevel: "info",
  });

  const bytes = fs.readFileSync(outfile);
  const gz = gzipSync(bytes).length;

  // Prove the exclusions rather than assume them: a stray static import
  // anywhere in the renderer silently re-admits 300KB+, and the third echarts
  // entry point (via lib/executiveTheme) was found exactly this way.
  //
  // Checked against the MODULE GRAPH, not the output text. Substring-matching
  // the bundle reports a false positive on the CSS comment "themed recharts
  // tooltip" in globals.css, which contains "echarts".
  const EXCLUDED_PACKAGES = ["echarts", "echarts-for-react", "leaflet", "react-leaflet"];
  const inputs = Object.keys(result.metafile.inputs);
  const leaks = EXCLUDED_PACKAGES.filter((packageName) =>
    inputs.some((f) => f.replace(/\\/g, "/").includes(`node_modules/${packageName}/`)),
  );

  console.log(
    `\n  citra.js  ${(bytes.length / 1024).toFixed(1)} KB raw   ` +
      `${(gz / 1024).toFixed(1)} KB gzip   ${path.relative(ROOT, outfile)}`,
  );
  if (leaks.length) {
    console.error(
      `  ✗ EXCLUSION FAILED — bundle still contains: ${leaks.join(", ")}`,
    );
  } else {
    console.log("  ✓ echarts and leaflet excluded");
  }

  if (ANALYZE) {
    const inputs = Object.entries(result.metafile.outputs)
      .flatMap(([, o]) => Object.entries(o.inputs ?? {}))
      .sort((a, b) => b[1].bytesInOutput - a[1].bytesInOutput)
      .slice(0, 15);
    console.log("\n  heaviest inputs:");
    for (const [file, info] of inputs) {
      console.log(`    ${(info.bytesInOutput / 1024).toFixed(1).padStart(7)} KB  ${file}`);
    }
  }

  // ── immutable artefact, keyed by CONTENT ────────────────────────────────
  // This used to be `citra-${pkg.version}.js`, which was a trap: that URL is
  // served `immutable, max-age=31536000`, but package.json's version does not
  // change between builds — so every deploy silently rewrote the bytes of a URL
  // browsers are told to cache for a YEAR and never revalidate. A customer who
  // pinned it would hold the old bundle permanently, with no way to recover
  // short of changing their own code.
  //
  // Keying on the content hash makes `immutable` true: new bytes always mean a
  // new URL, and any URL that ever existed keeps exactly the bytes it was
  // published with.
  const hash = createHash("sha256").update(bytes).digest("hex").slice(0, 12);
  const hashed = `citra-${hash}.js`;
  fs.writeFileSync(path.join(OUT_DIR, hashed), bytes);

  // Sweep older hashed builds so the image does not accumulate one 400 KB
  // bundle per deploy. Keeps the CURRENT hash only — anything a customer
  // pinned earlier is gone from this image, which is the honest outcome: a
  // pinned build lives as long as the deployment that published it.
  for (const f of fs.readdirSync(OUT_DIR)) {
    if (/^citra-[0-9a-f]{12}\.js$/.test(f) && f !== hashed) {
      fs.unlinkSync(path.join(OUT_DIR, f));
    }
  }
  // Legacy version-named copy is NOT written any more — see above.
  const legacy = path.join(OUT_DIR, `citra-${pkg.version}.js`);
  if (fs.existsSync(legacy)) fs.unlinkSync(legacy);

  // What the runtime serves at /v1/manifest.json, so a host (or the export
  // snippet) can resolve the current immutable URL without guessing.
  fs.writeFileSync(
    path.join(OUT_DIR, "manifest.json"),
    JSON.stringify({ version: pkg.version, hash, file: hashed,
                     bytes: bytes.length, gzip: gz }, null, 2) + "\n",
  );
  console.log(`  immutable: /v1/${hashed}   manifest: /v1/manifest.json`);
  return { bytes: bytes.length, gz, leaks, hash };
}

const res = await run();
process.exit(res.leaks.length ? 1 : 0);
