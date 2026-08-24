<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-auth

Shared JWT auth middleware + claim helpers used across every Citra
FastAPI service. Replaces what used to be `Citra-Service/auth_middleware.py`
being imported across services via PYTHONPATH.

## What it gives you

- `JWTAuthMiddleware` — FastAPI `BaseHTTPMiddleware` subclass that
  verifies HS256 JWTs and stamps `request.state` with the caller's
  identity + enterprise claims (`org_id`, `dept_ids`, `roles`,
  `service_account_admin_of`, `service_account_member_of`)
- `get_secure_user_id(request)` — pull the authenticated email out of
  request.state; raises 401 if missing
- `mint_workflow_system_token(user_id, ...)` — used by schedulers /
  cron-triggered workflow runs that have no live caller JWT
- Helpers: `get_current_user`, `is_authenticated`, `get_user_email`,
  `get_authenticated_user_id`

## Usage

```python
# In your FastAPI app's main.py
from fastapi import FastAPI
from citra_auth import JWTAuthMiddleware

app = FastAPI()
app.add_middleware(JWTAuthMiddleware)

# In a route handler
from citra_auth import get_secure_user_id

@app.get("/me")
async def me(request: Request):
    return {"email": get_secure_user_id(request)}
```

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `JWT_SECRET` | (REQUIRED) | HS256 shared secret. Must match what user-service signs with. |
| `JWT_ISSUER` | `Citra-AI` | Optional `iss` claim |
| `ENVIRONMENT` | `dev` | `prod`/`production` disables docs + DISABLE_AUTH |
| `DISABLE_AUTH` | (unset) | Dev/test only — when `true` AND env != prod, bypass JWT and inject a fake user |
