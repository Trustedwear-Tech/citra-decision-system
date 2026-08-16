// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const sendEmail = require('../shared/sendEmail');
const escapeHtml = require('../shared/escapeHtml');

const handler = async (req, res) => {
    try {
        // Parse the request body
        const requestData = req.body;

        const { name, email, mobile, subject, message } = requestData;

        // Validate required fields
        if (!name || typeof name !== 'string' || name.trim().length === 0) {
            return res.status(400).json({
                success: false,
                error: 'Name is required and must be a non-empty string'
            });
        }

        if (!email || typeof email !== 'string' || !/\S+@\S+\.\S+/.test(email)) {
            return res.status(400).json({
                success: false,
                error: 'Valid email address is required'
            });
        }

        if (!subject || typeof subject !== 'string' || subject.trim().length === 0) {
            return res.status(400).json({
                success: false,
                error: 'Subject is required and must be a non-empty string'
            });
        }

        if (!message || typeof message !== 'string' || message.trim().length === 0) {
            return res.status(400).json({
                success: false,
                error: 'Message is required and must be a non-empty string'
            });
        }

        // Mobile is required and validate format
        if (!mobile || typeof mobile !== 'string' || mobile.trim().length === 0) {
            return res.status(400).json({
                success: false,
                error: 'Mobile number is required'
            });
        }
        if (!/^\+?[\d\s\-\(\)]+$/.test(mobile)) {
            return res.status(400).json({
                success: false,
                error: 'Mobile number must be a valid format (e.g., +91 84969 77722)'
            });
        }

        // Prepare email content — internal lead notification sent to the support inbox.
        const emailSubject = `[Citra AI Lead] ${subject}`;
        const emailText = `
New contact-form submission — Citra AI

From:    ${name}
Email:   ${email}
Mobile:  ${mobile}
Subject: ${subject}

Message
-------
${message}

---
Source: citra-ai.com contact form.
Reply directly to ${email} to engage the lead.
        `.trim();

        const emailHtml = `
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>New lead — Citra AI</title>
</head>
<body style="margin:0;padding:0;background:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#334155;">
    <div style="max-width:620px;margin:0 auto;padding:24px 12px;">
        <div style="background:#FFFFFF;border-radius:16px;overflow:hidden;box-shadow:0 12px 30px -10px rgba(15,23,42,0.4);">

            <!-- Header -->
            <div style="background:linear-gradient(135deg,#0F172A 0%,#1E293B 100%);padding:22px 28px;">
                <div style="display:flex;align-items:center;">
                    <span style="display:inline-block;background:#3B82F6;color:#FFFFFF;padding:4px 10px;border-radius:6px;font-size:14px;font-weight:800;margin-right:8px;">C</span>
                    <span style="color:#F8FAFC;font-size:18px;font-weight:800;letter-spacing:-0.3px;">Citra AI · Lead</span>
                </div>
                <div style="margin-top:10px;color:#94A3B8;font-size:13px;">New contact-form submission from citra-ai.com</div>
            </div>

            <!-- Fields -->
            <div style="padding:24px 28px 8px 28px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border:1px solid #E2E8F0;border-radius:10px;border-collapse:separate;overflow:hidden;font-size:14px;">
                    <tr>
                        <td style="padding:12px 14px;width:32%;background:#F8FAFC;color:#64748B;font-weight:700;">Name</td>
                        <td style="padding:12px 14px;color:#0F172A;">${escapeHtml(name)}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 14px;background:#F8FAFC;color:#64748B;font-weight:700;border-top:1px solid #E2E8F0;">Email</td>
                        <td style="padding:12px 14px;color:#0F172A;border-top:1px solid #E2E8F0;">
                            <a href="mailto:${escapeHtml(email)}" style="color:#3B82F6;text-decoration:none;">${escapeHtml(email)}</a>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:12px 14px;background:#F8FAFC;color:#64748B;font-weight:700;border-top:1px solid #E2E8F0;">Mobile</td>
                        <td style="padding:12px 14px;color:#0F172A;border-top:1px solid #E2E8F0;">${escapeHtml(mobile)}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 14px;background:#F8FAFC;color:#64748B;font-weight:700;border-top:1px solid #E2E8F0;">Subject</td>
                        <td style="padding:12px 14px;color:#0F172A;border-top:1px solid #E2E8F0;font-weight:600;">${escapeHtml(subject)}</td>
                    </tr>
                </table>
            </div>

            <!-- Message -->
            <div style="padding:16px 28px 24px 28px;">
                <div style="font-size:12px;color:#64748B;font-weight:700;letter-spacing:1px;margin-bottom:8px;">MESSAGE</div>
                <div style="background:#F8FAFC;border-left:4px solid #3B82F6;border-radius:10px;padding:16px 18px;color:#0F172A;font-size:14px;line-height:1.65;">
                    ${escapeHtml(message).replace(/\n/g, '<br>')}
                </div>
            </div>

            <!-- Footer -->
            <div style="padding:18px 28px;background:#F1F5F9;text-align:center;border-top:1px solid #E2E8F0;">
                <p style="margin:0;font-size:11px;color:#94A3B8;">
                    Reply directly to <a href="mailto:${escapeHtml(email)}" style="color:#3B82F6;text-decoration:none;">${escapeHtml(email)}</a> to engage the lead.
                </p>
            </div>
        </div>
    </div>
</body>
</html>`;

        // Validate environment variable
        const defaultToEmail = process.env.EMAIL_DEFAULT_TO;
        if (!defaultToEmail) {
            return res.status(500).json({
                success: false,
                error: 'Email configuration error: EMAIL_DEFAULT_TO not set'
            });
        }

        // Send email to support team
        await sendEmail({
            to: defaultToEmail,
            subject: emailSubject,
            text: emailText,
            html: emailHtml
        });

        console.log(`Contact form email sent successfully from ${email}`);

        return res.status(200).json({
            success: true,
            message: 'Contact form submitted successfully'
        });

    } catch (error) {
        console.error(`Error processing contact form: ${error.message}`);

        // Handle AWS SES authentication errors specifically
        if (error.isAuthError) {
            return res.status(500).json({
                success: false,
                error: 'Email service configuration error. Please contact support.'
            });
        }

        return res.status(500).json({
            success: false,
            error: 'Failed to send contact form. Please try again later.'
        });
    }
};

module.exports = handler;