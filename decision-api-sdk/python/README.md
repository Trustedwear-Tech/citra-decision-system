<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-decision-api (Python)

Drive a Citra **Decision App** from desktop apps, automations, backends, or
notebooks. Sync, `requests`-based.

```bash
pip install -e .        # or: pip install requests, then use decision_app/ directly
```

```python
from decision_app import DecisionAppClient

client = DecisionAppClient("https://apps.citra-ai.com/api", token=user_jwt)  # token may be a callable

slug = "equipment-inspection-fraud-screen"
c = client.get_contract(slug)

rec = client.recommend(slug, c["run_actions"][0], {"inspection_id": "INS-2026-0013"})
# rec["status"] == "pending_approval" → show rec["decision"] / rec["reasoning"] / rec["planned_writes"]

client.approve(slug, rec["correlation_id"],
               decision="approve",
               overrides=[{"outcome": "Pass"}])   # governed override (allow-list enforced)
```

## Methods

`list_apps`, `get_app`, `get_contract` · `recommend`, `approve`, `decide_direct`,
`chat` · `get_panel_data`, `get_detail`, `get_field_options`, `get_notifications`,
`media_url`, `fetch_media` · `list_runs`, `get_audit`, `get_loop_metrics`,
`get_self_learning`, `set_self_learning`, `submit_feedback`, `calibrate_fraud` ·
`mint_runtime_token`.

Errors raise `DecisionApiError(status, detail, path)`. See
[`../API-REFERENCE.md`](../API-REFERENCE.md) and
[`../INTEGRATION.md`](../INTEGRATION.md). Runnable demo:
[`examples/fraud_screen.py`](examples/fraud_screen.py).
