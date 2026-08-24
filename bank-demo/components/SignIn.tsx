// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

/**
 * Acme Bank's own sign-in. Posts to the bank's OWN /api/login, which does the
 * exchange against Citra's user service server-side — the password never leaves
 * the bank's server and no CORS exception is needed on Citra's side.
 */
import { useState } from "react";

export default function SignIn({ onDone }: { onDone: (email: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.error || `Sign-in failed (${r.status})`);
      onDone(body.email ?? email);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ab-card ab-signin">
      <h2>Officer sign-in</h2>
      <p className="ab-sub">Acme Bank operations console</p>
      <form onSubmit={submit}>
        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="username"
               value={email} onChange={(e) => setEmail(e.target.value)} required />
        <label htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="current-password"
               value={password} onChange={(e) => setPassword(e.target.value)} required />
        {err && <p className="err" style={{ marginTop: 14 }}>{err}</p>}
        <button className="ab-btn" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="ab-note">
        Authenticated against Citra&apos;s user service by this application&apos;s own
        server. The session token is stored in an httpOnly cookie.
      </p>
    </div>
  );
}
