<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Motor Insurance Claim Intake

**BA goal:** "Build a motor-insurance claim intake app where the customer fills in an application form AND uploads a photo of the accident damage. The app should reject submissions where the form is incomplete (don't even look at the photo). For complete submissions, OCR the photo, cross-reference it against the customer's policy and recent claim history, and produce a recommended decision (approve / decline / refer-to-human) with reasoning."

## Why this is a good reference example

It exercises the **full** runtime tool palette in one app:

- `validate_form` — gates everything; no paid tool runs until the form is complete.
- `vision_ocr` — extracts free text from the user-uploaded damage photo.
- `mcp.insurance.lookup_policy` — fetches the customer's active policy record.
- `mcp.insurance.fetch_claim_history` — fetches past claims for fraud / pattern checks.
- `rag.insurance.policy_search` — consults internal SOP / policy documents.
- A workflow tool could be added (e.g. `workflow.send_decision_letter`) once the runtime supports it.

## Decision flow that the agent's `system_prompt` enforces

1. Call `validate_form` on the submitted form data. If `ok=false`, respond with the rejection list and stop.
2. Call `vision_ocr` on each uploaded image (max 3) to extract a damage description.
3. Call `mcp.insurance.lookup_policy` with the policy number from the form to confirm the policy is active and the vehicle matches.
4. Call `mcp.insurance.fetch_claim_history` to surface anything suspicious in the last 24 months.
5. Call `rag.insurance.policy_search` with the OCR'd damage type + claim amount to find the SOP rules.
6. Emit a structured decision: `{action: "approve|decline|refer", confidence: 0..1, reasoning: "..."}`.

## Why the cost gate matters

Vision tokens are 5–10× the cost of text tokens. Rejecting a form-incomplete submission **before** OCR saves real money on every duplicate / sloppy / spam submission — and most claim portals see a lot of those.
