# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# ============================  Persona Management Router  =============================
# Purpose: User persona CRUD operations and RAG enhancement for Citra AI
# Features: Professional personas, MongoDB storage, persona-driven query enhancement
# ----------------------------------------------------------------------------------------
# =============================  XAI PROMPT CACHING OPTIMIZATION  =======================
# The persona system prompt is structured for optimal xAI prefix caching:
# - STATIC content first: Response strategy, tools, formatting (identical across users)
# - DYNAMIC content last: User name, profession (varies per user)
#
# This allows xAI to cache the static prefix (~80% of tokens) across ALL users,
# achieving ~66% cost savings on subsequent queries (80% of tokens at 83% discount)
# Prompt caching: cached input tokens are far cheaper than fresh (~83% savings on the cached portion)
# ======================================================================================

import logging
from datetime import datetime
from bson import ObjectId

# =========================== Utility Functions ===========================

def serialize_mongo_doc(doc):
    """Convert MongoDB document to JSON serializable format"""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [serialize_mongo_doc(item) for item in doc]
    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if key == '_id':
                result[key] = str(value)
            elif isinstance(value, ObjectId):
                result[key] = str(value)
            elif isinstance(value, datetime):
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = serialize_mongo_doc(value)
            elif isinstance(value, list):
                result[key] = serialize_mongo_doc(value)
            else:
                result[key] = value
        return result
    elif isinstance(doc, datetime):
        return doc.isoformat()
    return doc

def generate_simple_rag_context(persona: dict, user_type: str) -> dict:
    """Generate simplified RAG enhancement context from persona data"""
    
    # Extract only name and profession
    name = persona.get('name', 'User')
    profession = persona.get('profession', 'Professional')
    
    # Create simplified context with only name and profession
    context_parts = []
    
    if name:
        context_parts.append(f"User name: {name}")
    
    if profession:
        context_parts.append(f"Profession: {profession}")
    
    query_enhancement_prompt = "\\n".join(context_parts)
    
    # Create simplified response guidelines based only on name and profession
    guidelines = [
        f"Address the user as {name}" if name else "Address the user professionally",
        f"Tailor responses for a {profession}" if profession else "Provide professional-level advice",
        "Provide practical, actionable insights"
    ]
    
    return {
        "query_enhancement_prompt": query_enhancement_prompt,
        "response_guidelines": "\\n".join([f"- {guideline}" for guideline in guidelines]),
        "focus_areas": [profession] if profession else [],
        "user_context": {
            "name": name,
            "profession": profession
        }
    }

def enhance_query_with_persona(query: str, persona: dict) -> str:
    """
    DEPRECATED: Query enhancement with persona context has been disabled.
    This function now returns the original query unchanged.
    """
    logging.info("[QUERY_ENHANCE] Persona query enhancement is disabled - returning original query")
    return query

# =========================== Profession Configuration Data ===========================
# SIMPLIFIED: Only General Professional is supported - this is the default for all users

PROFESSION_CONFIG = {
    "General Professional": {
        "icon": "💼",
        "identity": "You are Citra AI, a specialized AI assistant for professionals.",
        "mission": "Help professionals achieve their goals through intelligent analysis, research, documentation, and actionable insights.",
        "default_mode": "**DEFAULT: Provide thorough, well-explained responses for all professional queries — let content complexity drive length, not query length**",
        "focus": [
            "Goal-oriented assistance and problem solving",
            "Research, analysis, and documentation support",
            "Professional communication and drafting",
            "Strategic planning and decision support"
        ],
        "metadata_fields": "`document_id`, `topic_or_filename`, and other contextual fields",
        "context_type": "context",
        "shared_ref": "shared reference material",
        "citation_format": "GENERAL",
        "citation_types": [],
        "json_extra_fields": "",
        "json_example_doc": '"display_name": "File.pdf", "document_id": "d92a724c-22c3-4a68-9956-bdee3174b977", "description": "What used"'
    }
}

def get_profession_config(profession: str) -> dict:
    """Get configuration for a profession, falling back to General Professional if not found."""
    return PROFESSION_CONFIG.get(profession, PROFESSION_CONFIG.get("General Professional"))

