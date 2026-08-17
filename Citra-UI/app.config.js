// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import 'dotenv/config';

export default {
  expo: {
    name: "Citra AI",
    slug: "Citra AI",
    version: process.env.EXPO_PUBLIC_APP_VERSION || "1.0.0",
    orientation: "portrait",
    icon: "./assets/citra-logo.png",
    userInterfaceStyle: "light",
    newArchEnabled: true,
    scheme: "com.citra.citraai",
    splash: {
      image: "./assets/citra-logo.png",
      resizeMode: "contain",
      backgroundColor: "#ffffff"
    },
    ios: {
      supportsTablet: true,
      bundleIdentifier: "com.citra.citraai",
      buildNumber: process.env.EXPO_PUBLIC_BUILD_NUMBER || "1"
    },
    android: {
      adaptiveIcon: {
        foregroundImage: "./assets/citra-logo.png",
        backgroundColor: "#ffffff"
      },
      package: "com.citra.citraai",
      versionCode: parseInt(process.env.EXPO_PUBLIC_BUILD_NUMBER) || 1
    },
    web: {
      favicon: "./assets/citra-logo.png",
      name: "Citra AI",
      shortName: "Citra AI",
      lang: "en",
      scope: "/",
      themeColor: "#3498db",
      backgroundColor: "#ffffff",
      display: "standalone",
      orientation: "any",
      startUrl: "/",
      bundler: "metro"
    },
    plugins: [
      "expo-font",
      "expo-web-browser"
    ],
    extra: {
      // Note: EXPO_PUBLIC_ variables are automatically available in the app
      // We only need to define build-time variables here

      // Build configuration
      buildNumber: process.env.EXPO_PUBLIC_BUILD_NUMBER || "1",
      buildTimestamp: new Date().toISOString(),

      // Legacy support (deprecated - use EXPO_PUBLIC_ variables instead)
      environment: process.env.EXPO_PUBLIC_ENVIRONMENT || 'production',
      "eas": {
        "projectId": "545f4514-6d98-49b8-93c0-15d129169158"
      },
      "googleAndroidClientId": process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID,
      "googleIosClientId": process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
      "googleWebClientId": process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
    }
  }
};
