<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Plan: stamping the tree for BSL 1.1

> **SUPERSEDED — this repository is Apache-2.0.** The plan below argues for
> stamping every file `BUSL-1.1`. That is not what shipped: the tree was
> stamped `Apache-2.0`, `LICENSE` is Apache-2.0, and the production
> restriction described here was dropped before the repository was made
> public. An Apache grant is irrevocable, so it does not come back. The
> document is kept because the mechanics of stamping — who owns what, why the
> grant is restated per file, why a registered SPDX id matters — are still how
> the tree is maintained; only the licence being stamped changed. Read every
> `BUSL-1.1` below as `Apache-2.0`.

**Status:** superseded. Executed with Apache-2.0 in place of BUSL-1.1.

**Goal.** Every source file we wrote carries a notice naming Trustedwear Tech
Private Limited as owner, Rohit Kumar Chandan as author, and BUSL-1.1 as the
licence — consistently, automatically, and without ever making that claim over
code we did not write.

## First, what stamping actually buys

Headers do not stop anyone copying the code. Copying is a `git clone`, and the
repository is public by design — that is what source-available means. What
headers do is change the position you are in *after* it happens:

- They remove **"I didn't know it wasn't open source"** as a defence. A file
  that says BUSL-1.1 on line 1 makes innocent infringement very hard to argue.
- They travel with **fragments**. A copied file, or a class pasted into someone
  else's service, carries its own provenance. A LICENSE at the repo root does
  not survive copy-paste; a header does.
- They make removal an **affirmative act**. Stripping a notice to pass code off
  as your own is a separate, deliberate step — evidence of intent, and in some
  jurisdictions independently actionable as removal of copyright management
  information.

So the honest framing is: stamping makes infringement *provable and expensive*,
not impossible. The measures in Phase 5 matter as much as the headers.

---

## What is already right

Worth stating, because most of this is done:

| | Status |
|---|---|
| `LICENSE` | BUSL-1.1, Licensor **Trustedwear Tech Private Limited**, Change Date 2030-08-09 → Apache 2.0 |
| Additional Use Grant | Non-production defined explicitly, including 90-day pilots |
| `NOTICE` | Correct owner, states source-available ≠ open source |
| `CLA.md` | Contributors grant the Company a perpetual copyright and patent licence |
| CI | A **License Headers** job already exists in `ci.yml` and calls `--check` |

The framework is in place. What follows is a tool that does not match it.

---

## What is broken

All seven verified against the tree today.

### D1 — The stamper would write our copyright into 5,074 files we do not own **(blocker)**

`scripts/add-headers.sh` walks the **filesystem** with `find`, not the
repository. It skips a hardcoded handful of path fragments — `/venv/`,
`/myenv/`, `/node_modules/`, `/build/`, `/dist/` — and `.venv-seed/` is not one
of them.

Run it today and `--check` reports **6,079** files missing headers. Only **979**
of those are tracked by git. The other 5,100 are almost entirely
`.venv-seed/Lib/site-packages` — `certifi`, `boto3`, `anyio`, `pydantic` and
every other dependency — which it would rewrite to say *"Copyright (c) Trustedwear
Tech Private Limited. PROPRIETARY — all rights reserved."*

`.venv-seed` is gitignored, so those edits would not reach GitHub from this
tree. That limits the blast radius; it does not fix the tool. The tool has no
concept of *ours* versus *theirs* — it only knows five path fragments, and the
next vendored directory that is not on that list gets claimed. Asserting
ownership over other people's MIT and Apache code is the single most damaging
thing a licence-enforcement effort can do, because it is exactly what the other
side will lead with.

**Fix:** drive the file list from `git ls-files`. If it is not tracked, it is
not ours to stamp.

### D2 — The header contradicts the licence

The header the script writes says:

```
# PROPRIETARY - all rights reserved. See LICENSE.md. NOT an open-source grant.
# SPDX-License-Identifier: LicenseRef-Citra-AI-Proprietary
```

The repository is **BUSL-1.1** — source-available, with an explicit
non-production grant and an automatic conversion to Apache 2.0. "PROPRIETARY,
all rights reserved, not a grant" describes a different licence from the one in
`LICENSE`, and 14 files already carry it.

This is worse than having no header. A defendant reads two contradictory
statements of terms and argues the grant is ambiguous — and ambiguity in a
licence is generally construed against its author. A custom `LicenseRef-` id
also defeats every automated licence scanner, where `BUSL-1.1` is a registered
SPDX identifier those tools already recognise.

### D3 — It points at a file that does not exist

The header says *"See LICENSE.md"*. The file is `LICENSE`. A notice that
misdirects the reader to the actual terms is a weak notice.

### D4 — No author attribution

Nothing names Rohit Kumar Chandan anywhere in the tree.

One caution on how to fix this. For work owned by the company, the **copyright
holder is Trustedwear Tech Private Limited** — naming an individual as the
holder would contradict company ownership and undercut the CLA. The right shape
is owner and author on separate lines: the company holds it, the person wrote
it. That gives attribution without creating a competing ownership claim.

### D5 — Two files credit the wrong entity

`scripts/modules/common.sh` and `scripts/setup.sh` say
`Copyright (c) 2024-2026 Citra AI (https://github.com/Citra-AI)`. "Citra AI" is
a product and a GitHub org, not the legal entity. Only Trustedwear Tech Private
Limited can hold the copyright.

### D6 — The CI guard exists but has never run

`ci.yml` has a License Headers job that runs `--check` and exits 1 on any
unstamped file. With 979 tracked files unstamped it would fail every PR — so it
plainly is not running. `gh run list` returns **no runs at all** for this
repository: Actions are dead, which is how the drift went unnoticed.

