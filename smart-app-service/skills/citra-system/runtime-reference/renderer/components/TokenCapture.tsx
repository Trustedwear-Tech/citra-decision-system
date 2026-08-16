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
