// Display-format helpers (C7) shared by queue columns, detail fields, the
// timeline and split view. Semantic badge colors only — never hex from specs.

import { fmtMoney } from "@/lib/executiveTheme";
import type { BadgeColor, ColumnFormat } from "@/types/spec";

/** CSS class for a semantic badge color (see globals.css .badge-*). */
export function badgeClass(color?: BadgeColor): string {
  return color ? `badge-${color}` : "badge-neutral";
}

/** Resolve the semantic color for a value via a badge_colors map. */
export function badgeColorFor(
  value: unknown,
  colors?: Record<string, BadgeColor>,
): BadgeColor | undefined {
  if (!colors) return undefined;
  return colors[String(value ?? "")];
}

/** "2 days ago" / "in 3 hours" via Intl.RelativeTimeFormat. Falls back to the
 * raw value when it doesn't parse as a date — never blank. */
export function relativeTime(value: unknown, locale?: string): string {
  const raw = String(value ?? "");
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  const diffMs = d.getTime() - Date.now();
  const rtf = new Intl.RelativeTimeFormat(locale || "en-US", { numeric: "auto" });
  const abs = Math.abs(diffMs);
  const MIN = 60_000, HOUR = 3_600_000, DAY = 86_400_000;
  if (abs < HOUR) return rtf.format(Math.round(diffMs / MIN), "minute");
  if (abs < DAY) return rtf.format(Math.round(diffMs / HOUR), "hour");
  if (abs < 30 * DAY) return rtf.format(Math.round(diffMs / DAY), "day");
  if (abs < 365 * DAY) return rtf.format(Math.round(diffMs / (30 * DAY)), "month");
  return rtf.format(Math.round(diffMs / (365 * DAY)), "year");
}

/** Format one cell per its declared ColumnFormat. Returns null when the
 * format needs its own markup (status_pill / progress — callers render those
 * as components). */
export function formatCellText(
  value: unknown,
  format: ColumnFormat | undefined,
  locale?: string,
): string | null {
  if (format === "currency") {
    const n = typeof value === "number" ? value : Number(value);
    return Number.isFinite(n) ? fmtMoney(n) : String(value ?? "");
  }
  if (format === "relative_time") return relativeTime(value, locale);
  return null;
}

/** Progress fraction from a 0-1 or 0-100 value; null when unparseable. */
export function progressFraction(value: unknown): number | null {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return null;
  const frac = n > 1 ? n / 100 : n;
  return Math.max(0, Math.min(1, frac));
}