A guard that does not execute provides the *appearance* of enforcement, which is
worse than a known gap.

### D7 — Whole file types are not covered

The script handles `.py`, `.js`, `.ts`, `.jsx`, `.tsx` only. Not stamped at all:
23 `.sh`, 71 `.yml`, the Dockerfiles, `.sql`, and the `.md` documents — which
for a docs-heavy product like this one is a real amount of authored work.

---

## The canonical header

One text, three comment syntaxes. Proposed:

```python
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.
```

Line by line, because every one is load-bearing:

| Line | Why |
|---|---|
| Copyright + entity | Names the owner that can actually enforce. |
| `Author:` | Attribution to the human, without competing with company ownership. |
| `SPDX-License-Identifier: BUSL-1.1` | The registered identifier. Machine-readable to every scanner, and unambiguous. |
| The grant sentence | Puts the *substance* in the file, so a copied fragment states its own terms rather than pointing at a file the copier did not take. |
| `See LICENSE` | Correct path this time. |

Six lines. Long enough to be self-contained, short enough that nobody argues
about it in review.

---

## Phases

### Phase 0 — Agree the header text (XS)

The wording above. Worth a legal read before 979 files carry it, since changing
it afterwards is another whole-tree commit.

### Phase 1 — Rewrite the stamper (S)

Replace `scripts/add-headers.sh`. Requirements, each answering a defect above:

- **`git ls-files` drives the list.** Untracked means not ours. Kills D1.
- **Extension allowlist**, with the right comment syntax per language, extended
  to `.sh`, `.sql`, `.yml`, `Dockerfile`, `.md` (HTML comment). Kills D7.
- **Explicit exclusions** for things that are tracked but must not be stamped:
  generated files, fixtures whose bytes are asserted in tests, `.json` (no
  comment syntax), and `smart-app-service/skills/*/runtime-reference/` if those
  are shipped read-only as reference copies — check before stamping.
- **Idempotent.** Detect by SPDX line, never double-stamp.
- **Placement-safe.** After a shebang, after a Python `# -*- coding:` line,
  after `<?xml`. Before, and the file breaks.
- **Encoding- and EOL-safe.** This tree is LF via `.gitattributes`; the stamper
  must not rewrite CRLF or strip a BOM.
- **`--check` mode** unchanged in contract, so CI keeps working.

Add a unit test with a fixture per language — including the shebang and coding
cases. A stamper that corrupts a file is worse than an unstamped tree.

### Phase 2 — Repair the mis-stamped files (XS)

The 14 `LicenseRef-Citra-AI-Proprietary` files and the 2 wrong-entity files.
Convert to the canonical header. Small, and it removes the contradiction that
would be quoted back at us.

### Phase 3 — Stamp the tree (M, but mechanical)

979 tracked files in **one commit**, with no other change in it, so the diff is
reviewable as "headers only" and never obscures a logic change. Verify
afterwards that the test suites still pass — the risk is not the text, it is
placement in files with shebangs, encoding declarations or doctests.

### Phase 4 — Re-arm the guard (S)

The CI job already exists and is already correct in shape. Two things:

1. **Fix Actions first.** The job is worthless until runs execute at all — this
   is the same dead-CI problem that affects the whole repo, not a licence issue.
2. Add a **pre-commit hook** so a missing header is caught locally. There is no
   `.pre-commit-config.yaml` today; `lint-bare-except` is the precedent for how
   this repo enforces a rule, and the same shape applies.

### Phase 5 — The parts that actually deter theft

Headers establish notice. These establish *provenance and leverage*, which is
what an enforcement action needs:

- **Copyright registration.** Registering the work with the Indian Copyright
  Office creates a public record with a date. If you ever intend to enforce in
  the US, registration there is a **precondition to filing suit** and governs
  whether statutory damages are available at all — and it must generally
  predate the infringement to get them. This is the highest-leverage item on
  this list and it is paperwork, not engineering.
- **Signed commits and signed release tags.** Cryptographic proof of what was
  authored when, by whom. Far stronger evidence than a file header, and it costs
  one `git config` change.
- **A visible runtime notice.** The product should state its licence somewhere
  a user of a *deployed copy* sees it — a footer, `/health`, an API banner.
  Someone running a stripped copy in production then has to have removed a
  user-visible notice, which is a much harder position than removing a comment.
- **Fingerprints.** A handful of arbitrary-but-harmless unique strings — an
  unusual internal constant, a distinctive spelling in a log line, the specific
  ordering of an enum — make a copy identifiable even after headers are
  stripped and identifiers are renamed. Record where they are, privately, and do
  not document them in the public tree.
- **Contributor provenance.** The CLA exists; make signing it a merge
  requirement so ownership never fragments. A single unassigned outside
  contribution complicates every future claim.
- **Trademark.** In practice "Citra" as a registered mark is often the faster,
  cheaper lever than copyright: a competitor can rewrite code, but cannot sell
  it under your name, and a mark is enforceable without proving copying.

---

## Order

| # | Work | Size | Blocking? |
|---|---|---|---|
| 0 | Agree header wording | XS | yes — everything downstream carries it |
| 1 | Rewrite the stamper (git-driven, tested) | S | yes — do NOT run the current one |
| 2 | Repair the 16 mis-stamped files | XS | no |
| 3 | Stamp 979 tracked files, one commit | M | no |
| 4 | Fix Actions, re-arm the check, add pre-commit | S | no |
| 5 | Registration, signed tags, runtime notice, fingerprints | — | parallel; start registration now |

**Do not run `scripts/add-headers.sh` in its current form.** Until Phase 1
lands it will write a false, self-contradictory ownership claim into 5,074
third-party library files.
