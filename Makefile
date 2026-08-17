# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Citra Decision System — local quickstart.  Run `make help` for the list.
#
# Ported from the June 2026 draft (citra-ai-oss). The bring-up half of that
# draft is sound and is reused verbatim where it still applies; the
# source-registration half was rewritten, because the MCP source registry moved
# from the Mongo `dept_sources` collection to a local sources.json file and the
# old mode was REMOVED, not deprecated. See docs/change-the-demo.md.
.DEFAULT_GOAL := help
.PHONY: help wizard setup start install seed-demo down logs ps validate-sources ontology

COMPOSE := docker compose -f docker-compose.quickstart.yml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

wizard: ## Guided setup: configure your AI key, then install (easiest first run)
	./scripts/quickstart/wizard.sh

setup: ## Phase 1: generate .env, start data stores, create DB resources
	./scripts/quickstart/setup.sh

start: ## Phase 2: start all services + super-admin + the acme-bank demo
	./scripts/quickstart/start.sh

install: ## Phase 1 + Phase 2 (full bring-up from scratch)
	./scripts/quickstart/setup.sh && ./scripts/quickstart/start.sh

seed-demo: ## Re-seed the demo tenant (TENANT=acme-bank by default)
	./scripts/quickstart/seed-demo.sh $(or $(TENANT),acme-bank)

ontology: ## Build a sources.json from a live database, by interview (ARGS='--org x --dept y ...')
	bash scripts/quickstart/make-ontology.sh $(ARGS)

validate-sources: ## Check a sources.json against the MCP registry schema
	python source-mcp-template/validate_sources.py \
	  $(or $(FILE),demo-data/tenants/acme-bank/mcp/sources.json)

ps: ## Show running services
	$(COMPOSE) ps

logs: ## Tail the core service logs
	$(COMPOSE) logs -f citra-service citra-user-service smart-app-service

down: ## Stop everything (keeps data). `make down ARGS=-v` also wipes volumes.
	$(COMPOSE) down $(ARGS)
