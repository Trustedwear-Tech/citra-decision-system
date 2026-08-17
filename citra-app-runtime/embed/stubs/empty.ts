// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Empty module — the alias target for side-effect-only imports the embed does
 * not need, e.g. `import "leaflet/dist/leaflet.css"` inside LeafletMap.
 *
 * Resolving those to nothing keeps the leaflet package out of the bundle
 * entirely rather than pulling it in for a stylesheet no map will use.
 */
export {};
