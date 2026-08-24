// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/** @type {import('next').NextConfig} */
// Nothing clever on purpose. This app is meant to read like a bank's own
// codebase: no Citra packages, no build step for the card. The card arrives as
// one <script> tag from the Citra runtime at runtime.
module.exports = { reactStrictMode: true };
