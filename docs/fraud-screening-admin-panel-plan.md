<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Fraud Screening — Admin Visibility Panel & Feedback Loop (Plan)

> Status: PLAN (2026-07-19) · Owner: rohit@trustedweartech.com
> Companions: fraud-detection-coverage-matrix.md (what's detected),
> [fraud-detection-primitives-plan.md](fraud-detection-primitives-plan.md) (§7b rubric learning, §9 P2c/P3),
> wedge-strengthening-plan.md (doctrine: fraud = feature; no alarm queues).
> UI target: `Citra-UI/components/HomePanel.js` (admin card) + one drill-down page.

---

## 0. Do we need this — and what it is NOT

**Yes — as a governance/health view for the ADMIN, not a fraud console.** The
doctrine stands: officers never get an alarm queue; signals stay evidence inside
recommendations. But the admin who turned screening on has three questions
nobody can answer today without reading Mongo:

1. *Is screening working?* (how many cases screened, what did it find)
2. *Is it trusted?* (do officers confirm the flags, or dismiss them as noise)
3. *When it's noisy, what exactly do I change?* (the turn-off advisory)

That is a **calibration loop made visible** — the same "show the learning"
wedge move as the acceptance curve, applied to screening. What we still do NOT
build: alert streams, notifications, a fraud module officers work out of, or
any auto-tuning (every knob change is an explicit human action).

**"Alerted to whom" — important framing for the card.** Nothing alerts anyone.
A flagged case reaches exactly one person: the officer who was already
disposing that case (the flag rides the recommendation). So the panel shows
"seen by" = the disposing officer from the run/decision audit, not an alert
recipient list. The card's copy should say "surfaced to the deciding officer",
never "alerted" — admins must not expect a paging system.

## 1. What the admin sees

### 1.1 HomePanel card — "Screening Health" (org-wide, admin/builder-gated)

One card, five numbers, plain English, period selector (week / month / all):

| Label on card | Meaning (shown as tooltip) | Source |
|---|---|---|
| **Cases screened** | recommendations where the fraud screen ran | `smartapp_fraud_screenings` rows (every synthesis call persists, gated or not) |
| **Cases with warnings** | screened cases with ≥1 scored signal (points > 0) | same, `points > 0` |
| **Confirmed by officers** | warnings the officer marked "real issue" | case-feedback label (§2 — new stamp) |
| **False alarms** | warnings the officer dismissed with a reason | same |
| **False-alarm rate** | dismissed ÷ (confirmed + dismissed), with trend arrow vs prior period | derived |

Below the numbers: a one-line per-app table (app · screened · warned ·
false-alarm % · "needs attention" dot when false-alarm % > threshold, default
30%). Clicking a row opens the drill-down. This card sits beside the A.1
adoption card from the wedge plan — same endpoint family, same gating.

### 1.2 Drill-down page — per app

Four sections, in the order an admin actually reads them:

1. **What was found** — signal-type breakdown for the period, in plain words:
   "Reused evidence photo (14) · Photo predates the incident (6) · Photo taken
   away from the site (3) · Document edited after creation (5) · Same identifier
   on other cases (9)…". Each row = one signal key from the screenings
   `breakdown`, mapped to a fixed human label (a static dict in the UI — the
   signal keys are a closed set).
2. **What officers did with flagged cases** — for cases with warnings: approved
   unmodified / approved with changes / rejected, plus median time-to-decision.
   Join `smartapp_fraud_screenings.record_id` → decision records / run audit.
   Answers "did the flags actually change decisions", and names the deciding
   officer per case in the detail list ("surfaced to").
3. **Officer feedback on the flags** — confirmed vs dismissed counts per signal
   type, with the dismissal reasons listed verbatim (they're ≤500 chars by
   design). This is the trust ledger: an admin reading five dismissal reasons
   understands the noise source faster than any chart.
4. **Advisories** (§3) — only rendered when a signal type crosses the
   false-alarm threshold: "'Reused photo' was dismissed 6 of 7 times on
   headshot_url — likely an identity artifact. Fix: declare
   `artifact_role: identity` on that column in sources.json."

## 2. Feedback loop — what exists, what's missing

### Already built (do not rebuild)
- **Every screening persists**: `run_synthesis` inserts a row (tenant, app,
  record_id, points, per-signal breakdown, gated/escalated, sampled, timestamp)
  for BOTH below-gate and escalated cases.
- **Officer confirm/dismiss**: item-feedback endpoint, `modality="case"`,
  accept = "real issue", reject + reason = "false alarm"; reject folds into the
  L2 fraud-case rubric (screening judgment improves automatically). Gated to
  `fraud_enabled` apps.
- **L3 calibration**: `POST /apps/{slug}/fraud-calibration` computes per-signal
  officer-rejection hit-rate — the statistical basis for §3 advisories.
- **Dispositions**: approve/override/reject per record already audited.

### Gaps to close (the actual build)
1. **Stamp the feedback onto the screening row.** Case-modality feedback is
   rubric-only today — no queryable label survives, so "false-alarm rate"
   cannot be computed. Fix: in `submit_item_feedback` (modality="case"), also
   `$set` on the matching `smartapp_fraud_screenings` row (join on tenant +
   app_slug + record_id, latest row): `officer_verdict: confirmed|dismissed`,
   `verdict_reason`, `verdict_by`, `verdict_at`. One update, no new collection.
2. **Disposition join field.** Aggregation joins screenings → decision records
   by record_id; verify the record_id shapes match (screenings use the raw case
   key; decision records store record_keys) — add a `record_ref` stamp on the
   screening row if the join needs the qualified form.
3. **Aggregation endpoints**: `GET /org/screening-stats?period=` (card) and
   `GET /apps/{slug}/screening-stats?period=` (drill-down) — Mongo aggregations
   over screenings + the new verdict labels + disposition join. No cron, no new
   collections; render on demand.
4. **UI**: the HomePanel card + drill-down page, gated like the existing Admin
   cards. Static signal-key → plain-label map lives in the UI.
5. **Feedback affordance check** (UI): the officer-side confirm/dismiss buttons
   for a fraud screening exist on the review gate for `fraud_enabled` apps —
   verify they're wired in Citra-UI (backend path is live; the click-through is
   what produces §1.2-3's data). If not wired, that's part of this build.

## 3. Turning off a false alarm — the advisory matrix

The core admin question: "this flag is noise — where's the off switch?" There
are FIVE levers, each with a precise scope. The drill-down renders the right
one per noisy signal type via a fixed deterministic mapping (no LLM):

| False-alarm pattern (what the admin sees) | Lever | How (exact change) | Scope |
|---|---|---|---|
| Reuse flags on a photo column that legitimately repeats (headshot, ID scan, meter nameplate) | **Ontology — column role** | `sources.json`: set `artifact_role: "identity"` (reuse becomes verification) or `"supporting"` / `reuse_policy: "ignore"` (reuse ignored) on that column → re-crawl → republish | one column, one dataset |
| GPS "wrong location" flags on legitimate photos (premise coordinates are imprecise, or photos taken at the meter room vs billing address) | **Ontology — radius** | `sources.json`: raise `fraud_screening.gps_radius_km` (e.g. 5 → 25); or delete `location_lat_field`/`location_lon_field` to drop the GPS check entirely | one dataset |
| "Photo predates incident" flags because the date column is actually the REPORT date, not the incident date (photos legitimately older) | **Ontology — date field** | `sources.json`: point `incident_date_field` at the correct column, or remove it to drop the date check | one dataset |
| A whole dataset shouldn't be screened at all | **Ontology — master switch** | `sources.json`: `fraud_screening.applies: false` (hard opt-out — wins over column roles) | one dataset |
| One signal TYPE is noisy platform-wide (e.g. `metadata_anomaly` on a scanner that always stamps Photoshop) | **Weights (env)** | run L3 calibration → set `FRAUD_SIGNAL_WEIGHTS` (e.g. `{"metadata_anomaly": 0}`) — no deploy, service restart only | deployment |
| Flags are individually fine but too many weak cases escalate to T3 review | **Gate threshold** | raise `gate_min_points` on the app's `fraud_synthesis` tool (builder edit + republish) | one app |
| The screen judges a case wrongly given context (e.g. "WhatsApp images without EXIF are normal here") | **No action needed — L2 rubric** | already learning: each dismissal-with-reason folds into the fraud-case rubric automatically | one app, automatic |

Rules the advisory copy must state plainly:
- **Ontology changes require: edit sources.json (or its generator for demo
  tenants) → MCP re-crawl → app republish.** The panel should say this as a
  3-step checklist, because a half-applied change (edited JSON, no re-crawl) is
  invisible and erodes trust in the panel itself.
- **The ontology can only RELAX screening, never silently weaken it** — an
  un-annotated column stays evidence/suspicious. So every "turn off" is an
  explicit, auditable declaration in the source's own file — which is the
  governance story: the customer's IT owns the off switches, in a reviewable
  file, not a hidden admin toggle.
- **No auto-tuning.** The panel recommends; a human edits. (Same reason
  graduated autonomy was rejected — the system must never expand or contract
  its own judgment silently.)

### Advisory trigger logic (deterministic)
For each (app, signal_key) with ≥5 verdicts in the period and dismissal rate
≥ 70%: emit the mapped advisory. For reuse signals, group by column (the
finding carries it) so the advice names the exact column. Below those
thresholds, show nothing — a premature advisory teaches admins to ignore the
panel.

## 4. Explicitly out of scope
- Notifications/alerts on flags (the officer in the case flow is the only consumer).
- Officer-facing fraud worklists or queues.
- Auto-applying any advisory (incl. auto-zeroing weights).
- LLM-generated advisories — the mapping in §3 is a static table.

## 5. Build plan

| Step | What | Effort |
|---|---|---|
| 1 | Verdict stamp on screenings (feedback → `officer_verdict` on the row) | ✅ BUILT 2026-07-19 — `fraud_synthesis.stamp_officer_verdict`, wired into `submit_item_feedback` (modality=case; item_id `<record_id>-fraud`); miss = logged + `screening_stamped: false`, never fails the feedback |
| 2 | Two aggregation endpoints (org card, app drill-down) | ✅ BUILT 2026-07-19 — `GET /org/screening-stats?period=week\|month\|all` (admin-role-gated; totals + per-app rows + display names) and `GET /apps/{slug}/screening-stats` (same gate as fraud-calibration; signals w/ plain labels, verbatim dismissal reasons, §3 advisories at ≥5 verdicts & ≥70% dismissal). `fraud_synthesis.screening_stats` + static `SIGNAL_ADVISORIES` map (advisory text per signal key — the UI renders these verbatim). Latest-row-per-record dedupe; false-alarm rate = dismissed ÷ judged. 10 unit tests + live smoke on local (org endpoint 200/403/422; drill-down shape verified against seeded acme-power rows) |
| 3 | HomePanel "Screening Health" card | ✅ BUILT 2026-07-19 — `FeatureCard` in the Admin section (`screening-health-card`) opening `screens/ScreeningHealthScreen.js` (modal, same pattern as WorkflowControlScreen) via `services/ScreeningHealthService.js`. Verified present in the live dev-server bundle |
| 4 | Drill-down page (4 sections + advisory rendering from the static matrix) | ✅ BUILT 2026-07-19 — inside the same modal: org overview (5 stat tiles + per-app rows w/ >30% attention dot + truncation notice) → tap-through app view (signals w/ plain labels, verbatim dismissal reasons, amber advisory cards each ending with the 3-step ontology checklist). Server's `SIGNAL_ADVISORIES` text rendered verbatim — no advisory logic in the UI |
| 5 | Verify/wire the officer confirm/dismiss buttons on the review gate | ✅ VERIFIED already wired — `citra-app-runtime ItemFindingReview.tsx` renders case-modality findings as Confirm / Dismiss / Skip and POSTs `modality: "case"` to the feedback endpoint, which now stamps the verdict (step 1). No change needed |
| 6 | After 2+ weeks of pilot verdicts: run L3 calibration, tune `FRAUD_SIGNAL_WEIGHTS`, revisit advisory thresholds | recurring, manual |

**Proof metric:** false-alarm rate visibly declining over weeks on the same
app (the L2 rubric + ontology fixes working) — which is the fraud-side twin of
the acceptance curve, and belongs on the same slide.
