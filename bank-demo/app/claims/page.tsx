// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

"use client";

/**
 * Motor Claims — Acme Bank's insurance arm.
 *
 * The card slot here is deliberately EMPTY until a claims decision app exists
 * and its embed key is configured. It shows the second half of the story: the
 * same one-script-tag integration drops into a different business line, on a
 * different record type, without the bank changing anything structural.
 *
 * Set NEXT_PUBLIC_CITRA_CLAIMS_EMBED_KEY to light it up.
 */
import { useCallback, useState } from "react";
import CitraCard from "../../components/CitraCard";
import { CLAIMS, inr } from "../../lib/data";

export default function Claims() {
  const [sel, setSel] = useState(CLAIMS[0]);
  const [events, setEvents] = useState<string[]>([]);
  const onEvent = useCallback((name: string, payload: unknown) => {
    setEvents((p) => [`${name} ${JSON.stringify(payload)}`, ...p]);
  }, []);

  const claimsKey = process.env.NEXT_PUBLIC_CITRA_CLAIMS_EMBED_KEY || "";

  return (
    <>
      <div className="ab-crumb">
        Motor Claims › Open claims › <b>{sel.id}</b>
      </div>
      <div className="ab-split">
        <section>
          <div className="ab-card" style={{ marginBottom: 16 }}>
            <h2>
              Claim <span className="ab-id">{sel.id}</span>
            </h2>
            <p className="ab-sub">
              Policy {sel.policy} · {sel.status}
            </p>
            <div className="ab-fields">
              <div>
                <div className="ab-f-label">Insured</div>
                <div className="ab-f-value">{sel.insured}</div>
              </div>
              <div>
                <div className="ab-f-label">Vehicle</div>
                <div className="ab-f-value">{sel.vehicle}</div>
              </div>
              <div>
                <div className="ab-f-label">Incident</div>
                <div className="ab-f-value">{sel.incident}</div>
              </div>
              <div>
                <div className="ab-f-label">Repair estimate</div>
                <div className="ab-f-value">{inr(sel.estimate)}</div>
              </div>
              <div>
                <div className="ab-f-label">Garage</div>
                <div className="ab-f-value">{sel.garage}</div>
              </div>
            </div>
          </div>

          <h3 style={{ fontSize: 15, margin: "0 0 8px" }}>Open claims</h3>
          <ul className="ab-list">
            {CLAIMS.map((c) => (
              <li key={c.id}>
                <button
                  className={`ab-row${c.id === sel.id ? " on" : ""}`}
                  onClick={() => setSel(c)}
                >
                  <div className="ab-row-top">
                    <span className="ab-row-id">{c.id}</span>
                    <span className="ab-pill">{inr(c.estimate)}</span>
                  </div>
                  <div className="ab-row-meta">
                    {c.insured} · {c.vehicle} · {c.incident}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <div className="ab-card">
            <div className="ab-citra-head">
              <h2>Claim decision</h2>
              <span className="ab-tag">Citra</span>
            </div>
            {claimsKey ? (
              <CitraCard
                key={sel.id}
                runtimeUrl={process.env.NEXT_PUBLIC_CITRA_RUNTIME_URL || ""}
                embedKey={claimsKey}
                recordId={sel.id}
                onEvent={onEvent}
              />
            ) : (
              <p className="ab-note" style={{ margin: 0 }}>
                No claims decision app configured yet. Build one in Citra, copy its
                embed key from <b>My Apps → Export</b>, and set{" "}
                <code>NEXT_PUBLIC_CITRA_CLAIMS_EMBED_KEY</code>. The same one-line
                integration as Loan Origination — different record type, no other
                change to this screen.
              </p>
            )}
            {events.length > 0 && <div className="ab-log">{events.join("\n")}</div>}
          </div>
        </section>
      </div>
    </>
  );
}
