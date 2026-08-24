<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Decision Apps — Control Plane & UI Plan (auto-process safety + kill switches)

**Date:** 2026-07-09 · **Companion to:** `decision-app-prod-maintenance-risk.md`
**Goal:** give BAs/admins full operational control from the **UI (never the box)**, make the dangerous state (**autonomous writes**) impossible to miss, and provide a kill switch for every blast radius.

---

## Design principles
1. **Business admin never needs the box.** Every operational control lives in the UI/API; the box is for deploy/DB/secrets/node-health only.
2. **State is visible where the app lives.** Automation mode + health show on the app **card**, not buried three clicks deep in a panel.
3. **A kill for every scope.** One trigger → one app → a dept/org → the whole platform.
4. **Auto-process is privileged and loud.** Autonomous writes can't be turned on by accident, and an app that has them wears a red badge everywhere.
5. **Default-safe.** A mandatory hourly ceiling; guard fails closed.
6. **Two planes, not two apps.** Owners self-serve their own apps inline; admins get one central screen. No separate application.

---

## Two control planes

| | **Plane A — Per-app (owner/BA)** | **Plane B — Central Operations Control (dept/org admin)** |
|---|---|---|
| Who | The BA who created the app | dept_admin (their dept) · org_admin (whole org) |
| Scope | Apps you own | Every app in your admin authority |
| Where | On the **app card** + its panels (existing surface, enhanced) | **One new admin screen**, opened from HomePanel → Admin |
| Controls | Pause my app · disable my trigger · arm/disarm auto-process · see my runs/audit | Fleet view · per-app pause/disable across the dept/org · dept/org halt · **global RED BUTTON** · incidents |

**Answer to "do we need another UI?":** yes — **one** new admin screen (Plane B), like the other HomePanel admin screens. Per-app control (Plane A) stays inline on the cards. No standalone app, no separate scheduler console.

---

## The kill-switch ladder (blast-radius tiers)

| Tier | Scope | Who | Effect | Where in UI | Status |
|---|---|---|---|---|---|
| 1. Disable trigger | one trigger | owner | stops that trigger auto-firing | app card → Auto panel | **exists** |
| 2. **Pause app** | one app | owner + admin | stops runs/writes/automation, **keeps reads + audit** | app card **Quick-Pause** + central table | **BUILD** |
| 3. Archive app | one app | owner + admin | full stop incl. reads (`410`) | app card | exists |
| 4. **Dept / Org halt** | all apps in a dept/org | dept/org admin | freeze all automation in scope | central control | **BUILD** |
| 5. **Global halt (RED BUTTON)** | everything | org/super admin | runtime flag freezes all runs + writes + triggers + **webhooks** | central control top bar + banner | **BUILD** |

Tier 2 is the missing everyday tool (today you must archive, which kills the UI too). Tier 5 must be a **runtime flag** (Redis/DB, checked per request) — not the current restart-only `SCHEDULER_ENABLED`, and it must also cover the webhook/queue path.

---

## UI placement & layout

### 1. HomePanel → Admin section: one new card
Add **"Operations Control"** (gauge/shield icon), gated to dept/org admin, beside the existing admin cards. It shows a **live mini-status** so the health is visible before you even open it:

```
┌──────────────────────────┐
│  📟  Operations Control    │
│  3 autonomous · 0 failing  │   ← green
│  ── or ──                  │
│  ⛔ GLOBAL HALT ACTIVE      │   ← red when halted
└──────────────────────────┘
```

### 2. Central Operations Control screen (NEW, Plane B)
```
┌───────────────────────────────────────────────────────────────────┐
│  SYSTEM STATUS:  🟢 Normal            [ ⛔ GLOBAL HALT ]  (confirm) │  ← full-width status bar + red button
├───────────────────────────────────────────────────────────────────┤
│  Automation ON: 12   🔴 Auto-Process: 3   Triggers: 27            │  ← KPI strip; autonomous count is RED
│  Pending approvals: 8   Failures 24h: 4   Breaker trips: 1        │
├───────────────────────────────────────────────────────────────────┤
│  Scope:  ( My Dept )  ( Org )      Filter: [Auto-Process] [Failing]│  ← scope = admin authority
├───────────────────────────────────────────────────────────────────┤
│  APP                 OWNER   MODE            TRIG  LAST RUN  ERR%  │
│  Theft Triage        deepak  🔴 Auto-Process  ON   3m ok     0%   [Pause][Disable auto][Open][Audit]
│  Recovery Tracker    rohit   🟢 Recommend     ON   1m ok     2%   [Pause][Disable][Open][Audit]
│  DT Failure Resp.    deepak  ⚪ Manual         —    —         —    [Open]
│  …                                                                │
├───────────────────────────────────────────────────────────────────┤
│  INCIDENTS  ·  poll failed (Complaint-Routing) 12:01  ·  breaker  │  ← the alerting surface
│             tripped (Theft Triage) 11:40  ·  dead-letter ×2       │
└───────────────────────────────────────────────────────────────────┘
```
- **Status bar** carries the global RED BUTTON (confirm-guarded, org_admin) and, when halted, shows *who/when/reason* + an "Un-halt" action.
- **KPI strip** puts the two numbers that matter (autonomous apps, failures) in front.
- **Fleet table** = the "see all crons in one place" surface, scoped to the admin's dept/org. Row actions cover tiers 1–3 without leaving the screen.
- **Incidents** = the failures/dead-letters/breaker-trips feed that is log-only today.

