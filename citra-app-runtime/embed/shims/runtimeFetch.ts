/**
 * `@/lib/runtimeFetch` shim for the embed bundle.
 *
 * Aliased in at build time; src/lib/runtimeFetch.ts is untouched.
 *
 * The real one is a SAME-ORIGIN fetch that reads the user JWT captured from a
 * `?_t=` URL param on app launch. Neither half holds in an embed:
 *
 *   - Origin: the bundle runs on the bank's origin, so every `/api/...` call
 *     has to be rewritten onto the Citra runtime's origin.
 *   - Token: there is no launch URL to carry `?_t=`. The host supplies the
 *     officer's JWT through `Citra.init({ getToken })`, which is also why it
 *     can be refreshed without remounting — the callback is invoked per call,
 *     not captured once.
 *
 * Every browser→API call in the renderer already funnels through this one
 * function, which is what makes the swap a single file instead of an audit of
 * every panel.
 *
 * IT IS ALSO THE ONLY PLACE THE HOST'S CALLBACKS CAN HANG FROM. `onDecision`
 * needs to fire when an officer approves or rejects — but the approve button
 * lives inside PanelRenderer, which the embed build deliberately does not
 * modify. Observing the API traffic gives the same signal without touching the
 * renderer: an approve is a POST to `.../approve/...`, and its response body is
 * the outcome. See `subscribe()` below.
 */

type TokenSource = () => string | null | Promise<string | null>;

/** What an observer sees. `body` is the parsed JSON response when the response
 *  was JSON and OK — observers never need to re-read the stream. */
export interface ApiEvent {
  url: string;
  method: string;
  status: number;
  ok: boolean;
  requestBody: unknown;
  body: unknown;
}

type Observer = (e: ApiEvent) => void;

let baseUrl = "";
let getToken: TokenSource = () => null;
let embedKey: string | null = null;
const observers = new Set<Observer>();

/**
 * The embed key, sent on EVERY request as `X-Citra-Embed-Key`.
 *
 * Not decoration. Only the FIRST call (`/api/embed/{key}/spec`) carries the key
 * in its path; everything after it — run, panel data, detail, approve — is
 * addressed by SLUG, and slug resolution upstream is prod-first. A PROMOTED app
 * exists in both stores, so without this header a bank's UAT card would silently
 * read and write PRODUCTION records from the moment the app was promoted.
 *
 * The server treats it as a hint and verifies the key exists in that
 * environment bound to that slug, so this cannot be used to reach an
 * environment the page does not hold a key for.
 */
export function setEmbedKey(key: string | null) {
  embedKey = key;
}

/**
 * Configured once by the embed entry, before anything renders.
 *
 * SINGLE-INSTANCE BY DESIGN: `baseUrl` and `getToken` are process-wide, not
 * per-mount, because `runtimeFetch` is called as a plain function from deep
 * inside the renderer where no React context is reachable. Multiple `mount()`
 * calls on one page are supported and share this config — which is correct,
 * since they belong to one `Citra.init()` and therefore one Citra deployment
 * and one signed-in officer. Two DIFFERENT `Citra.init()` instances pointing at
 * different origins on one page is not supported; `init()` fails loud on that.
 */
export function configureRuntimeFetch(opts: {
  baseUrl: string;
  getToken: TokenSource;
}) {
  // Trailing slash would produce `//api/...` after concatenation. Harmless on
  // most servers, but it shows up in logs and CORS errors as a phantom path.
  baseUrl = opts.baseUrl.replace(/\/+$/, "");
  getToken = opts.getToken;
}

export function currentBaseUrl(): string {
  return baseUrl;
}

/** Observe every API response. Returns an unsubscribe function. */
export function subscribe(fn: Observer): () => void {
  observers.add(fn);
  return () => {
    observers.delete(fn);
  };
}

export function resetRuntimeFetch() {
  baseUrl = "";
  getToken = () => null;
  observers.clear();
}

function emit(e: ApiEvent) {
  observers.forEach((fn) => {
    try {
      fn(e);
    } catch (err) {
      // A throwing host callback must not break the officer's card. Report it
      // and carry on — swallowing silently would hide a bug in THEIR code.
      console.error("[citra-embed] an embed callback threw:", err);
    }
  });
}

export async function runtimeFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  if (!baseUrl) {
    // Fail loud. A relative /api call from the bank's page would hit THEIR
    // server and 404, and the panel would report a confusing parse error
    // instead of the real cause.
    throw new Error(
      "[citra-embed] runtimeFetch used before configureRuntimeFetch — " +
        "call Citra.init({ baseUrl }) first.",
    );
  }

  const url = input.startsWith("http")
    ? input
    : `${baseUrl}${input.startsWith("/") ? "" : "/"}${input}`;

  const token = await getToken();
  const headers = new Headers(init.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (embedKey) headers.set("X-Citra-Embed-Key", embedKey);
  // Explicitly omit credentials: auth travels as a bearer header, and sending
  // cookies would make the wildcard CORS policy illegal.
  const res = await fetch(url, { ...init, headers, credentials: "omit" });

  if (observers.size) {
    // Read from a CLONE so the renderer still gets an unconsumed body. Parsing
    // failures are ignored on purpose: a non-JSON or empty response is normal
    // for some routes and is not an observer's concern.
    let body: unknown = undefined;
    try {
      body = await res.clone().json();
    } catch {
      body = undefined;
    }
    let requestBody: unknown = undefined;
    try {
      requestBody =
        typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    } catch {
      requestBody = undefined;
    }
    emit({
      url,
      method: (init.method || "GET").toUpperCase(),
      status: res.status,
      ok: res.ok,
      requestBody,
      body,
    });
  }

  return res;
}
