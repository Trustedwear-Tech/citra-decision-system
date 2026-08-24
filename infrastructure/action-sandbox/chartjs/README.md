<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Baked-in Chart.js renderer

`citra-agent-sandbox-base` bakes Chart.js into the image so the
presentation / reporting skills can render web-grade charts. Chart.js is
JavaScript, so charts can't run inside WeasyPrint (PDF) or python-pptx
(PPTX) — they are pre-rendered to PNG here, then embedded as images.

| File | Role |
|---|---|
| `package.json` | Pins `chart.js` + `canvas` (node-canvas 3.x); `npm install`ed at image build time into `/srv/citra/chartjs/node_modules`. |
| `render.js` | Reads a Chart.js config JSON on stdin, writes a PNG on stdout. |

The Python side is `citra_toolkit.report.make_chartjs_png(config, ...)`,
which shells out to `node /srv/citra/chartjs/render.js`. matplotlib stays
in the image too (`make_chart_png`) for in-process analytical plots —
Chart.js is the polish layer for deliverables.

`render.js` needs node-canvas' native libs (cairo/pango — already present
for WeasyPrint — plus jpeg/gif/rsvg). The base `Dockerfile` installs the
runtime libs, builds the modules, then purges the build toolchain in the
same layer. See `infrastructure/action-sandbox/Dockerfile`.