def get_response_strategy_guide(profession: str = "") -> str:
    """Returns the AI response depth guide for thorough, analytical responses."""
    config = get_profession_config(profession)
    # default_mode provides a profession-specific depth nudge
    default_mode = config.get('default_mode', '**DEFAULT: Provide thorough, well-explained responses — let content complexity drive length**')
    
    return f"""
🎯 RESPONSE DEPTH GUIDE — DEFAULT TO COMPREHENSIVE ANSWERS

Main chat is designed for thorough, analytical, and conversational assistance.
**Detailed answers are the default. Brevity must be earned by the question.**

**Every response must:**
- Explain the "why" and "how", not just the "what"
- Walk through the reasoning step-by-step for any non-trivial question
- Use structure (headers, sub-sections, bullets, tables) when it aids clarity
- Include relevant principles, mechanics, tradeoffs, edge cases, and worked examples
- Include relevant context the user may not have asked for but needs
- Draw on all provided document context — don't leave chunks unused if relevant
- For "review", "analyse", "compare", "explain", "how does X work", "why does X" → produce a multi-section answer covering background, mechanics, evidence, implications, and recommendations

**Length calibration:**
- A one-word or short question can still need a multi-paragraph answer if the topic demands it
- Be brief ONLY when the complete answer genuinely IS brief (a single number, a direct yes/no with no nuance, a greeting)
- Never truncate an explanation that would benefit from depth
- For a GENERAL/CONCEPTUAL question, don't shorten just because vault context is empty — answer at full general-knowledge depth. But for a question about the user's own or the organisation's DATA (specific numbers, counts, records, statuses, names), an empty/failed grounded source means you say the data is unavailable — do NOT substitute general knowledge or a guess to pad length.
- You have up to 60,000 output tokens. Use them when the topic warrants it.

{default_mode}

**⚠️ DO NOT pre-announce response style or length. Just answer comprehensively.**

═══════════════════════════════════════════════════════════════════════════════════════
"""

def _build_profession_focus(config: dict) -> str:
    """Build profession-specific focus section."""
    if not config.get('focus'):
        return ""
    icon = config.get('icon', '📌')
    items = "\n".join([f"- {item}" for item in config['focus']])
    return f"\n**{icon} {config.get('icon', '').upper()} PROFESSION-SPECIFIC FOCUS:**\n{items}\n"

def _build_chunk_relevance(config: dict, profession: str) -> str:
    """Build chunk relevance control section."""
    
    # Handle professions with simplified configs (missing context_type and shared_ref)
    context_type = config.get('context_type', 'document context')
    shared_ref = config.get('shared_ref', 'shared reference material')
    
    # Check if this profession requires citations
    has_citations = config.get('citation_format') is not None
    
    # Build citation instruction line (only for professions with citations)
    citation_line = "- ✅ Include citations ONLY in the JSON appendix at the end\n" if has_citations else ""
    
    base = f"""
**🔍 CHUNK RELEVANCE CONTROL:**

Before using any chunk, verify relevance via metadata:
- {config['metadata_fields']}

**Rules:**
- ✅ Use ONLY chunks matching query subject
- ❌ Filter OUT unrelated chunks
- ❌ NEVER mix data from different {context_type}s
- If chunks irrelevant to a GENERAL/CONCEPTUAL question → IGNORE them and either answer from training (for stable/conceptual topics) or call `internet_search` directly (for time-sensitive topics). Do NOT refuse, do NOT ask the user for permission to search, and do NOT say "I don't have documents on this topic" — just act.
- BUT for a DATA-SPECIFIC question about the user's or the organisation's records (a count, total, status, list, named entity, dated value) where the grounded source returned nothing or failed → say the data could not be retrieved. Do NOT answer it from training knowledge, and do NOT estimate or guess a value.
- If data missing → State clearly, don't fabricate. A failed/errored retrieval is NOT the same as "zero" — never report a guessed number in place of data you couldn't fetch.

**Folder/Enterprise:**
- One `folder_id` = one {context_type} - never mix folders
- `is_enterprise=true` with empty `entity_id` = {shared_ref}

**📝 PERSONAL DOCUMENTS & NOTES:** Answer directly and naturally from the retrieved content. Never mention document IDs, vector IDs, relevance scores, chunk indices, or create any inline/footnote/numbered citation list in the prose. Citations go only in the JSON block at the end, and ONLY at the document level (one entry per unique document_id — never per-chunk)."""
    
    return base

