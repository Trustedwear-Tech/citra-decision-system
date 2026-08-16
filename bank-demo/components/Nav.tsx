// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const TABS = [
  { href: "/", label: "Loan Origination" },
  { href: "/collections", label: "Collections" },
  { href: "/claims", label: "Motor Claims" },
];

export default function Nav() {
  const path = usePathname();
  const [who, setWho] = useState<string | null>(null);

  // Who is signed in comes from the bank's own session endpoint. The token
  // itself is httpOnly and never reaches this component.
  useEffect(() => {
    fetch("/api/token")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setWho(d ? "signed in" : null))
      .catch(() => setWho(null));
  }, [path]);

  return (
    <>
      <nav style={{ display: "flex", gap: 22 }}>
        {TABS.map((t) => (
          <Link
            key={t.href}
            href={t.href}
            style={{
              color: path === t.href ? "#fff" : "#b7c6d4",
              textDecoration: "none",
              fontFamily: "system-ui, sans-serif",
              fontSize: 14,
              fontWeight: path === t.href ? 600 : 400,
            }}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      <div className="ab-who">{who ? "Officer · signed in" : ""}</div>
    </>
  );
}
