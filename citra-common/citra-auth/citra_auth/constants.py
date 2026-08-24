# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Shared identity vocabulary — the fixed string constants for the platform's
roles plus the small fixed token-sets (owner_type, entity_type).

Import these instead of hard-coding string literals (`"super_admin"`, …) so that:
  • a typo is an immediate AttributeError (`Roles.SUPERADMIN`), not a
    silently-wrong string that fails a check open or closed;
  • there is ONE authoritative list of valid role names per runtime;
  • adding a new role is a one-line change here, referenced everywhere.

This is ONLY the vocabulary (the names). Authorization LOGIC — which roles may
do what — stays inline in each service: a route composes its own set from these
constants, e.g. `{Roles.DEPT_ADMIN, Roles.ORG_ADMIN, Roles.SUPER_ADMIN}`. There
is deliberately NO central role→capability mapping here.

`Roles.ALL` mirrors the Citra-User-Service `CitraAIUser.roles` Mongoose enum
(the authoritative declaration). Keep the two aligned — `tests/test_constants.py`
pins this side; the Node side (`Citra-User-Service/src/constants/roles.js`) feeds
the model enum directly so it can't drift from Node code.
"""
from __future__ import annotations


class Roles:
    USER = "user"
    IT_WORKFLOW = "IT-workflow"
    DEPT_ADMIN = "dept_admin"
    ORG_ADMIN = "org_admin"
    SUPER_ADMIN = "super_admin"
    #: A non-admin who may BUILD & publish Decision Apps/APIs (the builder
    #: persona). Consumers see only apps/dashboards published to them; the
    #: builder card + build API are gated on this role (or any admin role).
    DECISION_APP_BUILDER = "decision-app-builder"

    #: Every valid platform role (mirror of the CitraAIUser.roles enum).
    ALL = frozenset({USER, IT_WORKFLOW, DEPT_ADMIN, ORG_ADMIN, SUPER_ADMIN, DECISION_APP_BUILDER})


class OwnerType:
    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    DEPT = "dept"
    ORG = "org"

    ALL = frozenset({USER, SERVICE_ACCOUNT, DEPT, ORG})


class EntityType:
    COMPANY = "company"
    STATE = "state"
    DISTRICT = "district"
    AGENCY = "agency"
    GENERAL = "general"

    ALL = frozenset({COMPANY, STATE, DISTRICT, AGENCY, GENERAL})