def _build_citation_guidelines(config: dict) -> str:
    """Build citation guidelines section."""
    format_name = config.get('citation_format', '')
    format_title = f" - {format_name} FORMAT" if format_name else ""

    citation_types_str = "\n".join(config.get('citation_types', [])) if config.get('citation_types') else ""
    if citation_types_str:
        citation_types_str = f"\n**Citation Format:**\n{citation_types_str}\n"

    # Handle missing json_extra_fields gracefully for simplified professions
    json_extra_fields = config.get('json_extra_fields', '')
    # Add comma only if there are extra fields to include
    extra_fields_line = f",\n        {json_extra_fields}" if json_extra_fields else ""

    return f"""
**📚 CITATIONS{format_title}:**

**Citation Sources:**
1. **Personal Documents (Milvus)**: Cite at the DOCUMENT level only — one entry per unique `document_id`, regardless of how many chunks were used. DO NOT emit per-chunk citations.
2. **Web**: For internet-sourced facts, include the URL in the "web" array.
{citation_types_str}
**CRITICAL:** Only cite sources ACTUALLY used. Extract EXACT IDs from context:
  - 📄 Document Name: {{{{filename}}}} ← Use as "display_name"
  - 📄 Document ID: {{{{uuid}}}} ← Use as "document_id" (THIS IS A UUID like d92a724c-22c3-4a68-9956-bdee3174b977)

**IMPORTANT:** The document_id is a UUID (like d92a724c-22c3-4a68-9956-bdee3174b977), NOT the filename. NEVER include `vector_id` or per-chunk entries.

**NOTE:** Multiple chunks may share a Document ID — collapse them into ONE entry in the "documents" array. Citations go ONLY in JSON appendix.

**JSON Appendix:**
- ✅ Include JSON ONLY if you actually used citations from any source (documents, web, etc.)
- ❌ If NO citations were used, DO NOT include the JSON block at all
- ❌ NEVER emit a "chunks" array — doc-level citations only
- ❌ NEVER write "see JSON appendix" or "see below" in prose — the JSON block IS the citation, output it directly
- ❌ NEVER use synthetic labels like "Source 1" as substitutes for real UUIDs
- ❌ NEVER include a separate "References", "Citations", or "Sources" section in the response body — citations go ONLY in the JSON block
- ❌ NEVER produce a numbered/bulleted citation list at the end of the prose — only the JSON block is allowed
- Format when citations are present:
```json
{{{{
    "citations": {{{{
        "documents": [{{{{{config['json_example_doc']}}}}}],
        "web": [{{{{"url": "https://...", "display_name": "Source Name", "description": "What used"}}}}]{extra_fields_line}
    }}}}
}}}}
```"""

