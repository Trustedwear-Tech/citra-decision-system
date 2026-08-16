<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra-Service

Core AI backend for the Citra platform. Handles all AI-powered features including chat, vault operations, document generation, diagrams, knowledge graphs, internet search, and database queries.

## Tech Stack

- Python 3.11 / FastAPI / Gunicorn
- MongoDB (document store)
- Milvus (vector search / RAG)
- Redis (cache / sessions)
- MinIO or S3 (file storage)

## Port

- **7001** (production)
- **8085** (local development with `uvicorn --reload`)

## Configuration

Supports two methods:

1. **`.env` file** — Copy `.env.example` to `.env` and fill in values.
2. **HashiCorp Vault** — Delete `.env`, set `VAULT_ADDR` + auth credentials. The `vault_env_loader.py` module loads secrets from Vault at startup. In production (`prod/*` mount path), the service will refuse to start if Vault secrets cannot be loaded.

See `.env.example` for all available environment variables.

## Local Development

```bash
python -m venv myenv
myenv\Scripts\activate        # Windows
# source myenv/bin/activate   # Linux/macOS
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8085
```

## Docker

```bash
docker build -t citra-service .
docker run -p 7001:7001 --env-file .env citra-service
```

## Health Check

```
GET /health
```

## Authentication

Citra-Service validates incoming requests using JWT tokens issued by **Citra-User-Service**. It does not handle login or registration directly.

- Every API request must include an `Authorization: Bearer <token>` header.
- Tokens are verified using the shared `JWT_SECRET` (must match the value in Citra-User-Service).
- Both Google OAuth and email/password tokens use the same JWT format — Citra-Service does not need to know which auth method was used.

For auth provider setup (Google OAuth, email/password, email configuration), see the **Citra-User-Service** README.

## Key Directories

| Path | Description |
|------|-------------|
| `services/` | Business logic — chat, presentations, reports, diagrams, vault, internet search |
| `routes/` | FastAPI route handlers |
| `vault_env_loader.py` | `.env` + HashiCorp Vault config loader |
| `llm_client.py` | Multi-provider LLM client (OpenAI, Perplexity, Google, self-hosted, etc.) |
| `main.py` | Application entry point |
