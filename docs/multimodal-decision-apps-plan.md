<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Multimodal Decision Apps — Image / Document / Record Analysis & Learning

**Status:** PLAN (not yet built) · **Owner:** TBD · **Last updated:** 2026-06-29

## 0. Summary

Today a Citra Decision App reasons over **records** (structured/tabular data) and a
shared **policy library** (RAG). Real applications — an insurance motor-accident
claim, a resume screen, a grievance with attachments — also carry **images** and
**documents** that drive the decision. This plan adds first-class **image analysis**
and **document extraction** as agent tools, a **per-item human-in-the-loop** review,
and a **prompt-rubric learning loop** that improves analysis from officer reasons —
*without* building a heavy multimodal vector store.

Core design choices (the "why" lives in the design discussion that produced this doc):

- **Three modality lanes**, each with its own tool + learning mechanism:
  - **Images** → analyze per image (multimodal LLM) → **structured findings** + recommendation. Learn the **criteria** (a versioned prompt rubric distilled from officer reject-reasons), **not** image vectors. Accident photos are visually unique, so criteria generalize where image-similarity does not.
  - **Documents** (PDF/Word/Excel/scanned) → **extraction tools** with **locate-then-extract** for large files → structured fields + **page citations**. Same rubric-learning per **document type**.
  - **Records** → keep the existing **text-vector similarity** few-shot retrieval (`grounding_refresh.py`).
- **Two feedback levels:** per-item corrections train *perception/extraction*; the final decision override trains *judgment*. **Review-by-exception**, confidence-gated.
- **Learning granularity is per (app, modality, type/task)** — per **type**, never per file instance.
- **Trust routing:** high-value / complex / novel → LLM + human (Decision App). Low-value / high-frequency / in-distribution → a distilled **SLM** (later). The Decision App's structured findings + reasoned overrides are the labeled data that eventually trains and continuously corrects that SLM.

## 1. Goals / Non-goals

**Goals**
1. Agent can **see** images and **extract** from documents, emitting **structured findings** that compose with the record decision.
2. Officers review/override **per item** and **at the final decision**; reasons are captured and turned into learning.
3. Learning for images/docs is a **transparent, versioned rubric** (prompt-criteria), auditable for regulated use.
4. Built apps can **display + download** images/docs (queue, detail, chat).
5. Large documents are handled at **bounded cost** regardless of size.
6. Lay the data foundation for a future **decision-table → distilled SLM** graduation.

**Non-goals (this phase)**
- Training/fine-tuning any model (SLM graduation is a later, separate effort — §8).
- Full multimodal **embedding/retrieval** of images (explicitly avoided; rubric learning replaces it). A narrow perceptual-hash for fraud/dedup is optional (§5.4).
- Cross-org image sharing.

## 2. Current state (what exists today)

| Capability | Where | Status |
|---|---|---|
| Vision tool (`vision_ocr`, OpenAI-compatible / Qwen-VL) | `smart-app-service/models.py:~507`, `ocr_proxy.py`, `config.py` (`ocr_enabled`, `vision_*`) | Exists, **opt-in**, OCR-oriented |
| Agent LLM call | `smart-app-service/runtime.py` `_build_messages` / `_call_llm` | **Text-only content**; image URLs arrive as strings — no multimodal content blocks |
| Signed document URLs | MCP `source-mcp-template/main.py` `/document_url`, `BUCKET_*`, `DOCUMENT_URL_TTL_SECONDS` | Exists; single-path signing |
| RAG policy library | `source-mcp-template/rag/semantic_engine.py`, `ingest_docs.py`, Milvus | Exists (org corpus, persistent) |
| Grounding / few-shot learning | `smart-app-service/grounding_refresh.py`, `GroundingContract` (`models.py:~716`) | **Text-only**; captures `input_fields` values + override corrections; canonical floor `min_canonical` (0 = disabled) |
| Decision + override capture | `smartapp_workflow_staging`, `decision_records`, dry-run replay on Approve | Exists at **decision** level (not per-item) |
| Image/file display | `citra-app-runtime/.../PanelRenderer.tsx` `FileView` (detail/attachment), `DocumentViewPanel` | **Works** in detail/document panels; **gap** in queue cells + chat |
| Column metadata | `ColumnSpec` / `CatalogueColumn` (`source-mcp-template/models.py`, `data-discovery-service/models.py`) | `type` + `semantic_type` only — **no `column_kind` (image/file)** |