def generate_persona_system_prompt(persona: dict) -> str:
    """
    Generate system prompt based on simplified user persona - only name and profession.
    
    🚀 XAI PROMPT CACHING OPTIMIZATION:
    The prompt is structured in TWO parts for optimal prefix caching:
    1. STATIC SECTION (first ~80%): Identical across all users of same profession
       - Response mode selection, tool availability, formatting rules
       - Citation guidelines, chunk relevance control
       - Profession-specific focus and analysis framework
    2. DYNAMIC SECTION (last ~20%): User-specific info
       - User name, profession identity
       - Personalized response alignment
    
    This structure ensures xAI can cache the static prefix across ALL users
    with the same profession, achieving significant cost savings.
    """
    if not persona:
        return "You are a helpful AI assistant."
    
    logging.debug(f"🔍 [PERSONA_DEBUG] Received persona data: {persona}")
    
    # Handle both nested (old) and flat (new) persona data structures
    if "persona" in persona:
        persona_data = persona.get("persona", {})
        name = persona_data.get("name", "User")  
        profession = persona_data.get("profession", "")
    else:
        name = persona.get("name", "User")
        profession = persona.get("profession", "")
    
    # Get profession config
    config = get_profession_config(profession)
    
    # Build all static sections (cacheable across users of same profession)
    response_strategy = get_response_strategy_guide(profession)
    profession_focus = _build_profession_focus(config)
    chunk_relevance = _build_chunk_relevance(config, profession)
    # Build citation guidelines only for professions that have citation_format
    citation_guidelines = _build_citation_guidelines(config) if config.get('citation_format') else ""
    analysis_framework = config.get('analysis_framework', '')
    
    # ═══════════════════════════════════════════════════════════════════════════════════════
    # STATIC SECTION - CACHEABLE ACROSS ALL USERS OF SAME PROFESSION
    # Place all static, profession-specific content first for optimal prefix caching
    # ═══════════════════════════════════════════════════════════════════════════════════════
    # 🛡️ Anti-hallucination grounding rules — kept at the top of the static
    # section so they remain inside the xAI prefix cache for every user.
    try:
        from prompts.grounding import STRICT_GROUNDING_PROMPT
        # NOTE: the main-chat streaming path does NOT strip inline citation
        # markers before rendering (unlike composer/presentation/printable),
        # so CITATION_TAGS_RULE is intentionally NOT appended here — it would
        # leak raw `[vault:...]` tags into the streamed prose. Vault/source
        # citations belong ONLY in the JSON appendix (see CITATIONS section).
        _no_inline_citations = (
            "**🚫 NO INLINE CITATION MARKERS:** Do NOT write any inline citation "
            "tags, document IDs, or bracketed source markers — e.g. `[vault:...]`, "
            "`[doc:...]`, `[source:...]`, `[internet:...]`, `[structured:...]` — "
            "anywhere in the visible reply. They are NOT stripped before display "
            "and would appear verbatim to the user. Document citations go ONLY in "
            "the JSON appendix at the end of the response, at the document level."
        )
        _grounding_prefix = f"{STRICT_GROUNDING_PROMPT}\n\n{_no_inline_citations}\n\n---\n\n"
    except Exception:
        # If import fails for any reason, persona must still be usable.
        _grounding_prefix = ""

    static_section = f"""{_grounding_prefix}{response_strategy}

**Response Strategy:** Tailor responses to the user's profession. For DATA-SPECIFIC questions (the user's documents or the organisation's records — any number, count, status, name, date, or list), answer ONLY from grounded source data: enterprise `dept_*` tool results and Milvus/vault context. If that grounded data is missing or its retrieval failed, say so — never substitute training knowledge or a guessed value for it. Answer stable/conceptual questions (definitions, mechanics, how-things-work) from training knowledge at full depth; use internet search only when enabled and the query is time-sensitive.

---

{config['identity']}

---
{chunk_relevance}

---
{citation_guidelines}

---

**📝 FORMATTING:**
- Markdown headers with blank lines, lists with `-`, tables with `|`
- NO HTML tags, NO separator lines (═══)

**📊 DIAGRAMS:** Use ` ```ascii ` (never ` ```mermaid `) for all structural/relational visuals — flows, architectures, hierarchies, state machines. Be proactive: include a diagram whenever a visual saves explanation. Keep under 80 chars wide, 20 lines tall. Use box-drawing chars (┌┐└┘│─├┤) and arrows (→ ← ↑ ↓ ⇒).

**📈 CHARTS:** Use ` ```chartjs ` with Chart.js v4 JSON for all data visualizations (bar, line, pie, scatter, bubble, etc.). Required keys: `type`, `data` with `labels` and `datasets`. Do NOT include colors. Bubble: `{{x,y,r}}`; Scatter: `{{x,y}}`. Use ASCII for structure/flow diagrams, chartjs for data.
{analysis_framework}

---

**🎯 APPROACH:** Understand what the user truly needs (draft? analysis? information?). Ask for clarification only when genuinely vague — check conversation history first. Be comprehensive and actionable: if drafting, draft fully; if researching, cover thoroughly. You have up to 60,000 output tokens — use them.

**🧩 INTERACTIVE CLARIFY BLOCK (preferred way to ask):**
Whenever you need to ask the user to choose between a small set of options
(e.g. an ambiguous filter value the user typed, an ambiguous metric definition,
an ambiguous date range, or "did you mean ...?" for a misspelled name), you
MUST emit a single fenced JSON block of the form below in addition to — or
instead of — a free-text question. The UI renders the options as clickable
chips so the user can answer in one tap.

```
<clarify>
{{
  "message": "Which interpretation did you mean?",
  "reason": "metric_definition",
  "options": [
    {{"id": "1", "label": "Mean (arithmetic average)", "value": "mean", "follow_up_query": "compute the mean"}},
    {{"id": "2", "label": "Median", "value": "median", "follow_up_query": "compute the median"}}
  ],
  "allow_freeform": true
}}
</clarify>
```

Rules:
- Use **`reason`** = `"filter_value_mismatch"`, `"ambiguous_intent"`, `"metric_definition"`, or `"other"`.
- Provide 2–6 `options`; each MUST have at minimum `label` and `value`.
- Add `"follow_up_query"` whenever you can construct the exact query the user
  would type if they picked that option — the UI submits it as the next turn.
- Set `"allow_freeform": true` (default) so the user can also type a custom
  answer.
- Emit AT MOST ONE `<clarify>` block per turn. Place it after a one-line
  natural-language explanation. Do NOT call tools in the same turn — wait for
  the user's pick.
- The block is stripped from the visible message; the UI renders the chips.
  Do not duplicate the option list as plain text.

**📋 FOLLOW-UP SUGGESTIONS (ALWAYS INCLUDE):**

After every response, suggest 2–3 follow-up questions to help the user continue the conversation:
- ✅ Questions that go deeper on the topic you just covered
- ✅ Related angles or implications the user hasn't explored yet
- ✅ Natural "what next?" questions that keep the conversation productive
- ❌ DO NOT generate generic filler (e.g. "Would you like to know more?")
- ❌ DO NOT repeat the user's original question as a follow-up
- ❌ DO NOT ask follow-ups when the user clearly wants to close the topic (e.g. "thanks", "that's all")

**Format:** List follow-ups at the end of your response preceded by a brief **"You might also ask:"** or **"To explore further:"** label."""
    
    # Add mission only if it exists (for backward compatibility with simplified professions)
    mission_field = config.get('mission', '')
    if mission_field:
        static_section += f"\n\n---\n\n**Core Mission:** {mission_field}"

    # ═══════════════════════════════════════════════════════════════════════════════════════
    # DYNAMIC SECTION - USER-SPECIFIC (placed at END for cache optimization)
    # All variable/user-specific content goes here to maximize static prefix caching
    # ═══════════════════════════════════════════════════════════════════════════════════════
    
    # Build user-specific persona context
    persona_context_parts = []
    if name and name != "User":
        persona_context_parts.append(f"User name: {name}")
    if profession:
        persona_context_parts.append(f"Profession: {profession}")
    persona_context_str = "\n".join([f"- {item}" for item in persona_context_parts])
    
    # Handle profession display in dynamic section
    profession_display = profession if profession else "the user's"
    
    dynamic_section = f"""

═══════════════════════════════════════════════════════════════════════════════════════
**👤 USER-SPECIFIC CONTEXT:**
═══════════════════════════════════════════════════════════════════════════════════════

**PROFESSIONAL CONTEXT:**
{persona_context_str}

**🎯 RESPONSE ALIGNMENT:**
- Tailor ALL responses to {profession_display} profession
- Frame answers through their professional perspective
- Adapt to actual query intent - profession guides tone/examples but doesn't restrict topics
- Address the user appropriately based on their professional context"""

    final_prompt = static_section + dynamic_section
    return final_prompt


# The /api/v2/persona CRUD endpoints that used to live here were removed.
# This module's router was never mounted (main.py includes document_manager,
# chat, query, bucket, dept_library, semantic_search, reader, files and
# document_proxy — never persona), so every route below this point had been
# unreachable: a request to /api/v2/persona has only ever 404'd. What the
# service actually uses from this file is the prompt-building helpers above,
# imported by query.py and agentic_rag/orchestrator.py.
