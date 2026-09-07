<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

# OIDC login

Let your own identity provider -- Okta, Microsoft Entra ID, Auth0, Keycloak,
Google Workspace, anything that speaks OpenID Connect -- sign officers into
the stack, and let its directory groups decide which departments and roles
they hold. Off by default: a fresh install uses local email and password.

## How it works

The user service accepts an OIDC **ID token** at `POST /api/auth/oidc`,
verifies its signature against the issuer's published keys (JWKS), checks the
issuer and the audience, and mints the same Citra JWT every other login path
mints: organisation, departments, roles, service accounts. Nothing downstream
knows or cares which door the user came in by.

Two things are deliberate:

- **The break-glass admin stays local.** Keep `local` in `AUTH_PROVIDERS`
  even on an SSO deployment. The super admin created by the wizard is a local
  account so the platform can be seeded before the IdP is wired, and recovered
  if the IdP is ever unreachable. An OIDC login is refused for an email that
  belongs to a local-only account, so nobody who can obtain a token for that
  address from your IdP can take over the admin through it.
- **Departments come from your directory, not from an admin screen.** With a
  group map in place, each login re-derives the user's departments and roles
  from the group claim in the token. Offboarding someone in the IdP removes
  their access at their next request. Without a group map a first-time SSO
  user lands with no departments at all and an admin grants them by hand,
  which is fine for ten users and unworkable for two hundred.

## Configure

All settings go in the root `.env` -- the quickstart feeds it to every
service. Add these lines; the wizard does not write them.

```
AUTH_PROVIDERS=oidc,local
OIDC_ISSUER=https://login.example.com/realms/bank
OIDC_CLIENT_ID=citra
# optional: else discovered from ${OIDC_ISSUER}/.well-known/openid-configuration
OIDC_JWKS_URI=
# optional: path to the group map, default ./config/oidc-group-map.json
OIDC_GROUP_MAP_PATH=
```

At your IdP, register a public (PKCE) client, allow the `openid email profile`
scopes, and make sure the ID token carries `email`. A token without an email
claim is rejected.

One issuer per deployment. A deployment is one organisation, and the token's
`iss` must equal `OIDC_ISSUER` exactly, trailing slash included.

Restart the user service and confirm:

```bash
curl -s http://localhost:7004/api/auth/oidc/health
```

`configured: true` means both the issuer and the client id are set. It reveals
no secrets and is safe to leave reachable.

## The group map

Copy
[`config/oidc-group-map.example.json`](https://github.com/Trustedwear-Tech/citra-decision-system/blob/main/citra-common/Citra-User-Service/config/oidc-group-map.example.json)
to `config/oidc-group-map.json` inside the user service directory (the
quickstart bind-mounts that directory, so the file is visible to the container
without a rebuild). The file existing is what turns the feature on.

```json
{
  "claim": "groups",
  "case_insensitive": true,
  "groups": {
    "Citra-Lending-Credit":   { "dept_ids": ["lending"] },
    "Citra-Claims-Assessors": { "dept_ids": ["claims"] },
    "Citra-Dept-Heads":       { "dept_ids": ["lending", "claims"], "roles": ["dept_admin"] },
    "Citra-App-Builders":     { "dept_ids": ["central_ops"], "roles": ["decision-app-builder"] }
  }
}
```

- `claim` is the token claim that holds the groups. Dotted paths work for
  IdPs that nest them; Keycloak's realm roles are `realm_access.roles`.
- Entra ID emits group **object ids**, not names, unless you turn on group
  name emission in the app registration. Decode a real token before writing
  the file.
- Every `dept_id` must exist in the departments seed, or the service refuses
  to start. Roles are optional and additive. `super_admin` can never be
  granted by a group; the service rejects the file if it tries.
- A malformed map, or an `OIDC_GROUP_MAP_PATH` that does not resolve, is a
  hard startup failure rather than a silent fall-back to zero access. A
  department is a data-access boundary -- the MCP scopes the rows an officer
  can read by it -- so a half-loaded map would be a security bug, not a
  degraded feature.

## The login screen

The browser side of the flow is not wired in the shipped UI. The login screen
asks the user service which providers are enabled and shows buttons for
`google` and `local`; it does not yet run the OIDC PKCE dance itself. Today
there are two ways to use OIDC:

- **From a host application.** If Decision Apps are embedded in your own
  portal, which already has the user signed in through your IdP, that portal
  posts the ID token it holds to `/api/auth/oidc` and uses the returned Citra
  JWT for the embed. This is the deployment the feature was built for.
- **A small front-end change.** Add an OIDC branch to
  `Citra-UI/components/SignUpScreen.js` that runs the PKCE flow with a
  standard client library and posts the resulting `id_token`. The endpoint,
  the provisioning and the group sync are all there; only the button is not.

The request and response shape:

```bash
curl -s -X POST http://localhost:7004/api/auth/oidc -H 'Content-Type: application/json' -d '{"idToken": "<ID token from your IdP>"}'
```

A verified token returns `success: true` and the Citra JWT. A bad, expired or
wrong-audience token returns 401 with the reason.

## What OIDC does not give you

SAML, SCIM provisioning endpoints, and multi-factor enforcement are not part
of this login path. Group sync on every login covers most of what SCIM is
used for -- grants and revocations happen in your directory -- but there is no
push-based user lifecycle API. MFA is your IdP's job: enforce it there, and
every Citra login inherits it.
