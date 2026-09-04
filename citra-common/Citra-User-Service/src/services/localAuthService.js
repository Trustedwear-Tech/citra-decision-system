// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const bcrypt = require('bcryptjs');
const crypto = require('crypto');
const CitraAIUser = require('../models/CitraAIUser');
const tokenService = require('./tokenService');
const envConfig = require('../config/environment');
const sendEmail = require('../shared/sendEmail');
const { ensurePersonalSA } = require('./personalSAService');
const { ensureWorkSA } = require('./workSAService');
const { applyToUserData, applyToUserDoc } = require('./deploymentDefaults');

const SALT_ROUNDS = 12;
const VERIFICATION_TOKEN_BYTES = 32;
const TOKEN_EXPIRY_HOURS = 24;
const RESET_TOKEN_EXPIRY_HOURS = 1;

class LocalAuthService {

  // ─── Registration ────────────────────────────────────────────────

  async register({ email, password, name, termsAcceptedAt }) {
    const normalizedEmail = email.trim().toLowerCase();

    // Check existing user
    const existing = await CitraAIUser.findOne({ email: normalizedEmail });
    if (existing) {
      if (existing.authProvider === 'google') {
        throw Object.assign(
          new Error('This email is registered with Google. Please sign in with Google, or use "Forgot Password" to add a password.'),
          { statusCode: 409, code: 'GOOGLE_ACCOUNT_EXISTS' }
        );
      }
      throw Object.assign(
        new Error('An account with this email already exists.'),
        { statusCode: 409, code: 'ACCOUNT_EXISTS' }
      );
    }

    // Hash password
    const passwordHash = await bcrypt.hash(password, SALT_ROUNDS);

    // Generate email verification token
    const verificationToken = crypto.randomBytes(VERIFICATION_TOKEN_BYTES).toString('hex');
    const verificationExpiry = new Date(Date.now() + TOKEN_EXPIRY_HOURS * 60 * 60 * 1000);

    // Build user data
    const userData = {
      email: normalizedEmail,
      name: name || '',
      passwordHash,
      authProvider: 'local',
      emailVerified: false,
      emailVerificationToken: verificationToken,
      emailVerificationExpiry: verificationExpiry,
      isActive: true,
      lastLogin: new Date()
    };

    if (termsAcceptedAt) {
      userData.gdpr_consent = {
        terms_accepted_at: new Date(termsAcceptedAt),
        privacy_accepted_at: new Date(termsAcceptedAt),
        consent_version: '1.0',
      };
    }

    // Stamp deployment defaults (org_id, dept_ids=[citra-software],
    // entity_type=company). See services/deploymentDefaults.js.
    applyToUserData(userData, null, true);

    const user = await CitraAIUser.create(userData);

    // Initialize usage tracking
    try {
      const usageTrackingService = require('./usageTrackingService');
      await usageTrackingService.initializeNewUser(user.email, user.email, 0);
      console.log(`[LOCAL-AUTH] New user ${user.email} initialized`);
    } catch (err) {
      console.warn('[LOCAL-AUTH] Failed to initialize usage tracking:', err.message);
    }

    // Idempotently ensure both Service Accounts (Personal for personal-output
    // resources, Work for durable team-portable resources).
    try {
      await ensurePersonalSA(user);
    } catch (err) {
      console.warn('[LOCAL-AUTH] ensurePersonalSA failed (continuing):', err.message);
    }
    try {
      await ensureWorkSA(user);
    } catch (err) {
      console.warn('[LOCAL-AUTH] ensureWorkSA failed (continuing):', err.message);
    }

    // Send verification email (non-blocking)
    this._sendVerificationEmail(user.email, user.name, verificationToken).catch(err => {
      console.error(`[LOCAL-AUTH] Failed to send verification email to ${user.email}:`, err.message);
    });

    // Generate JWT
    const token = await tokenService.generateToken(user);

    return {
      user: user.toJSON(),
      token,
      tokenType: 'Bearer',
      expiresIn: envConfig.jwtExpiresIn || '7d',
      isNewUser: true,
      emailVerified: false,
      accessStatus: { hasAccess: true, user_type: user.user_type || 'paid' }
    };
  }