## 3. Target architecture

```
                          ┌──────────────────── Decision App (officer UI) ───────────────────┐
 application (claim) ─────▶│  per-item review (accept / reject + reason)  ·  final sign-off   │
   ├─ images   ───▶ IMAGE ANALYSIS tool  ──▶ structured findings/img ─┐                       │
   ├─ docs     ───▶ DOC EXTRACTION tool  ──▶ structured fields + cites ┼─▶ SYNTHESIS ─▶ recommendation
   └─ record   ───▶ RECORD reasoning (text-vector few-shots) ─────────┘                       │
                          └──────────────────────────────────────────────────────────────────┘
        officer reasons ─┬─ per-item  ─▶ IMAGE rubric / DOC rubric (per type, versioned)
                         └─ final     ─▶ RECORD decision learning (few-shots / criteria)
```

Three independent learning channels, all emitting/consuming **structured** data so they
compose into **one decision** with **one audit trail**.

## 4. Component design

### 4.1 Shared foundations (Phase 0)

**`column_kind` on the catalogue column** — so every layer knows "this column is an image/file":
- Add `column_kind: Optional[Literal["plain","url","image_url","document_url","file"]]` (+ optional `mime_hint`) to `ColumnSpec` (`source-mcp-template/models.py`) and `CatalogueColumn` (`data-discovery-service/models.py`).
- Classifier heuristic: name contains photo/image/scan/doc/attachment + string type → infer kind; overridable in `sources.json`.
- Backfill demo: mark `evidence_photo_url` etc. in `demo-data/tenants/acme-power/mcp/sources.json`.

**Structured Finding contract** (the per-item analysis output) — new pydantic model in `smart-app-service/models.py`:
```python
class ItemFinding(BaseModel):
    item_id: str                 # image_id / doc_id / row key
    item_type: str               # e.g. "accident_photo" | "police_report" | "estimate"
    modality: Literal["image","document"]
    fields: Dict[str, Any]       # extracted/assessed structured values
    recommendation: Optional[str]
    confidence: float            # 0..1 — drives review-by-exception + routing
    rationale: str               # human-readable "what I saw / why"
    citations: List[Dict]        # {page:int} | {bbox:[...]} | {source_url:str}
    rubric_version: Optional[str]
```

**Rubric store** — new Mongo collection `smartapp_analysis_rubrics` (env-routed like the other smartapp collections):
```
{ tenant_id, app_slug, modality, task_type,        # the bucket key
  version, status: "draft|active|retired",
  criteria: [ {id, text, source_correction_ids[], added_at, added_by} ],
  updated_at, approved_by }
```
Indexed unique on `(tenant_id, app_slug, modality, task_type, version)`.

### 4.2 Document extraction tool (Phase 1)

A new agent tool `doc_extract` (registered in `tools_v2`), backed by an extraction
service. **Locate-then-extract**, bounded cost regardless of size:

1. **Parse, type-aware.** Native-text PDF/Word → text directly; **OCR only scanned pages** (reuse `ocr_proxy`); Excel/tables → parse as **structured rows**, not prose.
2. **Structure-aware chunk** by page/section; keep tables as tables; tag each chunk with `{page|sheet|section}`.
3. **Per-document ephemeral index** (in-memory or a short-lived Milvus collection, keyed by case+doc) — **never** the org RAG.
4. **Two modes:** *targeted* (schema-guided locate → extract specific fields; flat cost) for the common case; *whole-doc* (map-reduce per section → synthesize) only when a task needs everything.
5. **Output** `ItemFinding` with `fields` + **page citations** + confidence; **cache** (extract once per doc; re-asks reuse).

Files: new `smart-app-service/doc_extract.py` (+ tool def in `models.py`, wiring in `runtime.py` tool loop); reuse `ocr_proxy.py`.

### 4.3 Image analysis tool (Phase 2)

