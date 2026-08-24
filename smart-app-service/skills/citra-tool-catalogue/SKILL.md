---
name: citra-tool-catalogue
description: Read the runtime tool catalogue and decide which tools to declare in agent_spec.tools_v2
metadata:
  category: citra
  tools: [bash]
---
<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra Tool Catalogue

## Purpose

Tell the builder agent **which tools the runtime LLM is allowed to call** in the BA's tenant, and how to translate that into entries in `agent_spec.tools_v2[]`.

The runtime LLM is agentic. The AppSpec + AgentSpec wire it up; the AgentSpec's `tools_v2[]` is its tool palette. This skill is how you discover that palette.

## When to use

- Once, at the **start of Phase 2** (App path only). Read it before drafting `agent_spec.json`.
- Re-read whenever the BA's goal changes scope (e.g. they suddenly mention "and also OCR the policy PDF").

## How to read the catalogue

```bash
echo "$TOOL_CATALOGUE" | jq .
```

Each entry has at minimum `name`, `kind`, and `description`. Optional fields: `args_schema`, `returns`, `endpoint`.

The `kind` values you can declare in `agent_spec.tools_v2[]`:

| `kind` | What it does | Required fields in tools_v2 |
|---|---|---|
| `validate_form` | Local, deterministic form-completeness check. No LLM, no network, free. | `schema_ref` (FormPanel.id or .schema_ref) |
| `vision_ocr` | **RAW TEXT** OCR off an image/PDF (no structure, no review, no learning) — the agent reads the text and reasons itself. For a form-upload field, or the rare case you only need the text. `OCR_ENABLED=true`. | (none beyond name/description) |
| `image_analyze` | **STRUCTURED per-image JUDGMENT** against a learned rubric → an `ItemFinding` (your `field_schema` fields + recommendation + confidence) the officer reviews PER image; reject-reasons train the `task_type`'s rubric. **The DEFAULT for "assess / grade / judge a photo" decision apps — use this, NOT `vision_ocr`.** `OCR_ENABLED=true`. | `task_type`, `field_schema`; record-bind with `data_source_id` + `url_column` + `key_field` (agent passes a short `record_id`, never a signed URL) |
| `doc_extract` | **STRUCTURED field extraction from a DOCUMENT** (text PDF, scanned PDF, or doc image) against a learned rubric → an `ItemFinding`, reviewed PER document. **The DEFAULT for "extract fields from a report / invoice / ID and act on them" — use this, NOT `vision_ocr`.** `OCR_ENABLED=true`. | `task_type`, `field_schema`, optional `model_tier`; record-bind with `data_source_id` + `url_column` + `key_field` |
| `consistency_check` | **Deterministic record↔artifact CROSS-CHECK + entity linking + artifact fingerprinting** — fraud/consistency screen. No LLM, free. Compares the record's CLAIMED values vs values EXTRACTED by `doc_extract`/`image_analyze` (normalized per type), runs format+checksum validators (PAN, IFSC, GSTIN, VIN, Aadhaar, email, phone) + optional invoice arithmetic, AND links the case's high-precision identifiers into the cross-case entity index → `mismatches[]` + `entity_signals[]` (same phone/VIN/invoice-no on other cases = ring/double-dip evidence). **For DUPLICATE / REUSED photo or PDF detection you MUST record-bind it** (`data_source_id` + `url_columns` + `key_field` — same contract as `image_analyze`, but plural `url_columns`): the tool then resolves each artifact URL via the dept-MCP itself, downloads the bytes, and returns `artifact_findings[]` (SHA-256 exact-dup + dHash near-dup + metadata flags vs prior records) — no vision tool needed. WITHOUT the binding it does field/entity checks only and CANNOT see artifacts. When the source ontology declares `fraud_screening.incident_date_field` / `location_lat_field`+`location_lon_field`, the autowire also stamps `claim_context` and the tool ADDITIONALLY compares each evidence photo's EXIF against the record's CLAIMED incident date / site GPS (read server-side by key — never agent-supplied) → `exif_findings[]` (photo predates the incident / taken beyond `gps_radius_km` from the site / camera-model flip). The ontology can ALSO arm (all autowired, all read server-side by key): **`payment_proof`** — the reference/amount/date/party extracted from the PINNED receipt document (`artifact_role: payment_proof` column ONLY, never another attached bill) is verified against the declared payment ledger → `payment_findings[]` + `payment_verified` (ref-not-found is fact-grade; a full match is VERIFICATION that clears honest disputes); **`verify_against[]`** — the same shape pointed at ANY declared dataset (registry, asset master, estimate) → `verify_findings[]` + per-check `verifications[]`; **`date_rules[]`** — declarative date arithmetic on the record's own values (claim-days-after-policy-start, inspection-before-work-order, stale statements) → `date_rule_findings[]`; plus a `locale`/`domain` stamp (country decides which ID validators run and how ambiguous dates parse). The agent may ALSO pass extracted bank-statement rows as **`statement_rows`** → the running balance is reconciled row-by-row → `statement_findings[]` (fires only on repeated chain breaks; single breaks = OCR noise). Do NOT author `claim_context`, `payment_proof`, `verify_against`, `date_rules`, `locale`, `domain`, or `dataset_kind` — ontology-autowired only (autowire overwrites them on every publish). Agent MUST pass `record_id` (the case key, matching `key_field`). Never auto-rejects. | optional `field_types` (pin a field's type), `link_entities` (default true); for artifact dedup: `data_source_id` + `url_columns` (e.g. `["defect_photo_url"]`) + `key_field`; runtime arg `statement_rows` for extracted bank statements |
| `fraud_synthesis` | **T3 GATED fraud cross-examination.** Agent calls it LAST with `record_id` + case `context` + ALL screening `signals` (consistency_check output + every finding's `artifact_flags`). The TOOL gates server-side: below the severity gate → instant zero-cost return; at/above (or ~5% audit sample) → ONE reasoning pass weighing the signals together against the learned fraud CASE rubric → `{fraud_risk, key_indicators, benign_explanations, recommended_checks}`. Officer EVIDENCE, never a verdict. | optional `model_tier` (default large), `gate_min_points` (default 2), `sample_rate` (default 0.05) |
| `mcp` | **Read** a dept-MCP tool — enterprise data lookup / query. | `source_id`, `tool_name`, optional `input_schema_ref` |
| `mcp_action` | **Write** a dept-MCP record — an UPDATE / INSERT that changes record state (route a grievance, flag a record, record a verdict, approve a request). Bound to a catalogue `write_actions[]` entry. | `source_id`, `dataset_id`, `action_id`, `input_schema` |
| `rag` | Search a semantic (RAG) corpus. **Answered by the Citra platform reader (Milvus direct), NOT the dept-MCP** — semantic sources are short-circuited (the dept-MCP serves zero RAG). Bind a source whose catalogue `source_type` is `semantic`. At runtime the agent passes `query` for top-k passages, or `doc_path` (from a prior chunk's `metadata.doc_path`) to read an ENTIRE document in order. | `source_id`, optional `top_k` (default 8), optional `classification_max` |
| `llm` | Sub-agent style nested LLM call with its own system prompt. | `system_prompt`, optional `model_tier` ∈ `large`/`medium`/`small` (default `large` — see citra-agent-spec) |
| `code_exec` | Sandboxed Python: compute (PnL, FIFO, reconciliation) or generate a downloadable file (PDF / XLSX / DOCX / PPTX / CSV / JSON / PNG). The runtime LLM authors the script per a prescription you put in `agent_spec.system_prompt`. See `citra-code-exec/SKILL.md`. | (none beyond name/description; optional `timeout_seconds`, `allowed_outputs`) |
| `neighbor_samples` | Ground the agent in **decided** past cases — `mode:"canonical"` (always-on baseline) + `mode:"neighbors"` (per-case similar). Only when the app is grounded in history. See `citra-fewshot-from-history/SKILL.md`. | `collection` (`Historical_Refresh`), `mode`, optional `top_k` |

## Decision rules

1. **Form panel ⇒ `validate_form` is mandatory.** No exceptions. This is the cost gate.
2. **Form panel with `accepts_files=true` ⇒ `vision_ocr` is mandatory** (when `OCR_ENABLED=true`). If `OCR_ENABLED=false`, you cannot accept uploads — either drop the upload requirement, or push the goal to `requirements_unmet` and explain.
2a. **Image/document that DRIVES a DECISION ⇒ `image_analyze` / `doc_extract`, NOT `vision_ocr`.** If the goal is to assess / grade / judge a photo, or extract fields from a document AND act on them (recommend, route, approve, pass/fail), declare a STRUCTURED analysis tool **per `task_type`** — an image → `image_analyze`, a document → `doc_extract` — record-bound (`data_source_id` + `url_column` + `key_field`). They return an `ItemFinding` the officer reviews per item, and reject-reasons train the rubric. `vision_ocr` returns RAW TEXT with no review or learning — only use it when the agent genuinely needs just the text. Then set `item_review_gate` on the AppSpec. See `citra-agent-spec/references/image-analyze.md`.
2b. **Money/asset disposition + identity + artifacts ⇒ PROPOSE `consistency_check`.** When the app decides a disposition over money or asset value (approve/reject/pay/settle a claim, loan, reimbursement, KYC) AND the case carries identity fields plus supporting documents/images — PROPOSE a "Fraud & Consistency Screening" step to the BA (never add it silently, and never for simple routing/FAQ/status apps): declare `consistency_check`, and instruct the agent (in `system_prompt`) to (1) read the record FIRST, (2) pass full case context in every `image_analyze`/`doc_extract` `query`, (3) cross-check claimed vs extracted values with `consistency_check`, and (4) cite every mismatch/`artifact_flags`/`exif_findings` signal (duplicate artifact, metadata anomaly, photo-predates-incident, GPS-far-from-site) as EVIDENCE in the recommendation. Screening is evidence for the officer — it must never auto-reject. See `docs/fraud-detection-primitives-plan.md`.
3. **Multi-source goals ⇒ MCP / RAG tools.** If the agent must consult enterprise records (policies, claim history, customer ledger) declare the relevant `mcp.*` and/or `rag.*` tools so the LLM can call them at decision time.
3a. **The action *changes* a record ⇒ `mcp_action` is mandatory.** If the goal is to route / triage / flag / approve / record-a-verdict on each record — i.e. the BA expects the record's *state to change* — a read-only agent is a half-built app: it recommends but the record never moves and the queue never clears. Declare an `mcp_action` write tool bound to the matching `write_actions[]` entry from the discovery catalogue (`citra-mcp-discover` records these — `id`, `dataset_id`, `input_schema`, copied **verbatim**, never invented). Then the `agent_spec.system_prompt` MUST instruct the agent to call it once the decision is final. See `citra-agent-spec/SKILL.md` → "`mcp_action` — write tools".
4. **Side-effects ⇒ a catalogued `mcp_action` write.** Sending letters, triggering payouts, opening tickets — model each as an `mcp_action` the dept-MCP exposes (it's audited + governed by plan-then-apply). SmartApps do not call workflows.
5. **Compute / report drafting / file generation ⇒ `code_exec`.** PnL on a trade book, FIFO matching, drafting a PDF claim report, building an Excel template — anything that needs Python + libraries (pandas, reportlab, python-docx, etc.) and produces a downloadable artefact. Read `citra-code-exec/SKILL.md` for the prescription pattern. **Do not** use it for inline charts (use `ChartPanel`) or single-field calculations (use `FormPanel`).
6. **Don't add tools "just in case".** The runtime sends the tool catalogue to the LLM on every turn; extra tools = more tokens + worse decisions. Cut to what the goal actually needs.

## Order constraint

Within `tools_v2[]`, list `validate_form` **before** `vision_ocr`. The Pydantic cross-validator enforces this and it documents intent.

## What you do NOT do here

- You do **not** call the proxy at `SMART_APP_PROXY_BASE_URL` from the builder pod. That endpoint is for the runtime engine only. The builder's job is to declare tools in JSON; the runtime invokes them.
- You do **not** mint or read `SMART_APP_INTERNAL_SECRET` from your skill code. The runtime engine receives its own secret at app-launch time.
- You do **not** fabricate a tool that isn't in `TOOL_CATALOGUE`. If the BA's goal needs something missing, surface it via `app_spec.requirements_unmet[]`.

## Quick checklist before leaving Phase 2

- [ ] Read `TOOL_CATALOGUE` and decided exactly which kinds you need.
- [ ] Every FormPanel maps to a `validate_form` tool.
- [ ] Every upload-accepting FormPanel has `vision_ocr` declared (assuming `OCR_ENABLED=true`).
- [ ] System prompt explicitly tells the LLM to call `validate_form` first and reject incomplete submissions.
- [ ] No tool listed that isn't in `TOOL_CATALOGUE`.
