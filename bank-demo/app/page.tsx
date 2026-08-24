// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

/**
 * Loan Origination — Acme Bank's own screen, plus the Citra decision card.
 *
 * This is the integration in miniature: the bank already has the worklist, the
 * record and the officer's session. Citra contributes ONE div and one script.
 * The record the officer selects here is the record the card decides on.
 */
import { useCallback, useEffect, useState } from "react";
import CitraCard from "../components/CitraCard";
import SignIn from "../components/SignIn";
import { APPLICATIONS, inr } from "../lib/data";

export default function LoanOrigination() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [selected, setSelected] = useState(APPLICATIONS[0]);
  const [events, setEvents] = useState<string[]>([]);

  useEffect(() => {
    fetch("/api/token")
      .then((r) => setSignedIn(r.ok))
      .catch(() => setSignedIn(false));
  }, []);

  const onEvent = useCallback((name: string, payload: unknown) => {
    setEvents((prev) => [
      `${new Date().toISOString().slice(11, 19)}  ${name}\n${JSON.stringify(payload, null, 1)}`,
      ...prev,
    ]);
  }, []);

  if (signedIn === null) return <p className="ab-sub">Loading…</p>;
  if (!signedIn) return <SignIn onDone={() => setSignedIn(true)} />;

  const a = selected;
  return (
    <>
      <div className="ab-crumb">
        Loan Origination › Applications › <b>{a.id}</b>
      </div>

      <div className="ab-split">
        {/* ── Acme Bank's own application screen ── */}
        <section>
          <div className="ab-card" style={{ marginBottom: 16 }}>
            <h2>
              Application <span className="ab-id">{a.id}</span>
            </h2>
            <p className="ab-sub">
              {a.product} loan · received {a.received} · {a.branch}
            </p>
            <div className="ab-fields">
              <div>
                <div className="ab-f-label">Applicant</div>
                <div className="ab-f-value">{a.applicant}</div>
              </div>
              <div>
                <div className="ab-f-label">Amount requested</div>
                <div className="ab-f-value">{inr(a.amount)}</div>
              </div>
              <div>
                <div className="ab-f-label">FOIR</div>
                <div className="ab-f-value">
                  {a.foir}%{" "}
                  {a.foir > 50 && <span className="ab-pill bad">above cap</span>}
                </div>
              </div>
              <div>
                <div className="ab-f-label">Sourcing channel</div>
                <div className="ab-f-value">{a.channel}</div>
              </div>
              <div>
                <div className="ab-f-label">Income proof</div>
                <div className="ab-f-value">{a.proof}</div>
              </div>
              <div>
                <div className="ab-f-label">Status</div>
                <div className="ab-f-value">{a.status}</div>
              </div>
            </div>
            <p className="ab-note">
              This panel is Acme Bank&apos;s own screen. The card alongside is
              Citra&apos;s, added with one script tag.
            </p>
          </div>

          <h3 style={{ fontSize: 15, margin: "0 0 8px" }}>Queue</h3>
          <ul className="ab-list">
            {APPLICATIONS.map((x) => (
              <li key={x.id}>
                <button
                  className={`ab-row${x.id === a.id ? " on" : ""}`}
                  onClick={() => setSelected(x)}
                >
                  <div className="ab-row-top">
                    <span className="ab-row-id">{x.id}</span>
                    <span className={`ab-pill${x.foir > 50 ? " warn" : ""}`}>
                      FOIR {x.foir}%
                    </span>
                  </div>
                  <div className="ab-row-meta">
                    {x.applicant} · {x.product} · {inr(x.amount)} · {x.channel}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* ── the Citra decision card ── */}
        <section>
          <div className="ab-card">
            <div className="ab-citra-head">
              <h2>Credit decision</h2>
              <span className="ab-tag">Citra</span>
            </div>
            {/* Remounts when the officer picks another application, so the card
                always decides on the record actually on screen. */}
            <CitraCard
              key={a.id}
              runtimeUrl={process.env.NEXT_PUBLIC_CITRA_RUNTIME_URL || ""}
              embedKey={process.env.NEXT_PUBLIC_CITRA_EMBED_KEY || ""}
              recordId={a.id}
              onEvent={onEvent}
            />
          </div>

          <div className="ab-card" style={{ marginTop: 16 }}>
            <h2 style={{ fontSize: 16 }}>Acme Bank&apos;s event log</h2>
            <p className="ab-sub">
              Populated by the card&apos;s <code>onRecommendation</code> /{" "}
              <code>onDecision</code> callbacks — this is how the bank&apos;s own
              screen reacts to what the officer decided.
            </p>
            <div className="ab-log">{events.join("\n\n") || "(nothing yet)"}</div>
          </div>
        </section>
      </div>
    </>
  );
}
