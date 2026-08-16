// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Templated Email Handler
 * 
 * Handles various email templates for the application including:
 * - team_invitation: Send invitation to join a team
 * - invitation_claimed: Security notification when invite is claimed by different email
 */
const sendEmail = require('../shared/sendEmail');
const escapeHtml = require('../shared/escapeHtml');

/**
 * Validate a URL to prevent javascript: and data: injection in href attributes
 */
function sanitizeUrl(url) {
    if (!url || typeof url !== 'string') return '#';
    const trimmed = url.trim();
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    return '#';
}

const APP_URL = process.env.APP_URL || 'https://citra-ai.com';
const SUPPORT_EMAIL = process.env.EMAIL_SUPPORT || 'support@citra-ai.com';

/**
 * Generate team invitation email.
 * Brand: Citra is the agentic operating layer — Smart Apps, Deep Analytics
 * Chat, and Workflow Engine, composed across a single tenant.
 */
function getTeamInvitationTemplate(data) {
    const { team_name, invited_by_name, invite_link, personal_message } = data;

    const subject = `You're invited to ${team_name} on Citra AI`;

    const messageBlock = personal_message
        ? `\n\nA note from ${invited_by_name}:\n"${personal_message}"\n`
        : '';

    const text = `
Hello,

${invited_by_name} has invited you to collaborate on "${team_name}" inside their Citra AI tenant.
${messageBlock}
Accept the invitation:
${invite_link}

This invitation expires in 7 days.

What you'll have access to inside the tenant:
• Citra Smart Apps — agentic apps authored in natural language, with shared master records and approver gates.
• Deep Analytics Chat — sandboxed agentic analyst that turns a month of analyst effort into one overnight, cited run.
• Agentic Workflow Engine — 24/7 IT-grade orchestration with AI decision nodes.
• Everyday Tools — Presentations, Reports, Dashboards, Knowledge Graphs, Mindmaps, Diagrams, and the Reader & Review sidebar.

Sovereign by architecture: zero copy, zero ETL, zero egress. Everything runs inside your perimeter.

If you weren't expecting this invitation, you can safely ignore this email.

— The Citra AI Team
${APP_URL} · ${SUPPORT_EMAIL}
    `;

    const html = `
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Team Invitation — Citra AI</title>
</head>
<body style="margin:0;padding:0;background-color:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;line-height:1.6;color:#334155;">
    <div style="max-width:640px;margin:0 auto;padding:24px 12px;">
        <div style="background:#FFFFFF;border-radius:20px;overflow:hidden;box-shadow:0 20px 50px -20px rgba(15,23,42,0.45);">

            <!-- Header -->
            <div style="background:linear-gradient(135deg,#0F172A 0%,#1E293B 50%,#0F172A 100%);padding:24px 32px;border-bottom:1px solid rgba(148,163,184,0.15);">
                <div style="font-size:20px;font-weight:800;color:#F8FAFC;letter-spacing:-0.5px;">
                    <span style="display:inline-block;background:#3B82F6;color:#FFFFFF;padding:4px 10px;border-radius:6px;font-size:14px;font-weight:800;margin-right:8px;">C</span>
                    Citra AI
                </div>
            </div>

            <!-- Hero -->
            <div style="background:linear-gradient(135deg,#0F172A 0%,#1E293B 100%);padding:32px 32px 36px 32px;">
                <div style="display:inline-block;background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.35);padding:5px 12px;border-radius:999px;margin-bottom:14px;">
                    <span style="color:#60A5FA;font-size:11px;font-weight:700;letter-spacing:1.2px;">TENANT INVITATION</span>
                </div>
                <h1 style="margin:0 0 12px 0;font-size:26px;font-weight:800;color:#F8FAFC;line-height:1.25;letter-spacing:-0.8px;">
                    You're invited to <span style="color:#60A5FA;">${escapeHtml(team_name)}</span>
                </h1>
                <p style="margin:0;color:#94A3B8;font-size:15px;line-height:1.6;">
                    <strong style="color:#E2E8F0;">${escapeHtml(invited_by_name)}</strong> has invited you to collaborate inside their Citra AI tenant — the agentic operating layer where Smart Apps, Deep Analytics Chat, and the Workflow Engine all share one set of records.
                </p>
                <div style="margin-top:24px;">
                    <a href="${sanitizeUrl(invite_link)}" style="display:inline-block;background:linear-gradient(135deg,#3B82F6 0%,#2563EB 100%);color:#FFFFFF;text-decoration:none;font-weight:700;font-size:15px;padding:14px 30px;border-radius:10px;box-shadow:0 10px 25px -8px rgba(59,130,246,0.6);">
                        Accept invitation →
                    </a>
                    <div style="margin-top:14px;color:#64748B;font-size:12px;">This invitation expires in 7 days.</div>
                </div>
            </div>

            ${personal_message ? `
            <!-- Personal message -->
            <div style="padding:24px 32px 0 32px;background:#FFFFFF;">
                <div style="background:#F8FAFC;border-left:4px solid #8B5CF6;border-radius:10px;padding:18px 20px;">
                    <div style="color:#64748B;font-size:12px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;margin-bottom:6px;">A note from ${escapeHtml(invited_by_name)}</div>
                    <div style="color:#1E293B;font-size:14px;font-style:italic;line-height:1.55;">"${escapeHtml(personal_message)}"</div>
                </div>
            </div>
            ` : ''}

            <!-- What you'll access -->
            <div style="padding:28px 32px 8px 32px;background:#FFFFFF;">
                <div style="text-align:center;font-size:11px;font-weight:800;color:#3B82F6;letter-spacing:1.4px;margin-bottom:6px;">WHAT'S INSIDE</div>
                <div style="text-align:center;font-size:20px;font-weight:800;color:#0F172A;margin-bottom:20px;letter-spacing:-0.4px;">Citra products in this tenant</div>

                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:separate;border-spacing:0 10px;">
                    <tr>
                        <td style="background:#F8FAFC;border-left:4px solid #8B5CF6;border-radius:10px;padding:14px 16px;">
                            <div style="color:#0F172A;font-size:14px;font-weight:700;margin-bottom:3px;">Citra Smart Apps</div>
                            <div style="color:#475569;font-size:12.5px;line-height:1.55;">Agentic apps authored in natural language, with shared master records and approver gates.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="background:#F8FAFC;border-left:4px solid #22D3EE;border-radius:10px;padding:14px 16px;">
                            <div style="color:#0F172A;font-size:14px;font-weight:700;margin-bottom:3px;">Deep Analytics Chat</div>
                            <div style="color:#475569;font-size:12.5px;line-height:1.55;">A sandboxed agentic analyst — a month of analyst effort in one cited overnight run.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="background:#F8FAFC;border-left:4px solid #60A5FA;border-radius:10px;padding:14px 16px;">
                            <div style="color:#0F172A;font-size:14px;font-weight:700;margin-bottom:3px;">Agentic Workflow Engine</div>
                            <div style="color:#475569;font-size:12.5px;line-height:1.55;">IT-grade orchestration with AI decision nodes, 24/7 on your infrastructure.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="background:#F8FAFC;border-left:4px solid #F472B6;border-radius:10px;padding:14px 16px;">
                            <div style="color:#0F172A;font-size:14px;font-weight:700;margin-bottom:3px;">Everyday Tools</div>
                            <div style="color:#475569;font-size:12.5px;line-height:1.55;">Presentations, Reports, Dashboards, Knowledge Graphs, Mindmaps, Diagrams, Reader &amp; Review.</div>
                        </td>
                    </tr>
                </table>
            </div>

            <!-- Sovereign strip -->
            <div style="padding:20px 32px 24px 32px;background:#FFFFFF;text-align:center;">
                <div style="display:inline-block;padding:10px 18px;background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.25);border-radius:999px;">
                    <span style="color:#16A34A;font-size:12px;font-weight:700;">Sovereign by architecture · zero copy · zero ETL · zero egress</span>
                </div>
            </div>

            <!-- Footer -->
            <div style="padding:22px 32px;background:#F1F5F9;text-align:center;border-top:1px solid #E2E8F0;">
                <p style="margin:0 0 10px 0;font-size:12px;color:#64748B;">
                    If you weren't expecting this invitation, you can safely ignore this email.
                </p>
                <p style="margin:0;font-size:11px;color:#94A3B8;">
                    © ${new Date().getFullYear()} Citra AI · Trustedwear Tech Pvt Ltd ·
                    <a href="${APP_URL}" style="color:#3B82F6;text-decoration:none;">Visit citra-ai.com</a> ·
                    <a href="mailto:${SUPPORT_EMAIL}" style="color:#3B82F6;text-decoration:none;">Support</a>
                </p>
            </div>
        </div>
    </div>
</body>
</html>
    `;

    return { subject, text, html };
}

