// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Create Test User and Test Welcome Email
 * Creates a test user in the database and tests the welcome email endpoint
 */

require('dotenv').config();
const mongoose = require('mongoose');
const jwt = require('jsonwebtoken');
const CitraAIUser = require('../src/models/CitraAIUser');

async function createTestUserAndTestEmail() {
  try {
    // Connect to MongoDB
    await mongoose.connect(process.env.MONGODB_CONNECTION_STRING);
    console.log('Connected to MongoDB');

    // Create test user
    const testUser = {
      email: 'deeepakumar@gmail.com',
      name: 'Test User',
      googleId: 'test_google_id_123',
      isActive: true,
      user_type: 'paid'
    };

    // Check if user already exists
    let user = await CitraAIUser.findOne({ email: testUser.email });
    if (!user) {
      user = new CitraAIUser(testUser);
      await user.save();
      console.log('✅ Test user created:', user.email);
    } else {
      console.log('✅ Test user already exists:', user.email);
    }

    // Generate JWT token for the user
    const token = jwt.sign(
      {
        user_id: user.email,  // Use email as user_id (as per googleAuthService)
        email: user.email,
        name: user.name,
        googleId: user.googleId
      },
      process.env.JWT_SECRET,
      { expiresIn: process.env.JWT_EXPIRES_IN || '7d' }
    );

    console.log('\n🔑 JWT Token Generated for Test User\n');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
    console.log('Token:', token);
    console.log('\nUser ID:', user._id.toString());
    console.log('Email:', user.email);
    console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

    // Test the welcome email endpoint
    console.log('Testing welcome email endpoint...');

    const fetch = require('node-fetch');
    const response = await fetch('http://localhost:7004/api/auth/send-welcome-email', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        email: user.email,
        profession: 'Software Engineer'
      })
    });

    const result = await response.json();
    console.log('Response status:', response.status);
    console.log('Response:', JSON.stringify(result, null, 2));

    if (response.ok) {
      console.log('✅ Welcome email endpoint test successful!');
    } else {
      console.log('❌ Welcome email endpoint test failed');
    }

  } catch (error) {
    console.error('Error:', error);
  } finally {
    await mongoose.disconnect();
  }
}

createTestUserAndTestEmail();