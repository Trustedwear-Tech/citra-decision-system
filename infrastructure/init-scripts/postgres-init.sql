-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
-- Author: Rohit Kumar Chandan
-- SPDX-License-Identifier: Apache-2.0
--
-- Licensed under the Apache License, Version 2.0 (the "License"); you may not
-- use this file except in compliance with the License. You may obtain a copy of
-- the License at http://www.apache.org/licenses/LICENSE-2.0

--
-- Shared Postgres bootstrap for the local quickstart stack.
--
-- Runs ONCE, on first start of an empty `postgres_data` volume (Postgres only
-- executes /docker-entrypoint-initdb.d on an uninitialised data directory). To
-- re-run it you must drop the volume:
--     docker compose -f docker-compose.quickstart.yml down -v
--
-- Creates the system-of-record database the acme-bank demo writes to. The
-- credentials below are LOCAL DEMO credentials and are deliberately in the
-- public tree: they are unreachable outside this compose network and match what
-- demo-data/tenants/acme-bank/mcp/docker-compose.yml expects. Do NOT reuse this
-- pattern for anything real -- production connection secrets belong in Vault.

-- ── Acme Bank (lending / collections / claims demo) ───────────────────────
CREATE ROLE acme_bank WITH LOGIN PASSWORD 'acme_bank_demo_pw';
CREATE DATABASE acme_bank OWNER acme_bank;
GRANT ALL PRIVILEGES ON DATABASE acme_bank TO acme_bank;