### 3. App-card enhancements (Plane A — front, not hidden)
Every app card gets a **status strip** and a **Quick-Pause**, so the critical state is visible in the list itself:

```
┌───────────────────────────────────────────────┐
│ 🔴 Auto-Process · autonomous writes   ●healthy │  ← mode pill (⚪Manual / 🟢Recommend / 🔴Auto-Process) + health dot + last-run
│ Theft Triage Co-pilot            v1 · Published │
│ Triages smart-meter tamper events …            │
│ [Open] [⏸ Pause] [Auto panel] [Audit] [Versions]│  ← Quick-Pause promoted to the front row
└───────────────────────────────────────────────┘
```

### 4. Global status banner (persistent, top of app)
Reuse the existing HomePanel banner pattern (same slot as the impersonation banner). Whenever a halt/pause affecting the user is active:
```
⛔ GLOBAL HALT ACTIVE — automation frozen by rohit@ at 12:04. Approvals & reads still work. [Open Control]
```
This guarantees the most important state is never hidden.

### 5. Auto-process arming ritual (the safety gate)
Enabling `auto_process` must open an **"Arm autonomous writes"** modal — not a silent toggle:
```
⚠ Arm autonomous writes — Theft Triage Co-pilot
This app will COMMIT to source systems with NO human approval when:
  • action: disconnect_recommend   • confidence ≥ 0.8   • amount ≤ ₹50,000
Hourly ceiling (required): [ 20 ] commits/hour
Type the app name to confirm:  [_______________]
                                   [ Cancel ]  [ Arm ]
```
- org_admin only (backend already requires it — the UI must match).
- Mandatory hourly ceiling (no "unlimited").
- Writes an audit event; after arming the app wears the 🔴 badge everywhere.
- The current panel's false *"nothing is committed automatically"* copy is removed; the panel shows the real `execution_mode` + policy.

---

## Backend needed (each maps to a screen element)
- **Per-app pause:** new `paused` state / `runs_enabled` flag, checked in `/run`, `/tool`, `/approve`, webhook, and `tick_once`. Powers Quick-Pause + table Pause.
- **Global + dept/org halt:** a **runtime** flag (Redis/DB) with scope, checked at the top of every run/trigger/webhook path → `503`. Powers the RED BUTTON + banner. Must cover the queue consumer (webhooks).
- **Fleet endpoint:** `GET /admin/automation` — every app in the admin's scope with mode, trigger states, last run + status, error rate, auto-commit rate. Powers the fleet table + KPIs + HomePanel mini-status.
- **Incidents feed:** failures / dead-letters / breaker-trips (currently log-only) surfaced via an endpoint.
- **Arming + truth:** trigger serializer returns `execution_mode` + policy; arming requires ceiling + org_admin + audit event; guard defaults **fail-closed** with a mandatory hourly ceiling.

---

## Phasing (cover the risk fastest)

**P0 — safety, small UI (stops the foot-guns, gives a red button):**
1. Trigger-panel truth-fix (show mode + policy; kill the false copy).
2. Auto-process **arming ritual** (confirm modal + mandatory ceiling + org_admin + audit).
3. **Per-app pause** (Quick-Pause on card + backend flag).
4. **Global halt runtime flag** + top status bar toggle + persistent banner.
5. **Mode/health pill + 🔴 autonomous badge** on every card.

**P1 — central visibility (self-serve, no box):**
6. **Operations Control screen** (fleet table + KPIs + incidents) via `GET /admin/automation`.
7. HomePanel Admin **"Operations Control"** card with live mini-status.
8. **Dept/Org halt**.

**P2 — hardening:**
9. Alerting/notifications on failures/dead-letters/breaker trips.
10. Surface breaker/rate-limit state per app; interactive-path quotas.

Rationale: P0 is mostly small, high-leverage changes on surfaces that already exist (cards + trigger panel) plus one runtime flag — it removes the "admin arms autonomous writes by accident / can't stop it" class of incident. P1 delivers the single-pane fleet control that replaces box/Mongo queries. P2 makes it observable.
