"use client";

/**
 * ItemFindingReview — per-item human-in-the-loop review of one ItemFinding
 * (image or document analysis), per docs/multimodal-decision-apps-plan.md.
 *
 * Renders the analyzed item (image preview), the model's structured fields +
 * recommendation + confidence + rationale, and exactly three terminal actions —
 * Accept / Reject(+reason) / Cancel. EVERY item (record / image / document) is
 * reviewed; there is no auto-accept and no other option. Reject REQUIRES a reason
 * — it is posted to `/api/apps/{slug}/items/{itemId}/feedback` and becomes a
 * learned criterion in the (app, modality, task_type) rubric, so the next similar
 * item is judged with it (learning is per task_type, never per file). Accept and
 * Cancel are recorded but do not change the rubric.
 *
 * Self-contained: the parent passes the finding + a displayable (signed) image
 * URL. Reuses the runtime's --citra-* design tokens.
 */
import { useState } from "react";
import { runtimeFetch } from "@/lib/runtimeFetch";

export interface ItemFinding {
  item_id: string;
  item_type: string; // == task_type
  // image/document = per-artifact; api = a System-of-Record/bureau check
  // (check_evaluate); case = the case-level fraud screening (fraud_synthesis).
  modality: "image" | "document" | "api" | "case";
  subject?: string | null; // few-word "what is this image/doc" — rubric anchor
  fields: Record<string, unknown>;
  recommendation?: string | null;
  confidence: number; // 0..1
  rationale: string;
  citations?: Array<Record<string, unknown>>;
  rubric_version?: string | null;
  // Per-artifact fraud evidence from the runtime (duplicate / near-dup /
  // metadata anomalies). Heterogeneous by design — see fraud_checks.artifact_flags.
  artifact_flags?: Record<string, unknown> | null;
}

/** Name the RECORD a duplicate file was seen on — "which other inspection used
 *  this photo?" is the whole point of the flag, and it is useless as
 *  "[object Object]".
 *
 *  fraud_checks emits {item_id, record_ref, seen_at}; older payloads carry a
 *  plain string. `record_ref` may be dataset-qualified (`dataset:KEY`), so show
 *  the bare key, which is what the officer sees everywhere else in the app.
 *  An unrecognised shape yields "" and is dropped rather than stringified. */
function priorRefLabel(ref: unknown): string {
  if (typeof ref === "string") return ref;
  if (ref && typeof ref === "object") {
    const r = ref as Record<string, unknown>;
    const rec = typeof r.record_ref === "string" ? r.record_ref : "";
    if (rec) return rec.split(":").pop() || rec;
    if (typeof r.item_id === "string" && r.item_id) return r.item_id;
  }
  return "";
}

/** Distill artifact_flags into officer-readable evidence lines. Returns [] when
 *  there is nothing fraud-relevant (clean artifact) — the block then hides.
 *  Defensive on shape: the payload is heterogeneous and best-effort upstream. */
function artifactEvidence(flags?: Record<string, unknown> | null): string[] {
  if (!flags || typeof flags !== "object") return [];
  const out: string[] = [];
  const priorRefs = Array.isArray(flags.prior_refs) ? (flags.prior_refs as unknown[]) : [];
  if (flags.duplicate || priorRefs.length > 0) {
    const labels = priorRefs.map(priorRefLabel).filter(Boolean);
    out.push(
      `Exact duplicate — identical file seen before${
        labels.length ? ` (records: ${labels.slice(0, 3).join(", ")}${labels.length > 3 ? "…" : ""})` : ""
      }`
    );
  }
  const nearDups = Array.isArray(flags.phash_near_dups) ? (flags.phash_near_dups as unknown[]) : [];
  if (nearDups.length > 0) out.push(`Near-duplicate image — ${nearDups.length} visually matching prior artifact(s)`);
  const imageIndex = flags.image_index as Record<string, unknown> | undefined;
  const similar = imageIndex && Array.isArray(imageIndex.similar) ? (imageIndex.similar as unknown[]) : [];
  if (similar.length > 0) out.push(`Similar image — ${similar.length} visually related prior artifact(s) (CLIP)`);
  const meta = flags.metadata as Record<string, unknown> | undefined;
  const metaFlags = meta && Array.isArray(meta.flags) ? (meta.flags as unknown[]) : [];
  for (const f of metaFlags.slice(0, 4)) out.push(`Metadata: ${String(f)}`);
  for (const [k, v] of Object.entries(flags)) {
    if (k.endsWith("_error") || (k === "cross_case_checks" && typeof v === "string")) {
      out.push(`⚠ Check degraded — ${String(v)}`);
    }
  }
  return out;
}

