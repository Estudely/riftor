# riftor — common dev tasks. Requires uv (https://docs.astral.sh/uv/).
.PHONY: help dev run lint typecheck test smoke check build clean install-hooks demo-headless

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

demo-headless: ## Offline --prompt smoke (no API key; uses RIFTOR_DEMO_RESPONSE)
	RIFTOR_DEMO_RESPONSE='demo ok' uv run riftor --prompt 'say hi'

check: lint typecheck test smoke ## Run every CI gate locally

build: ## Build wheel + sdist
	uv build

clean: ## Remove build/test artifacts
	rm -rf dist build .pytest_cache .ruff_cache **/__pycache__

install-hooks: ## Install pre-commit hooks
	uv run pre-commit install
