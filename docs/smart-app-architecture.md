<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Smart App — Build & Runtime Architecture (verified)

> Status: **verified against code 2026-05-20.** This document records the *actual*
> build→runtime data path. It supersedes the speculative sections of
> `smart-app-builder-plan.md` wherever they disagree.
> Companion: `smart-app-fewshot-from-history-plan.md`.

## TL;DR

A Smart App is **compiled once by an agentic builder, then executed many times by a
fast, non-agentic runtime.** The builder does the expensive thinking — RAG probing,
policy extraction, few-shot authoring — and bakes the result into `agent_spec.json`.
The runtime just executes that spec for one dedicated job with minimum latency.

This is a deliberate **compile-time vs. run-time split**. It is correct as designed.
There is no gap in the skill / RAG / tools architecture. The one real gap is
**auditability** — see §6.

---

## 1. Two planes

| Plane | Image | Lifetime | Agentic? | Job |
|---|---|---|---|---|
| **Build** | `citra-app-builder` (open-claw) | ephemeral, per session | **Yes** — multi-phase agent loop | Turn a BA's goal into `app_spec.json` + `agent_spec.json` |
| **Runtime** | `smart-app-service` + `citra-app-runtime` | long-lived, shared pool | **No** — bounded single-shot | Execute the published spec for one dedicated job |

`citra-app-runtime` (Next.js) is a **pure frontend** — it renders panels and proxies
`/run` to `smart-app-service`. All runtime LLM execution lives in
[`smart-app-service/runtime.py`](../smart-app-service/runtime.py).

---

## 2. Build plane — the agent does the expensive thinking (once)

The builder runs as an open-claw sandbox pod through phased skills under
[`smart-app-service/skills/`](../smart-app-service/skills/). Verified behaviors:

### 2.1 RAG access — via the dept-MCP `/query` endpoint

The builder **does** use RAG. `citra-rag-probe` (Phase 1) POSTs to each dept-MCP's
`/query` endpoint, which runs the full **embed → Milvus → rerank** pipeline inside
`source-mcp-template`. `citra_discovery_query` is the same call, tool-wrapped.
The builder uses this to learn domain vocabulary and policy.

### 2.2 Policy is extracted at build time and **baked into the prompt**

Decision rules, SOP thresholds, and adjudication logic are written by the builder
into `agent_spec.system_prompt`
([`agent_spec.schema.json:20`](../smart-app-service/schemas/agent_spec.schema.json)).
Policy is **not** re-fetched at runtime — see §5.2.

### 2.3 Few-shot "skill samples" are authored and indexed into Milvus

`citra-fewshot-from-history` packages historical decision records (input + decision +
reasoning) and indexes them into the Milvus collection `samples_<agent_id>` via the
`sample_vector_sink` workflow node. These describe *how past cases were processed*.

### 2.4 The published artifact fully specifies the runtime

`agent_spec.json` carries: `system_prompt` (role + policy), `input_schema`,
`tools_v2[]` (incl. `neighbor_samples` config), `mcps[]`, `rag[]`, `actions[]`,
`hitl_policy`. The runtime needs nothing else — it just loads and executes.

---

## 3. Runtime plane — fast, single-purpose execution

[`smart-app-service/runtime.py`](../smart-app-service/runtime.py) `execute_run()`:

1. **Skill match on the user's data.** `_query_neighbor_samples` embeds the user's
   actual input (`case_input` — e.g. the claim JSON) and vector-searches
   `samples_<agent_id>` in Milvus.
   - `canonical` mode → static curated examples (filter `is_canonical == true`, no vector).
   - `neighbors` mode → per-case similarity search on the embedded user input.
2. **Pre-injection.** Matches are folded into the system prompt *before* the first
   LLM call (`SIMILAR PAST CASES` / `REPRESENTATIVE PAST DECISIONS` blocks). **Zero
   extra round-trips.**
3. **Single LLM call** for the dedicated job, with `tools_v2` available on demand.
4. **OCR is present** — `vision_ocr` tool kind, gated on `settings.ocr_enabled`
   ([`tools_v2_dispatch.py:492-532`](../smart-app-service/tools_v2_dispatch.py)). The
   LLM calls it only when claim data contains images.
