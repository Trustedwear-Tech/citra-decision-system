# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# Citra Decision System — local quickstart.  Run `make help` for the list.
#
# Ported from the June 2026 draft (citra-ai-oss). The bring-up half of that
# draft is sound and is reused verbatim where it still applies; the
# source-registration half was rewritten, because the MCP source registry moved
# from the Mongo `dept_sources` collection to a local sources.json file and the
# old mode was REMOVED, not deprecated. See docs/change-the-demo.md.
.DEFAULT_GOAL := help
.PHONY: help wizard setup start install seed-demo up stop down logs ps validate-sources ontology

COMPOSE := docker compose -f docker-compose.quickstart.yml

# ARGS is passed through to the underlying script, so every flag those
# scripts accept is reachable from make. `--help` on any of them lists
# its own options: ./scripts/quickstart/wizard.sh --help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

wizard: ## Guided setup, easiest first run (ARGS=--fresh wipes .env + volumes first)
	./scripts/quickstart/wizard.sh $(ARGS)

setup: ## Phase 1: generate .env, start data stores, create DB resources
	./scripts/quickstart/setup.sh

start: ## Phase 2: services + super-admin + demo (ARGS='--no-demo' or '--demo <tenant>')
	./scripts/quickstart/start.sh $(ARGS)

install: ## Phase 1 + Phase 2 (full bring-up from scratch)
	./scripts/quickstart/setup.sh && ./scripts/quickstart/start.sh

seed-demo: ## Re-seed the demo tenant (TENANT=acme-bank by default)
	./scripts/quickstart/seed-demo.sh $(or $(TENANT),acme-bank)

ontology: ## Build a sources.json from a live database, by interview (ARGS='--org x --dept y ...')
	bash scripts/quickstart/make-ontology.sh $(ARGS)

validate-sources: ## Check a sources.json against the MCP registry schema
	python source-mcp-template/validate_sources.py \
	  $(or $(FILE),demo-data/tenants/acme-bank/mcp/sources.json)

up: ## Start the containers again after `down`/`stop` (no seeding, no rebuild)
	$(COMPOSE) up -d

stop: ## Stop the containers without removing them (resume with `make up`)
	$(COMPOSE) stop

ps: ## Show running services
	$(COMPOSE) ps

logs: ## Tail the core service logs
	$(COMPOSE) logs -f citra-service citra-user-service smart-app-service

down: ## Stop everything (keeps data). `make down ARGS=-v` also wipes volumes.
	$(COMPOSE) down $(ARGS)
