<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra AI — Reference Architecture (as-built)

**Audience:** sales engineering, solution architects, a technical evaluator.
**Purpose:** the canonical description of the current design, using the
acme-power deployment as the reference implementation.

---

## 1. What the product is

Citra AI is a **Decision-App platform**: business analysts assemble governed
"Decision Apps" over the customer's own data that make recommendations a human
approves, overrides, or rejects — and the platform **learns from those decisions**
(a decision-memory loop). Around that sit chat over enterprise data, IT-authored
workflows, and a document/SOP knowledge base.

## 2. Topology (single-tenant)

```
        ┌───────────────────────── Dedicated Citra cloud (per customer) ─────────────────────────┐
        │  Route53/ACM ▶ ALB ▶ EC2 × N (Traefik) ▶ docker-compose service stack                  │
        │                                                                                          │
        │   Citra-Service · smart-app-service · citra-app-runtime · citra-workflow · citra-worker  │
        │   data-discovery · discovery · reranker · skill · duckdb · playwright · monitoring       │
        │   Citra-User-Service (SSO/identity)                                                      │
        │                                                                                          │
        │   Managed data:  AWS Mongo (DocumentDB/Atlas) · Zilliz (vectors) · S3 · Vault (secrets)  │
        └──────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                    │ VPN / PrivateLink (governed, audited)
        ┌───────────────────────── Customer estate ─┴─────────────────────────┐
        │  dept-MCP (structured-only)  ──▶  customer source systems             │
        │  (SQL / SAP / REST / Mongo; connection creds stay local)             │
        └──────────────────────────────────────────────────────────────────────┘
```

- **Single-tenant** — one org per deployment, dedicated everything.
- **Runtime** — AWS ALB → EC2 boxes → Traefik → docker-compose. Provisioned by
  Terraform (`infrastructure/terraform/`); **not** Kubernetes (see that README
  for the rationale — nothing to orchestrate at one-tenant scale).
- **Data stores are managed** — AWS Mongo, Zilliz, S3, and Vault; nothing
  stateful is self-hosted except Vault.

## 3. The customer-side dept-MCP

The one component that reaches into the customer's estate.

- Runs where the customer's **source systems** live; their connection credentials
  never leave. Exposes a **governed, audited query interface** (SQL/OData/REST/
  Mongo), never raw DB access.
- **Structured-only** — it does **no RAG**. Semantic queries are answered
  platform-side (§5). So the MCP has **no vector store / embedding / reranker**
  dependency and no Redis dependency (in-process cache) — it is infra-stateless
  in the customer estate.
- **Test + prod MCPs** (permanent, by design) — the Citra platform is always
  prod; the customer runs a *test* MCP (QA sources) and a *prod* MCP (prod
  sources). Builder/BA work always hits the test MCP; promotion rebinds an app to
  the prod MCP. Counterpart sources share a `source_id` so promotion is a rebind,
  not a rewrite.
- **Config-only per deployment** — sources from a mounted file (`SOURCES_FILE`, or
  inline `SOURCES_JSON`; the central Mongo mode was removed);
  LLM calls to the platform proxy with a scoped per-tenant key.

## 4. Decision Apps + the memory loop

- BAs assemble Decision Apps (non-code); each app binds datasets (structured via
  MCP, semantic via the platform reader) and produces recommendations.
- Officers approve / override / reject; every decision is written to an
  append-oriented **ledger** and distilled into **rubrics** (principles beside
  precedents).
- Future recommendations are **grounded** in that accumulated memory. Memory is
  the customer's asset — exportable to their own bucket.

## 5. RAG short-circuit (platform-side semantic)

- All semantic/RAG search is answered by **Citra-Service** (`/semantic/search` +
  the in-process reader), which owns the embedding + Zilliz + reranker plumbing.
- Consumers route by the catalogued source **kind**: `semantic` → the platform
  reader; every structured kind → the MCP. Deterministic, fail-loud, never an NL
  classifier. All consumer surfaces (smart-app runtime, chat, the runtime RAG
  proxy, and the platform main chat) use one path.
- Agent/trigger runs (no end-user) use a scoped, org-bounded service token so RAG
  reads are authorized without an interactive user.
- The **SOP Library** is the ingestion side: department-native document upload,
  one collection per department, surfaced as a standard `kind=semantic` source.

## 6. Identity

- **SSO via the customer IdP** (OIDC), JIT provisioning with zero access, a
  local break-glass admin, HS256 enterprise JWTs carrying org/dept/role/SA.
  (`Citra-User-Service`, `AUTH_PROVIDERS` allowlist.)

## 7. LLM proxy

- A platform-side OpenAI-compatible relay holds the real provider key; every
  consumer (incl. the customer MCP) calls it with a **scoped per-tenant key**.
  It meters per tenant, enforces per-tenant budgets + a model allowlist, and does
  429-backoff + prompt-caching centrally.

## 8. Provisioning

- Terraform module (`modules/citra-tenant`) provisions VPC → ALB → `box_count`
  EC2 (Traefik + compose) → S3 → IAM → optional Route53/ACM, with **configurable
  per-service replica counts** (hot path at 2, rest at 1). The existing GHCR +
  SSM flow ships the images. One `tfvars` per customer.

## 9. Service inventory (platform)

Core: `Citra-Service` (chat, RAG reader, personal vault), `smart-app-service`
(Decision-App build/runtime, LLM proxy, token metering), `citra-app-runtime`
(app UI runtime), `citra-workflow` + `citra-worker` (IT workflows), `Citra-User-
Service` (identity). Supporting: `data-discovery-service` (catalogue crawl),
`discovery-service` (MCP registry), `reranker-service`, `duckdb-query-service`,
`playwright-render-service`, `monitoring-service`,
`action-sandbox-host` (builder sandboxes). Parked/future: action-chat,
collaboration.
