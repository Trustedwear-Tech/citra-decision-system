<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Layer 2 — Builder eval

The builder is an LLM that turns a BA goal into a published AppSpec. It's
**nondeterministic**, so we don't assert one exact spec — we assert a **corpus
pass-rate** against metamorphic expectations: *"this goal must yield these
building blocks and pass every publish validator + the smoke gate."*

```
goals_corpus.yaml   BA goals, each with expects: {panels, tool_kinds, validators}
eval_runner.py      --report (offline) | --live (drives the real builder)
```

## What the corpus buys you
The union of every goal's `expects` is the **vocabulary the corpus exercises**.
Today: 8 panel types, 9 tool kinds, 11 validators. Grow the corpus until that
union covers everything you support — that's the builder denominator in
`../coverage_report.py`.

## Run

```bash
# offline: report corpus vocabulary + emit builder.json coverage cell
python eval_runner.py --report

# live (gated): drive the REAL builder per goal, then check the published spec
SAS_BASE_URL=https://…  SAS_JWT=<builder-jwt>  python eval_runner.py --live
```

`--live` reuses the proven `POST /build → stream chat → publish` flow (the same
one the directory-lookup run exercised in prod), then for each goal asserts:
1. every publish validator passes,
2. the smoke gate passes,
3. the panels + tool_kinds the goal `expects` appear in the published spec.

Pass criterion = **corpus pass-rate ≥ target** (nondeterministic LLM). Layer on
**record/replay** — store each session's LLM turns — for deterministic
regression once a goal is green.

## Extending
Add a goal whose `expects` names a panel/tool/validator not yet in the union.
Keep goals realistic (a BA would actually ask this); a synthetic goal that games
the vocabulary proves nothing.