Tool `image_analyze` (per image, **analyze once**, cache):
1. Resolve the image column → **sign** the S3 URL (batch endpoint, §4.5) → fetch.
2. Send to the **multimodal model** with the relevant **rubric** (§4.4) as the criteria checklist. Either (a) as a tool that returns an `ItemFinding`, or (b) inject **multimodal content blocks** into the main agent call by extending `runtime.py` `_build_messages`/`_call_llm` to emit `{"type":"image_url",...}` blocks for `column_kind=image_url` inputs (closes the "agent sees only the URL string" gap).
3. Emit **`ItemFinding`** per image: `{severity, parts_affected, airbag, consistency_with_claim, …}` + confidence + rationale + citation (image ref).

### 4.4 Prompt-rubric learning loop (Phase 3) — the heart

> **Standing standard = the LIVE SOP, fetched by the TOOL and cached — NOT a stored seed,
> NOT passed by the agent.** The builder sets **`sop_source`** (a RAG source id) on the
> image/doc tool; at call time the tool fetches the SOP from that source **server-side and
> CACHES it per (app, task_type)**, so N items (e.g. 10 photos of one claim) share ONE
> fetch and the agent never carries the SOP (no per-image re-emit / context bloat). It's
> applied above the learned rubric; if `sop_source` is set but the fetch is empty the tool
> fails loud (`sop_unavailable`). **No `seed_criteria` are stored in Mongo** — a seeded copy
> would go stale and adds build/refresh plumbing for no gain. The rubric in Mongo holds
> **only the LEARNED layer** (officer corrections); the SOP supplies the standing standard
> fresh (cache TTL) and the learned rubric refines it on top. Prompt assembly at runtime:
> **case brief (`query`) + cached SOP + learned `summary`.** (This supersedes both the
> earlier "seed at publish" idea and the interim "agent passes `sop`" idea —
> `seed_rubric()` stays unused.)

- **Capture per-item feedback.** Extend the Decision App UI + staging model to record accept/reject **per `ItemFinding`** with a reason (today override capture is decision-level only). Route each correction to its bucket `(app, modality, task_type)`.
- **Consolidate, don't append.** A scheduled distillation job feeds the bucket's accumulated corrections to an LLM: *"update the rubric for `accident-damage-assessment` given these N officer corrections; dedupe, resolve conflicts, keep it coherent."* Produces a new **draft rubric version**.
- **Govern.** A human approves draft → `active`; versions are retained (audit: who/when/why each criterion). The rubric is **human-readable policy** — a regulated-use advantage over opaque weights/vectors.
- **Retrieve the relevant subset.** As rubrics grow, key criteria by `task_type` / case attributes and pull only the relevant slice into the analysis prompt (light **text** retrieval over the *rules* — reuses the records-lane vector tech, never image vectors). Avoids prompt bloat / "lost-in-the-middle."

### 4.5 Synthesis, display, fraud (Phase 2/4)

- **Synthesis step:** per-item findings → one pass over all findings + the record → **overall recommendation** (this is where cross-image consistency / fraud signals surface; per-image-once must not lose the join).
- **Runtime display (Phase 4):** presign endpoint for queue/detail **image columns**; render thumbnails in `FileChip`; auto-sign raw S3 keys in detail `FileView`; render chat-evidence images. Files: `citra-app-runtime/.../PanelRenderer.tsx`, new `api/image/[slug]/[panelId]` route; optional `render_hint` + `image_column` on panel models.
- **Fraud/dedup (optional):** cheap **perceptual hash** to flag the *same photo reused* across claims. Narrow, orthogonal to the judgment; the only place image-similarity earns its keep.

### 4.6 `task_type` — the rubric bucket key (one tool, many domains)

`modality` ("image" / "document") is **not** the unit of learning. The unit is
**`task_type`** — the *semantic role* of the item. The same `image_analyze` tool and the
same multimodal model serve every domain; only the **rubric** (criteria) and the
**finding-schema** (`ItemFinding.fields`) differ per `task_type`.

```
modality = image
  ├─ task_type = "motor-accident-damage"      → rubric A, fields {severity, parts_affected, airbag, consistency}
  ├─ task_type = "machine-inspection-defect"  → rubric B, fields {defect_type, wear_level, safety_critical, part_id}
  └─ task_type = "odometer-reading"           → rubric C, fields {reading_km, plausible}
modality = document
  ├─ task_type = "police-report"   → rubric D, fields {fir_no, accident_date, fault_party}
  ├─ task_type = "repair-estimate" → rubric E, fields {line_items[], total, labour}
  └─ task_type = "resume"          → rubric F, fields {name, skills[], years_exp}
```

