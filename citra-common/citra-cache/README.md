<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-cache

Shared Redis cache manager. Replaces what used to be
`Citra-Service/cache_manager.py` imported across services via PYTHONPATH.

## Usage

```python
from citra_cache import get_cache_manager

cache = get_cache_manager()
cache.set("k", "v", ttl=60)
val = cache.get("k")
```

## Env vars

Same Redis env vars as `citra-queue` — shares the same cluster:

| Var | Default |
|---|---|
| `REDIS_HOST` | `localhost` |
| `REDIS_PORT` | `6379` |
| `REDIS_DB` | `0` |
| `REDIS_PASSWORD` | (none) |
| `REDIS_KEY_PREFIX` | `""` |
