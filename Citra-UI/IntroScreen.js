// IntroScreen.js
//
// The OSS build's landing page.
//
// MainApp.js is shared verbatim between the private Citra-AI repo and this
// one, and it imports './IntroScreen'. In the private repo that file is the
// citra-ai.com marketing site: 5,766 LOC of commercial content — third-party
// endorsement names, usage counters that would read as false on a fresh
// install, externally-hosted assets — none of which belongs in a
// source-available release, so it is deny-listed from the sync (see
// scripts/oss-release/sync_public.py and docs/open-source-release-plan.md
// §7.4).
//
// Deny-listing it left this repo importing a file it does not have, which
// broke `npm run web:build` outright — the UI image could not be built from a
// clean checkout at all. The fix is for the public tree to carry its OWN
// IntroScreen rather than to lack one: MainApp.js then stays byte-identical
// across both repos and keeps syncing normally, and the landing page remains
// the single intended difference between them.
//
// The prop contract is IntroScreen's, not LandingScreen's: `userEmail` is
// accepted and ignored so the call site needs no special case.
//
// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1

import React from 'react';
import LandingScreen from './components/LandingScreen';

const IntroScreen = ({ onAction, isAuthenticated }) => (
  <LandingScreen onAction={onAction} isAuthenticated={isAuthenticated} />
);

export default IntroScreen;
