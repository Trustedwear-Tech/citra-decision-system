// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * The visible, loud failure for a panel that cannot render inside an embed.
 *
 * Shared by the chart and map stubs. Charts and maps are excluded from the
 * embed bundle by build-time alias (scripts/build-embed.mjs) because echarts
 * alone is several times the weight of everything else in the bundle, and no
 * embed surface uses them.
 *
 * Reaching this component means an out-of-allowlist panel got past the publish
 * check — a bug, not a user error. Per the platform's fail-loud rule for
 * unknown panels, it must be visible and named rather than an empty div: a
 * blank space in a bank's production screen reads as "the integration is
 * broken" and costs a support cycle to trace back to a panel type.
 */
import * as React from "react";

export function UnsupportedInEmbed({ what }: { what: string }) {
  React.useEffect(() => {
    console.error(
      `[citra-embed] a "${what}" panel reached the embed renderer, but ${what} ` +
        `panels are not supported in embedded cards and are excluded from this ` +
        `bundle. Remove it from the embed page in the builder.`,
    );
  }, [what]);

  return (
    <div className="panel-error" role="alert">
      <strong>This panel can&apos;t be shown here.</strong>
      <span>
        {what.charAt(0).toUpperCase() + what.slice(1)} panels aren&apos;t
        available in an embedded card. Open the full app to see it.
      </span>
    </div>
  );
}

export default UnsupportedInEmbed;
