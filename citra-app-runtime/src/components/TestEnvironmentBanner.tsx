// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * TestEnvironmentBanner — a prominent, unmissable bar shown at the top of any
 * app served from the TEST store (AppDetail.environment === "test").
 *
 * Why unconditional (not role-gated): test data looks real, and an officer must
 * never mistake a rehearsal against test systems for live work. Showing the
 * warning to ANYONE viewing a test app is strictly safer than gating it — and
 * end users don't reach test apps through normal listing anyway. It is a
 * warning, not an access control.
 *
 * Server component, self-contained inline styles (no stylesheet dependency).
 */
export default function TestEnvironmentBanner() {
  return (
    <div
      role="status"
      aria-label="Test environment"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 16px",
        background: "#7c2d12", // deep amber/brown — high contrast, unmistakable
        color: "#fff7ed",
        fontSize: 13,
        fontWeight: 600,
        letterSpacing: 0.2,
        position: "sticky",
        top: 0,
        zIndex: 1000,
        borderBottom: "2px solid #ea580c",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          background: "#ea580c",
          color: "#fff",
          borderRadius: 4,
          padding: "1px 7px",
          fontSize: 11,
          fontWeight: 800,
          letterSpacing: 1,
        }}
      >
        TEST
      </span>
      <span style={{ fontWeight: 600 }}>
        Test environment — data comes from test systems. Actions here do not
        affect production.
      </span>
    </div>
  );
}
