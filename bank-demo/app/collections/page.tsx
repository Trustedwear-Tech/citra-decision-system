// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

/**
 * Collections — an Acme Bank screen with NO Citra card on it.
 *
 * It earns its place in the demo by showing what "embedded" actually means: the
 * bank has many screens, and Citra sits on the one where a decision is made.
 * Nothing here loads citra.js at all.
 */
import { useState } from "react";
import { COLLECTIONS, inr } from "../../lib/data";

export default function Collections() {
  const [sel, setSel] = useState(COLLECTIONS[0]);

  return (
    <>
      <div className="ab-crumb">
        Collections › Delinquent accounts › <b>{sel.id}</b>
      </div>
      <div className="ab-split">
        <section>
          <h3 style={{ fontSize: 15, margin: "0 0 8px" }}>Follow-up queue</h3>
          <ul className="ab-list">
            {COLLECTIONS.map((c) => (
              <li key={c.id}>
                <button
                  className={`ab-row${c.id === sel.id ? " on" : ""}`}
                  onClick={() => setSel(c)}
                >
                  <div className="ab-row-top">
                    <span className="ab-row-id">{c.id}</span>
                    <span
                      className={`ab-pill${
                        c.bucket.startsWith("61") ? " bad" : c.bucket.startsWith("31") ? " warn" : ""
                      }`}
                    >
                      {c.bucket}
                    </span>
                  </div>
                  <div className="ab-row-meta">
                    {c.borrower} · {inr(c.outstanding)} outstanding · {c.emisMissed} EMI missed
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <div className="ab-card">
            <h2>
              Account <span className="ab-id">{sel.id}</span>
            </h2>
            <p className="ab-sub">{sel.borrower}</p>
            <div className="ab-fields">
              <div>
                <div className="ab-f-label">Outstanding</div>
                <div className="ab-f-value">{inr(sel.outstanding)}</div>
              </div>
              <div>
                <div className="ab-f-label">Bucket</div>
                <div className="ab-f-value">{sel.bucket}</div>
              </div>
              <div>
                <div className="ab-f-label">EMIs missed</div>
                <div className="ab-f-value">{sel.emisMissed}</div>
              </div>
              <div>
                <div className="ab-f-label">Last contact</div>
                <div className="ab-f-value">{sel.lastContact}</div>
              </div>
              <div>
                <div className="ab-f-label">Promise to pay</div>
                <div className="ab-f-value">{sel.promiseToPay ?? "—"}</div>
              </div>
            </div>
            <p className="ab-note">
              No Citra card on this screen — a decision app is added where a
              decision is made, not everywhere.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}
