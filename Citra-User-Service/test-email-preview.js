// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Simple test to preview the General Professional email template
 * No database connection required
 */

const { getWelcomeEmailTemplate } = require('./src/shared/emailTemplates.js');
const fs = require('fs');
const path = require('path');

// Create test user
const testUser = {
  name: 'Alex Professional',
  email: 'alex@example.com'
};

// Generate email template
console.log('Generating email template for General Professional...\n');
const emailTemplate = getWelcomeEmailTemplate(testUser, 'General Professional');

console.log('='.repeat(80));
console.log('SUBJECT:');
console.log('='.repeat(80));
console.log(emailTemplate.subject);
console.log('\n');

console.log('='.repeat(80));
console.log('PLAIN TEXT VERSION:');
console.log('='.repeat(80));
console.log(emailTemplate.text);
console.log('\n');

// Save HTML version to file for preview
const htmlPath = path.join(__dirname, 'email-preview.html');
fs.writeFileSync(htmlPath, emailTemplate.html);
console.log('='.repeat(80));
console.log('HTML VERSION:');
console.log('='.repeat(80));
console.log(`HTML email saved to: ${htmlPath}`);
console.log('Open this file in a browser to preview the email design.');
console.log('\n');

console.log('✅ Email template generated successfully!');
console.log('\nKey features in the email:');
console.log('- Subject emphasizes "Choose Vault AI, Not Chat AI"');
console.log('- Explains the dilution problem with other tools');
console.log('- Highlights 6 core capabilities');
console.log('- Shows 5 example queries');
console.log('- Platform capabilities section');
console.log('- Citations and research tools');
console.log('- Closing: "What are you building with Vault AI today?"');