Consequences:
- A motor-claim photo correction trains **rubric A only**; a machine-defect correction trains **rubric B only** — they never cross-contaminate, even within the same platform.
- **One app can declare several `task_type`s** across both modalities. A *Motor Claim Co-pilot* might have image `task_type`s `[motor-accident-damage, odometer-reading]`, doc `task_type`s `[police-report, RC-book, repair-estimate]`, plus the record decision. A *Machine Inspection Co-pilot* reuses the identical tooling with image `task_type=machine-inspection-defect` and doc `task_type=maintenance-log` — **no new code**, just new rubrics + field schemas.
- **Rubric store key is `(tenant_id, app_slug, modality, task_type)`** (already in §4.1). `ItemFinding.item_type == task_type`, so feedback routes to the correct bucket automatically.

**Assigning `task_type` at runtime** (two paths, prefer the first):
1. **Declared by the builder** — each image/doc column, source, or upload field is mapped to a `task_type` in the app/panel/data-source config. Reliable, explicit, no extra model call. Default.
2. **Routed by a cheap classifier** — only when items arrive as an *unstructured bundle* (a folder of mixed photos/scans): a lightweight first-pass labels each item's `task_type`, then dispatches to the right rubric+schema. Use only where declaration isn't possible.

The **field schema per `task_type`** should be authored alongside the rubric (builder
artifact). The structured `fields` are what feed the record decision, the audit trail,
and — later — the decision table for that specific `task_type`'s SLM.

## 5. Two-level feedback & granularity (must-read)

- **Per-item** (each image / each doc extraction): accept/reject → trains that **modality+type rubric** ("misread damage as cosmetic" → damage rubric; "pulled wrong total" → estimate-extraction rubric).
- **Final decision** override → trains **record/decision** learning.
- **Granularity is per TYPE, not per file.** Rejecting a finding on resume #5000 improves the *"resume extraction" rubric* → helps resume #5001 (a different file).
- **Review-by-exception:** officers don't hand-approve every photo/doc; AI flags low-confidence items, default-accept the rest, one **accountable sign-off** on the final money decision. Corrections still captured per item-type.

## 6. Trust / cost routing

Optimize **expected value**, not per-decision cost. On a high-value claim, `₹5` of LLM
processing is noise vs the amount-at-risk; the SLM's `₹1` only matters at high volume of
low-value, in-distribution decisions.

| Segment | Engine |
|---|---|
| High value / low freq / complex / novel | LLM + image/doc tools + **human sign-off** (Decision App) |
| Low value / high freq / repetitive / in-distribution | distilled **SLM**, auto/background, **escalate** low-confidence + high-value |
| Middle | SLM proposes, human reviews exceptions |

SLMs **cannot be trusted for high-amount claims** because those are the tail (out-of-distribution), error cost is unbounded/asymmetric, accountability is required, and a model's *predicted* reason ≠ a *defensible case-specific* one. So the SLM gets the pen only where the downside is bounded.

## 7. Data model & API summary

- **New models:** `ItemFinding`, rubric documents (`smartapp_analysis_rubrics`), `column_kind`/`mime_hint` on column specs.
- **New endpoints:**
  - MCP: `POST /document_urls` (batch sign).
  - smart-app-service: `POST /apps/{slug}/items/{item_id}/feedback` (per-item accept/reject + reason); `doc_extract` / `image_analyze` tool execution.
  - runtime: `POST /api/image/{slug}/{panelId}` (presign for display).
- **Extended:** agent `_build_messages`/`_call_llm` for multimodal content blocks; staging row to carry per-item findings + per-item decisions.

## 8. Future: decision-table → SLM graduation (separate effort)

Once the Decision App has accumulated enough **structured findings + reasoned overrides**:
1. Image/doc tools already emit **structured features** → assemble a **decision table** (features → human decision).
2. **Distill a small fine-tuned VLM** (reasoning, not just a classifier) from LLM+officer-validated rows.
3. **Tier:** SLM auto-handles the stable head; LLM+human handles tail/novel/high-value; **retrieval/rubric stays on top** for *fresh* overrides between retrains (a fine-tuned model only learns at retrain time).
4. Overrides keep flowing into **both** the rubric/retrieval (instant) and the next SLM training set (periodic). The cheap loop built now *manufactures* the labeled data the SLM needs.

