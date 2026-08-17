// MobileIntroScreen.js
//
// The native-platform half of the OSS landing page. See IntroScreen.js in this
// directory for why both files exist here rather than being inherited from the
// private repo.
//
// LandingScreen is written against react-native primitives and branches on
// Platform.OS internally, so web and native render from one component and this
// file adds nothing but the import path MainApp.js expects.
//
// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1

import React from 'react';
import LandingScreen from './components/LandingScreen';

const MobileIntroScreen = ({ onAction, isAuthenticated }) => (
  <LandingScreen onAction={onAction} isAuthenticated={isAuthenticated} />
);

export default MobileIntroScreen;
