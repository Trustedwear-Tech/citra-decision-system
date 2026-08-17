// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

"use client";

/**
 * HeroBriefCopilot — the DEFAULT narrator band on every DASHBOARD PAGE
 * (page.kind='dashboard') of an app that has an agent_id. It is NOT a
 * BA-authored panel and never clutters the grid: it's a single full-width
 * elevated band pinned above the panels.
 *
 * It is PAGE-SCOPED: an app may have several dashboard pages, and the brief
 * on each is scoped to that page (by title) so "Operations" and "Finance"
 * dashboards get distinct briefs.
 *
 * It auto-runs ONCE per dashboard page, on first load. The result is cached
 * (module-level, per page load), so switching tabs and returning restores the
 * brief instead of re-running the expensive generation. After that the band
 * is a copilot that only responds when the user asks. A hard reload re-briefs.
 *
 * Collapsed (~160px): on mount it auto-fires the narrator brief and shows the
 *   reply (markdown). A returned chart block renders small inline.
 * Expanded (~520px): the band grows into a full conversational copilot —
 *   message list + input. Every assistant turn renders reply markdown PLUS
 *   any blocks[].chart via chartToEchartsOption(..., {compact:true}) at a
 *   controlled compact size.
 *
 * Both modes call the SAME POST /api/chat/{slug} and parse the shared
 * ChatResponse {reply, blocks, tool_calls} contract.
 */

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Markdown from "./Markdown";
import { runtimeFetch } from "@/lib/runtimeFetch";
import { readAgentStream, AgentStreamError } from "@/lib/agentStream";
import { chartToEchartsOption, type ChartSpec } from "@/lib/chartToEcharts";
import { EXEC_THEME_NAME } from "@/lib/executiveTheme";

// echarts-for-react must be client-only.
const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

function briefPromptFor(pageTitle?: string): string {
  return pageTitle
    ? `Brief me on the "${pageTitle}" dashboard — what changed and why, in 4-6 sentences. Focus on what this view shows.`
    : "Brief me on this dashboard — what changed and why, in 4-6 sentences.";
}

// The executive brief auto-runs ONCE per (slug, page) — on first load of a
// dashboard page. Results are cached for the lifetime of this page load, so
// switching tabs and returning RESTORES the brief instead of re-running the
// (expensive, agentic) generation. After first load the band is a copilot
// that only responds when the user asks. A hard reload clears the cache, so a
// genuinely new session re-briefs. briefInflight dedupes concurrent first
// mounts (React StrictMode double-mount / rapid navigation) to a single call.
const briefCache = new Map<string, { brief: ChatResponse; turns: Turn[] }>();
const briefInflight = new Map<string, Promise<ChatResponse>>();
const briefKey = (slug: string, pageId?: string) => `${slug}::${pageId ?? ""}`;

interface ChartBlock {
  type: "chart";
  spec: ChartSpec;
  data: Record<string, unknown>[];
}

interface ChatResponse {
  reply: string;
  blocks?: ChartBlock[];
  tool_calls?: number;
}

interface Turn {
  role: "user" | "assistant";
  content: string;
  blocks?: ChartBlock[];
}

function chartBlocks(blocks: ChatResponse["blocks"]): ChartBlock[] {
  return (blocks ?? []).filter(
    (b): b is ChartBlock => b?.type === "chart" && !!b.spec,
  );
}

function ChatChart({ block }: { block: ChartBlock }) {
  const option = chartToEchartsOption(block.spec, block.data ?? [], {
    compact: true,
  });
  return (
    <div className="hero-chat-chart">
      <ReactECharts
        option={option}
        theme={EXEC_THEME_NAME}
        notMerge
        style={{ height: 240, maxHeight: 280, width: "100%" }}
        opts={{ renderer: "svg" }}
      />
    </div>
  );
}

