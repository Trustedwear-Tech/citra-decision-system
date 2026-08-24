<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra-UI

Frontend application for the Citra platform. Built with React Native and Expo for web and mobile.

## Tech Stack

- React Native / Expo
- Tiptap (rich text editor)

## Local Development

```bash
npm install

# Web
npx expo start --clear --web

# Android
npx expo start --clear --android

# Expo Go (iOS/Android)
npx expo start --clear --go
```

## Configuration

Set environment variables before building:

```env
EXPO_PUBLIC_ENVIRONMENT=self-hosted           # or: production
EXPO_PUBLIC_CITRA_API_URL=http://localhost:8085/citra-ai
```

## Authentication

The UI automatically discovers which auth providers are enabled by calling the backend's `GET /api/auth/providers` endpoint. No frontend-specific auth configuration is needed — the sign-in screen adapts based on the backend response.

- If only **Google OAuth** is enabled, the Google sign-in button is shown.
- If only **email/password** is enabled, the email login/register form is shown.
- If **both** are enabled, users can choose either method.

### Deep Link Routes

When email/password auth is enabled, the backend sends emails containing links that open in the UI:

| Route | Purpose |
|-------|---------|
| `/verify-email?token=...` | Email verification after registration |
| `/reset-password?token=...` | Password reset flow |

These routes are handled by the app's URL router. Make sure `APP_URL` in the **Citra-User-Service** `.env` points to the URL where this UI is hosted (e.g., `http://localhost:19006` for local development).

## Key Files

| Path | Description |
|------|-------------|
| `IntroScreen.js` | Landing page with product showcase |
| `components/auth/EmailAuthScreen.js` | Email/password login and registration forms |
| `components/auth/ResetPasswordScreen.js` | Password reset deep link handler |
| `App.js` | Application root and navigation |
| `app.config.js` | Expo configuration |
| `AsyncStorageManager.js` | Client-side storage |
