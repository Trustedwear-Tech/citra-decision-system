// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * What you get when you open an EMBED app's URL directly in Citra.
 *
 * An embed page has no Citra screen. Its surface is a card inside the
 * CUSTOMER's own application, mounted by citra.js, and the host passes the
 * record id in. Rendering its panels here produces a queue with no rows and a
 * detail with no record — which reads as a broken app rather than a page that
 * was never meant to be opened this way.
 *
 * So: say what this app is, and how to actually use it. A preview IS available
 * — the panels render normally once a record is supplied via `?id=` — because a
 * BA does need to see their own composition.
 *
 * Deliberately NOT `.panel-error` and NOT inside `.panel-host-grid`: this is
 * informational, not a failure, and a grid cell squeezes it into a narrow
 * column. Styled from the design tokens so it follows the app's theme.
 */
import Icon from "@/components/Icon";

export default function EmbedPageNotice({
  title,
  slug,
}: {
  title: string;
  slug: string;
}) {
  return (
    <div
      role="note"
      style={{
        background: "var(--citra-surface)",
        border: "1px solid var(--citra-border)",
        borderRadius: "var(--r-lg)",
        boxShadow: "var(--elev-1)",
        padding: "22px 24px",
        maxWidth: 720,
        margin: "8px 0 24px",
        color: "var(--citra-fg)",
        lineHeight: 1.55,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontWeight: 700,
          fontSize: 16,
          marginBottom: 10,
        }}
      >
        {/* From the closed 112-icon set (publish rule I-01) — an icon name
            outside it fails tsc here, which is how the first attempt was
            caught. */}
        <Icon name="Package" size={18} />
        {title} runs inside your application
      </div>

      <p style={{ margin: "0 0 12px", color: "var(--citra-muted)" }}>
        This is an <strong style={{ color: "var(--citra-fg)" }}>embedded
        card</strong> — it has no screen in Citra. Officers use it without
        leaving your own system.
      </p>

      <p style={{ margin: "0 0 12px", color: "var(--citra-muted)" }}>
        To put it in place, open{" "}
        <strong style={{ color: "var(--citra-fg)" }}>My Apps</strong> and choose{" "}
        <strong style={{ color: "var(--citra-fg)" }}>Copy embed script</strong>.
        Paste those few lines into the page you want the card on — no build
        step, no framework.
      </p>

      <p style={{ margin: 0, color: "var(--citra-muted)" }}>
        To preview it here with a real record, add an id to this URL:{" "}
        <code
          style={{
            background: "var(--citra-surface-2)",
            border: "1px solid var(--citra-border)",
            borderRadius: "var(--r-sm)",
            padding: "2px 6px",
            fontSize: 13,
          }}
        >
          /{slug}?id=YOUR-RECORD-ID
        </code>
        . The host application supplies that id automatically once embedded.
      </p>
    </div>
  );
}