/**
 * Generate invitation-claimed security notification email.
 * Sent when a different email address claims a tenant invitation.
 */
function getInvitationClaimedTemplate(data) {
    const { team_name, invited_by_name, original_email, claiming_email, claiming_user_name, claimed_at } = data;

    const subject = `Security notice: your Citra AI invitation to ${team_name} was claimed`;

    const text = `
Security Notice — Citra AI Tenant Invitation Claimed

Hello,

This is an audit-trail notification about your invitation to the Citra AI tenant "${team_name}".

What happened
• You were invited by ${invited_by_name} to join "${team_name}"
• The invitation was sent to: ${original_email}
• A different account has now claimed this invitation:
  - Email: ${claiming_email}
  - Name:  ${claiming_user_name}
  - At:    ${claimed_at}

What this means
The account at ${claiming_email} received the invitation link and used it to join the tenant. This typically happens when the invitation email is forwarded or the link is shared with a colleague.

If you did NOT share this invitation
This could indicate unauthorized access to your email or to the invitation link. We recommend:
1. Contact the tenant administrator (${invited_by_name}) to verify the new member
2. Rotate your email password if you suspect compromise
3. Review your account security settings on Citra AI

If this was intentional
No action is needed. ${claiming_user_name} has joined the tenant. Their actions will appear in the per-tenant audit log alongside every Smart App, Deep Analytics Chat run, and Workflow Engine execution.

Questions? Reply to this email or write to ${SUPPORT_EMAIL}.

— Citra AI Security
${APP_URL}
    `;

    const html = `
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Security Notice — Citra AI</title>
</head>
<body style="margin:0;padding:0;background-color:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;line-height:1.6;color:#334155;">
    <div style="max-width:640px;margin:0 auto;padding:24px 12px;">
        <div style="background:#FFFFFF;border-radius:20px;overflow:hidden;box-shadow:0 20px 50px -20px rgba(15,23,42,0.45);">

            <!-- Header -->
            <div style="background:linear-gradient(135deg,#0F172A 0%,#1E293B 50%,#0F172A 100%);padding:24px 32px;border-bottom:1px solid rgba(148,163,184,0.15);">
                <div style="font-size:20px;font-weight:800;color:#F8FAFC;letter-spacing:-0.5px;">
                    <span style="display:inline-block;background:#3B82F6;color:#FFFFFF;padding:4px 10px;border-radius:6px;font-size:14px;font-weight:800;margin-right:8px;">C</span>
                    Citra AI
                </div>
                <div style="margin-top:10px;color:#94A3B8;font-size:12px;letter-spacing:0.4px;">Audit-trail notification</div>
            </div>

            <!-- Hero -->
            <div style="background:linear-gradient(135deg,#0F172A 0%,#1E293B 100%);padding:30px 32px 32px 32px;">
                <div style="display:inline-block;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.4);padding:5px 12px;border-radius:999px;margin-bottom:14px;">
                    <span style="color:#FBBF24;font-size:11px;font-weight:700;letter-spacing:1.2px;">SECURITY NOTICE</span>
                </div>
                <h1 style="margin:0 0 12px 0;font-size:24px;font-weight:800;color:#F8FAFC;line-height:1.3;letter-spacing:-0.6px;">
                    Your invitation to <span style="color:#FBBF24;">${escapeHtml(team_name)}</span> was claimed by another account.
                </h1>
                <p style="margin:0;color:#94A3B8;font-size:14.5px;line-height:1.6;">
                    Every action inside a Citra tenant is audit-logged. This is the audit-trail entry for the claim.
                </p>
            </div>

            <!-- What happened table -->
            <div style="padding:28px 32px 8px 32px;background:#FFFFFF;">
                <div style="font-size:11px;font-weight:800;color:#F59E0B;letter-spacing:1.4px;margin-bottom:10px;">WHAT HAPPENED</div>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border:1px solid #E2E8F0;border-radius:12px;border-collapse:separate;overflow:hidden;font-size:13.5px;">
                    <tr>
                        <td style="padding:12px 16px;color:#64748B;font-weight:600;width:38%;background:#F8FAFC;">Tenant</td>
                        <td style="padding:12px 16px;color:#0F172A;">${escapeHtml(team_name)}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 16px;color:#64748B;font-weight:600;background:#F8FAFC;border-top:1px solid #E2E8F0;">Invited by</td>
                        <td style="padding:12px 16px;color:#0F172A;border-top:1px solid #E2E8F0;">${escapeHtml(invited_by_name)}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 16px;color:#64748B;font-weight:600;background:#F8FAFC;border-top:1px solid #E2E8F0;">Original recipient</td>
                        <td style="padding:12px 16px;color:#0F172A;border-top:1px solid #E2E8F0;">${escapeHtml(original_email)}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 16px;color:#92400E;font-weight:700;background:#FEF3C7;border-top:1px solid #FDE68A;">Claimed by</td>
                        <td style="padding:12px 16px;color:#0F172A;background:#FFFBEB;border-top:1px solid #FDE68A;">
                            <strong>${escapeHtml(claiming_user_name)}</strong><br>
                            <span style="color:#64748B;font-size:12.5px;">${escapeHtml(claiming_email)}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:12px 16px;color:#64748B;font-weight:600;background:#F8FAFC;border-top:1px solid #E2E8F0;">Claimed at</td>
                        <td style="padding:12px 16px;color:#0F172A;border-top:1px solid #E2E8F0;">${escapeHtml(claimed_at)}</td>
                    </tr>
                </table>
            </div>

            <!-- Why this happens -->
            <div style="padding:20px 32px 0 32px;background:#FFFFFF;">
                <div style="background:#F0F9FF;border-left:4px solid #0EA5E9;border-radius:10px;padding:16px 18px;">
                    <div style="color:#0C4A6E;font-size:14px;font-weight:700;margin-bottom:6px;">What this means</div>
                    <div style="color:#075985;font-size:13px;line-height:1.6;">
                        The account at <strong>${escapeHtml(claiming_email)}</strong> received the invitation link and used it to join the tenant.
                        This typically happens when an invitation is forwarded or the link is shared with a colleague.
                    </div>
                </div>
            </div>

            <!-- If unauthorized -->
            <div style="padding:14px 32px 0 32px;background:#FFFFFF;">
                <div style="background:#FEF2F2;border-left:4px solid #EF4444;border-radius:10px;padding:16px 18px;">
                    <div style="color:#991B1B;font-size:14px;font-weight:700;margin-bottom:6px;">If you did NOT share this invitation</div>
                    <ol style="margin:0;padding-left:20px;color:#7F1D1D;font-size:13px;line-height:1.7;">
                        <li>Contact the tenant administrator (<strong>${escapeHtml(invited_by_name)}</strong>) to verify the new member.</li>
                        <li>Rotate your email password if you suspect compromise.</li>
                        <li>Review your account security on Citra AI.</li>
                    </ol>
                </div>
            </div>

            <!-- If intentional -->
            <div style="padding:14px 32px 28px 32px;background:#FFFFFF;">
                <div style="background:#F0FDF4;border-left:4px solid #22C55E;border-radius:10px;padding:16px 18px;">
                    <div style="color:#166534;font-size:14px;font-weight:700;margin-bottom:6px;">If this was intentional</div>
                    <div style="color:#15803D;font-size:13px;line-height:1.6;">
                        No action is needed. <strong>${escapeHtml(claiming_user_name)}</strong> has joined ${escapeHtml(team_name)} and every subsequent action — Smart App runs, Deep Analytics Chat queries, Workflow Engine executions — is recorded in the per-tenant audit log.
                    </div>
                </div>
            </div>

            <!-- Footer -->
            <div style="padding:22px 32px;background:#F1F5F9;text-align:center;border-top:1px solid #E2E8F0;">
                <p style="margin:0 0 10px 0;font-size:12px;color:#64748B;">
                    Questions? <a href="mailto:${SUPPORT_EMAIL}" style="color:#3B82F6;text-decoration:none;">${SUPPORT_EMAIL}</a>
                </p>
                <p style="margin:0;font-size:11px;color:#94A3B8;">
                    © ${new Date().getFullYear()} Citra AI · Trustedwear Tech Pvt Ltd ·
                    <a href="${APP_URL}" style="color:#3B82F6;text-decoration:none;">citra-ai.com</a>
                </p>
            </div>
        </div>
    </div>
</body>
</html>
    `;

    return { subject, text, html };
}

