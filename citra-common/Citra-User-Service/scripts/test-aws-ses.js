// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

require('dotenv').config();
const sendEmail = require('../src/shared/sendEmail');
const { getWelcomeEmailTemplate } = require('../src/shared/emailTemplates');

async function testWelcomeEmail() {
    console.log('🔧 Testing Welcome Email Template with AWS SES...\n');
    
    // Check environment variables
    console.log('Environment Variables:');
    console.log(`AWS_ACCESS_KEY_ID: ${process.env.AWS_ACCESS_KEY_ID ? '✅ Set (length: ' + process.env.AWS_ACCESS_KEY_ID.length + ')' : '❌ Not Set'}`);
    console.log(`AWS_SECRET_ACCESS_KEY: ${process.env.AWS_SECRET_ACCESS_KEY ? '✅ Set (length: ' + process.env.AWS_SECRET_ACCESS_KEY.length + ')' : '❌ Not Set'}`);
    console.log(`AWS_REGION: ${process.env.AWS_REGION || '❌ Not Set'}`);
    console.log('');
    
    if (!process.env.AWS_ACCESS_KEY_ID || !process.env.AWS_SECRET_ACCESS_KEY) {
        console.error('❌ AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY is not set in environment variables');
        return;
    }
    
    console.log('📧 Sending test email...\n');
    
    try {
        // Create a mock user object for testing
        const testUser = {
            name: 'Deepak Kumar',
            email: 'deeepakumar@gmail.com'
        };
        
        // Generate welcome email content
        const emailTemplate = getWelcomeEmailTemplate(testUser);
        
        const result = await sendEmail({
            to: testUser.email,
            subject: emailTemplate.subject,
            text: emailTemplate.text,
            html: emailTemplate.html
        });
        
        console.log(`✅ Welcome email sent successfully to ${testUser.email}!`);
        console.log('Subject:', emailTemplate.subject);
        console.log('MessageId:', result.response.MessageId);
        console.log('Response:', JSON.stringify(result, null, 2));
        
    } catch (error) {
        console.error('❌ Failed to send welcome email:');
        console.error('Error message:', error.message);
        
        if (error.isAuthError) {
            console.error('\n⚠️ AUTHENTICATION ERROR:');
            console.error('The AWS SES credentials appear to be invalid.');
            console.error('Please verify:');
            console.error('1. AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are correct in .env file');
            console.error('2. The IAM user has SES permissions');
            console.error('3. The AWS region is correct');
            console.error('4. Sender email is verified in AWS SES (info@citra-ai.com)');
        } else if (error.originalError && error.originalError.code === 'MessageRejected') {
            console.error('\n⚠️ MESSAGE REJECTED:');
            console.error('The email was rejected by AWS SES.');
            console.error('Please check:');
            console.error('1. Sender email is verified in AWS SES');
            console.error('2. AWS SES is not in sandbox mode, or recipient is verified');
            console.error('3. Daily sending limits have not been exceeded');
        }
        
        if (error.originalError) {
            console.error('\nOriginal error details:');
            console.error(error.originalError);
        }
    }
}

testWelcomeEmail();
