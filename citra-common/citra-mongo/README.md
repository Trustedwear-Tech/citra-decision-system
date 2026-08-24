<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-mongo

Shared MongoDB connection manager. Singleton sync + async client with
connection-pool tuning and event-loop-aware re-creation (handles
Gunicorn pre-forking correctly).

Replaces what used to be `Citra-Service/mongodb_manager.py` being
copied/imported across services via PYTHONPATH.

## Usage

```python
from citra_mongo import get_async_mongo_client, MONGODB_DATABASE

db = get_async_mongo_client()[MONGODB_DATABASE]
doc = await db["Workflows"].find_one({"workflow_id": wid})
```

For sync code:

```python
from citra_mongo import get_mongo_client, get_database_name
db = get_mongo_client()[get_database_name()]
```

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `MONGODB_CONN_STRING` | (REQUIRED) | Atlas SRV or self-hosted URI |
| `MONGODB_DATABASE` | `citra-ai` | DB name |
| `MONGODB_MAX_POOL_SIZE` | `20` | Pool ceiling |
| `MONGODB_MIN_POOL_SIZE` | `1` | Pool floor (keep warm) |
| `MONGODB_MAX_IDLE_TIME_MS` | `60000` | Cull idle conns (avoids cloud-NAT staleness) |
| `MONGODB_SERVER_SELECTION_TIMEOUT_MS` | `30000` | Replica-set failover budget |
| `MONGODB_CONNECT_TIMEOUT_MS` | `10000` | Initial connect budget |
| `MONGODB_SOCKET_TIMEOUT_MS` | `60000` | Per-op timeout |
| `MONGODB_HEARTBEAT_FREQUENCY_MS` | `30000` | DNS lookup interval |
| `ENVIRONMENT` | `development` | `production` enables strict TLS |
| `MONGODB_APP_NAME` | `citra` | `appName` tag — sent to Atlas so you can see which service is connecting |
