---
name: citra-rag-probe
description: Sample a semantic (RAG) source via citra_discovery_query to learn domain vocabulary and doc types
metadata:
  category: citra
  tools: [bash]
---

# Citra RAG Probe

## Purpose
Phase 1 (Internship) — actually look inside the customer's RAG content so the agent learns the domain's vocabulary, doc types, and gaps before drafting prompts.

## When to Use
- After `citra-mcp-discover` has identified relevant dept-mcps.
- When the BA mentions domain terms you're unsure about ("what does 'NCB' mean here?").
- Before drafting `AgentSpec.system_prompt`.

## How to query — the `citra_discovery_query` tool
You sample a discovered source through the **`citra_discovery_query`** MCP tool — it forwards your
JWT (so RLS / dept scope holds) and **routes by source kind automatically**: a STRUCTURED source is
queried on its dept-MCP; a **SEMANTIC (RAG) source is short-circuited to the Citra platform reader
(Milvus direct) — the dept-MCP serves no RAG.** You don't pick the route; just pass the RAG payload.
Pass the tool name from `citra_discovery_search` plus the RAG query payload:
```json
{"tool": "citra_discovery_query",
 "args": {"tool_name": "<tool name from citra_discovery_search>",
          "args": {"query": "what is the NCB policy?", "top_k": 5, "doc_types": ["policy"]}}}
```
To read an **entire document** (all sections, in order) instead of top-k passages — e.g. to learn a
full SOP — pass `doc_path` (found in a prior chunk's `metadata.doc_path`); `query` is then ignored:
```json
{"tool": "citra_discovery_query",
 "args": {"tool_name": "<rag source>",
          "args": {"query": "full SOP", "doc_path": "policy/dt_failure_response_sop.md"}}}
```

## Workflow

Narrate every step per [`AGENTS.md`](../../AGENTS.md). Probing is slow (multiple HTTPS round-trips per dept-MCP); the BA should see what you're sampling.

1. **Narrate** before probing each dept-MCP, then call `citra_discovery_query`:
   ```
   > 🔍 Sampling the claims knowledge base to learn your team's vocabulary...
   ```
   ```json
   {"tool": "citra_discovery_query",
    "args": {"tool_name": "<source from citra_discovery_search>",
             "args": {"query": "<probe>", "top_k": 5}}}
   ```
   Persist each result to `/workspace/build/probe-<dept>.json` with `exec` if you want it on disk for Phase 2.
2. Run 3–5 probes per dept covering the goal's likely vocabulary. If probing is taking >5s for one dept (large index, slow MCP), emit a mid-flight narration:
   ```
   > 🔍 Still sampling — this dept has a lot of policy documents...
   ```
3. **Emit a finding** with the headline doc-type counts:
   ```
   > ✅ Claims KB: 240 policy docs, 80 SOPs, 35 regulations
   ```
4. From the returned chunks, extract: doc types present, recurring acronyms, policy ids, naming conventions, classification levels seen.
5. **Narrate** the dictionary write:
   ```
   > 📝 Building a domain dictionary from what I learned...
   > ✅ Dictionary saved (12 key terms: NCB, IDV, OD, TPL, ...)
   ```
6. Use this dictionary when drafting `AgentSpec.system_prompt`.

## Probes that yield maximum information
- "Summarise the most important policy."
- "What is the SOP for <core action>?"
- "Show a typical contract clause about <topic>."
- "List the regulatory constraints I must respect."

## Output
- `/workspace/build/probe-<dept>.json` — raw probe results.
- `/workspace/build/domain.md` — distilled vocabulary, used by Phase 2.

## Hard Rules
- Never expose probe JSON to the BA. Translate to plain language.
- Respect `classification_max` — if you don't have permission to read confidential docs, say so.
- If probes return nothing useful, ask the BA to point you at a sample document instead of guessing.
