// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Generate Test JWT Token
 * Quick utility to generate a JWT token for API testing
 */

require('dotenv').config();
const jwt = require('jsonwebtoken');

// Test user data
const testUser = {
  user_id: 'test_user_123',
  email: 'test@example.com',
  name: 'Test User'
};

// Generate token
const token = jwt.sign(testUser, process.env.JWT_SECRET, {
  expiresIn: process.env.JWT_EXPIRES_IN || '7d'
});

console.log('\n🔑 Test JWT Token Generated\n');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
console.log('Token:');
console.log(token);
console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
console.log('User Data:');
console.log(JSON.stringify(testUser, null, 2));
console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
console.log('Usage in curl:');
console.log(`curl -H "Authorization: Bearer ${token}" ...`);
console.log('\n');
