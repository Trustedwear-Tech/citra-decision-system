# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

from fastapi.encoders import jsonable_encoder


def serialize_validation_error_details(error_details):
    """Convert FastAPI validation details into JSON-safe structures."""
    return jsonable_encoder(
        error_details,
        custom_encoder={
            bytes: lambda value: value.decode("utf-8", errors="replace"),
            BaseException: lambda exc: str(exc),
        },
    )
