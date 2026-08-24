---
name: citra-code-exec
description: Add a code_exec tool when the smart-app needs to compute or generate downloadable files (PDF/XLSX/DOCX/PPTX/CSV/JSON/PNG)
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

# Citra Code-Exec Tool

## Purpose
Wire a `tools_v2[].kind="code_exec"` entry when the BA's goal involves
**computation** (PnL, FIFO matching, aggregations, reconciliation) or
**file generation** (drafting a PDF report, building an Excel template,
creating a PPTX deck, exporting CSV).

The BA does NOT write Python. The BA writes a **prescription** in the
agent's `system_prompt` describing what kind of script the runtime LLM
should author at call time. The sandbox runs the script and uploads the
output file(s) to S3 with presigned download URLs.

## When to Use
- "Approve this claim and draft a 4-page PDF report" → code_exec
- "Compute realised PnL on this trade book" → code_exec
- "Convert this batch of invoices into one Excel" → code_exec
- "Generate a presentation deck from these inputs" → code_exec

## When NOT to Use
- Inline charts on a panel → use ChartPanel + chartjs spec, not code_exec.
- A single field calculation a FormPanel can do → use FormPanel
  computed fields.
- Pulling data from an enterprise system → use `kind=mcp` first; pipe
  the result *through* code_exec only if post-processing is needed.

## Authoring the `tools_v2[]` entry
```jsonc
{
  "name": "draft_claim_report",         // any snake_case label
  "kind": "code_exec",
  "description": "Draft a multi-page PDF claim report. Required inputs come from the form context (claim_id, policy_number, claim_amount, photos[]). Returns {output_files:[{filename,download_url}]}. Mention the URL in the reply so the user can download.",
  "timeout_seconds": 90,
  "allowed_outputs": ["pdf"]
}
```

`description` MUST tell the runtime LLM:
1. What inputs to use from the panel/form context.
2. What output filename pattern to choose.
3. To echo the `download_url` back in the reply.

## Authoring the prescription in `system_prompt`
Add a section like:

> When `draft_claim_report` is invoked, write a Python script that reads the form
> context, uses `reportlab.platypus` to build the PDF (claim summary card → policy
> excerpt → a photos grid embedding each image in `input_files` → approver signature +
> audit footer), writes it to `/workspace/output/claim_<claim_id>_report.pdf`, and sets
> `output_filename` to match. Then reply: "Report drafted. [Download](<download_url>)"
> using `output_files[0].download_url`.

The more concrete the prescription, the more reliable the generated
script. List exact section headings, exact filenames, exact libraries
to use. Avoid vague phrases like "make a nice report".

## Allowed libraries (sandbox image)
- `pandas`, `openpyxl`, `xlrd` — Excel / CSV
- `python-docx` — Word
- `python-pptx` — PowerPoint
- `Pillow` — image manipulation
- `xlsxwriter` — formatted Excel
- `reportlab` — PDF generation
- `pdfplumber` — PDF reading
- `jsonschema` — validation

NOT available: `subprocess`, `os.system`, `socket`, `urllib`, `requests`,
or any other network library. The sandbox is offline.

## Wiring to a panel button
Add a `tool_buttons[]` entry on the panel where you want the action
exposed:
```jsonc
{
  "id": "claim_form",
  "type": "form",
  "schema_inline": { ... },
  "tool_buttons": [
    {
      "label": "Approve & Draft Report",
      "tool_name": "draft_claim_report",
      "confirm": "Approve this claim and generate the PDF?"
    }
  ]
}
```
The runtime UI renders the button, the click POSTs to
`/api/apps/<slug>/tool/draft_claim_report`, the runtime invokes the
LLM with form context, the LLM authors and runs the script, and the
returned `download_url` shows up as a clickable link in the panel
result area — typically auto-opening the file for download.

## Hard Rules
- **Never** put real Python in the BA-facing description. The
  prescription is plain English; the LLM writes the code.
- **Always** specify the output filename in the prescription. Without
  it, the LLM may pick a name that collides with another panel.
- **Always** instruct the LLM to echo the `download_url` in its reply.
  Otherwise the user has no way to see the generated file.
- **Set `timeout_seconds` proportional to task size.** Default 60s is
  enough for a report; raise to 180s for multi-thousand-row PnL.
- **Restrict `allowed_outputs`** to the formats your panel UI expects.
  The list is a UI gate (the sandbox itself only enforces filesystem
  isolation).

## BA-friendly output
> "I'll add a 'Draft Report' button to the claim form. When you click
> it after approving, an AI script will generate the PDF and give you
> a download link. Roughly 30 seconds. Confirm?"
