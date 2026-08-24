# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Middleware package for Citra Service

This package contains middleware components for:
- Credit checking and validation
- Usage tracking and billing (all in Python, no Node.js calls)
- Authorization and access control
"""

from .credit_check_middleware import (
    get_usage_service,
    check_user_credits,
    track_query_usage,
    estimate_query_cost,
    get_active_pricing,
    require_credits,
    InsufficientCreditsError
)

from .authorization_middleware import (
    AuthorizationMiddleware,
    require_resource_access,
    create_authorization_dependency
)

__all__ = [
    # Credit middleware
    'get_usage_service',
    'check_user_credits',
    'track_query_usage',
    'estimate_query_cost',
    'get_active_pricing',
    'require_credits',
    'InsufficientCreditsError',
    # Authorization middleware
    'AuthorizationMiddleware',
    'require_resource_access',
    'create_authorization_dependency'
]
