// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const { SESClient, SendEmailCommand } = require('@aws-sdk/client-ses');
const nodemailer = require('nodemailer');
const envConfig = require('../config/environment');

// ── Provider initialisation (explicit — no fallback) ──

let sendViaSes = null;
let sendViaSmtp = null;

if (envConfig.emailProvider === 'ses') {
  const sesClient = new SESClient({
    region: envConfig.awsRegion,
    credentials: {
      accessKeyId: envConfig.awsAccessKeyId,
      secretAccessKey: envConfig.awsSecretAccessKey
    }
  });

  sendViaSes = async (params) => {
    const command = new SendEmailCommand(params);
    return sesClient.send(command);
  };

  console.log('📧 Email provider: AWS SES');
} else if (envConfig.emailProvider === 'smtp') {
  const transporter = nodemailer.createTransport({
    host: envConfig.smtpHost,
    port: envConfig.smtpPort,
    secure: envConfig.smtpSecure,
    auth: {
      user: envConfig.smtpUser,
      pass: envConfig.smtpPass
    }
  });

  sendViaSmtp = async (mailOptions) => {
    return transporter.sendMail(mailOptions);
  };

  console.log('📧 Email provider: SMTP');
} else {
  console.warn('⚠️  EMAIL_PROVIDER not set (expected "ses" or "smtp"). Email sending is disabled.');
}

/**
 * Send an email using the configured provider (SES or SMTP).
 * @param {Object} options
 * @param {string} options.to - Recipient email
 * @param {string} [options.from] - Sender email (defaults to EMAIL_DEFAULT_SENDER)
 * @param {string} options.subject - Email subject
 * @param {string} options.text - Plain text content
 * @param {string} [options.html] - HTML content (optional)
 * @returns {Promise<{success: boolean, response?: any}>}
 */
async function sendEmail({ to, from, subject, text, html }) {
  const sender = from || envConfig.defaultFromEmail;

  // ── SES ──
  if (sendViaSes) {
    // SES requires at least one of Text or Html in the Body. When only
    // html is provided (e.g. workflow email with tables), synthesize a
    // plain-text fallback by stripping tags so Text.Data is never null.
    const plainText = text || (html ? html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim() : '');
    const params = {
      Destination: { ToAddresses: [to] },
      Message: {
        Body: {
          Text: { Data: plainText, Charset: 'UTF-8' }
        },
        Subject: { Data: subject, Charset: 'UTF-8' }
      },
      Source: sender
    };
    if (html) {
      params.Message.Body.Html = { Data: html, Charset: 'UTF-8' };
    }

    try {
      const response = await sendViaSes(params);
      if (envConfig.nodeEnv === 'development') {
        console.log('Email parameters:', JSON.stringify(params));
        console.log('AWS SES response:', JSON.stringify(response));
      }
      console.log(`Email sent successfully to ${to} with subject "${subject}" and MessageId: ${response.MessageId}`);
      return { success: true, response };
    } catch (error) {
      console.error(`Failed to send email (SES) to ${to}: ${error.message}`);
      throw error;
    }
  }

  // ── SMTP ──
  if (sendViaSmtp) {
    const mailOptions = { from: sender, to, subject, text };
    if (html) mailOptions.html = html;

    try {
      const info = await sendViaSmtp(mailOptions);
      console.log(`Email sent (SMTP) to ${to} — MessageId: ${info.messageId}`);
      return { success: true, response: info };
    } catch (error) {
      console.error(`Failed to send email (SMTP) to ${to}: ${error.message}`);
      throw error;
    }
  }

  // ── No provider configured ──
  throw new Error('Email sending is disabled. Set EMAIL_PROVIDER to "ses" or "smtp" in environment.');
}

module.exports = sendEmail;
