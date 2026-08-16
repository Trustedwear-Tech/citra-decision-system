/**
 * featureFlags.js
 *
 * Build-time product-surface switches. These are NOT user settings and NOT
 * runtime config — they exist so a surface can be taken off the product
 * without deleting the code that implements it.
 *
 * Keep the list short. A flag earns its place only when the feature it gates
 * is genuinely expected to come back.
 */

/**
 * Personal Data Store (a.k.a. the personal vault / personal folders).
 *
 * OFF: main chat is a purely ENTERPRISE surface — it searches dept MCP
 * sources and the enterprise SOP library, and nothing else. With this false:
 *
 *   • the chat composer's "+" (unified upload modal) is hidden — main chat
 *     takes no file input at all,
 *   • the personal Data Store screen, the right-rail folder panel and the
 *     mobile folder panel are not mounted,
 *   • the "Data Store" / "Personal Store" source toggles are gone from the
 *     chat header, the mobile toggle row and the Connection Scope modal,
 *   • `use_personal_data` is never sent as true from main chat, and
 *     Citra-Service ignores it on the main-chat path anyway (query.py) —
 *     `personal_data_tool` / `list_vault_files` are not offered to the chat
 *     LLM (streaming_response.py).
 *
 * This flag deliberately does NOT reach the composers (report / presentation /
 * printable) or Quick Chat — those still read the vault. Turning it back on is
 * a one-line change here; every call site stays wired.
 */
export const PERSONAL_VAULT_ENABLED = false;

export default { PERSONAL_VAULT_ENABLED };
