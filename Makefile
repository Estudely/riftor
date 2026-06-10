# riftor — common dev tasks. Requires uv (https://docs.astral.sh/uv/).
.PHONY: help dev run lint typecheck test smoke check build clean install-hooks

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

dev: ## Sync deps (incl. dev extras)
	uv sync --extra dev

run: ## Launch the TUI
	uv run riftor

lint: ## Lint with ruff
	uv run ruff check riftor dev tests

typecheck: ## Type-check with pyright
	uv run pyright riftor

test: ## Run the pytest suite
	uv run pytest

smoke: ## Run the headless TUI smoke suite
	uv run python dev/smoke.py

check: lint typecheck test smoke ## Run every CI gate locally

build: ## Build wheel + sdist
	uv build

clean: ## Remove build/test artifacts
	rm -rf dist build .pytest_cache .ruff_cache **/__pycache__

install-hooks: ## Install pre-commit hooks
	uv run pre-commit install

telemetry-keys: ## Bake telemetry keys from env vars for a release build
	@echo "# Generated at build time — not committed to git." > riftor/_telemetry_keys.py
	@echo "SENTRY_DSN = \"$${RIFTOR_SENTRY_DSN:-}\"" >> riftor/_telemetry_keys.py
	@echo "POSTHOG_API_KEY = \"$${RIFTOR_POSTHOG_KEY:-}\"" >> riftor/_telemetry_keys.py
	@echo "POSTHOG_HOST = \"$${RIFTOR_POSTHOG_HOST:-https://us.i.posthog.com}\"" >> riftor/_telemetry_keys.py
	@echo "wrote riftor/_telemetry_keys.py"