  // ─── Login ───────────────────────────────────────────────────────

  async login({ email, password }) {
    const normalizedEmail = email.trim().toLowerCase();

    const user = await CitraAIUser.findByEmailWithPassword(normalizedEmail);
    if (!user) {
      throw Object.assign(new Error('Invalid email or password.'), { statusCode: 401 });
    }

    // Check if user has a password (could be Google-only account)
    if (!user.passwordHash) {
      throw Object.assign(
        new Error('This account uses Google sign-in. Please sign in with Google.'),
        { statusCode: 401, code: 'GOOGLE_ONLY_ACCOUNT' }
      );
    }

    const isMatch = await bcrypt.compare(password, user.passwordHash);
    if (!isMatch) {
      throw Object.assign(new Error('Invalid email or password.'), { statusCode: 401 });
    }

    // Backfill enterprise context (org_id, dept_ids, entity_type) for
    // legacy users registered before deployment defaults existed.
    // Without org_id, ensureWorkSA returns null silently and the JWT
    // carries work_sa_id=null — which is exactly the banner case.
    // Idempotent: applyToUserDoc only sets fields that are empty.
    const changed = applyToUserDoc(user);
    if (changed) {
      console.log(`[LOCAL-AUTH][defaults-backfill] org_id=${user.org_id} dept_ids=${JSON.stringify(user.dept_ids)} for ${user.email}`);
    }

    // Update last login
    user.lastLogin = new Date();
    await user.save();

    // Idempotently ensure both Service Accounts on every login (not just
    // registration). Repairs four drift cases: legacy users predating the
    // SA system, users whose org_id arrived after signup so the original
    // ensure returned null, users whose org_id we just backfilled above,
    // and SA docs that were deleted out-of-band. Must run before
    // generateToken so the fresh ids make it into the JWT claims.
    try {
      await ensurePersonalSA(user);
    } catch (err) {
      console.warn('[LOCAL-AUTH] ensurePersonalSA failed (continuing):', err.message);
    }
    try {
      await ensureWorkSA(user);
    } catch (err) {
      console.warn('[LOCAL-AUTH] ensureWorkSA failed (continuing):', err.message);
    }

    const token = await tokenService.generateToken(user);

    return {
      user: user.toJSON(),
      token,
      tokenType: 'Bearer',
      expiresIn: envConfig.jwtExpiresIn || '7d',
      isNewUser: false,
      emailVerified: user.emailVerified,
      accessStatus: { hasAccess: true, user_type: user.user_type || 'paid' }
    };
  }

  // ─── Email verification ──────────────────────────────────────────

  async verifyEmail(token) {
    const user = await CitraAIUser.findOne({
      emailVerificationToken: token,
      emailVerificationExpiry: { $gt: new Date() }
    }).select('+emailVerificationToken');

    if (!user) {
      throw Object.assign(new Error('Invalid or expired verification link.'), { statusCode: 400 });
    }

    user.emailVerified = true;
    user.emailVerificationToken = undefined;
    user.emailVerificationExpiry = undefined;
    await user.save();

    return { message: 'Email verified successfully.' };
  }

  async resendVerification(email) {
    const normalizedEmail = email.trim().toLowerCase();
    const user = await CitraAIUser.findOne({ email: normalizedEmail });

    if (!user) {
      // Don't reveal whether account exists
      return { message: 'If an account with that email exists, a verification email has been sent.' };
    }

    if (user.emailVerified) {
      return { message: 'Email is already verified.' };
    }

    const verificationToken = crypto.randomBytes(VERIFICATION_TOKEN_BYTES).toString('hex');
    user.emailVerificationToken = verificationToken;
    user.emailVerificationExpiry = new Date(Date.now() + TOKEN_EXPIRY_HOURS * 60 * 60 * 1000);
    await user.save();

    await this._sendVerificationEmail(user.email, user.name, verificationToken);

    return { message: 'If an account with that email exists, a verification email has been sent.' };
  }

  // ─── Forgot / reset password ─────────────────────────────────────

