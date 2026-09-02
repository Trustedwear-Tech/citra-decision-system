<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## Configuration

**There is one configuration file: `.env` at the repository root**, copied from
`.env.example`. Every service is fed it by `docker-compose.quickstart.yml`
(`env_file: [.env]`) -- services do not read per-directory `.env` files, so
there is exactly one place to look and one place to change.

`.env.example` ships with working local defaults; the only value you must set
is `LLM_API_KEY`. Going beyond a local evaluation means changing three things,
and each has a commented alternative in the file next to the value it replaces:

- **Your own domain.** Point `FRONTEND_URL`, `WEBSITE_URL`, `APP_URL`,
  `BASE_URL`, `CORS_ALLOWED_ORIGINS` and `CITRA_UI_ORIGIN` at it, and set
  `FORCE_HTTPS=true`. Turn `ALLOW_DEV_LOGIN` off -- it is a local-only
  passwordless path.
- **Your own model endpoint.** Point `LLM_BASE_URL` / `EMBEDDING_BASE_URL` /
  `VISION_BASE_URL` at your own vLLM (or any OpenAI-compatible) server instead
  of a hosted provider, and no prompt or document leaves your network. Note
  that changing the embedding model or `EMBEDDING_DIMENSION` means
  re-ingesting: the Milvus collection is created at that dimension.
- **Real secrets.** `make setup` generates fresh random values for
  `JWT_SECRET`, the MCP keys, and the signing and encryption keys. The
  database and object-store passwords are still the shipped defaults --
  change them before the stack is reachable on a network, or move them into
  Vault (`VAULT_ADDR`, commented at the end of `.env.example`).

Two directories keep their own env file, because they are not part of the
compose stack and so are never fed the root `.env`: `Monitoring-Service/`
(runs standalone) and `bank-demo/` (a separate Next.js app started with
`npm run dev`).

See `docs/change-the-demo.md` to point it at your own data sources instead of
the bundled demo, and `ARCHITECTURE.md` for how the pieces fit together --
the service map, the file-defined MCP, and the conventions this tree holds
itself to.
