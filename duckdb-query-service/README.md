<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# duckdb-query-service

Centralized analytical SQL query engine for the Citra platform.

## Tech Stack

- Python 3.11 / FastAPI / DuckDB
- MongoDB (metadata)
- Redis (cache)

## Port

- **7301**

## Purpose

Citra-Service sends tabular data (CSV, Excel, database results) to this service for SQL analytics. Data is loaded into in-memory DuckDB tables with TTL-based eviction.

## Configuration

Supports two methods:

1. **`.env` file** — Copy `.env.example` to `.env` and fill in values.
2. **HashiCorp Vault** — Delete `.env`, set `VAULT_ADDR` + auth credentials. The `vault_env_loader.py` module loads secrets from Vault at startup.

Key variables:

```env
PORT=7301
MONGODB_CONN_STRING=mongodb://root:citradev@localhost:27017/?authSource=admin&replicaSet=rs0
REDIS_HOST=localhost
REDIS_PORT=6379
TABLE_TTL=3600
MAX_TABLES=100
```

## Local Development

```bash
python -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
python main.py
```

## Docker

```bash
docker build -t duckdb-query-service .
docker run -p 7301:7301 --env-file .env duckdb-query-service
```

## Health Check

```
GET /health
```

## Resource Requirements

- CPU: 2 cores recommended
- RAM: 4GB recommended (in-memory tables)
