<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Clean-room test of citra-decision-system

Status: plan, nothing executed. Written 2026-08-17.

Goal: prove the published repo works for someone who has never seen it — wizard,
setup, and app creation — against two different data situations, from an empty
Docker host.

---

## 0. Blockers to clear before starting

**Docker Desktop's daemon is stopped.** It went down partway through the `.env`
migration and has not come back. Nothing below runs until it does.

**The two supply-chain-finance SOPs are still in the acme-bank library.** They
are step 1 for a reason: they are the only thing in the plan that changes what a
tester sees, and leaving them makes every later observation ambiguous.

---

## 1. Remove the orphaned supply-chain-finance SOPs

### Why they have to go

`demo-data/tenants/acme-bank/mcp/sources.json` declares five sources:

| source_id | kind |
|---|---|
| `loan_origination` | structured |
| `loan_servicing` | structured |
| `insurance_claims` | structured |
| `sales_crm` | structured |
| `acme_bank_policy_library` | semantic (the SOP library) |

There is **no supply-chain-finance source**. So the two SOPs describe a process
with no tables behind it: a Decision App grounded in them can cite a clause and
then find nothing to act on. That is worse than a missing SOP — it looks like
the product working right up until the write step.

### Where a SOP actually lives

Deleting the row is not enough. Each dept-library document has three
touchpoints:

1. **Mongo** — the dept-library document record
2. **Milvus** — chunks in the ONE shared dept collection (text lives *in*
   Milvus, per `dept_library_store.py`, so this is not just vectors)
3. **MinIO / S3** — the uploaded file itself

`DELETE /api/dept-library/folders/{folder_id}/documents/{document_id}`
(`dept_library.py:851`) is the only path that clears all three. Deleting from
Mongo by hand leaves orphaned chunks that still answer queries — the exact
failure this whole exercise is meant to catch.

### Steps

1. `GET /api/dept-library/folders` — find the acme-bank policy-library folder id.
2. `GET .../folders/{id}/documents` — identify the two SCF documents by title.
3. Record their ids and titles in this doc before deleting.
4. `DELETE .../folders/{id}/documents/{doc_id}` for each.
5. **Verify the Milvus side specifically**: query the shared dept collection for
   a distinctive phrase from each SOP and confirm zero hits. A 200 from the
   delete endpoint is not proof the chunks went.

---

## 2. Back up every database first

`C:\Github\Citra-AI\backup\` already holds a **prod** backup (2026-08-08) —
`citra-backup.tar.gz`, `extracted/`, `s3/`. **Do not overwrite it.** New backups
go to `backup/local-2026-08-17/`.

Everything below is wiped in step 3, so this is the only chance.

| store | what | how |
|---|---|---|
| MongoDB | all databases (`dev`, `citra`, app state) | `mongodump --uri` with root creds, `--gzip --archive` |
| Milvus | the shared dept collection + app collections | `milvus-backup`, or export collections to parquet via pymilvus |
| MinIO | buckets (uploaded SOPs, media, artefacts) | `mc mirror` the whole endpoint |
| Postgres | the acme-bank system-of-record | `pg_dump -Fc` per database |
| Redis / queue-redis | ephemeral | skip — regenerated on boot |

**Verify each dump before wiping**: `mongorestore --dry-run`, count objects
mirrored from MinIO, `pg_restore -l` on the dump. An unverified backup is not a
backup, and step 3 is irreversible.

---

## 3. Wipe Docker completely

```
docker compose ... down -v          # in each repo, drops named volumes
docker system prune -a --volumes    # images, containers, networks, build cache
docker volume ls                    # must be empty of citra-* volumes
```

The point is that leftover volumes are what make a "clean" test lie: Mongo comes
up already seeded, and the wizard appears to work when it never ran.

---

## 4. Scenario A — acme-bank demo, from scratch

Clone fresh into a **new directory**, not the existing checkout, so nothing
untracked leaks in:

```
git clone --recursive https://github.com/Trustedwear-Tech/citra-decision-system
cd citra-decision-system
make wizard
```

Check, in order:

1. **Clone is complete** — `citra-common/` populated (this needed
   citra-common to be public; it now is).
2. **Wizard generates `.env`** with fresh secrets and does not touch anything
   below the FINE-TUNING marker.
3. **`make install`** brings the stack up; every service healthy.
4. **acme-bank seeds** — orgs, sources, SOP library, Postgres SoR.
   `data-discovery-service` must NOT exit on its `ORG_ID` guard: that guard
   fired all session because `orgs` was empty, and a real seed is the test of
   whether it was only ever an unseeded-DB artefact.
5. **Builder** — create a Decision App against acme-bank, publish it, run one
   decision end to end, confirm citations resolve to the policy library and a
   write reaches Postgres.

---

## 5. Scenario B — a new Postgres, ontology generated by the wizard

The harder and more valuable case: the tester's own database, not the demo.

1. Stand up an empty Postgres and load a small schema **not** derived from
   acme-bank (a few related tables, realistic column names).
2. Register it through the wizard / source-registration path so the MCP picks
   it up.
3. **Generate the ontology from that schema** and read it critically: do the
   entities match the tables, are relationships inferred, is anything invented?
4. Build a Decision App on it and run a decision.

The comparison is the point. Scenario A proves the seeded demo path. Scenario B
proves the product works on data it has never seen — which is what a self-hoster
actually does, and the only one that can fail in an interesting way.

---

## 6. What to record

For each scenario, capture: what failed, at which step, and whether it is a
product bug or a missing prerequisite. Two things I would watch specifically,
because both surfaced during this session:

- **`ORG_ID` guard in `data-discovery-service`** — failed for every value
  because `orgs` was empty. A real seed should clear it. If it does not, it is
  a product bug, not the local DB.
- **`.env` coverage** — the file has 80 REQUIRED keys. If the stack needs a key
  that is only in the FINE-TUNING zone, the split is wrong and the wizard needs
  to fill it.

---

## 7. Order, and why

1. Delete the SCF SOPs — changes what the test observes
2. Back up and **verify** — irreversible step next
3. Wipe Docker
4. Scenario A — proves the happy path
5. Scenario B — proves the general case

Steps 1–3 are prerequisites and should not be interleaved with 4–5: a
half-cleaned host produces results nobody can trust.
