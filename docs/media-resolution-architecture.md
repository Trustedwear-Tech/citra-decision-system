<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Media Resolution Architecture — Plan

> **Status: PHASES 1–3 BUILT + verified on acme-power (S3 + http handlers); proxy
> streaming for blob/file/dms is the remaining future phase.** How `image_analyze`
> / `doc_extract` (and any future media tool) get the photo/document a SoR record
> points at, for **any** enterprise source — not just public-cloud S3.
>
> **Built (2026-06-30):** MCP `POST /resolve_media` (read ref by key under the
> caller's PDP → presign-on-demand for `s3://`, passthrough for `http(s)://`);
> runtime `call_dept_mcp_resolve_media` + rewired `_resolve_media_url`; acme-power
> `equipment_inspections` columns migrated to `s3://` native refs. Verified
> end-to-end: resolver → MCP → fresh presigned URL → fetched real image bytes; the
> July-6 expiry cliff is gone.

## 1. Problem

A Decision App's media tools need the bytes of an image/document referenced by a
System-of-Record row. Today the resolver ([tools_v2_dispatch.py `_resolve_media_url`](../smart-app-service/tools_v2_dispatch.py))
reads the row by key, pulls a `url_column` **string**, and does a plain HTTP GET
on it. That assumes the column holds an **HTTP(S)-reachable URL** the *cloud
runtime* can fetch. Two failures:

1. **It rots.** The acme-power fixture bakes a 7-day S3 presigned URL into the
   column; it 403s after expiry.
2. **It only works for public/HTTP sources.** Real enterprise media lives behind
   the firewall — intranet file shares (UNC/SMB/NFS), DMS (SharePoint/Documentum/
   FileNet), on-prem object stores (MinIO/ECS), DB **BLOB** columns, internal HTTP.
   A cloud runtime often **cannot route to those at all**.

## 2. Core principle — the agent passes a REFERENCE, never a URL or bytes

The currency that flows through the app and the agent is a **media reference** —
*"the photo of inspection INS-2026-0013"* = `(dataset, key, column)`. It is:

- **tiny** — a few strings; passes freely through tool args and function calls;
- **permanent** — it's a pointer to a row+column, so it never expires and never
  needs minting (no presign, no handle, no token);
- **source-agnostic** — the agent has no idea whether the bytes are in S3, a file
  share, a DMS, or a BLOB; that's the MCP's problem;
- **already how it works** — the LLM passes only `record_id`; the `dataset` +
  `column` binding lives in the tool spec. **The LLM already passes a reference.**

**Raw bytes appear exactly once — streamed by the MCP straight into the single
call that consumes them (the vision / doc model) — and are discarded there.** Bytes
are never the currency: they never ride through tool results, the agent's LLM
context, or get passed function-to-function. (Earlier drafts that made bytes — or a
minted URL handle — the currency are both rejected.)

## 3. Decisions (agreed)

| Decision | Choice |
|---|---|
| Who fetches/streams the bytes | **The dept-MCP** — it sits inside the source network and holds the source's credentials. One governed boundary owns all source access. |
| What the agent/app passes around | A **media reference** `(dataset, key, column)` — not a URL, not bytes. |
| How the vision/doc model receives media | **Auto** — when the model endpoint can reach the MCP stream, point it there (zero bytes in the runtime); otherwise the runtime streams from the MCP **once** and base64-inlines into that one call. |
| What the SoR column stores | A **native reference** (`s3://…`, `\\host\share\…`, `dms:<sys>:<id>`, `blob://<col>`, or a plain `http(s)://`), **never** a baked presigned URL. |

## 4. The MCP capability — `resolve_media` streams bytes

```
POST {mcp}/resolve_media          (sibling of /run_query, /execute_action)
  body: { dataset_id, key_field, key_value, column }
  auth: Authorization: Bearer <service-api-key>,  X-User-JWT: <end-user jwt>
  → 200 with the RAW BYTES streamed in the response body
       Content-Type: image/jpeg | application/pdf | …
       (the MCP MAY instead 302-redirect to a short-lived direct URL — see §6 —
        as a pure transport optimization; the caller just follows it)
```

- No `{url, mode, expires_at}` handle is returned to the runtime or the agent —
  the response **is** the bytes (or a redirect the caller transparently follows).
- The end-user JWT is forwarded so the MCP applies the **same row-level
  visibility** as a read — you can only resolve media on a record you may see.
- Streamed, not buffered: the MCP pipes source → response so a large PDF doesn't
  balloon memory.

## 5. End-to-end flow

```
SoR column  →  native ref (s3://key | \\share\file | dms:id | blob://col | http://…)
                     │  (LLM never sees the ref — it's read server-side by the MCP)
agent: image_analyze(record_id = "INS-2026-0013")
                     │   ← the ONLY thing the LLM passes: the record key
runtime, at the moment it must call the vision model:
   • model endpoint CAN reach the MCP  (self-hosted vision / internal)
        → hand it {mcp}/resolve_media?... ; the model streams bytes itself.
          Runtime touches zero bytes. ✅
   • model endpoint CANNOT (external vision API + private source)
        → runtime POSTs {mcp}/resolve_media, streams the bytes ONCE, base64-inlines
          into that single API call, discards them. Bytes never leave that function.
                     │
tool returns the ItemFinding (text only).  Reference is cheap to keep; bytes are gone.
```

Re-review of the same item just resolves the same reference again — no expiry, no
re-minting. Nothing durable holds a fetchable URL or bytes.

## 6. Pluggable scheme registry (MCP side)

`resolve_media` reads the row, takes the column's native ref, and dispatches on its
scheme to a small handler registry. Each handler either **streams the bytes** or, as
an optimization, **302-redirects to a short-lived direct URL**. **Unknown scheme →
fail loud** (no silent fallback — house rule).

| Ref scheme | Handler | Transport |
|---|---|---|
| `http(s)://` (reachable) | fetch + pipe (or 302 passthrough) | stream / redirect |
| `s3://bucket/key` | boto3 `get_object` pipe — **or** 302 to a fresh presigned URL | stream / redirect |
| `azure://` / `gs://` | cloud SDK pipe / presign-redirect | stream / redirect |
| `\\host\share\…`, `file://`, `smb://`, NFS | LAN read → pipe | stream |
| `dms:<system>:<docid>` | DMS API fetch → pipe | stream |
| `blob://<column>` (bytes already in the row) | pipe the row's bytes | stream |

The 302-to-presigned option for `s3` is how the runtime can be kept out of the byte
path even for cloud sources, *when the consumer can reach S3*. New source type = one
new handler; the runtime, the tools, and the agent never change.

## 7. Runtime changes (small)

- **`_resolve_media_url` → `_resolve_media`** ([tools_v2_dispatch.py:274](../smart-app-service/tools_v2_dispatch.py)) —
  no longer returns a column URL string. It calls a new
  `call_dept_mcp_resolve_media(dataset, key_field, key_value, column, user_jwt)`
  helper (mirrors [`call_dept_mcp_read`](../smart-app-service/proxy_clients.py):
  `resolve_source` → `{query_endpoint, api_key}`, attaches `X-User-JWT`) and
  returns **a byte stream / response** for the consumer step.
- **Consumer feed** — the existing byte path
  ([`_fetch_image_url`](../smart-app-service/ocr_proxy.py) → `ocr_image` →
  base64 data-URI → vision; and the `doc_extract` large-LLM path) is **reused**;
  it's simply fed the MCP stream instead of doing its own outbound GET, and runs
  **only** on the "inline" branch. A `vision_can_reach_mcp` deployment flag picks
  point-the-model vs stream-and-inline.
- **The tool/LLM surface is unchanged** — `image_analyze(record_id=…)` already
  passes only the reference. No agent or spec change beyond renaming `url_column`
  to a scheme-neutral `media_column` (back-compatible).
- **Headless fallback** — a caller that supplies a direct `image_url`/`document_url`
  still works (unchanged), bypassing `resolve_media`.

## 8. Migration

- **Column contract:** store a native ref, not a presigned URL. For acme-power:
  `defect_photo_url = s3://demo-source-citra/acme-power/inspections/defect_photo.jpg`
  (and the report likewise), and let the MCP stream / presign-redirect on demand.
  This also **removes the July-6 expiry cliff** on the current fixture.
- **Catalogue:** `column_kind` (`image_url`/`document_url`) already drives tool
  selection; no change. Optionally add an explicit `ref_scheme` hint.
- **Back-compat:** a column still holding a plain reachable `http(s)://` is just the
  `http` handler — old data keeps working with no migration.

## 9. Governance / security

- **One boundary.** All source access — read, write, **and media** — goes through
  the dept-MCP, the only component holding source creds + network reach. The runtime
  never holds per-source storage credentials.
- **Same PDP.** `X-User-JWT` forwarded → media resolves under the caller's
  visibility; can't fetch media for a record you can't read.
- **Nothing durable is fetchable.** No presigned URLs in the DB, no handles in
  context; any direct URL (e.g. an S3 302) is minted per call with tight expiry.
- **SSRF guard stays** on any direct URL the runtime fetches; the MCP's intranet
  reach is intentional and lives behind the MCP boundary, not the runtime.

## 10. Phasing

1. ✅ **MCP `resolve_media`** — scheme registry with `s3` (presign-on-demand) and
   `http(s)` (passthrough); same PDP + audit as `/run_query`. (Streaming pipe for
   non-URL-able sources deferred to Phase 4.)
2. ✅ **Runtime** — `call_dept_mcp_resolve_media` (proxy_clients) + rewired
   `_resolve_media_url` (resolves the app data_source alias → MCP source/dataset →
   fresh URL). *(`url_column → media_column` rename + the explicit
   point-the-model-vs-inline switch still TODO; today it's inline via the
   unchanged `_fetch_image_url`.)*
3. ✅ **Migrated acme-power** `equipment_inspections` columns to `s3://` refs;
   verified image_analyze resolver end-to-end (kills the expiry cliff).
4. ⏳ **More handlers** — the `/media` streaming proxy + `blob`, `file`/`smb`,
   `dms`, as real sources need them.

Net: the agent passes a reference, the MCP owns the bytes, and bytes touch the
runtime only when an external model forces a single edge-inline. The currency is
never a URL and never a blob.
