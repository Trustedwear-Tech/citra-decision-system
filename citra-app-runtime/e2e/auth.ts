import * as fs from "fs";
import * as path from "path";
import jwt from "jsonwebtoken";

/**
 * Mint a user JWT the runtime accepts via the `?_t=` handoff.
 *
 * Uses the SAME shared `JWT_SECRET` as smart-app-service / Citra-Service (read
 * from Citra-Service/.env), so the runtime + smart-app-service + the dept-MCP
 * all validate it. This mirrors the Citra-UI impersonation handoff for an
 * automated context — it is a test harness, not a production bypass; the token
 * is a normal end-user JWT, scoped to one BSPHCL officer.
 *
 * Override the identity with E2E_USER_ID / E2E_TENANT_ID if testing another app.
 */
function readEnv(file: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of fs.readFileSync(file, "utf-8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
  return out;
}

export function mintToken(): string {
  const envPath =
    process.env.CITRA_SERVICE_ENV ||
    path.resolve(__dirname, "../../Citra-Service/.env");
  const env = readEnv(envPath);
  const secret = env.JWT_SECRET;
  if (!secret) throw new Error(`JWT_SECRET not found in ${envPath}`);
  const issuer = env.JWT_ISSUER || "Citra-AI";
  const userId =
    process.env.E2E_USER_ID || "cmd-asha@bsphcl-power-demo.citra.ai";
  const tenantId = process.env.E2E_TENANT_ID || "bsphcl";
  return jwt.sign(
    {
      user_id: userId,
      email: userId,
      name: "E2E Officer",
      org_id: tenantId,
      tenant_id: tenantId,
      dept_ids: [
        "central_pmu",
        "billing_revenue",
        "ami_metering",
        "distribution_ops",
        "vigilance_field",
      ],
      roles: ["org_admin"],
      iss: issuer,
    },
    secret,
    { algorithm: "HS256", expiresIn: "2h" }
  );
}

let _cached: string | null = null;
export function token(): string {
  if (!_cached) _cached = mintToken();
  return _cached;
}

/** Runtime URL for an app page, with the `?_t=` token handoff appended. */
export function appUrl(slug: string, pagePath = ""): string {
  const p = pagePath ? `/${pagePath.replace(/^\//, "")}` : "";
  return `/${slug}${p}?_t=${encodeURIComponent(token())}`;
}