## 9. Phased delivery

| Phase | Deliverable | Key files |
|---|---|---|
| **0 Foundations** | `column_kind` + classifier; `ItemFinding` model; rubric collection + CRUD | `source-mcp-template/models.py`, `data-discovery-service/models.py`, `smart-app-service/models.py`, sources.json |
| **1 Doc extraction** | `doc_extract` tool: parse/OCR → chunk → ephemeral index → locate-then-extract → structured fields + citations + cache | new `smart-app-service/doc_extract.py`, `ocr_proxy.py`, `runtime.py` |
| **2 Image analysis + synthesis** | `image_analyze` per-image findings; multimodal content blocks; cross-item synthesis | `runtime.py`, `models.py`, MCP `/document_urls` |
| **3 Rubric learning** | per-item feedback capture + routing; consolidation job; versioned/governed rubric; relevant-subset retrieval | Decision App UI, staging model, new consolidation worker |
| **4 Runtime display** | queue/detail image thumbnails + presign; chat-evidence images | `citra-app-runtime/.../PanelRenderer.tsx`, new api route, panel models |
| **5 (future) SLM graduation** | decision-table assembly; distillation; tiered routing | separate plan |

## 10. Risks & mitigations

- **Rubric bloat / conflicting rules** → consolidation (not append), versioning, relevant-subset retrieval, human approval.
- **Multimodal cost on high volume** → confidence-gated escalation; SLM for the head (Phase 5); analyze/extract **once** + cache.
- **Lossy per-item analysis** → require **structured findings + citations**; cross-item synthesis catches inconsistencies a per-image-once view misses.
- **Large-doc context blow-up** → locate-then-extract / map-reduce; never dump full text.
- **Image access / signing** → batch presign, short TTL, runtime proxy; never expose raw bucket to the browser.
- **Auditability (regulated)** → rubric is versioned human-readable policy; every decision carries findings + citations + reasons.

## 11. Open questions

1. Multimodal blocks **in the main agent call** vs a **vision tool** returning findings — pick per cost/latency; likely tool-by-default, inline-blocks for the highest-value apps.
2. Ephemeral per-document index: in-memory vs short-lived Milvus collection (cleanup policy).
3. Rubric consolidation cadence + who approves (per-app admin vs central).
4. Per-item feedback UX density — how much to surface vs auto-accept by confidence threshold (per-app config).
5. Which models: multimodal reasoning model + (later) the small VLM to distill.

---

## Appendix A — Worked example: **Motor Claim Co-pilot**

**Decision:** for each claim, recommend `approve | investigate | reject` + a payout band,
with rationale. Officer signs off (accountable on the money decision).

**Declared items** (builder maps each source/column/upload → `modality` + `task_type`):

| modality | task_type | source mapping |
|---|---|---|
| image | `motor-accident-damage` | `claim_photos` where `photo_type='damage'` (S3 url col) |
| image | `odometer-reading` | `claim_photos` where `photo_type='odometer'` |
| document | `police-report` | upload field `fir_pdf` / `claim_docs.police_report` |
| document | `repair-estimate` | upload field `estimate` / `claim_docs.estimate` |
| document | `rc-book` | `claim_docs.rc` |
| record | `claim-decision` | `claims` row + policy (RAG policy library: coverage, exclusions, IDV) |

**Field schemas** (`ItemFinding.fields` per `task_type`):
```yaml
motor-accident-damage:   { severity: enum[none,minor,moderate,major,total],
                           parts_affected: [string], airbag_deployed: bool,
                           structural: bool, consistency_with_claim: enum[match,partial,mismatch] }
odometer-reading:        { reading_km: int, plausible_vs_policy: bool }
police-report:           { fir_no: string, accident_date: date, fault_party: enum[insured,third_party,unknown] }
repair-estimate:         { line_items: [{part,amount}], total: number, labour: number, padding_suspected: bool }
rc-book:                 { reg_no: string, owner_matches_policy: bool, vehicle_class: string }
```