  async forgotPassword(email) {
    const normalizedEmail = email.trim().toLowerCase();

    // No mail provider, no reset link. The quickstart configures none, so on
    // a fresh self-hosted install this is the normal case -- and answering
    // "a link has been sent" strands the operator with no way to tell why
    // nothing arrived.
    //
    // Checked BEFORE the user lookup, and deliberately: whether the
    // deployment can send mail is a property of the deployment, not of the
    // account, so the answer is identical for an address that exists and one
    // that does not. Nothing is enumerable here.
    //
    // The token is NOT returned. Handing a reset token to an unauthenticated
    // caller is account takeover by design -- anyone who knows an address
    // gets in without the mailbox. The client renders the manual steps
    // instead.
    if (!envConfig.emailProvider) {
      return {
        email_delivery: false,
        message:
          'This deployment has no email provider configured (EMAIL_PROVIDER is unset), ' +
          'so no reset link can be sent. Reset the password directly instead.',
      };
    }

    const user = await CitraAIUser.findOne({ email: normalizedEmail });

    // Always return same message to prevent email enumeration
    const safeMessage = 'If an account with that email exists, a password reset link has been sent.';

    if (!user) {
      return { message: safeMessage };
    }

    // If user is Google-only, still send email telling them to use Google sign-in
    if (user.authProvider === 'google' && !user.passwordHash) {
      // Inform them but don't expose account existence
      return { message: safeMessage };
    }

    const resetToken = crypto.randomBytes(VERIFICATION_TOKEN_BYTES).toString('hex');
    user.passwordResetToken = resetToken;
    user.passwordResetExpiry = new Date(Date.now() + RESET_TOKEN_EXPIRY_HOURS * 60 * 60 * 1000);
    await user.save();

    await this._sendPasswordResetEmail(user.email, user.name, resetToken);

    return { message: safeMessage };
  }

  async resetPassword(token, newPassword) {
    const user = await CitraAIUser.findOne({
      passwordResetToken: token,
      passwordResetExpiry: { $gt: new Date() }
    }).select('+passwordResetToken');

    if (!user) {
      throw Object.assign(new Error('Invalid or expired reset link.'), { statusCode: 400 });
    }

    user.passwordHash = await bcrypt.hash(newPassword, SALT_ROUNDS);
    user.passwordResetToken = undefined;
    user.passwordResetExpiry = undefined;

    // If Google user adds password, upgrade to 'both'
    if (user.authProvider === 'google') {
      user.authProvider = 'both';
    }

    await user.save();

    return { message: 'Password has been reset successfully.' };
  }

  // ─── Change password (authenticated) ─────────────────────────────

  async changePassword(email, currentPassword, newPassword) {
    const user = await CitraAIUser.findByEmailWithPassword(email);
    if (!user) {
      throw Object.assign(new Error('User not found.'), { statusCode: 404 });
    }

    if (!user.passwordHash) {
      throw Object.assign(
        new Error('This account uses Google sign-in. Use "Forgot Password" to set a password.'),
        { statusCode: 400, code: 'NO_PASSWORD_SET' }
      );
    }

    const isMatch = await bcrypt.compare(currentPassword, user.passwordHash);
    if (!isMatch) {
      throw Object.assign(new Error('Current password is incorrect.'), { statusCode: 401 });
    }

    user.passwordHash = await bcrypt.hash(newPassword, SALT_ROUNDS);
    await user.save();

    return { message: 'Password changed successfully.' };
  }

  // ─── Private helpers ─────────────────────────────────────────────

