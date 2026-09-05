// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

/**
 * The factor grid — the customer's declared rubric, rendered with its evidence.
 *
 * See docs/factor-scorecard-plan.md. Three things about this component are
 * decisions rather than styling, and changing them changes what the officer
 * concludes:
 *
 *  1. READING ORDER IS FIXED. Gate, then composite, then rows. A breached
 *     policy limit makes the composite irrelevant, so "68/100 — declined" under
 *     a failed gate invites an argument with the number instead of a reading of
 *     the gate. When `gated` is set the server suppresses the totals and this
 *     renders the gate alone above the rows.
 *
 *  2. THE CELL IS THE PRODUCT, NOT THE TOTAL. Any incumbent can print 18/25.
 *     None can open that row onto the invoices, the policy paragraph and the
 *     learned clause underneath — so every row expands, and a row with nothing
 *     to show says so rather than looking clickable.
 *
 *  3. NO DOMAIN VOCABULARY. Every user-facing word comes from
 *     `terminology`, which the app declares. This file does not know whether it
 *     is showing a dealer scorecard or an airworthiness assessment.
 *
 * It renders `checklist` mode — judged criteria with no total — by simply
 * having no composite to draw. That is the whole difference, on purpose: a
 * checklist is not a composite with the number hidden.
 */

import { useState } from "react";
import { runtimeFetch } from "@/lib/runtimeFetch";
import type { FactorScoreRow, FactorScorecard, GateResult } from "../types/spec";

function gateTone(status: GateResult["status"]): string {
  if (status === "pass") return "green";
  if (status === "fail") return "red";
  return "amber";                       // 'flag' — unevaluated, needs a human
}

function gateLabel(status: GateResult["status"]): string {
  if (status === "pass") return "PASS";
  if (status === "fail") return "FAIL";
  return "NOT EVALUATED";
}

/** Fill proportion for the score meter. Never a confidence value. */
function fillPct(row: FactorScoreRow): number | null {
  if (row.score == null || !row.weight) return null;
  return Math.max(0, Math.min(100, (row.score / row.weight) * 100));
}

function Citations({ items }: { items?: Record<string, unknown>[] }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="sc-cites">
      {items.map((c, i) => {
        const url = c.source_url as string | undefined;
        const label =
          (c.label as string) ||
          (c.doc as string) ||
          (c.source as string) ||
          url ||
          JSON.stringify(c);
        return (
          <li key={i}>
            {url ? (
              <a href={url} target="_blank" rel="noreferrer">{label}</a>
            ) : (
              label
            )}
          </li>
        );
      })}
    </ul>
  );
}

function ClausesFired({ items }: { items?: Record<string, unknown>[] }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="sc-clauses">
      {items.map((c, i) => {
        const relation = String(c.relation ?? "applied");
        return (
          <li key={i}>
            <span className={`sc-clause-rel sc-clause-${relation}`}>
              {relation === "overruled" ? "overruled" : "applied"}
            </span>{" "}
            <span className="sc-clause-id">{String(c.clause_id ?? "")}</span>
            {c.note ? <> — {String(c.note)}</> : null}
          </li>
        );
      })}
    </ul>
  );
}

