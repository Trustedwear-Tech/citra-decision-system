// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

const path = require('path');

// Fix for packages that use ESM exports
config.resolver.unstable_enablePackageExports = true;

config.resolver.nodeModulesPaths = [
    path.resolve(__dirname, 'node_modules'),
];

module.exports = config;
