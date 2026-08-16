// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import "./globals.css";
import type { Metadata } from "next";
import Nav from "../components/Nav";

export const metadata: Metadata = {
  title: "Acme Bank — Operations",
  description: "Acme Bank & Insurance Ltd — internal operations console",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="ab-top">
          <div className="ab-top-inner">
            <div className="ab-brand">
              Acme Bank <span>&amp; Insurance</span>
            </div>
            <Nav />
          </div>
        </header>
        <main className="ab-main">{children}</main>
      </body>
    </html>
  );
}