function OverrideForm({
  row,
  onSubmit,
  onCancel,
}: {
  row: FactorScoreRow;
  onSubmit: (score: number, reason: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [score, setScore] = useState(String(row.score ?? ""));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const n = Number(score);
  const max = row.weight ?? undefined;
  // Client-side mirror of the server's ceiling. The server is the authority —
  // this only spares the officer a round-trip to be told the obvious.
  const scoreBad = score.trim() === "" || Number.isNaN(n) || n < 0 ||
                   (max !== undefined && n > max);
  // A reason is not optional. An unexplained number is the artefact the whole
  // scorecard exists to replace, so the button stays disabled without one.
  const ready = !scoreBad && reason.trim().length > 0 && !busy;

  return (
    <div className="sc-override">
      <div className="sc-override-line">
        <label>
          Score
          <input
            type="number" value={score} min={0} max={max} step="any"
            onChange={(e) => setScore(e.target.value)}
            className={scoreBad ? "sc-input-bad" : undefined}
            aria-label="Corrected score"
          />
          {max !== undefined && <span className="sc-of">/{max}</span>}
        </label>
      </div>
      <label className="sc-override-reason">
        Why are you changing it? <span className="sc-req">required</span>
        <textarea
          rows={2} value={reason} maxLength={500}
          placeholder="What did the model get wrong, and how do you know?"
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {err && <p className="sc-override-err">{err}</p>}
      <div className="sc-override-actions">
        <button
          type="button" className="sc-btn-primary" disabled={!ready}
          onClick={async () => {
            setBusy(true); setErr(null);
            try {
              await onSubmit(n, reason.trim());
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Saving…" : "Save correction"}
        </button>
        <button type="button" className="sc-btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function Row({
  row,
  rowNoun,
  bandNoun,
  startExpanded,
  onOverride,
}: {
  row: FactorScoreRow;
  rowNoun: string;
  bandNoun: string;
  startExpanded: boolean;
  onOverride?: (factorId: string, score: number, reason: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(startExpanded);
  const [editing, setEditing] = useState(false);
  const detail =
    Boolean(row.rationale) ||
    (row.citations?.length ?? 0) > 0 ||
    (row.clauses_fired?.length ?? 0) > 0 ||
    Boolean(row.overridden_by) ||
    Boolean(row.sop_drift) ||
    Boolean(onOverride);
  const pct = fillPct(row);

  return (
    <div className={`sc-row${row.unscored ? " sc-row-unscored" : ""}`}>
      <button
        type="button"
        className="sc-row-head"
        onClick={() => detail && setOpen((v) => !v)}
        aria-expanded={detail ? open : undefined}
        // A row with nothing underneath must not look clickable — a dead
        // expander reads as "the evidence failed to load".
        disabled={!detail}
      >
        <span className="sc-row-label">
          {row.label}
          {row.scope === "case" && <span className="sc-scope">this case</span>}
        </span>

        <span className="sc-row-score">
          {row.unscored ? (
            <span className="sc-unscored">not scored</span>
          ) : row.score != null && row.weight != null ? (
            <>
              <b>{row.score}</b>
              <span className="sc-of">/{row.weight}</span>
            </>
          ) : null}
        </span>

        {pct != null && (
          <span className="sc-meter" aria-hidden>
            <span className="sc-meter-fill" style={{ width: `${pct}%` }} />
          </span>
        )}

        {row.band && (
          <span className="sc-band" title={bandNoun}>
            {row.band}
          </span>
        )}

        {/* An override must be visible without opening the row. A corrected
            score that looks identical to a model score is the failure mode
            this whole feature is built to avoid. */}
        {row.overridden_by && (
          <span className="sc-edited" title={`Corrected by ${row.overridden_by}`}>
            corrected
          </span>
        )}
        {row.sop_drift && (
          <span className="sc-drift" title="The policy passage behind this factor changed after the rubric was confirmed">
            policy changed
          </span>
        )}

        {detail && <span className="sc-caret">{open ? "▾" : "▸"}</span>}
      </button>

      {open && detail && (
        <div className="sc-row-body">
          {row.rationale && <p className="sc-rationale">{row.rationale}</p>}
          <Citations items={row.citations} />
          <ClausesFired items={row.clauses_fired} />
          {/* Shown apart from the score, and labelled, so the two quantities
              are never read as one. Confidence is the model's certainty; the
              score is policy. */}
          {row.confidence != null && (
            <p className="sc-confidence">
              model confidence {Math.round(row.confidence * 100)}%
            </p>
          )}

          {row.sop_drift && (
            <p className="sc-drift-note">
              This {rowNoun} was extracted from a policy passage that has since
              changed. The score below still applies the weights that were
              confirmed against the older version — worth checking before you
              rely on it.
            </p>
          )}

          {/* Provenance for a correction: what the model said, who changed it,
              and why. Kept even after a second edit, because original_score is
              only ever written once. */}
          {row.overridden_by && (
            <p className="sc-override-note">
              <b>Corrected</b> from{" "}
              {row.original_score ?? "no score"} by {row.overridden_by}
              {row.overridden_at
                ? ` on ${new Date(row.overridden_at).toLocaleDateString()}`
                : null}
              {row.override_reason ? ` — "${row.override_reason}"` : null}
            </p>
          )}

          {onOverride && !editing && (
            <button type="button" className="sc-btn sc-btn-edit"
                    onClick={() => setEditing(true)}>
              {row.overridden_by ? "Correct again" : "Correct this score"}
            </button>
          )}
          {onOverride && editing && (
            <OverrideForm
              row={row}
              onCancel={() => setEditing(false)}
              onSubmit={async (score, reason) => {
                await onOverride(row.factor_id, score, reason);
                setEditing(false);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

export function ScorecardView({
  card,
  expanded = false,
  hideUnscored = false,
  slug,
  correlationId,
  onCardChange,
}: {
  card: FactorScorecard;
  expanded?: boolean;
  hideUnscored?: boolean;
  /** Supplying slug + correlationId turns the rows editable. Omit them and the
   *  grid is read-only, which is what an already-decided case should show. */
  slug?: string;
  correlationId?: string;
  onCardChange?: (next: FactorScorecard) => void;
}) {
  const t = card.terminology;
  const gates = card.gates ?? [];
  const blocking = gates.filter((g) => g.status !== "pass");
  const rows = hideUnscored ? card.rows.filter((r) => !r.unscored) : card.rows;
  const drifted = card.sop_drift_factor_ids ?? [];

  // Overriding is only offered when the caller supplied the identity the write
  // needs AND a way to receive the recomputed card. Two exclusions, both
  // because the server would refuse — and an action that always fails is worse
  // than no action:
  //   * a GATED case: a hard policy gate decided it, so the composite is
  //     suppressed and editing a factor beneath it changes nothing;
  //   * CHECKLIST mode: this form collects a score, and a checklist has none.
  //     Band correction exists on the API; the picker needs the DECLARED band
  //     list, which the card does not carry yet.
  const canOverride = Boolean(
    slug && correlationId && onCardChange && !card.gated && card.mode === "composite",
  );

  const submitOverride = canOverride
    ? async (factorId: string, score: number, reason: string) => {
        // The SERVER recomputes. The response is the new card, so the officer's
        // grade and the ledger's grade come from one calculation rather than
        // two that could disagree.
        const res = await runtimeFetch(
          `/api/apps/${slug}/factors/${encodeURIComponent(factorId)}/override`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ correlation_id: correlationId, score, reason }),
          },
        );
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const d = body?.detail;
          throw new Error(
            (typeof d === "string" ? d : d?.message) ||
              `Could not save the correction (${res.status}).`,
          );
        }
        onCardChange!(body.scorecard as FactorScorecard);
      }
    : undefined;

  return (
    <div className="sc">
      <div className="sc-head">{t.panel}</div>

      {/* Above everything the officer might rely on: part of this rubric was
          confirmed against a policy document that has since moved. */}
      {drifted.length > 0 && (
        <div className="sc-drift-banner">
          <b>Policy changed since this rubric was confirmed</b> — {drifted.length}{" "}
          {t.row}
          {drifted.length === 1 ? "" : "s"} ({drifted.join(", ")}) were extracted
          from passages that have since been edited. Scoring continues, but this
          app needs re-extraction.
        </div>
      )}

      {/* 1 — gates. Above everything, because a failed gate makes the rest
             supporting detail rather than the answer. */}
      {gates.length > 0 && (
        <div className="sc-gates">
          {gates.map((g) => (
            <div key={g.gate_id} className={`sc-gate sc-gate-${gateTone(g.status)}`}>
              <span className="sc-gate-status">{gateLabel(g.status)}</span>
              <span className="sc-gate-label">{g.label}</span>
              {g.rationale && <span className="sc-gate-why">{g.rationale}</span>}
            </div>
          ))}
        </div>
      )}

      {/* 2 — the composite. Absent by design in checklist mode, and suppressed
             when a gate is not clear. */}
      {card.mode === "composite" && !card.gated && card.total != null && (
        <div className="sc-composite">
          {card.grade && (
            <span className="sc-grade">
              <span className="sc-grade-noun">{t.composite}</span>
              <b>{card.grade}</b>
            </span>
          )}
          <span className="sc-total">
            {card.total}
            <span className="sc-of">/{card.max_total}</span>
          </span>
          {card.percent != null && (
            <span className="sc-percent">{card.percent}%</span>
          )}
          {/* An override that silently moved the grade would be the worst
              possible version of this feature. Both figures, side by side. */}
          {card.overridden && (
            <span className="sc-was" title="Before officer corrections">
              corrected — was{" "}
              {card.grade_before_override ?? "—"}
              {card.percent_before_override != null
                ? ` (${card.percent_before_override}%)`
                : null}
            </span>
          )}
        </div>
      )}

      {card.mode === "composite" && card.gated && (
        <p className="sc-suppressed">
          {blocking.some((g) => g.status === "fail")
            ? `A hard policy gate failed, so no ${t.composite.toLowerCase()} is shown — the gate decides this case.`
            : `A hard policy gate could not be evaluated, so no ${t.composite.toLowerCase()} is shown. This case needs a human.`}
        </p>
      )}

      {/* 3 — the rows, as supporting detail. */}
      <div className="sc-rows">
        {rows.map((r) => (
          <Row
            key={r.factor_id}
            row={r}
            rowNoun={t.row}
            bandNoun={t.band}
            startExpanded={expanded}
            onOverride={submitOverride}
          />
        ))}
      </div>

      {/* A composite computed over part of the rubric looks identical to a
          complete one unless it says so. */}
      {card.mode === "composite" &&
        (card.unscored_factor_ids?.length ?? 0) > 0 && (
          <p className="sc-partial">
            {card.unscored_factor_ids!.length} {t.row}
            {card.unscored_factor_ids!.length === 1 ? "" : "s"} produced no
            finding, so this {t.composite.toLowerCase()} covers part of the
            rubric, not all of it.
          </p>
        )}
    </div>
  );
}

export default ScorecardView;