type Decision = "accept" | "reject" | "cancel";

/** Reject-reason budget. A rubric criterion should be one crisp sentence or
 *  two — long essays dilute the summarizer and the precedent prompts. Enforced
 *  natively (maxLength), shown live as a counter, and re-checked server-side. */
export const REASON_MAX = 500;

/** Minimum words an officer must write before a correction is accepted.
 *  A correction is only worth storing if a stranger could act on it later —
 *  "wrong" teaches nobody anything. This replaced a chip taxonomy that could be
 *  satisfied in one click without typing a character. Lives here beside
 *  REASON_MAX because PanelRenderer already imports from this module; the
 *  reverse direction would be a cycle. */
export const MIN_REASON_WORDS = 10;

export function reasonWordCount(s: string): number {
  return (s || "").trim().split(/\s+/).filter(Boolean).length;
}

export function ItemFindingReview({
  slug,
  finding,
  imageUrl,
  onResolved,
}: {
  slug: string;
  finding: ItemFinding;
  imageUrl?: string; // signed/displayable URL for image modality
  onResolved?: (decision: Decision) => void;
}) {
  const [reason, setReason] = useState("");
  const [showReject, setShowReject] = useState(false);
  const [resolved, setResolved] = useState<Decision | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The display URL captured at analysis time can be a signed/expiring link; by
  // review time (async queue/auto-recommend) it may be dead. Surface that
  // explicitly rather than rendering a silent broken thumbnail.
  const [imgError, setImgError] = useState(false);
  // Click the thumbnail to view the full photo (the cropped thumb can hide the
  // detail a reviewer needs — e.g. the nameplate asset id).
  const [lightbox, setLightbox] = useState(false);

  const submit = async (decision: Decision) => {
    if (decision === "reject" && reasonWordCount(reason) < MIN_REASON_WORDS) {
      setError(
        `Write at least ${MIN_REASON_WORDS} words — a correction the app can `
        + "learn from has to say what the agent got wrong.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await runtimeFetch(
        `/api/apps/${encodeURIComponent(slug)}/items/${encodeURIComponent(finding.item_id)}/feedback`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            modality: finding.modality,
            task_type: finding.item_type,
            decision,
            reason: decision === "reject" ? reason.trim() : undefined,
            // The taxonomy pick — validated server-side against the app's
            // declared codes; the aggregatable half of the correction.
            // Anchor the reason to WHAT this item was, so the rubric learns per
            // subject-type. Prefer the model's subject; fall back to the first
            // sentence of the rationale for findings produced before `subject`.
            subject:
              decision === "reject"
                ? (finding.subject || finding.rationale?.split(".")[0] || "")
                    .toString()
                    .trim()
                    .slice(0, 120) || undefined
                : undefined,
          }),
        }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail?.message || body?.detail || `HTTP ${res.status}`);
      }
      setResolved(decision);
      onResolved?.(decision);
    } catch (e) {
      setError(e instanceof Error ? e.message : "feedback failed");
    } finally {
      setBusy(false);
    }
  };

  const conf = Math.round((finding.confidence ?? 0) * 100);
  const confColor =
    conf >= 75 ? "var(--citra-success, #16a34a)" : conf >= 50 ? "var(--citra-warning, #d97706)" : "var(--citra-danger, #dc2626)";

  // A case-level fraud screening reads as Confirm / Dismiss, not Accept / Reject
  // — fraud is EVIDENCE the officer weighs, never an auto-reject. The disposition
  // still trains the (case, fraud-screening) rubric so the app learns which
  // patterns are real vs. false alarms for this tenant's data.
  const isCase = finding.modality === "case";
  const L = isCase
    ? {
        accept: "Confirm", reject: "Dismiss", cancel: "Skip",
        confirmReject: "Confirm dismiss",
        acceptDone: "✓ Confirmed — recorded as a real concern.",
        rejectDone: "✗ Dismissed — the app learns this pattern is a false alarm for your data.",
        cancelDone: "⊘ Skipped — no change.",
        rejectPrompt: "Why is this a false alarm? (teaches the app which fraud signals are noise for your data)",
      }
    : {
        accept: "Accept", reject: "Reject", cancel: "Cancel",
        confirmReject: "Confirm reject",
        acceptDone: "✓ Accepted",
        rejectDone: "✗ Rejected — reason saved to the rubric (improves future analysis).",
        cancelDone: "⊘ Cancelled — no change to the rubric.",
        rejectPrompt: "Why is this wrong? (becomes a criterion for this item type — the AI learns from it)",
      };

  return (
    <div
      style={{
        border: "1px solid var(--citra-border, #e5e7eb)",
        borderRadius: 10,
        padding: 12,
        marginBottom: 12,
        background: "var(--citra-surface, #fff)",
      }}
    >
      {lightbox && imageUrl ? (
        <div
          onClick={() => setLightbox(false)}
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed", inset: 0, zIndex: 1000,
            background: "rgba(0,0,0,0.85)", display: "flex",
            alignItems: "center", justifyContent: "center", padding: 24, cursor: "zoom-out",
          }}
        >
          <button
            onClick={(e) => { e.stopPropagation(); setLightbox(false); }}
            aria-label="Close full image"
            style={{
              position: "absolute", top: 12, right: 18, fontSize: 30, lineHeight: 1,
              color: "#fff", background: "transparent", border: "none", cursor: "pointer",
            }}
          >
            ×
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt={finding.item_id}
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: "92vw", maxHeight: "92vh", objectFit: "contain",
              borderRadius: 8, boxShadow: "0 8px 40px rgba(0,0,0,0.5)",
            }}
          />
        </div>
      ) : null}
      <div style={{ display: "flex", gap: 12 }}>
        {finding.modality === "image" && imageUrl && !imgError ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={imageUrl}
            alt={finding.item_id}
            onError={() => setImgError(true)}
            onClick={() => setLightbox(true)}
            title="Click to view full image"
            style={{
              width: 180, maxHeight: 180, borderRadius: 8, objectFit: "contain",
              background: "var(--citra-bg, #f3f4f6)", flex: "0 0 auto", cursor: "zoom-in",
            }}
          />
        ) : finding.modality === "image" ? (
          <div
            style={{
              width: 180, height: 120, flex: "0 0 auto", borderRadius: 8,
              border: "1px dashed var(--citra-border, #e5e7eb)",
              display: "flex", flexDirection: "column", alignItems: "center",
              justifyContent: "center", gap: 4, fontSize: 11,
              color: "var(--citra-muted, #6b7280)", textAlign: "center", padding: 8,
            }}
          >
            <span>Image unavailable{imgError ? " (link may have expired)" : ""}</span>
            {imageUrl ? (
              <a href={imageUrl} target="_blank" rel="noreferrer" style={{ color: "var(--citra-primary, #2563eb)" }}>
                Open original ›
              </a>
            ) : null}
          </div>
        ) : null}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <strong style={{ fontSize: 13 }}>{finding.item_id}</strong>
            <span style={{ fontSize: 11, color: "var(--citra-muted, #6b7280)" }}>{finding.item_type}</span>
            <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: confColor }}>
              {conf}% confidence
            </span>
          </div>
          {finding.recommendation ? (
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: "var(--citra-muted, #6b7280)" }}>Recommendation: </span>
              <code>{finding.recommendation}</code>
            </div>
          ) : null}
          <table style={{ fontSize: 12, borderCollapse: "collapse", marginBottom: 6 }}>
            <tbody>
              {Object.entries(finding.fields || {}).map(([k, v]) => (
                <tr key={k}>
                  <td style={{ color: "var(--citra-muted, #6b7280)", paddingRight: 10, verticalAlign: "top" }}>{k}</td>
                  <td style={{ fontWeight: 500 }}>{Array.isArray(v) ? v.join(", ") : String(v ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(() => {
            // Fraud evidence — the runtime collects artifact_flags into every
            // finding "so it MUST reach the officer's per-item review payload
            // structurally"; render it, or that promise ends at the API.
            const evidence = artifactEvidence(finding.artifact_flags);
            return evidence.length > 0 ? (
              <div
                style={{
                  fontSize: 11.5, marginBottom: 6, padding: "6px 8px", borderRadius: 6,
                  background: "var(--citra-danger-bg, #fef2f2)",
                  border: "1px solid var(--citra-danger, #dc2626)",
                  color: "var(--citra-text, #374151)",
                }}
              >
                <strong style={{ color: "var(--citra-danger, #dc2626)" }}>🛡️ Artifact evidence</strong>
                <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                  {evidence.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              </div>
            ) : null;
          })()}
          {finding.rationale ? (
            <div style={{ fontSize: 12, color: "var(--citra-text, #374151)", fontStyle: "italic" }}>
              {finding.rationale}
            </div>
          ) : null}
        </div>
      </div>

      {resolved ? (
        <div
          style={{
            marginTop: 8,
            fontSize: 12,
            color:
              resolved === "accept"
                ? "var(--citra-success,#16a34a)"
                : resolved === "reject"
                ? "var(--citra-danger,#dc2626)"
                : "var(--citra-muted,#6b7280)",
          }}
        >
          {resolved === "accept" ? L.acceptDone : resolved === "reject" ? L.rejectDone : L.cancelDone}
        </div>
      ) : (
        <div style={{ marginTop: 8 }}>
          {isCase ? (
            <div
              style={{
                fontSize: 11.5, color: "var(--citra-text,#374151)",
                background: "var(--citra-bg,#f9fafb)",
                border: "1px solid var(--citra-border,#e5e7eb)",
                borderRadius: 6, padding: "6px 8px", marginBottom: 8,
              }}
            >
              🛡️ <strong>Fraud check</strong> — this is <em>evidence</em> for you to weigh, never an
              automatic reject. <strong>Confirm</strong> if it&apos;s a real concern, <strong>Dismiss</strong> if
              it&apos;s a false alarm. Your reason teaches the app which fraud patterns matter for your data.
            </div>
          ) : null}
          {showReject ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={L.rejectPrompt}
                rows={2}
                maxLength={REASON_MAX}
                aria-describedby={`reason-count-${finding.item_id}`}
                style={{ width: "100%", fontSize: 12, padding: 6, borderRadius: 6, border: "1px solid var(--citra-border,#e5e7eb)" }}
              />
              <div
                id={`reason-count-${finding.item_id}`}
                aria-live="polite"
                style={{
                  alignSelf: "flex-end", fontSize: 11,
                  color: reasonWordCount(reason) < MIN_REASON_WORDS
                    ? "var(--citra-warning,#d97706)"
                    : "var(--citra-muted,#6b7280)",
                }}
              >
                {reasonWordCount(reason) < MIN_REASON_WORDS
                  ? `${reasonWordCount(reason)}/${MIN_REASON_WORDS} words — say what the agent got wrong`
                  : `${reasonWordCount(reason)} words · ${reason.length}/${REASON_MAX}`}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={() => submit("reject")}
                  disabled={busy || reasonWordCount(reason) < MIN_REASON_WORDS}
                  style={btn("danger")}
                >
                  {L.confirmReject}
                </button>
                <button onClick={() => { setShowReject(false); setError(null); }} disabled={busy} style={btn("ghost")}>
                  Back
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => submit("accept")} disabled={busy} style={btn(isCase ? "danger" : "primary")}>
                {L.accept}
              </button>
              <button onClick={() => { setShowReject(true); setError(null); }} disabled={busy} style={btn(isCase ? "primary" : "danger")}>
                {L.reject}
              </button>
              <button onClick={() => submit("cancel")} disabled={busy} style={btn("ghost")}>
                {L.cancel}
              </button>
            </div>
          )}
          {error ? <div style={{ marginTop: 6, fontSize: 12, color: "var(--citra-danger,#dc2626)" }}>{error}</div> : null}
        </div>
      )}
    </div>
  );
}

function btn(variant: "primary" | "danger" | "ghost"): React.CSSProperties {
  const base: React.CSSProperties = {
    fontSize: 12,
    fontWeight: 600,
    padding: "6px 12px",
    borderRadius: 6,
    cursor: "pointer",
    border: "1px solid transparent",
  };
  if (variant === "primary") return { ...base, background: "var(--citra-primary, #2563eb)", color: "#fff" };
  if (variant === "danger") return { ...base, background: "transparent", color: "var(--citra-danger,#dc2626)", borderColor: "var(--citra-danger,#dc2626)" };
  return { ...base, background: "transparent", color: "var(--citra-muted,#6b7280)", borderColor: "var(--citra-border,#e5e7eb)" };
}

export default ItemFindingReview;
