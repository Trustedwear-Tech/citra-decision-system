// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import Link from "next/link";
import { fetchApps } from "@/lib/specClient";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  let apps: Awaited<ReturnType<typeof fetchApps>>["apps"] = [];
  let error: string | null = null;
  try {
    const res = await fetchApps();
    apps = res.apps;
  } catch (err) {
    error = err instanceof Error ? err.message : String(err);
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <div className="app-title">Citra Power AI Apps</div>
          <div className="app-desc">Runtime — pick an app to open.</div>
        </div>
      </header>

      {error && <div className="error">Failed to load apps: {error}</div>}

      {!error && apps.length === 0 && (
        <div className="panel">
          <div className="panel-empty">
            No apps published yet. Use the Decision App Builder to create one.
          </div>
        </div>
      )}

      {apps.length > 0 && (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>App</th>
                <th>Status</th>
                <th>Version</th>
                <th>Deployed</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((a) => (
                <tr key={a.app_id}>
                  <td>
                    <Link href={`/${a.slug}`}>{a.title}</Link>
                    {a.description && (
                      <div style={{ color: "var(--citra-muted)", fontSize: 12 }}>
                        {a.description}
                      </div>
                    )}
                  </td>
                  <td>
                    <span className="chip">{a.status}</span>
                  </td>
                  <td>v{a.version}</td>
                  <td>
                    {a.deployed_at
                      ? new Date(a.deployed_at).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
