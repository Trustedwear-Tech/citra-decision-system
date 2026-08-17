// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

"use client";

import { useEffect } from "react";

/** App-wide error boundary — a thrown render error (bad spec, transient backend)
 *  now degrades to a branded, recoverable screen instead of a blank page / the
 *  React overlay. `reset()` re-renders the segment (a real retry). */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface, never swallow.
    console.error("[citra-app] unhandled render error", error);
  }, [error]);

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24 }}>
      <div style={{ maxWidth: 460, textAlign: "center" }}>
        <div style={{ fontSize: 40, marginBottom: 8 }} aria-hidden>⚠</div>
        <h1 style={{ fontSize: 20, margin: "0 0 8px" }}>Something went wrong</h1>
        <p style={{ color: "var(--citra-muted, #64748b)", margin: "0 0 20px", lineHeight: 1.5 }}>
          This app hit an unexpected error. Retry, or reopen it from your app list.
        </p>
        <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
          <button
            type="button"
            onClick={() => reset()}
            style={{
              background: "var(--citra-primary, #2563eb)", color: "#fff", border: "none",
              borderRadius: 8, padding: "8px 18px", fontWeight: 600, cursor: "pointer",
            }}
          >
            Retry
          </button>
          <a
            href="/"
            style={{
              border: "1px solid var(--citra-border, #cbd5e1)", borderRadius: 8,
              padding: "8px 18px", fontWeight: 600, color: "inherit", textDecoration: "none",
              display: "inline-flex", alignItems: "center",
            }}
          >
            Back to your apps
          </a>
        </div>
      </div>
    </main>
  );
}