**Seed rubrics** (expert-authored v1; grow from officer reasons):
```text
motor-accident-damage (rubric A, v1):
  - Airbag deployment ⇒ treat as at least 'major'; flag possible total loss.
  - A bumper/fender crack with no panel deformation is 'cosmetic', not 'structural'.
  - If visible damage does not match the claimed point of impact ⇒ consistency='mismatch' → investigate.
  - Rust/oxidation at the damage edge ⇒ pre-existing damage signal → investigate.
repair-estimate (rubric E, v1):
  - Line items not visible in any damage photo ⇒ padding_suspected=true.
  - Labour > 60% of parts on a cosmetic claim ⇒ flag.
```

**Synthesis → decision:** cross-check *damage severity & consistency* (images) × *fault &
date* (police-report) × *estimate total & padding* (estimate) × *coverage/IDV/exclusions*
(record + policy RAG). Output `{recommendation, payout_band, confidence, rationale,
citations}`. Low confidence or high amount → human review.

**Learning event (example):** officer rejects AI `severity=major` with reason *"front
bumper crack is cosmetic; no chassis involvement."* → routes to **rubric A** → consolidation
adds/strengthens the "cosmetic vs structural" criterion → v2 (human-approved). Next similar
photo on a *different* claim now reads it correctly. The **estimate** and **record** lanes are untouched.

---

## Appendix B — Worked example: **Machine Inspection Co-pilot**

Same platform tooling, different `task_type`s + rubrics — **no new code**.

**Decision:** for each asset inspection, recommend `pass | schedule-maintenance |
fail-safety-critical` + action, with rationale. Engineer signs off.

**Declared items:**

| modality | task_type | source mapping |
|---|---|---|
| image | `machine-inspection-defect` | `inspection_photos` (S3 url col) |
| image | `thermal-scan` | `inspection_photos` where `kind='ir'` (optional) |
| image | `nameplate-reading` | `inspection_photos` where `kind='nameplate'` |
| document | `maintenance-log` | `asset_docs.maint_log` (PDF/Excel) |
| document | `spec-sheet` | `asset_docs.spec` (thresholds) |
| record | `inspection-decision` | `assets` row + latest sensor/SCADA readings + spec thresholds (RAG: SOPs/standards) |

**Field schemas:**
```yaml
machine-inspection-defect: { defect_type: enum[crack,corrosion,oil_leak,overheat,wear,none],
                             wear_level: enum[low,medium,high], safety_critical: bool,
                             location: string }
thermal-scan:              { max_temp_c: number, hotspot: bool, exceeds_threshold: bool }
nameplate-reading:         { asset_id: string, rating: string, matches_master: bool }
maintenance-log:           { last_service: date, overdue: bool, recurring_fault: enum[yes,no] }
spec-sheet:                { max_temp_c: number, service_interval_days: int }
```

**Seed rubrics:**
```text
machine-inspection-defect (rubric B, v1):
  - Any visible crack on a load-bearing/rotating part ⇒ safety_critical=true → fail.
  - Oil leak + discoloration around a bushing ⇒ overheat risk → schedule-maintenance.
  - Surface rust without pitting ⇒ wear_level='low'; pitting/flaking ⇒ 'high'.
thermal-scan (rubric, v1):
  - hotspot > spec max_temp_c ⇒ exceeds_threshold=true → fail-safety-critical.
```

**Synthesis → decision:** *defect_type & safety_critical* (images) × *hotspot vs spec*
(thermal × spec-sheet) × *overdue/recurring* (maintenance-log) × *sensor readings vs
thresholds* (record). Output `{recommendation, action, confidence, rationale, citations}`.

**Learning event (example):** engineer overrides AI `pass` → `schedule-maintenance` with
reason *"hairline crack near the weld seam — early fatigue, not surface scratch."* → routes
to **rubric B** → adds "weld-seam hairline ⇒ fatigue, not cosmetic" criterion → v2. The
thermal and record lanes are unaffected.

---

**What the two appendices demonstrate:** identical platform machinery (`image_analyze`,
`doc_extract`, the rubric store keyed by `(tenant, app, modality, task_type)`, the
two-level feedback). The entire domain difference between *insurance claims* and *machine
inspection* lives in **declared task_types + per-task field schemas + seed rubrics** —
authored by the builder, then improved by officer/engineer reasons. That is the reuse
thesis of this plan.
