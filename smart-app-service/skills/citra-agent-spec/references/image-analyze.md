<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# `image_analyze` — structured per-image judgment (tools_v2)

Use `image_analyze` when the agent must **judge** an image (assess accident damage,
machine defect, document condition) and that judgment drives a decision — **not**
when you only need OCR'd text (that's `vision_ocr`). It returns a STRUCTURED
`ItemFinding`, the officer accepts/rejects it **per image**, and reject-reasons
become learned criteria (a per-`task_type` rubric). See
`docs/multimodal-decision-apps-plan.md` for the full design.

Requires `OCR_ENABLED=true` (reuses the vision proxy). If false, push the image
requirement to `requirements_unmet`.

## Tool shape (one entry per image `task_type`)

```json
{
  "kind": "image_analyze",
  "name": "analyze_damage_photo",
  "task_type": "motor-accident-damage",
  "field_schema": {
    "severity": "one of none|minor|moderate|major|total",
    "parts_affected": "list of strings",
    "airbag_deployed": "true/false",
    "structural": "true/false",
    "consistency_with_claim": "one of match|partial|mismatch"
  }
}
```

- `task_type` is the **semantic role** of the image — the per-type **learning bucket**
  and schema key. `motor-accident-damage` and `machine-inspection-defect` are
  different `task_type`s → different rubrics/schemas, **same tool + model**.
- `field_schema` = `{field_name: "type/description"}`. The model returns these
  fields verbatim, plus `recommendation`, `confidence (0–1)`, `rationale`.
- The LLM calls it with `{ image_url, item_id? }` — **one call per image**. The
  `task_type`/`field_schema` are fixed server-side (the LLM cannot change them).

## Output — `ItemFinding`

```json
{ "item_id": "PHOTO-12", "item_type": "motor-accident-damage", "modality": "image",
  "fields": { "severity": "major", "parts_affected": ["bonnet","radiator"], "airbag_deployed": true,
              "structural": true, "consistency_with_claim": "match" },
  "recommendation": "approve-investigate", "confidence": 0.82,
  "rationale": "Frontal crush with deployed airbag; consistent with a head-on claim.",
  "citations": [{"source_url": "<image>"}], "rubric_version": "v1" }
```

The runtime renders each finding for the officer to **accept** or **reject (+reason)**.

## Multiple `task_type`s in one app

A claims app commonly has several: image `motor-accident-damage` + `odometer-reading`,
document `police-report` + `repair-estimate` (`doc_extract`, when available), plus the
record decision. Declare one `image_analyze`/`doc_extract` tool per `task_type`. A
machine-inspection app reuses the identical tooling with `task_type=machine-inspection-defect`.

## Learning (prompt-criteria, NOT image vectors)

- The active **rubric** for `(tenant, app, image, task_type)` is loaded server-side and
  prepended to the analysis prompt. Officer reject-reasons append to it (per `task_type`).
