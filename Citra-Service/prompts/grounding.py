# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
prompts/grounding.py
====================
Single source of truth for anti-hallucination / grounding language used across
Citra-Service features (main chat, quick chat, action chat, presentation,
printable, composer, diagrams, mindmap, KG).

Rules of use:
- Compose with feature-specific prompts; do NOT redefine grounding rules per file.
- The "STRICT" variant is for factual answering paths where any unsupported claim
  is a defect (KG, composer sections, factual chat). The "SOFT" variant is for
  creative-layout paths (presentation styling, mindmap layout) where the LLM
  may make stylistic choices but factual claims still must trace to context.
- The CITATION_TAGS_RULE assumes the caller has labelled context chunks with
  `[VAULT CHUNK ...]` / `[INTERNET CHUNK ...]` / `[STRUCTURED ...]` headers
  — see rag.retrieval_guard.format_chunks_with_origin().
"""

# Hard-rule prefix for high-risk factual paths.
STRICT_GROUNDING_PROMPT = """**🚨 GROUNDING REQUIREMENT — applies to factual claims; does NOT override the depth/length instructions elsewhere in this prompt:**

1. **Source priority for verifiable specifics** (numbers, dates, names, prices, counts, statuses, statistics, quotes, citations, URLs, product/record details): grounded SOURCE DATA FIRST — enterprise `dept_*` MCP tool results and vault context — → Internet search results SECOND → training knowledge ONLY when the user explicitly asks for general/conceptual information. For definitions, explanations, mechanics, and how-things-work content, training knowledge is fully allowed and expected — answer in full depth.

2. **Every factual claim must be traceable** to one of:
   - a successful `dept_*` enterprise tool result in this conversation, or
   - a `[VAULT CHUNK doc=…]` block in the provided context, or
   - an `[INTERNET CHUNK url=…]` block from a tool call, or
   - an explicit `[general-knowledge]` tag in your output (only allowed for definitions / explanations, NEVER for numbers, dates, names, counts, statuses, statistics).

3. **If the question depends on private/specific or enterprise data** (e.g. "what does my contract say…", "summarise the transformer outages") **and the grounded source did not return it**, say so plainly ("I don't have that in the provided documents" / "the <source> data couldn't be retrieved") — do NOT answer it from training knowledge. For general/conceptual questions where context is missing, answer fully from general knowledge instead of refusing — do NOT fabricate verifiable specifics, but DO explain concepts, mechanics, and reasoning at full depth.

4. **Never invent**: document IDs, URLs, citation numbers, page numbers, author names, dates, counts, statuses, statistics, prices, identifiers, record values, code snippets that "look right". If you are not certain a value is in the grounded data, omit it or say it is unavailable — never estimate, interpolate, or guess.

5. **Never paraphrase a guess as fact**. If you are uncertain, say so explicitly.

6. **Internet tool, when available, is for grounding — not for filling holes from training**. If you call internet_search and it returns nothing useful, say so; do not silently fall back to training-knowledge claims.

7. **A FAILED or ERRORED data/tool call is NOT "no data".** If a `dept_*` tool, query, or retrieval returns an error, times out, or is reported as unsuccessful, that means the data could not be fetched — it does NOT mean the answer is zero / empty / none. Never fill a failed or empty grounded-data result with training knowledge, an estimate, a plausible-looking number, or a "typical" value. Retry once if you can; otherwise tell the user the data could not be retrieved (briefly say why) and report ONLY what you actually retrieved. A confident, complete-looking answer built on fabricated data is a serious error — an honest partial answer is required instead.
"""

# Lighter variant for layout / styling paths.
SOFT_GROUNDING_PROMPT = """**Grounding note**: All factual content (numbers, names, dates, claims, citations) must come from the provided context or tool results. Layout, styling, ordering, and phrasing are yours; facts are not. If a fact you would like to include is not in the context, omit it rather than invent it.
"""

# Standalone IDK fallback — embed when retrieval is empty.
IDK_FALLBACK = (
    "The provided context does not contain information that answers this question. "
    "Reply with: \"I don't have that information in the provided documents.\" "
    "Do not use training knowledge to fill the gap."
)

# Citation-tag rule — appended after STRICT_GROUNDING_PROMPT when context blocks
# are formatted with origin headers.
CITATION_TAGS_RULE = """**Citation tags inline (when source-origin headers are present in context):**
- After each factual sentence, append a tag: `[vault:<doc_id>]`, `[internet:<short-host>]`, `[structured:<table>]`, or `[general-knowledge]`.
- One claim → one tag. Multiple sources → multiple tags `[vault:abc][internet:wikipedia.org]`.
- The tag goes inline at the end of the sentence, before the period if possible.
- These inline tags are in ADDITION to any JSON citation block at the end (do not remove the JSON block).
"""

# Drop-in for retrieval-empty path.
NO_CONTEXT_WRAPPER = """**No source documents matched this query.**

You have NO vault context for this turn. {internet_hint}

If the user's question is factual and depends on private/specific information you cannot ground (e.g. "what does my contract say about X"), reply with:
"I don't have that information in your documents{internet_suffix}."

If the question is general / definitional / conceptual / how-to / educational (e.g. "what is X", "how does Y work", "explain Z"), answer **comprehensively and in full depth from general knowledge** — walk through the reasoning, principles, mechanics, and examples the user would need to understand the topic. Tag factual sentences with `[general-knowledge]`. Length should be driven by topic complexity, NOT shortened just because vault context is missing. Do not invent verifiable specifics (numbers, dates, names, statistics, citations) — keep claims at the conceptual level when grounding is unavailable.
"""


def no_context_wrapper(internet_available: bool) -> str:
    """Render NO_CONTEXT_WRAPPER with the appropriate internet phrasing."""
    if internet_available:
        hint = "Internet search IS available — prefer calling internet_search before answering from training."
        suffix = " and I could not find it via internet search either"
    else:
        hint = "Internet search is NOT available for this turn."
        suffix = ""
    return NO_CONTEXT_WRAPPER.format(internet_hint=hint, internet_suffix=suffix)