/**
 * Get email template by type
 */
function getEmailTemplate(templateType, templateData) {
    switch (templateType) {
        case 'team_invitation':
            return getTeamInvitationTemplate(templateData);
        case 'invitation_claimed':
            return getInvitationClaimedTemplate(templateData);
        default:
            throw new Error(`Unknown template type: ${templateType}`);
    }
}

/**
 * Express handler for sending templated emails
 */
const handler = async (req, res) => {
    try {
        const { to, templateType, templateData } = req.body;
        
        // Validate required fields
        if (!to || typeof to !== 'string') {
            return res.status(400).json({ success: false, error: 'Invalid or missing recipient email' });
        }
        
        if (!templateType || typeof templateType !== 'string') {
            return res.status(400).json({ success: false, error: 'Invalid or missing template type' });
        }
        
        if (!templateData || typeof templateData !== 'object') {
            return res.status(400).json({ success: false, error: 'Invalid or missing template data' });
        }
        
        console.log(`📧 Sending ${templateType} email to ${to}`);
        
        // Get the appropriate template
        let emailContent;
        try {
            emailContent = getEmailTemplate(templateType, templateData);
        } catch (templateError) {
            return res.status(400).json({ success: false, error: templateError.message });
        }
        
        // Send the email
        await sendEmail({
            to: to,
            from: process.env.EMAIL_DEFAULT_SENDER || 'info@citra-ai.com',
            subject: emailContent.subject,
            text: emailContent.text,
            html: emailContent.html
        });
        
        console.log(`✅ ${templateType} email sent successfully to ${to}`);
        
        return res.status(200).json({ success: true, message: `Email sent successfully to ${to}` });
        
    } catch (error) {
        console.error('Error sending templated email:', error);
        
        if (error.message && error.message.includes('timeout')) {
            return res.status(503).json({ success: false, error: 'Operation timed out. Please try again.' });
        }
        
        return res.status(500).json({ success: false, error: 'Error sending email' });
    }
};

module.exports = handler;
module.exports.getEmailTemplate = getEmailTemplate;
module.exports.getTeamInvitationTemplate = getTeamInvitationTemplate;
module.exports.getInvitationClaimedTemplate = getInvitationClaimedTemplate;
