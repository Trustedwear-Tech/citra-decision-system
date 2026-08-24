# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra-queue — Redis-backed job queue, one source of truth for
producer + consumer across all Citra services.
"""
from .queue import (
    DEFAULT_QUEUE,
    DEFAULT_MAX_RETRIES,
    RESULT_TTL_SECONDS,
    GROUP,
    Job,
    JobPermanentFailure,
    enqueue,
    get_status,
    consume_one,
    claim_stale,
    queue_depth,
    dlq_depth,
    default_consumer_name,
    mark_running,
    mark_done,
    mark_failed,
)

__all__ = [
    "DEFAULT_QUEUE",
    "DEFAULT_MAX_RETRIES",
    "RESULT_TTL_SECONDS",
    "GROUP",
    "Job",
    "JobPermanentFailure",
    "enqueue",
    "get_status",
    "consume_one",
    "claim_stale",
    "queue_depth",
    "dlq_depth",
    "default_consumer_name",
    "mark_running",
    "mark_done",
    "mark_failed",
]
