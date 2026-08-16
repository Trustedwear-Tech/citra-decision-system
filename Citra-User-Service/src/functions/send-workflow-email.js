// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const sendEmail = require('../shared/sendEmail');

/**
 * Workflow transactional email handler.
 *
 * Sends an email directly to the specified recipient.
 * Used by Citra-Service workflow nodes and notification system.
 *
 * Body: { to: string, subject: string, body: string }
 */
const handler = async (req, res) => {
    try {
        const { to, subject, body } = req.body;

        if (!to || typeof to !== 'string' || !/\S+@\S+\.\S+/.test(to)) {
            return res.status(400).json({ success: false, error: 'Valid recipient email is required' });
        }
        if (!subject || typeof subject !== 'string' || subject.trim().length === 0) {
            return res.status(400).json({ success: false, error: 'Subject is required' });
        }
        if (!body || typeof body !== 'string' || body.trim().length === 0) {
            return res.status(400).json({ success: false, error: 'Body is required' });
        }

        // Detect HTML content and send as html param so SES uses the correct MIME type
        const isHtml = /<[a-z][\s\S]*>/i.test(body);

        const emailParams = {
            to: to.trim(),
            subject: subject.trim(),
        };
        if (isHtml) {
            emailParams.html = body.trim();
        } else {
            emailParams.text = body.trim();
        }

        await sendEmail(emailParams);

        return res.status(200).json({ success: true, message: 'Email sent' });
    } catch (error) {
        console.error(`Workflow email error: ${error.message}`);
        if (error.isAuthError) {
            return res.status(500).json({ success: false, error: 'Email service configuration error' });
        }
        return res.status(500).json({ success: false, error: 'Failed to send email' });
    }
};

module.exports = handler;
