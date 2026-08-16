# citra-app-runtime

> Multi-tenant Next.js runtime that renders **Citra Power AI Apps** from the
> AppSpec JSON stored in `smart-app-service`.
>
> See [`docs/smart-app-builder-plan.md`](../docs/smart-app-builder-plan.md) for
> the full architecture.

## What it does

- `/` — lists all published apps (My Apps directory).
- `/{slug}` — fetches `AppSpec` + `AgentSpec` from `smart-app-service` and
  renders the panels.
- v0 panels supported: `form`, `queue`, `detail`, `dashboard`, `agent_chat`,
  `document_view`, `markdown`.

In v0 the panels render **statically** (schema, columns, metrics scaffolds).
Live data and agent invocations land in phase 7 once
`smart-app-service POST /apps/{slug}/run` is wired.

## Run locally

```powershell
# 1. Make sure smart-app-service is running on :9100 with at least one
#    published app (POST /publish with the fixtures from
#    smart-app-service/tests/fixtures/).
cd c:\Github\Citra-AI\citra-app-runtime
npm install
$env:SMART_APP_SERVICE_URL = "http://localhost:9100"
npm run dev
# open http://localhost:3100
```

## Environment

| Var | Default | Purpose |
|---|---|---|
| `SMART_APP_SERVICE_URL` | `http://localhost:9100` | Backend specs + run |
| `CITRA_SERVICE_URL` | `http://localhost:8085` | Reserved for runtime agent calls |
| `PORT` | `3100` | HTTP listen port |

## Layout

```
src/
  app/
    layout.tsx          # Root HTML shell
    page.tsx            # / — My Apps list
    globals.css
    [slug]/
      page.tsx          # /{slug} — render an app
  components/
    PanelRenderer.tsx   # 7 panel types
  lib/
    specClient.ts       # smart-app-service HTTP client
  types/
    spec.ts             # AppSpec / AgentSpec TS types (mirror JSON Schemas)
```

## Phase status

- [x] Phase 4: Render-only Next.js shell with 7 panel types
- [ ] Phase 7: Wire form submit / agent_chat / queue actions to
      `smart-app-service POST /apps/{slug}/run`
- [ ] Phase 9: Theming, auth, tenant subdomains, audit log