5. On-demand MCP `/query` for **live data** lookups (eligibility, duplicate checks).

Note: runtime skill matching uses **Milvus `samples_<agent_id>`**. `Skill-Service`
was a parallel registry never wired into the build/runtime path; it was retired and
deleted 2026-07-17. Domain knowledge lives in the SOP Library (documents) and app
memory (learned decisions); the builder's own vocabulary ships as file-based skills
under `smart-app-service/skills/`.

---

## 4. Design rationale — why it is built this way

- **Cost lives in the build, not the run.** RAG probing, policy synthesis, and
  few-shot authoring are expensive and happen *once* per app. A published app then
  serves thousands of runs cheaply.
- **The runtime app has exactly one job**, designed and frozen by the builder. It
  does not need to re-discover its task on every request.
- **Few-shot is pre-injected, not retrieved in-loop** — the closest past cases are
  fetched and placed in the prompt before the first inference, collapsing what would
  be multiple round-trips into one call.
- **Auditability by construction.** Policy is a versioned, inspectable string in
  `agent_spec`. When policy changes, the app is rebuilt and republished with a
  version bump — so every run is traceable to a known policy version.

---

## 5. ⚠️ Design guardrails — do NOT change these

### 5.1 Do NOT add an agentic / multi-turn tool loop at runtime

The runtime is intentionally a **bounded single-shot executor**, not an agent.
Tools (`vision_ocr`, MCP `/query`) fire **on demand, once**, when the job needs them
— that is fine and necessary. What must **not** be added is an open-ended
`reason → act → reason → act` loop.

**Why:** an agentic loop adds an LLM round-trip per tool step. For an app with one
dedicated job and a builder-authored prompt + matched few-shot already in context,
the loop adds **large latency for zero decision-quality gain**. The thinking was
already done at build time. Keep the runtime fast.

> Rule of thumb: a tool call to *fetch a fact* is fine. A loop to *decide what to do
> next* belongs in the builder, not the runtime.

### 5.2 Do NOT re-fetch policy via RAG at runtime

Policy is baked into `system_prompt` at build time. Re-retrieving it per run is slow
and redundant. Correct split:

- **Policy / decision rules** → baked at build time (build-time RAG).
- **Live data** (member eligibility, duplicate-claim check) → on-demand runtime MCP
  `/query`. This is *data*, not *rules*.

When policy changes: **rebuild and republish the app** (version bump). Do not turn
the runtime into a policy-retrieval engine.

---

## 6. Known gap — auditability (the real work item)

**Verified:** today a completed `/run` persists **nothing**. Only `pending_runs`
rows for approvals are written
([`main.py:2625-2638`](../smart-app-service/main.py)). The decision, the reasoning,
the few-shot samples and RAG/tool results that drove it — all returned in the
response and then dropped. `timeline` records *which* tools were called but **not
their results**. The `SmartAppRecord` ("decision/audit") collection is never written
by `runtime.py`.

For regulated workloads (insurance-claim adjudication) this is a hard miss. See the
auditability plan below.

---



Covers, in one place:

- The `app_run_audit` ledger — full row schema, per-tenant hash chain, fatal-on-write persist behaviour
- Write-path guardrails (F1–F8): default-on approval for write actions, `write_events` capture, `audit_missing` flag, approver allowlist, OTel trace_id, chat write block
- The flagged-review process and the admin / ops runbook
- How BAs add an Undo panel to a Smart App by wiring `SmartAppService.reverseWrite()` — and why the admin tool does NOT itself ship a Reverse button
- File map and known gaps (pre-image diff, per-action override, eval gate, cross-surface `DecisionRecord` generalisation)

This SmartApp audit remains **surface-first by design** — it proves the decision-audit shape on the highest-stakes surface before it is generalised platform-wide. LLM decisions happen across ~7 surfaces (SmartApp runtime/builder, workflow runtime/builder, enterprise chat, action chat, presentation skills); the platform-wide plan to put all of them on one shared `DecisionRecord` ledger plus lifecycle governance gates is in `llm-governance.md`.
