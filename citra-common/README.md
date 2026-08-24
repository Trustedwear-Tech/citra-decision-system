<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-common

Shared libraries used by more than one Citra product. Extracted from `Citra-AI`
with `git filter-repo`, so **every package keeps its original commit history** —
160 commits, not a fresh copy.

| Package | What it is |
|---|---|
| `citra-auth` | JWT middleware, role constants, token minting, revocation hooks |
| `citra-cache` | Redis cache client (`REDIS_DB`-aware) |
| `citra-llm` | OpenAI-compatible LLM + embedding client |
| `citra-mongo` | Mongo connection and database-name resolution |
| `citra-queue` | Redis Streams job queue |
| `citra-service-utils` | Tracing, circuit breakers, data classifier, Vault bootstrap, optional Milvus client |
| `Citra-User-Service` | Node/Express identity service — accounts, orgs, depts, roles, SSO, email |

## Why this repo exists

`Citra-AI` and `citra-flows` are separate products that both need the same
plumbing. Before this repo, each carried its own copy of all six Python
packages. The copies were **not** kept in sync — see the conflicts below.

The rule for what belongs here: **would both products break if this changed?**
If only one would, it belongs in that product.

**Not here, deliberately:** anything product-specific — `citra-workflow` is
flows'; Decision Apps, MCP and discovery are Citra-AI's.

## Source of truth: Citra-AI

**Where the two copies disagreed, Citra-AI's version wins.** That is the standing
rule, decided 2026-08-14. citra-flows' local edits to these packages are not
carried over; flows adopts what is here.

Every package in this repo is verified identical to Citra-AI's tree.

### What that means for citra-flows

Two things change for flows when it adopts this repo:

**`citra-llm` default `EMBEDDING_MODEL` becomes `baai/bge-m3`** (flows had
`text-embedding-3-small`). This is the intended value. It is also safe here:
flows stores no vectors of its own — its vector node queries whatever external
store a user points it at — so there is no existing corpus to invalidate.

**`vault_bootstrap.py` gets Citra-AI's docstring**, which illustrates
`VAULT_ADDR` with a specific deployment's private IP. Worth genericising if this
repo is ever published, but it is a comment and changes no behaviour.

`citra-service-utils` keeps its optional `milvus` extra
(`pip install citra-service-utils[milvus]`) — opt-in, and forces a vector
database on no consumer.

### One thing to watch

`Citra-User-Service/src/routes/userAdminRoutes.js` has an **uncommitted** change
in Citra-AI's working tree at extraction time (impersonation TTL 4h -> 2 days).
This repo carries the committed value. When that change lands, it needs to land
here rather than in Citra-AI — that is the point of the move.

## Consuming it

Git submodule in each product, pinned per repo:

```bash
git submodule add <citra-common-url> citra-common
```

Then point the path installs at the submodule instead of the vendored copy.
Neither product depends on the other at runtime — they share libraries, not
services.

## Known wart

`citra-service-utils` has two docstrings that name Citra-AI services
(`data-discovery-service`) as example consumers — `data_classifier.py:6` and
`milvus_client.py:13`. Harmless (comments, and both copies already had them), but
they should be genericised the next time those files are touched.