- **Standing standard = the LIVE SOP, fetched by the TOOL — NOT a stored seed, NOT passed
  by the agent.** When an SOP/policy governs the judgment, set **`sop_source`** on the
  `image_analyze` / `doc_extract` tool to the RAG source id that holds that SOP (e.g. the
  policy corpus), and optionally **`sop_query`** (defaults to a task_type-derived query).
  The tool fetches the SOP **server-side and CACHES it per (app, task_type)** — so 10 photos
  of one claim share ONE fetch and the agent **never carries the SOP** (no per-image re-emit,
  no context bloat). It's applied as the authoritative standard (above the learned rubric);
  if `sop_source` is set but the fetch yields nothing, the tool fails loud (`sop_unavailable`)
  rather than judging blind. This keeps the standard FRESH (cache TTL), so **do NOT author or
  seed rubric criteria in Mongo** — the SOP supplies the standing standard at runtime and
  officer reject-reasons (the learned rubric) refine it on top. Leave `sop_source` unset if
  no SOP applies (runs on the agent's `query` + learned rubric).

  **How to wire `sop_source` at build time (the builder does this, from the catalogue):**
  1. **Find the SOP corpus.** Policy/SOP corpora are **RAG sources** in the same catalogue
     you use to wire `rag` tools (look for sources described as policy / SOP / circular /
     procedure libraries — e.g. an "…policy library"). Pick the one whose docs govern this
     `task_type`.
  2. **Confirm it has the SOP — test-query it at build.** Run a RAG query against that
     source for the task's procedure; if relevant SOP text comes back, wire it. If nothing
     relevant exists, **leave `sop_source` unset** (don't wire a dead source → runtime
     `sop_unavailable`).
  3. **Set the fields.** `sop_source` = that source's `source_id`, plus EITHER:
     - **`sop_query`** — a short query for the *judgment* criteria (evidence validity, severity
       thresholds, reject conditions); the tool fetches the top matching passages. Use when the
       SOP is large and only part of it governs this task_type; OR
     - **`sop_doc_path`** — the doc_path of ONE governing SOP document (from a RAG probe result's
       `metadata.doc_path`). The tool then loads that ENTIRE document (all sections, in order) as
       the standard — no passage-matching, nothing missed. Prefer this when a single, self-contained
       SOP governs the task and you want its full text. (`sop_query` is ignored when `sop_doc_path`
       is set.)

     The SOP is answered by the Citra platform reader (Milvus direct) — the dept-MCP serves no RAG.

  ```json
  { "kind": "image_analyze", "name": "analyze_defect_photo",
    "task_type": "asset-inspection-defect",
    "data_source_id": "ds_inspections", "url_column": "defect_photo_url", "key_field": "inspection_id",
    "sop_source": "acme_power_policy_library",
    "sop_doc_path": "policy/dt_failure_response_sop.md",
    "field_schema": { "defect_type": "string", "severity": "none|minor|moderate|major", "component_affected": "string" } }
  ```
- The active **learned rubric** for `(tenant, app, image, task_type)` is still loaded
  server-side and prepended after the SOP. Per-image feedback posts to
  `POST /apps/{slug}/items/{item_id}/feedback`
  `{modality, task_type, decision: accept|reject, reason, subject}` — reject **requires** a
  reason; `subject` (a few words on what the item was) anchors the lesson to a subject-type.

## Resolve URLs SERVER-SIDE — never route a signed URL through the LLM

A signed S3/GCS URL is long and `&`-laden; when the agent copies it from a record
into the tool call, the LLM can corrupt the signature → **403**. So bind the tool to
the record instead:

- Set **`data_source_id`** (the app data_source whose record holds the URL),
  **`url_column`** (the column with the image/doc URL), and **`key_field`** (the
  dataset's key).
- **Find the media columns by `column_kind` in the catalogue**, not by guessing
  from names: `column_kind: "image_url"` → an `image_analyze` candidate,
  `"document_url"` → a `doc_extract` candidate (`mime_hint` gives the content
  type, e.g. `application/pdf`). A dataset can carry several — one tool per
  column per task_type. If a column obviously holds media but the catalogue
  shows no `column_kind`, flag it to the BA (the source declaration should be
  fixed and re-crawled) rather than binding on a name hunch.
- The tool then exposes a single **`record_id`** arg — the agent passes the SHORT id,
  and the tool reads the URL from the record server-side (blob refs are presigned
  there too). The LLM never sees the URL.

Only fall back to a direct `image_url`/`document_url` for headless/API callers that
genuinely supply a fetchable URL themselves.

## Avoiding quality loss — ALWAYS pass `query`

`image_analyze` / `doc_extract` take a **`query`** arg. The lead agent (which holds
the full case context — the record, policy RAG, related lookups) MUST pass that
context + the specific question in `query`. The vision model then reasons **on the
pixels WITH the case context**, so the tool is the *reasoner*, not a lossy caption.
Calling it with just the URL (no `query`) makes it reason blind — a quality loss.

Good: `query="Claim CLM-123: claimant reports a low-speed rear-end collision on
2026-03-04, estimate ₹85k. Assess whether the visible damage is consistent with
that, whether it is structural, and any pre-existing-damage signals."`

## system_prompt guidance (tell the agent how to use it)

> "For each accident photo, FIRST gather the case facts (record + policy), then call
> `analyze_damage_photo` with the `image_url` AND a `query` containing those facts +
> what to check. Use the returned `severity`, `structural`, and
> `consistency_with_claim` to recommend a decision. Surface each photo's finding for
> officer review; do not finalise until photos with low `confidence` are confirmed."

## Which model reviews the document (`doc_extract`)

- **Text-layer PDFs** are reviewed by the **large reasoning model** by default — the
  document reviewer should be the strong model, not a small/vision one. The builder
  can override per tool with `model_tier` (`large` default / `medium` / `small`),
  e.g. downgrade a high-volume, low-stakes extraction for cost.
- **Scanned / image documents** (no text layer) and all `image_analyze` calls go
  through the **multimodal vision model** regardless of `model_tier` — they require
  seeing the page pixels. `model_tier` does not apply there.

## Per-item review gate — ASK the business analyst

Whenever an app declares ANY `image_analyze` / `doc_extract` tool, the runtime can
**gate the record-level Apply on per-item review**. This is governed by the app
spec field **`item_review_gate`** and is set by you (the builder) from what the BA
tells you. **Ask the BA** which mode the process needs:

- **`hard`** *(default — use unless the BA says otherwise)* — the officer cannot
  Apply the record decision until **every** analyzed image AND document has been
  dispositioned (Accept / Reject+reason / Cancel). Nothing slips through unreviewed.
- **`soft`** — Apply is allowed but the officer is warned about any un-reviewed
  items first. Use when throughput matters more than 100% review coverage.
- **`none`** — items are reviewable but never block the decision. Use only for
  low-stakes/advisory apps.

The gate is **only enforced when a run actually produces item findings** — an app
with image/doc tools that returns no images/docs for a given record is never
blocked. Reject and Cancel at the record level are never gated (only Apply).

Set it on the AppSpec, e.g. `"item_review_gate": "hard"`. Omit it to get `hard`.

## Rules
- `OCR_ENABLED=true` required (publish gate; the runtime token won't carry the scope otherwise).
- If the app has `image_analyze`/`doc_extract` tools, set `item_review_gate` per the BA (default `hard`).
- One `image_analyze` tool per `task_type`; `name` must be unique in `tools_v2`.
- For a JOINT judgment across several photos + the record (consistency / fraud), the
  agent calls the tool per image, then **synthesises** in its own reasoning before the
  final recommendation — per-image-once must not lose the cross-image join.
- Keep `field_schema` to the fields that actually drive the decision (they become the
  audit trail and, later, the decision-table columns).
- Do NOT author `artifact_role` / `reuse_policy` on these tools. They are **ontology-
  autowired** from the dataset's `sources.json` (per-url-column `artifact_role`) so a
  reuse hit on an `identity` artifact (a headshot/ID legitimately reused by the same
  applicant across cases) reads as verification, not fraud. The builder's fraud
  autowire stamps them; anything you set is overwritten for an ontology-annotated column.
