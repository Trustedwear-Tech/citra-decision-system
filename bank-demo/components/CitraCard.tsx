// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

/**
 * The entire Citra integration. Everything else in this project is Acme Bank's
 * own application.
 *
 * Three things happen here, and they are the three things any integrator does:
 *   1. load citra.js from the Citra runtime (one script tag)
 *   2. Citra.init({ getToken }) — the HOST supplies the officer's token
 *   3. citra.mount(el, { embed, recordId }) — the HOST says which record
 *
 * There is no Citra npm package, no build step, and no shared React. The card
 * renders inside a shadow root, so the bank's CSS and the card's cannot reach
 * each other.
 */
import { useEffect, useRef, useState } from "react";

type DecisionEvent = { action?: string; decision?: string; [k: string]: unknown };

declare global {
  interface Window {
    Citra?: {
      init(opts: {
        baseUrl?: string;
        getToken: () => string | Promise<string | null> | null;
        onError?: (e: unknown) => void;
      }): {
        mount(
          target: string | Element,
          opts: {
            embed: string;
            recordId: string;
            theme?: Record<string, unknown>;
            onRecommendation?: (e: unknown) => void;
            onDecision?: (e: DecisionEvent) => void;
          },
        ): { destroy(): void };
      };
    };
  }
}

export default function CitraCard({
  runtimeUrl,
  embedKey,
  recordId,
  onEvent,
}: {
  runtimeUrl: string;
  embedKey: string;
  recordId: string;
  onEvent: (name: string, payload: unknown) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mounted = useRef(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mounted.current || !hostRef.current) return;

    if (!embedKey) {
      setError(
        "No CITRA_EMBED_KEY configured. Get one from My Apps → Export on the " +
          "decision card, then set it in .env.local and restart.",
      );
      return;
    }

    // Called before EVERY request, so a refreshed session is picked up without
    // remounting. Same-origin — the token never sits in JS-readable storage.
    async function getToken(): Promise<string | null> {
      try {
        const r = await fetch("/api/token");
        if (!r.ok) return null;
        return (await r.json()).token ?? null;
      } catch {
        return null;
      }
    }

    function mount() {
      const Citra = window.Citra;
      if (!Citra || !hostRef.current) return;
      mounted.current = true;
      const citra = Citra.init({
        baseUrl: runtimeUrl,
        getToken,
        onError: (e) => setError(String(e)),
      });
      citra.mount(hostRef.current, {
        embed: embedKey,
        recordId,
        // Acme Bank's own look, passed in — the card is themed BY the host.
        theme: { primary: "#0f766e", radius: 8, density: "compact" },
        onRecommendation: (e) => onEvent("onRecommendation", e),
        onDecision: (e) => onEvent("onDecision", e),
      });
    }

    if (window.Citra) {
      mount();
      return;
    }
    // Loaded here rather than as a literal <script src> because the runtime
    // origin is configuration — a customer points this at their deployment. In
    // a real integration it IS a plain script tag, exactly as the snippet shows.
    const s = document.createElement("script");
    s.src = `${runtimeUrl.replace(/\/+$/, "")}/v1/citra.js`;
    s.onload = mount;
    s.onerror = () =>
      setError(`Could not load ${s.src} — is the Citra runtime reachable?`);
    document.head.appendChild(s);
  }, [runtimeUrl, embedKey, recordId, onEvent]);

  return (
    <>
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {/* The ONLY Citra-owned element in this application. */}
      <div id="citra-decision" ref={hostRef} />
    </>
  );
}
