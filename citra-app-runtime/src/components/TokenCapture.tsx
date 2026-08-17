// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

"use client";

import { useEffect } from "react";
import { getUserToken } from "@/lib/userToken";

/**
 * Eagerly capture the `?_t=` handoff token on page MOUNT.
 *
 * getUserToken() was previously called only lazily — on the first client-side
 * data fetch (runtimeFetch). A page whose first user action is a pure
 * NAVIGATION (e.g. a form whose on_submit only navigates) therefore navigated
 * before the token was ever mirrored into the SSR cookie: the next route's
 * server render called smart-app-service with no token, got 401, and crashed
 * to the error boundary. Mounting this on every app page guarantees the
 * capture (sessionStorage + cookie mirror + URL strip) happens before any
 * interaction can leave the page.
 */
export default function TokenCapture() {
  useEffect(() => {
    getUserToken();
  }, []);
  return null;
}
