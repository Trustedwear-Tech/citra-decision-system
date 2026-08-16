/**
 * userToken — capture and reuse the end-user's JWT inside the runtime origin.
 *
 * The runtime renders Smart Apps in its OWN origin (`apps.…` or
 * `:3100` locally), separate from Citra-UI. That means:
 *   - The browser's Citra-UI auth (localStorage / cookies) is NOT visible
 *     to the runtime tab.
 *   - The runtime's API proxy (`/api/data/[slug]/[panelId]/route.ts`)
 *     defaults to a `scope=citra-app-runtime` service token, which has
 *     no user identity — dept-MCPs then reject the query with 403
 *     "org/dept denied: user_org=None".
 *
 * Handoff strategy: Citra-UI appends the active user's JWT to the
 * runtime URL as `?_t=<token>` when opening a Smart App. The first
 * client-side read of `getUserToken()` after page load captures that
 * query param, stashes it in sessionStorage (per-tab, gone on tab
 * close), and strips it from the visible URL so it doesn't bleed into
 * browser history / bookmarks / external referer headers.
 *
 * sessionStorage is intentional:
 *   - per-tab isolation — closing the tab clears the token
 *   - survives reloads of the runtime URL (the panel fetch happens on
 *     every render)
 *   - not shared with other Smart App tabs (each gets its own handoff)
 *
 * Trade-off vs. a proper SSO/cookie handshake: the JWT briefly lives in
 * a URL query parameter (visible in `Referer`, `history`, server access
 * logs of the target origin). For local dev + single-tenant prod this
 * is acceptable; for multi-tenant SaaS we'd want a /api/auth/handshake
 * endpoint that POSTs the token into a HttpOnly Same-Site cookie.
 */

const KEY = "citra_user_token";

let initialized = false;

/**
 * Returns the captured user JWT for this runtime tab, or null if none.
 *
 * The first call captures the `?_t=` query param into sessionStorage
 * (and strips it from the URL). Subsequent calls just read from
 * sessionStorage. SSR-safe: returns null when `window` is undefined.
 */
export function getUserToken(): string | null {
  if (typeof window === "undefined") return null;

  if (!initialized) {
    initialized = true;
    try {
      const url = new URL(window.location.href);
      const t = url.searchParams.get("_t");
      if (t) {
        try {
          sessionStorage.setItem(KEY, t);
        } catch {
          /* sessionStorage may be disabled (private mode, etc.) — fail silent */
        }
        try {
          // Mirror into a per-tab session cookie so a full reload's SSR (which
          // can't read sessionStorage) still carries the user token. No Max-Age
          // → cleared on browser close; SameSite=Strict; Secure over HTTPS so
          // the JWT never rides a plaintext request. Not HttpOnly (set from JS)
          // — the documented single-tenant trade-off vs a server handshake.
          const secure = window.location.protocol === "https:" ? "; secure" : "";
          document.cookie = `${KEY}=${encodeURIComponent(t)}; path=/; samesite=strict${secure}`;
        } catch {
          /* document.cookie may be blocked — fall back to sessionStorage only */
        }
        url.searchParams.delete("_t");
        try {
          window.history.replaceState({}, "", url.toString());
        } catch {
          /* replaceState may fail in sandboxed contexts */
        }
      }
    } catch {
      /* URL parse failed — fall through to sessionStorage read */
    }
  }

  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/**
 * Drop the captured token. Called from Citra-UI's logout path is
 * unnecessary (closing the tab clears sessionStorage), but useful if a
 * runtime page wants to expose a "Sign out" affordance of its own.
 */
export function clearUserToken(): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* noop */
  }
  try {
    document.cookie = `${KEY}=; path=/; max-age=0; samesite=strict`;
  } catch {
    /* noop */
  }
}
