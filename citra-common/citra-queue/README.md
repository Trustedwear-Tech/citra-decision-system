<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-queue

Redis-backed async job queue used across Citra services. Replaces the
previously-duplicated `worker_queue.py` files in `Citra-Service/services/`
and `Citra-Worker/`.

## Wire format

```
Redis keys:
  {prefix}citra:worker:queue:<queue_name>      LIST  job JSON blobs (FIFO)
  {prefix}citra:worker:job:<job_id>            HASH  job state + result
  {prefix}citra:worker:job:<job_id>:result     STRING  signal key for blocking await
```

Job lifecycle:
```
enqueue → status=queued
worker BLPOP → status=running, started_at set
handler returns → status=done, result + finished_at set
handler raises  → status=failed, error + finished_at set, retries++
                  (re-enqueued unless max_retries exhausted)
```

## Usage — producer (Citra-Service, citra-workflow)

```python
from citra_queue import enqueue, get_status

job_id = enqueue(
    "workflow.run",
    {"workflow_id": "...", "user_id": "alice@acme.com"},
    tenant_id="acme",
    request_id="trace-abc",
)
state = get_status(job_id)  # {"status": "running", ...}
```

## Usage — consumer (Citra-Worker)

```python
from citra_queue import consume_one, mark_running, mark_done, mark_failed, JobPermanentFailure

while True:
    job = await consume_one(["default", "high"], block_seconds=5)
    if not job:
        continue
    await mark_running(job)
    try:
        result = await handle(job)
        await mark_done(job, result)
    except JobPermanentFailure as e:
        await mark_failed(job, str(e), permanent=True)
    except Exception as e:
        await mark_failed(job, str(e), permanent=False)
```

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_DB` | `0` | Redis DB |
| `REDIS_USERNAME` | (none) | optional auth |
| `REDIS_PASSWORD` | (none) | optional auth |
| `REDIS_KEY_PREFIX` | `""` | namespace isolation for multi-tenant clusters |
| `CITRA_WORKER_RESULT_TTL` | `604800` (7d) | how long job state survives in Redis |
| `CITRA_WORKER_MAX_RETRIES` | `3` | retry budget for transient failures |
