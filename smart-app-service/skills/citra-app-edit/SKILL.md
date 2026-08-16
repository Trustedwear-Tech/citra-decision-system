---
name: citra-app-edit
description: Modify an existing published Power AI App via diff + re-publish
metadata:
  category: citra
  tools: [bash]
---

# Citra App Edit

## Purpose
Edit a previously-published app: add a panel, change a sub-agent, adjust a threshold, swap a data source. The change goes through the same validate → self-test → publish loop, producing a new version.

## When to Use
- BA opens an existing app from "My Apps" and clicks Edit.
- BA says "add a fraud check", "change approval limit to 50k", "show invoice number on the queue".

## Workflow
1. Pull current spec:
   ```bash
   curl -sS -H "Authorization: Bearer $CITRA_JWT" \
     "$SMART_APP_SERVICE_URL/apps/$SLUG" \
     > /workspace/build/current.json
   ```
2. Save base copies:
   ```bash
   jq .app_spec   /workspace/build/current.json > /workspace/build/app_spec.json
   jq .agent_spec /workspace/build/current.json > /workspace/build/agent_spec.json
   ```

### Edit
3. Ask the BA to describe the change in plain language. Re-confirm before editing.
4. Apply the **smallest possible diff** to the JSON. Don't rewrite unaffected sections.
4a. **Form-schema changes vs existing data — warn, don't silently break.** App-owned data the BA already entered lives in `smart_app_records`, keyed by the form-field `id`s as they were when the records were written. If your edit **renames or removes a form field, changes its `type`, or makes a previously-optional field required**, existing records still carry the **old** shape:
   - **Renamed/removed field** → old records keep the old key; the new column reads empty for them. Prefer keeping the old field `id` and only changing its `label` (a label change is safe — `id` is the data key, `label` is display). If the `id` truly must change, tell the BA plainly that rows entered before today won't show a value in the new column until they're re-saved.
   - **Type change** (e.g. text → number) → old string values may not parse. Warn the BA; don't force it.
   - **Newly-required field** → existing rows have no value for it; the runtime renders them fine (required is enforced only on new writes), but flag it so the BA isn't surprised by blanks.
   Never delete/rewrite existing records to fit the new schema — that's data loss, and not the builder's call. Surface the impact in one plain sentence and let the BA decide.
5. Re-validate (`citra-app-spec` / `citra-agent-spec` snippets).
6. Re-run `citra-self-test`. Add new tests covering the change.
7. **Re-publish to test.** Call `citra-app-publish` (no `mode`, no preview) — it upserts the same `slug` into the **test** environment, where writes COMMIT against `test_`-prefixed collections. Preserve the existing audience (carry `SEED_APP_SPEC.audience` unchanged — never silently change who can see the app on an edit). Narrate verbatim:
   > "I've published your changes — your app is **live at `<test_url>`** in the test environment. Open it and try them against real data."
8. **Hand off the test URL and stay available for further changes.** The builder publishes to **test only** — hand the BA the test URL and STOP; there is nothing beyond test for the builder (W-07).
9. Show the BA a plain-language diff: "I added a Fraud Check sub-agent and a Risk column on the queue. Approval limit is now 50,000."

## Hard Rules
- **Carry `SEED_APP_SPEC.case_signature` across unchanged** unless the BA asked to change it. It reads as an optional block and it is not: a clause fires only if its `scope_facets` are a subset of the case's facets, so an app that loses its signature derives `case_facets: []` on every case and **nothing it has ever learned can fire again** — while the clauses still read `active` in the store. Publish rule **CS-03 rejects** a version that drops a signature the published app already had. If a facet's column moved, rename the family and put the old name in that facet's `aliases`; the existing clauses migrate instead of going dark. **If the BA asks to change how the app groups its learning, re-propose the new list and update `confirmed_families` (CS-04).** Talk about it as *the business categories the app files its learning under* — never say "facet" or "signature" to a BA, and warn them plainly when a category is disappearing: "dropping product means the lessons your team taught about personal loans specifically will stop being applied."
- Never delete data the BA didn't ask to remove. Archive panels/sub-agents you no longer use by setting `archived: true` (if the schema supports it) or moving them out of the active spec but keeping a comment in the build log.
- Don't change `slug` here. Use a fresh build instead.
- **Editing a `factor_set` is an ordinary chat edit** — the BA can add, remove, relabel, re-weight or re-band factors, and change `terminology`, exactly like any other spec change. Read `citra-app-spec` -> `references/factor-set.md` first. Two things that are NOT ordinary:
  - **`mode` cannot change on a published app** (publish rule FS-02 rejects it). `composite` <-> `checklist` is a new app, because every past decision was recorded under the old mode. Tell the BA that rather than attempting the edit.
  - **A weight change is retroactive in appearance, not in fact.** Past decisions keep the grade they were made under (the card is frozen on the row and the ledger), but the SAME case re-run tomorrow will grade differently. Say so before re-publishing — a credit team will ask.
- Re-weighting is one of the most likely edits a BA asks for, and one they are entitled to: it is **their** rubric. Never argue them out of a weight; do confirm you have understood which factor and what it becomes.
- Always re-run self-test on edits — even tiny edits.
- A failed publish must roll back local files to the pre-edit state so the BA can retry cleanly.

## Safety rules (citations)

This skill defers to **[citra-safety-rules](../citra-safety-rules/SKILL.md)**. The rules load-bearing on an edit:

- **H-04** — `hitl_policy.allow_writes_in_chat` is deprecated; an edit MUST NOT (re-)introduce it. Move any write into a queue-action or form `on_submit`.
- **W-07** — edits re-publish to **test** (writes commit there); the builder's job ends at the test URL.

## Recovering from a bad edit
The previous version is still on the server (`GET /apps/$SLUG?version=<n-1>`). Tell the BA: "Want me to revert to the version from yesterday?" and on confirmation, fetch that version, write it to build files, re-publish unchanged.
