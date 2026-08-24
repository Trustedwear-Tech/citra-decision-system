# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra-mongo — shared MongoDB connection manager."""
from .manager import (
    MongoDBManager,
    MONGODB_DATABASE,
    get_async_mongo_client,
    get_mongo_client,
    get_async_database,
    get_sync_database,
    get_mongodb_manager,
    get_database_name,
    close_all_connections,
)

__all__ = [
    "MongoDBManager",
    "MONGODB_DATABASE",
    "get_async_mongo_client",
    "get_mongo_client",
    "get_async_database",
    "get_sync_database",
    "get_mongodb_manager",
    "get_database_name",
    "close_all_connections",
]
