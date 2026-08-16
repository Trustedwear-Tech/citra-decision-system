#!/usr/bin/env node
// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Environment Startup Script
 * 
 * This script helps you start the application with the correct environment configuration.
 * It automatically detects whether to use .env files or Vault based on configuration.
 */

const { loadVaultSecrets } = require('./src/config/vault-env-loader');

async function startWithEnvironment() {
  console.log('🚀 Starting User Service with environment configuration...\n');
  
  try {
    // Load environment variables (from .env or Vault)
    await loadVaultSecrets();
    
    console.log('\n📋 Environment Summary:');
    console.log(`   NODE_ENV: ${process.env.NODE_ENV || 'development'}`);
    console.log(`   PORT: ${process.env.PORT || '3000'}`);
    console.log(`   Database Server: ${process.env.DB_SERVER ? '✅ Configured' : '❌ Missing'}`);
    console.log(`   Vault: ${process.env.VAULT_ADDR ? '✅ Enabled' : '❌ Disabled'}`);
    console.log(`   Razorpay: ${process.env.RZP_KEY_ID ? '✅ Configured' : '❌ Not configured'}`);
    
    console.log('\n🎯 Starting Express server...\n');
    
    // Start the main application
    require('./server.js');
    
  } catch (error) {
    console.error('💥 Failed to start application:', error.message);
    process.exit(1);
  }
}

// Only run if this script is executed directly (not required as a module)
if (require.main === module) {
  startWithEnvironment();
}

module.exports = { startWithEnvironment };
