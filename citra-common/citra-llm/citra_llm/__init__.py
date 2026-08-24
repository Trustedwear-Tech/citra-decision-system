# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra-llm — unified LLM client factory + OSS chat-completion wrapper.

Exposes:
  - get_llm_client / get_embedding_client / get_vision_client / get_search_client
  - get_llm_model / get_embedding_model / get_embedding_dimension / ...
  - llm_call          (chat completion w/ tier resolution, billing-aware
                       when the optional billing middleware is installed)

For the legacy `from llm_oss import llm_call` pattern, use:
  from citra_llm.oss import llm_call
"""
from .client import (  # noqa: F401
    get_llm_client,
    get_llm_model,
    get_llm_extra_body,
    get_default_model,
    get_embedding_client,
    get_embedding_model,
    get_embedding_dimension,
    get_vision_client,
    get_vision_model,
    get_vision_extra_body,
    get_search_client,
    get_search_model,
    reset_clients,
)

__all__ = [
    "get_llm_client",
    "get_llm_model",
    "get_llm_extra_body",
    "get_default_model",
    "get_embedding_client",
    "get_embedding_model",
    "get_embedding_dimension",
    "get_vision_client",
    "get_vision_model",
    "get_vision_extra_body",
    "get_search_client",
    "get_search_model",
    "reset_clients",
]
