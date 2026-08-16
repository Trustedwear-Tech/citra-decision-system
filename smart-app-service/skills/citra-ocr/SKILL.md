---
name: citra-ocr
description: Vision / OCR design rules for AgentSpec tools_v2 — when to declare vision_ocr and how the runtime calls it
metadata:
  category: citra
  tools: [bash]
---

# Citra OCR (vision_ocr tool)

> ⚠️ **STOP — does the image/document DRIVE A DECISION?** If the goal is to assess /
> grade / judge a photo, or extract fields from a document and ACT on them (recommend,
> route, approve, pass/fail), use **`image_analyze`** / **`doc_extract`** (structured
> `ItemFinding` + per-item review + rubric learning), NOT `vision_ocr`. See
> `citra-agent-spec/references/image-analyze.md`. `vision_ocr` is ONLY for raw text the
> agent reads itself, with no per-item review or learning.

## Purpose

Design rules for the rare case where you need RAW TEXT off an uploaded image/document and the agent reasons over the text itself — and for the upload-form OCR gate. (For damage shots / reports / IDs that drive a decision, see the banner above.)

The runtime LLM does **not** OCR images by itself. It calls a tool. That tool is `vision_ocr`, and it is served by `smart-app-service` over the internal proxy.

## When to declare `vision_ocr` in `tools_v2[]`

Declare it when, **and only when**, ALL of these hold:

1. `OCR_ENABLED=true` in the builder env.
2. The app has a `FormPanel` with `accepts_files=true` (image/PDF uploads), OR a panel that surfaces image content for inspection.
3. The agent's decision actually depends on text or content extracted from the image. (If the image is just a thumbnail attached to a record, you don't need OCR.)

If `OCR_ENABLED=false`, you **cannot** declare `vision_ocr`. The publish endpoint will reject the AgentSpec with `code=ocr_not_configured`. Either drop the upload requirement or surface the gap in `requirements_unmet`.

## Cost gate — design the agent to avoid wasted OCR calls

Vision tokens are expensive. Always have the agent run cheap checks **first** and short-circuit.

The mandatory pattern:

1. User submits the form + uploads an image.
2. Agent calls `validate_form` (free, deterministic, local).
3. If `validate_form` returns `ok=false` → agent rejects the submission immediately, returns the list of missing/invalid fields. **No OCR happens.**
4. If `validate_form` returns `ok=true` → agent calls `vision_ocr` on the upload(s).
5. Agent cross-references the OCR output against enterprise data (`mcp.*`, `rag.*`).
6. Agent emits the final decision / next action.

This sequence is enforced by:

- A Pydantic cross-validator: `validate_form` must come **before** `vision_ocr` in `tools_v2[]`.
- A regex check: the agent's `system_prompt` must mention `validate_form` (case-insensitive substring).

## The system-prompt template

Drop this block into the agent's `system_prompt` and customise the domain-specific bits:

```
You are <role>. The user has submitted <FormPanel.title> and may have
uploaded supporting documents.

Tooling rules — follow strictly:

1. ALWAYS call validate_form FIRST with the user's submitted form data.
   If it returns ok=false, respond with a friendly rejection that lists
   the missing/invalid fields. Do NOT call vision_ocr or any other tool
   in that case — we save the user time and we save token cost.

2. Only after validate_form returns ok=true, call vision_ocr on each
   uploaded image to extract its text/structure.

3. Then consult enterprise data via the mcp.* and rag.* tools to
   cross-reference policy / history / SOPs.

4. Emit your final decision in the response schema below.
```

> **OCR ≠ storage — the #1 file pitfall.** `vision_ocr` only **reads** the upload (transient, to extract text); it does **NOT** persist the file. If the same upload must also be **stored on the record**, that is the separate `format:"file"` store path (see `citra-app-spec/references/forms-and-files`): name the form's file field to **match the write column** and have the agent **pass the uploaded file straight through to that column** — the platform stores the blob and fills a downloadable link. **NEVER** add a step like *"set `<col>` to the uploaded file's URL"*: the agent has no real URL, will fabricate one (e.g. `s3://…`), and the stored value won't download. When the write has a file/document column, add this step to the template: *"Pass the uploaded `<file_field>` straight through to `<write_column>` unchanged — the platform stores it and records the link; do NOT construct or set a URL."*

## What you write in `agent_spec.tools_v2[]`

Minimal example:

```json
{
  "tools_v2": [
    {"kind": "validate_form", "name": "validate_form", "schema_ref": "claim_form"},
    {"kind": "vision_ocr",    "name": "vision_ocr"}
  ]
}
```

The `validate_form` → `vision_ocr` ordering shown above is the canonical OCR pattern — apply it to any document-extraction goal (claim forms, meter photos, invoices): gate the costly vision call behind the free deterministic form-completeness check.

## What you do NOT do

- You do **not** include the `VISION_API_KEY` in any spec, env file, or skill output.
- You do **not** call `{SMART_APP_PROXY_BASE_URL}/ocr` from a skill. The runtime calls it; the builder declares the tool.
- You do **not** invent OCR options ("use Tesseract instead"). If the deployment's `OCR_ENABLED=false`, the answer is `requirements_unmet`.