  _renderTransactionalEmail({ headline, intro, ctaLabel, ctaUrl, expiryNote, footnote }) {
    return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${headline} — Citra AI</title>
</head>
<body style="margin:0;padding:0;background-color:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;line-height:1.6;color:#334155;">
  <div style="max-width:560px;margin:0 auto;padding:24px 12px;">
    <div style="background:#FFFFFF;border-radius:18px;overflow:hidden;box-shadow:0 16px 40px -16px rgba(15,23,42,0.45);">
      <div style="background:linear-gradient(135deg,#0F172A 0%,#1E293B 50%,#0F172A 100%);padding:22px 28px;">
        <div style="font-size:18px;font-weight:800;color:#F8FAFC;letter-spacing:-0.3px;">
          <span style="display:inline-block;background:#3B82F6;color:#FFFFFF;padding:4px 10px;border-radius:6px;font-size:13px;font-weight:800;margin-right:8px;">C</span>
          Citra AI
        </div>
        <div style="margin-top:8px;color:#94A3B8;font-size:12px;letter-spacing:0.3px;">The agentic operating layer of the enterprise</div>
      </div>
      <div style="padding:28px;">
        <h2 style="margin:0 0 12px 0;font-size:22px;font-weight:800;color:#0F172A;letter-spacing:-0.4px;">${headline}</h2>
        <p style="margin:0 0 20px 0;color:#475569;font-size:14.5px;line-height:1.6;">${intro}</p>
        <a href="${ctaUrl}" style="display:inline-block;background:linear-gradient(135deg,#3B82F6 0%,#2563EB 100%);color:#FFFFFF;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 26px;border-radius:10px;box-shadow:0 8px 20px -8px rgba(59,130,246,0.55);">${ctaLabel}</a>
        <p style="margin:18px 0 0 0;color:#64748B;font-size:12.5px;">${expiryNote}</p>
        <p style="margin:8px 0 0 0;color:#64748B;font-size:12.5px;">${footnote}</p>
      </div>
      <div style="padding:18px 28px;background:#F1F5F9;text-align:center;border-top:1px solid #E2E8F0;">
        <p style="margin:0;font-size:11px;color:#94A3B8;">
          © ${new Date().getFullYear()} Citra AI · Trustedwear Tech Pvt Ltd · Incubated at IIT Patna
        </p>
      </div>
    </div>
  </div>
</body>
</html>`;
  }

  async _sendVerificationEmail(email, name, token) {
    const verifyUrl = `${envConfig.appUrl}/verify-email?token=${token}`;
    const userName = name || email.split('@')[0];

    await sendEmail({
      to: email,
      subject: 'Verify your email — Citra AI',
      text: `Hi ${userName},\n\nConfirm your email to activate your Citra AI workspace. Citra is the agentic operating layer of the enterprise — Smart Apps your team authors in plain language, Deep Analytics Chat, and a 24/7 Workflow Engine.\n\nVerify your email:\n${verifyUrl}\n\nThis link expires in ${TOKEN_EXPIRY_HOURS} hours.\n\nIf you did not create an account, please ignore this email.\n\n— Citra AI`,
      html: this._renderTransactionalEmail({
        headline: 'Verify your email',
        intro: `Hi ${userName}, confirm your email to activate your Citra AI workspace and start authoring Smart Apps, running Deep Analytics Chat, and composing Workflow Engine pipelines — all inside your perimeter.`,
        ctaLabel: 'Verify email →',
        ctaUrl: verifyUrl,
        expiryNote: `This link expires in ${TOKEN_EXPIRY_HOURS} hours.`,
        footnote: 'If you did not create an account, you can safely ignore this email.'
      })
    });
  }

  async _sendPasswordResetEmail(email, name, token) {
    const resetUrl = `${envConfig.appUrl}/reset-password?token=${token}`;
    const userName = name || email.split('@')[0];

    await sendEmail({
      to: email,
      subject: 'Reset your password — Citra AI',
      text: `Hi ${userName},\n\nA password reset was requested for your Citra AI account.\n\nReset your password:\n${resetUrl}\n\nThis link expires in ${RESET_TOKEN_EXPIRY_HOURS} hour(s). If you did not request this, you can safely ignore this email — your account stays secure.\n\n— Citra AI Security`,
      html: this._renderTransactionalEmail({
        headline: 'Reset your password',
        intro: `Hi ${userName}, we received a request to reset the password for your Citra AI account. Use the button below to set a new one.`,
        ctaLabel: 'Reset password →',
        ctaUrl: resetUrl,
        expiryNote: `This link expires in ${RESET_TOKEN_EXPIRY_HOURS} hour(s).`,
        footnote: 'If you did not request this, you can safely ignore this email — your account stays secure.'
      })
    });
  }
}

module.exports = new LocalAuthService();
