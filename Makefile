PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: help install venv test lint fmt api cli-list cli-restricted cli-robots cli-licences cli-feed cli-audit \
	cli-email cli-email-profiles cli-agent-status cli-agent-plan cli-agent-crawl cli-auctions cli-auction-pvp cli-schedule \
	up down clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create the local virtualenv
	python3 -m venv .venv

install: venv ## Install runtime + dev dependencies
	$(PIP) install -U pip
	$(PIP) install -e ".[ml]"
	$(PIP) install pytest pytest-asyncio ruff

test: ## Run the test suite (no external services required)
	$(PY) -m pytest -q

lint: ## Lint with ruff
	.venv/bin/ruff check app tests

fmt: ## Auto-fix lint issues
	.venv/bin/ruff check --fix app tests

api: ## Run the FastAPI app (synthetic demo data)
	$(PY) -m uvicorn app.api.main:app --reload --port 8000

cli-list: ## List sources and their legal status
	$(PY) -m app.fetchers.cli list

cli-restricted: ## List restricted / licence-required sources
	$(PY) -m app.fetchers.cli restricted

cli-robots: ## Check robots.txt for one or more URLs
	$(PY) -m app.fetchers.cli check-robots $(URLS)

cli-licences: ## Licence register vs. every source (no licence => no access)
	$(PY) -m app.fetchers.cli licences

cli-feed: ## Run one authorized feed: SRC=name URLS=... or SRC=name DIR=...
	$(PY) -m app.fetchers.cli feed $(SRC) $(if $(URLS),--url $(URLS)) $(if $(DIR),--directory $(DIR))

cli-audit: ## Show the access evidence log (allowed AND refused attempts)
	@test -f var/audit/access.jsonl && tail -20 var/audit/access.jsonl || echo "no audit log yet"

cli-email: ## Channel C - ingest the email-alert mailbox (IMAP or .eml drop)
	$(PY) -m app.email_alert.cli ingest

cli-email-profiles: ## List the portal profiles the email parser knows
	$(PY) -m app.email_alert.cli profiles

cli-agent-status: ## Channel D - what the Agent Import is allowed to touch (no network)
	$(PY) -m app.agent_import.cli status

cli-agent-plan: ## Channel D - show the crawl plan for SRC=name (no network)
	$(PY) -m app.agent_import.cli plan $(SRC)

cli-agent-crawl: ## Channel D - crawl SRC=name (refuses if policy forbids)
	$(PY) -m app.agent_import.cli crawl $(SRC)

cli-auctions: ## Auction sources and their policy verdict
	$(PY) -m app.auction_fetcher.cli sources

cli-auction-pvp: ## Ingest a licensed PVP export drop: DIR=/path/to/drop
	$(PY) -m app.auction_fetcher.cli pvp-export $(DIR)

cli-schedule: ## Print the acquisition schedule plan (policy-aware)
	$(PY) -m app.scheduler.plan plan

daily-run: ## Run one daily acquisition job (lock + checkpoint)
	$(PY) -m app.scheduler.daily run

daily-status: ## Show the last daily run and the next expected run
	$(PY) -m app.scheduler.daily status

cron-status: ## Show the TREI crontab entry (marker # TREI-DAILY)
	$(PY) -m app.scheduler.cronops status

cron-install: ## Install the DST-safe TREI daily entry (backs up first)
	$(PY) -m app.scheduler.cronops install

cron-disable: ## Remove ONLY the # TREI-DAILY entry (backs up first, refuses ambiguity)
	$(PY) -m app.scheduler.cronops disable

up: ## Start the container stack
	docker compose -f docker/docker-compose.yml up --build

down: ## Stop the container stack
	docker compose -f docker/docker-compose.yml down

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__
