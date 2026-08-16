import Link from "next/link";

/** Branded 404 — replaces Next's bare default so a wrong slug, a removed page,
 *  or an expired launch token doesn't dump the user out of the product with no
 *  orientation. (The common real cause is the ?_t= launch token expiring.) */
export default function NotFound() {
  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24 }}>
      <div style={{ maxWidth: 440, textAlign: "center" }}>
        <div style={{ fontSize: 40, marginBottom: 8 }} aria-hidden>🔍</div>
        <h1 style={{ fontSize: 20, margin: "0 0 8px" }}>This app or page isn’t available</h1>
        <p style={{ color: "var(--citra-muted, #64748b)", margin: "0 0 20px", lineHeight: 1.5 }}>
          It may have been moved or removed — or your session expired. Reopen it
          from your app list to continue.
        </p>
        <Link href="/" style={{ color: "var(--citra-primary, #2563eb)", fontWeight: 600 }}>
          ← Back to your apps
        </Link>
      </div>
    </main>
  );
}
