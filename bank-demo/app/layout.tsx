// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
