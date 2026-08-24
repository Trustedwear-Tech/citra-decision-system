<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-llm

Unified LLM client factory. Replaces what used to be
`Citra-Service/llm_client.py` imported across services via PYTHONPATH.

## Usage

```python
from citra_llm import get_llm_client, get_llm_model, get_embedding_client

client = get_llm_client()
model  = get_llm_model()
resp   = client.chat.completions.create(model=model, messages=[...])
```

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | (required) | Chat endpoint |
| `LLM_API_KEY` | (optional) | Bearer key |
| `LLM_MODEL` | (required) | Default model |
| `EMBEDDING_BASE_URL` | (optional) | Embeddings endpoint |
| `EMBEDDING_API_KEY` | (optional) | Bearer key |
| `EMBEDDING_MODEL` | `baai/bge-m3` | Default embedding model |
| `EMBEDDING_DIMENSION` | `768` | Dimensionality |
| `VISION_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL` | (optional) | Vision / OCR config |
| `SEARCH_BASE_URL`, `SEARCH_API_KEY`, `SEARCH_MODEL` | (optional) | Search-augmented LLM |
