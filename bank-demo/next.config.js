// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/** @type {import('next').NextConfig} */
// Nothing clever on purpose. This app is meant to read like a bank's own
// codebase: no Citra packages, no build step for the card. The card arrives as
// one <script> tag from the Citra runtime at runtime.
module.exports = { reactStrictMode: true };
