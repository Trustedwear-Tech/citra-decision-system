<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra-User-Service

User authentication and management service for the Citra platform.

## Tech Stack

- Node.js 22 / Express
- MongoDB
- Redis

## Port

- **7004** (production)

## Features

- **Dual authentication** — Google OAuth and/or email/password (config-driven)
- JWT token generation and validation
- Email verification and password reset flows
- User profile management
- Team management and invitations
- Subscription and billing

## Configuration

Supports two methods:

1. **`.env` file** — Copy `.env.example` to `.env` and fill in values.
2. **HashiCorp Vault** — Delete `.env`, set `VAULT_ADDR` + auth credentials. The `vault_env_loader.js` module loads secrets from Vault at startup.

See `.env.example` for all available environment variables.

## Authentication Setup

Citra supports two authentication providers that can be enabled independently or together.

### 1. Choose Auth Providers

Set `AUTH_PROVIDERS` in your `.env` file to a comma-separated list of providers to enable:

```env
# Google OAuth only (default)
AUTH_PROVIDERS=google

# Email/password only
AUTH_PROVIDERS=local

# Both providers enabled
AUTH_PROVIDERS=google,local
```

The UI automatically discovers which providers are enabled via `GET /api/auth/providers` and shows the appropriate login options.

### 2. Google OAuth Setup

Required when `AUTH_PROVIDERS` includes `google`:

```env
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
```

1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Create an OAuth 2.0 Client ID (Web application).
3. Add your domain to **Authorized JavaScript origins** and **Authorized redirect URIs**.
4. Copy the Client ID and Client Secret into your `.env`.

### 3. Email/Password (Local Auth) Setup

Required when `AUTH_PROVIDERS` includes `local`:

```env
AUTH_PROVIDERS=local            # or: google,local
EMAIL_PROVIDER=smtp             # or: ses
APP_URL=http://localhost:19006  # Base URL for email verification and password reset links
JWT_SECRET=your-jwt-secret      # Secret key for signing JWT tokens
```

#### Email Provider — SMTP

```env
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-smtp-username
SMTP_PASS=your-smtp-password
SMTP_FROM=noreply@yourdomain.com
```

#### Email Provider — Amazon SES

```env
EMAIL_PROVIDER=ses
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-aws-access-key
AWS_SECRET_ACCESS_KEY=your-aws-secret-key
SES_FROM_EMAIL=noreply@yourdomain.com
```

> **Note:** `EMAIL_PROVIDER` must be set explicitly to `smtp` or `ses`. There is no automatic fallback — the service will fail to start if it is missing when local auth is enabled.

### 4. Seed an Admin User (Optional)

When using email/password auth, you can create an initial admin user:

```bash
node src/scripts/create-admin.js admin@example.com YourPassword "Admin Name"
```

The admin user is created with email already verified, so they can log in immediately.

### Auth Flow Summary

| Flow | Steps |
|------|-------|
| **Register** | User submits email + password → account created → verification email sent → user clicks link → email verified |
| **Login** | User submits email + password → JWT returned (email must be verified) |
| **Forgot Password** | User submits email → reset link sent → user clicks link → enters new password |
| **Google OAuth** | User clicks "Sign in with Google" → Google redirect → JWT returned |

### Local Auth API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/local/register` | Register with email and password |
| POST | `/api/auth/local/login` | Login with email and password |
| GET | `/api/auth/local/verify-email` | Verify email via token in query string |
| POST | `/api/auth/local/forgot-password` | Request a password reset email |
| POST | `/api/auth/local/reset-password` | Reset password with token |
| POST | `/api/auth/local/change-password` | Change password (authenticated) |
| POST | `/api/auth/local/resend-verification` | Resend the verification email |
| GET | `/api/auth/providers` | Returns list of enabled auth providers |

## Local Development

```bash
npm install
npm start
```

## Docker

```bash
docker build -t citra-user-service .
docker run -p 7004:7004 --env-file .env citra-user-service
```

## Health Check

```
GET /health
```
