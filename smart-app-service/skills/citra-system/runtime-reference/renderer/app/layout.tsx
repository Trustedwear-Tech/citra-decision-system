// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

import "./globals.css";
import type { ReactNode } from "react";
import { Inter } from "next/font/google";

// Self-hosted at build time by next/font (no runtime call to Google) — keeps the
// sovereign / air-gapped guarantee while giving the runtime a premium variable
// font instead of the system stack. Exposed as --font-inter for globals.css.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata = {
  title: "Citra Power AI App",
  description: "Citra runtime"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
