# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Pins the shared role/owner/entity vocabulary so an accidental rename is
caught. Keep Roles.ALL aligned with Citra-User-Service/src/constants/roles.js
(ALL_ROLES) — both are just this 6-name list."""
from citra_auth import Roles, OwnerType, EntityType


def test_roles_all():
    assert Roles.ALL == {
        "user", "IT-workflow", "dept_admin", "org_admin", "super_admin",
        "decision-app-builder",
    }


def test_decision_app_builder_role():
    assert Roles.DECISION_APP_BUILDER == "decision-app-builder"


def test_role_values():
    assert Roles.USER == "user"
    assert Roles.IT_WORKFLOW == "IT-workflow"
    assert Roles.DEPT_ADMIN == "dept_admin"
    assert Roles.ORG_ADMIN == "org_admin"
    assert Roles.SUPER_ADMIN == "super_admin"


def test_owner_type():
    assert OwnerType.ALL == {"user", "service_account", "dept", "org"}


def test_entity_type():
    assert EntityType.ALL == {"company", "state", "district", "agency", "general"}
