<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| A service is unhealthy or restarting | `make logs` -- usually a missing key in `.env` |
| Recommendations error or hang | `LLM_API_KEY` unset, or the model / base URL is wrong |
| Mongo never becomes healthy | the replica set is initiated by the one-shot `mongodb-init-rs` container; check `docker logs citra-mongodb-init-rs` |
| Milvus exits or OOMs | raise Docker's memory to 8 GB+; it is the heaviest container |
| "Milvus collection does not exist" | `docker compose -f docker-compose.quickstart.yml exec citra-service python scripts/setup_milvus_schema.py` |
| Uploads fail | confirm the bucket exists in the MinIO console (http://localhost:9001) |
| Builder's dataset palette is empty | the catalogue crawl found nothing -- check the MCP registered: `docker logs citra-ds-mcp-demo-acme-bank` should show `[REGISTRATION] Registered tool:` with no failures |
| Demo data missing after impersonating | the demo MCP must be up: `docker compose -f demo-data/tenants/acme-bank/mcp/docker-compose.yml ps` |
| A port is already allocated | another Citra stack is running; override the published port in `.env` (e.g. `MINIO_API_PORT`) |

> All credentials in `.env` are local development defaults. Change every
> password and secret before this stack is reachable on any network.
