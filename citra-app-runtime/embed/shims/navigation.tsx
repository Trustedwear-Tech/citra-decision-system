// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * `next/navigation` shim for the embed bundle.
 *
 * Aliased in at build time by scripts/build-embed.mjs — the runtime's own
 * source is NOT modified. PanelRenderer and PageBody import
 * `useRouter/usePathname/useSearchParams` from "next/navigation"; inside a
 * customer's page there is no Next router and no URL we are allowed to own
 * (the host owns the address bar).
 *
 * These are NOT no-ops. The panels use the URL as their filter-state store:
 * FilterBar writes a selection with `router.push(pathname?a=b)` and reads it
 * back through `useSearchParams()`, and PageBody merges live search params
 * over the server-passed ones. Stub these out and every filter control in an
 * embedded card silently stops working.
 *
 * So the shim keeps the same contract against an in-memory store, scoped per
 * mount. `useSyncExternalStore` gives the same re-render-on-change behaviour
 * the real hooks have.
 */
import {
  createContext,
  useContext,
  useSyncExternalStore,
  useCallback,
  type ReactNode,
} from "react";

/** The virtual path an embedded page reports. Never shown to anyone. */
export const EMBED_PATHNAME = "/embed";

type Listener = () => void;

class ParamStore {
  /** Replaced (not mutated) on every change so the snapshot identity moves —
   *  useSyncExternalStore compares by reference to decide on a re-render. */
  private current: URLSearchParams = new URLSearchParams();
  private history: URLSearchParams[] = [];
  private listeners = new Set<Listener>();

  subscribe = (cb: Listener) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  getSnapshot = () => this.current;

  private emit() {
    this.listeners.forEach((cb) => cb());
  }

  /** Seed from the host's mount options before the first render. */
  reset(init?: Record<string, string>) {
    this.current = new URLSearchParams(init ?? {});
    this.history = [];
    this.emit();
  }

  /**
   * Apply a `router.push(href)`. Only the QUERY of the href is honoured — an
   * embed renders one page, so a push that targets a different path is a
   * navigation we cannot service.
   */
  push(href: string) {
    const qIndex = href.indexOf("?");
    const path = qIndex === -1 ? href : href.slice(0, qIndex);
    const query = qIndex === -1 ? "" : href.slice(qIndex + 1);

    if (path && path !== EMBED_PATHNAME) {
      // Fail loud: a multi-page app composed into an embed would land here and
      // otherwise just appear to ignore clicks. The card keeps working; the
      // developer gets told exactly what is unsupported.
      console.error(
        `[citra-embed] navigation to "${path}" is not supported inside an ` +
          `embedded card — an embed renders a single page. Remove the ` +
          `cross-page navigate action from this panel, or use the full app.`,
      );
      return;
    }
    this.history.push(this.current);
    this.current = new URLSearchParams(query);
    this.emit();
  }

  back() {
    const prev = this.history.pop();
    if (!prev) return; // nothing to go back to — the card is the top of stack
    this.current = prev;
    this.emit();
  }
}

/**
 * One store PER MOUNT, delivered by context.
 *
 * A module-level "active store" would be simpler and wrong: two cards on the
 * same page would share one set of filter params, so changing a filter in one
 * would silently re-filter the other. Context scopes the store to the React
 * tree that owns it, which is exactly the boundary a mount is.
 *
 * The default store exists only so the hooks are safe if a panel is rendered
 * outside a provider (it cannot happen via `mount()`, but a render crash is a
 * worse failure than a no-op store).
 */
const ParamStoreContext = createContext<ParamStore>(new ParamStore());

export function createParamStore(init?: Record<string, string>): ParamStore {
  const store = new ParamStore();
  store.reset(init);
  return store;
}

export function ParamStoreProvider({
  store,
  children,
}: {
  store: ParamStore;
  children: ReactNode;
}) {
  return (
    <ParamStoreContext.Provider value={store}>
      {children}
    </ParamStoreContext.Provider>
  );
}

export function useSearchParams(): URLSearchParams {
  const store = useContext(ParamStoreContext);
  return useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
}

export function usePathname(): string {
  return EMBED_PATHNAME;
}

export interface EmbedRouter {
  push: (href: string) => void;
  replace: (href: string) => void;
  back: () => void;
  forward: () => void;
  refresh: () => void;
  prefetch: (href: string) => void;
}

export function useRouter(): EmbedRouter {
  const store = useContext(ParamStoreContext);
  const push = useCallback((href: string) => store.push(href), [store]);
  const back = useCallback(() => store.back(), [store]);
  return {
    push,
    // An embed has no history entry to replace, so replace == push. Keeping
    // the distinction would only matter to a back button we do not render.
    replace: push,
    back,
    forward: () => {},
    // `refresh()` re-fetches the RSC payload in Next. Panels here fetch their
    // own data through runtimeFetch on mount/params-change, so a no-op is
    // correct rather than merely tolerable.
    refresh: () => {},
    prefetch: () => {},
  };
}

export type { ParamStore };
