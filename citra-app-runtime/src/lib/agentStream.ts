// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Read a keepalive-SSE stream from the agent proxy routes (/api/chat,
 * /api/run, /api/apps/.../approve) and resolve to the terminal result.
 *
 * smart-app-service wraps long agent turns in an SSE stream (see sse.py): an
 * immediate `{type:"status"}` plus periodic `{type:"heartbeat"}` frames keep the
 * gateway connection alive (no 504 on turns that outlast ALB ~60s / CF ~100s),
 * then a terminal `{type:"done", result}` or `{type:"error", status, message}`.
 * This reads that stream, surfaces progress via an optional callback, and
 * returns `result` — the SAME JSON shape the endpoint used to return
 * synchronously — or throws `AgentStreamError` on failure.
 *
 * Defensive fallback: if the response isn't `text/event-stream` (a pre-flight
 * 4xx returned as JSON, or an older non-streaming server), it is parsed as JSON
 * exactly as the callers did before.
 */

export class AgentStreamError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "AgentStreamError";
    this.status = status;
  }
}

export async function readAgentStream<T = unknown>(
  res: Response,
  onProgress?: (text: string) => void
): Promise<T> {
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("text/event-stream") || !res.body) {
    // Not a stream (pre-flight error JSON, or a non-streaming server): behave
    // exactly as the old `await res.json()` callers did.
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
      const msg =
        (typeof data.detail === "string" && data.detail) ||
        (typeof data.error === "string" && data.error) ||
        `request failed (${res.status})`;
      throw new AgentStreamError(msg, res.status);
    }
    return data as T;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let result: T | undefined;
  let settled = false;

  // Returns true once the terminal `done` event is seen; throws on `error`.
  const handleFrame = (frame: string): void => {
    for (const line of frame.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      let ev: Record<string, unknown>;
      try {
        ev = JSON.parse(payload);
      } catch {
        continue;
      }
      if (ev.type === "status" || ev.type === "heartbeat") {
        if (onProgress) {
          onProgress(typeof ev.text === "string" ? ev.text : "Working…");
        }
      } else if (ev.type === "done") {
        result = ev.result as T;
        settled = true;
      } else if (ev.type === "error") {
        throw new AgentStreamError(
          typeof ev.message === "string" ? ev.message : "agent turn failed",
          typeof ev.status === "number" ? ev.status : 502
        );
      }
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (value) buf += decoder.decode(value, { stream: true });
      let idx: number;
      // SSE frames are separated by a blank line.
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        handleFrame(frame);
      }
      if (done) break;
    }
    // Flush any trailing frame without a terminating blank line.
    if (buf.trim()) handleFrame(buf);
  } catch (e) {
    // An `error` frame (or parse failure) threw — abort the upstream body so
    // the connection isn't left half-read, then propagate.
    try {
      await reader.cancel();
    } catch {
      /* already closing */
    }
    throw e;
  }

  if (!settled || result === undefined) {
    throw new AgentStreamError("agent stream ended without a result", 502);
  }
  return result;
}
