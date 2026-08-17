---
name: citra-system
description: The builder's onboarding to the product it builds for. The pod ships the ENTIRE SmartApp system as read-only code — the renderer (how a spec renders), the executor (how it's queried/called/run), and the validators (how it's checked). Read ARCHITECTURE.md FIRST, read the relevant code slices for what you're building, then author a correct spec. Code is ground truth; gates are the floor. Includes the runtime-verifier backstop.
metadata:
  category: citra
  tools: [bash, exec]
---
<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra — understand the system, then build

You author a spec for a system that **already exists** — two engines render and
execute it, and a validator checks it. Historically the builder couldn't see that
system, so it *guessed* the half that lives in the runtime (what happens on a
click, how a metric computes, how a read is planned) and got corrected by gates.
That is over. **The whole system is in the pod as real code, and you read it like a
codebase before you build** — the way a developer reads the repo before writing a
feature.

```
runtime-reference/
  renderer/    citra-app-runtime/src/   — HOW a spec RENDERS
  executor/    smart-app-service/*.py   — HOW a spec is QUERIED / CALLED / RUN
  validators/  smart-app-service/*.py   — HOW a spec is CHECKED at publish
  MANIFEST.md                           — index + snapshot date
```

## The order of operations (do this — it is the build, not a checklist)

1. **Read `ARCHITECTURE.md` (next to this file) FIRST.** It is your onboarding: the
   spec lifecycle, how components assemble, the **verb-half** (the 9 things the
   runtime does that the spec never shows — record-passing on click, agg
   compute+format, NL reads, ref grammar, detail binding, file fallback, navigate
   params, chart pushdown, input injection), and a navigation table.
2. **For the components you'll actually use, read the code slices** the navigation
   table points to. Targeted reads (`grep -n` to the symbol), not the whole tree —
   the snapshot is large; reading it all wastes the build.
   - **MUST read BOTH layers for each feature — render AND execute — plus the
     validator.** A feature can be correct in one layer and broken in the other:
     a metric the *renderer* would display fine but the *executor* can't compute
     (→ blank tile); a chart the *executor* parses but the *renderer* needs as a
     structured block (→ raw JSON). The nav table lists a renderer file AND an
     executor file per feature for this reason — read **every** file it lists for
     your feature, and the `validators/` rule that gates it. Reading only one
     layer is the #1 way a bug survives.
3. **Author the spec to match what the code actually does.** The field contract is
   `executor/models.py` (+ `renderer/types/spec.ts`); the rules that reject a spec
   are in `validators/`. Build to *those*, not to a remembered rule.
4. **Gates are the floor.** If `citra_spec_validate` / `static_checks` /
   `/builder/preview-smoke` / `/builder/smoke-run` fire, you skipped reading the
   slice that governs that behavior — go read it.

> **Context discipline:** do NOT load the whole reference into your prompt. Use the
> map + targeted reads, exactly like working in a real codebase. **The code wins
> over any prose skill** — if they disagree, follow the code and note the drift.

## Backstop — the runtime-verifier sub-agent (optional second pass)

After composing, you MAY delegate a spec↔runtime check to the `runtime-verifier`
sub-agent, which reads the relevant slices in its **own disposable context** and
returns a compact verdict — so you don't have to load runtime code into your own
prompt for the *verification* pass. Use it when you authored a lot, or want a second
opinion before smoke:

```
sessions_spawn(agentId="runtime-verifier",
  task="Verify the spec against the runtime snapshot. Specs at
        /workspace/build/app_spec.json and /workspace/build/agent_spec.json.
        Return the RUNTIME-VERDICT block.")
sessions_yield()   # ends the turn; the verdict returns as the next message
```
Apply every fix it returns (runtime is ground truth), re-validate, then smoke. This
is a backstop — your primary correctness comes from understanding the system up
front (steps 1–3), not from the verifier catching you.

## Rules
- **Read the map first, every build.** It is the difference between coding and guessing.
- **Read targeted, not everything.** The map tells you where to look.
- **Code > prose.** `runtime-reference/` is authoritative; skills are the guide to it.
- **If the reference is missing**, STOP and report a builder-pod misconfiguration —
  do not fall back to guessing from memory.
