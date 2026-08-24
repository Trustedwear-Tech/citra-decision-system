// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Empty module — the alias target for side-effect-only imports the embed does
 * not need, e.g. `import "leaflet/dist/leaflet.css"` inside LeafletMap.
 *
 * Resolving those to nothing keeps the leaflet package out of the bundle
 * entirely rather than pulling it in for a stylesheet no map will use.
 */
export {};