export default function HeroBriefCopilot({
  slug,
  pageId,
  pageTitle,
}: {
  slug: string;
  /** Current dashboard page id — re-fires the brief when it changes. */
  pageId?: string;
  /** Current dashboard page title — scopes the brief to this view. */
  pageTitle?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  // Brief (collapsed) state.
  const [brief, setBrief] = useState<ChatResponse | null>(null);
  const [briefLoading, setBriefLoading] = useState(true);
  const [briefError, setBriefError] = useState<string | null>(null);

  // Conversation (expanded) state.
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);

  async function chat(
    messages: { role: "user" | "assistant"; content: string }[],
  ): Promise<ChatResponse> {
    const res = await runtimeFetch(`/api/chat/${encodeURIComponent(slug)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    // The turn is keepalive-streamed (SSE) so it can't 504 on a long brief;
    // readAgentStream returns the same ChatResponse from the terminal `done`
    // event (or parses a pre-flight JSON error).
    try {
      return await readAgentStream<ChatResponse>(res);
    } catch (e) {
      if (e instanceof AgentStreamError) {
        throw new Error(e.message || `Chat failed (HTTP ${e.status})`);
      }
      throw e;
    }
  }

  // Auto-run the brief ONCE per dashboard page, on first load. On revisit
  // (tab switch back) restore the cached brief instead of re-running — the
  // band then behaves as a copilot that only responds when the user asks.
  useEffect(() => {
    const key = briefKey(slug, pageId);
    const cached = briefCache.get(key);
    if (cached) {
      setBrief(cached.brief);
      setTurns(cached.turns);
      setBriefLoading(false);
      setBriefError(null);
      return;
    }
    let cancelled = false;
    const prompt = briefPromptFor(pageTitle);
    setBriefLoading(true);
    setBriefError(null);
    // Share one in-flight request across concurrent mounts (StrictMode /
    // fast nav) so the brief is generated exactly once.
    let p = briefInflight.get(key);
    if (!p) {
      p = chat([{ role: "user", content: prompt }]);
      briefInflight.set(key, p);
      p.finally(() => briefInflight.delete(key));
    }
    p.then((resp) => {
      const turns: Turn[] = [
        { role: "user", content: prompt },
        {
          role: "assistant",
          content: resp.reply,
          blocks: chartBlocks(resp.blocks),
        },
      ];
      briefCache.set(key, { brief: resp, turns });
      if (cancelled) return;
      setBrief(resp);
      setTurns(turns);
    })
      .catch((e: unknown) => {
        if (!cancelled) setBriefError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setBriefLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Auto-run only on first mount per (slug, page); cache handles revisits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, pageId]);

  useEffect(() => {
    if (expanded) {
      logRef.current?.scrollTo({
        top: logRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [turns, sending, expanded]);

  async function send(text: string) {
    const msg = text.trim();
    if (!msg || sending) return;
    const history = turns.map((t) => ({ role: t.role, content: t.content }));
    const next: Turn[] = [...turns, { role: "user", content: msg }];
    setTurns(next);
    setInput("");
    setSending(true);
    try {
      const resp = await chat([...history, { role: "user", content: msg }]);
      setTurns((t) => [
        ...t,
        {
          role: "assistant",
          content: resp.reply,
          blocks: chartBlocks(resp.blocks),
        },
      ]);
    } catch (e) {
      setTurns((t) => [
        ...t,
        { role: "assistant", content: `⚠ ${(e as Error).message}` },
      ]);
    } finally {
      setSending(false);
    }
  }

  const briefCharts = chartBlocks(brief?.blocks);

  return (
    <section
      className={`hero-brief${expanded ? " hero-brief-expanded" : ""}`}
      aria-label="Dashboard copilot"
    >
      <div className="hero-brief-band">
        <div className="hero-brief-avatar">✦</div>
        <div className="hero-brief-body">
          <div className="hero-brief-label">Executive brief</div>
          {!expanded &&
            (briefLoading ? (
              <div className="hero-brief-loading">
                <span className="q-spin" /> Reading the dashboard…
              </div>
            ) : briefError ? (
              <div className="hero-brief-err">⚠ {briefError}</div>
            ) : (
              <div className="hero-brief-text">
                <Markdown
                  content={brief?.reply ?? "No brief available."}
                  className="md-chat"
                />
                {briefCharts.length > 0 && (
                  <div className="hero-brief-mini">
                    <ChatChart block={briefCharts[0]} />
                  </div>
                )}
              </div>
            ))}
        </div>
        <button
          type="button"
          className="hero-brief-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Collapse ▴" : "Ask ▾"}
        </button>
      </div>

      {expanded && (
        <div className="hero-chat">
          <div className="hero-chat-log" ref={logRef}>
            {turns.map((t, i) => (
              <div key={i} className={`hero-chat-msg hero-chat-msg-${t.role}`}>
                <div className="hero-chat-bubble">
                  {t.role === "assistant" ? (
                    <Markdown content={t.content} className="md-chat" />
                  ) : (
                    t.content
                  )}
                  {(t.blocks ?? []).map((b, bi) => (
                    <ChatChart key={bi} block={b} />
                  ))}
                </div>
              </div>
            ))}
            {sending && (
              <div className="hero-chat-msg hero-chat-msg-assistant">
                <div className="hero-chat-bubble chat-typing">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
          </div>
          <form
            className="hero-chat-input"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={`Ask a follow-up about ${pageTitle ? `"${pageTitle}"` : "this dashboard"}…`}
              disabled={sending}
            />
            <button
              type="submit"
              className="q-btn q-btn-primary"
              disabled={sending || !input.trim()}
            >
              {sending ? <span className="q-spin" /> : "Send"}
            </button>
          </form>
        </div>
      )}
    </section>
  );
}
