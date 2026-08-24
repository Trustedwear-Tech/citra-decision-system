// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

'use strict';
//
// Chart.js -> PNG renderer (baked into citra-agent-sandbox-base)
// ==============================================================
// Reads a JSON payload on stdin, writes a PNG on stdout.
//
//   payload = {
//     "config":     <standard Chart.js configuration object>,  // required
//     "width":      <px, default 900>,
//     "height":     <px, default 500>,
//     "background": <CSS colour, default "white">
//   }
//
// citra_toolkit.report.make_chartjs_png() is the Python caller. Chart.js
// is JavaScript, so charts for PPTX (python-pptx) and PDF (WeasyPrint)
// alike are pre-rendered to raster here.
//
// We drive Chart.js directly on a node-canvas surface (no
// chartjs-node-canvas wrapper): `chart.js/auto` registers every
// controller/element/scale, and node-canvas 3.x provides the 2D context.
// Background is filled explicitly — Chart.js draws on transparency.
//
const { createCanvas } = require('canvas');
const { Chart } = require('chart.js/auto');

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { raw += chunk; });
process.stdin.on('end', () => {
  try {
    const payload = JSON.parse(raw || '{}');
    const config = payload.config;
    if (!config || typeof config !== 'object') {
      throw new Error('payload.config (a Chart.js configuration object) is required');
    }

    const width = Number(payload.width) || 900;
    const height = Number(payload.height) || 500;
    const background = payload.background || 'white';

    const canvas = createCanvas(width, height);
    const ctx = canvas.getContext('2d');

    // Server-side render: no animation, no responsive resizing, fixed DPR
    // so the chart is fully drawn the moment the constructor returns.
    config.options = config.options || {};
    config.options.animation = false;
    config.options.responsive = false;
    config.options.devicePixelRatio = 1;

    // DejaVu Sans is the only font family guaranteed in the sandbox image
    // (fonts-dejavu-core). Chart.js' default 'Helvetica' isn't present.
    Chart.defaults.font.family = 'DejaVu Sans';

    const chart = new Chart(ctx, config);
    chart.draw();

    // Opaque background. Chart.js clearRect()s the canvas on init, so the
    // fill must happen AFTER the draw — `destination-over` lays it BEHIND
    // the chart. Without this the transparent canvas flattens to black
    // when embedded in a PDF/PPTX.
    ctx.save();
    ctx.globalCompositeOperation = 'destination-over';
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, width, height);
    ctx.restore();

    const buffer = canvas.toBuffer('image/png');
    chart.destroy();
    process.stdout.write(buffer);
  } catch (err) {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
  }
});
