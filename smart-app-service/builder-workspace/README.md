# Builder Workspace Template

This directory is **mounted into every ephemeral builder pod** that `smart-app-service` spawns
on `action-sandbox-host` (open-claw) for a build/edit session.

## Contents

- `AGENTS.md` — top-level orchestration: the four-phase build (Internship → Expertise → Compose → Deploy)
  and the hard rules every builder pod must follow.

## Mount paths inside the builder pod

| Source | Pod path |
|---|---|
| `smart-app-service/builder-workspace/AGENTS.md` | `/workspace/.openclaw/workspace/AGENTS.md` |
| `smart-app-service/schemas/*.json` | `/workspace/.openclaw/workspace/schemas/` (read-only) |
| `smart-app-service/builder-workspace/static_checks.py` | `/workspace/.openclaw/workspace/builder-workspace/static_checks.py` (Layer-A static-check harness) |
| `smart-app-service/skills/*` (all `citra-*` skills — incl. `citra-app-ui-design`, `citra-{app,agent}-spec`, `citra-dashboard-spec`, `citra-mcp-discover`, `citra-tool-catalogue`, `citra-fewshot-from-history`, `citra-rag-probe`, `citra-safety-rules`, `citra-code-exec`, `citra-ocr`, `citra-ui-{fields,panels,charts}`, `citra-self-test`, `citra-app-publish`, `citra-app-edit`) | `/skills/` |
| BA-provided files | `/workspace/input/` |
| Pod scratch | `/workspace/build/` |

The skills bundle and schemas come from sibling repos at build time — the pod itself is stateless.

## Versioning

Bump the version in this README's git history when the orchestration phases or hard rules change in a
way that existing in-flight builds shouldn't see. The pod image is rebuilt to pin a snapshot.
