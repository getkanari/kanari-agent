.PHONY: help install test test-path lint format typecheck check build publish clean \
        run run-once run-local

# ── Help ─────────────────────────────────────────────────────────────────────
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Setup ────────────────────────────────────────────────────────────────────
install: ## Install all dependencies (including dev)
	poetry install --with dev
	pre-commit install

# ── Quality ──────────────────────────────────────────────────────────────────
test: ## Run tests with coverage (fails below 80%)
	poetry run pytest --cov=doorman_agent --cov-report=term-missing --cov-fail-under=80

test-path: ## Run tests with path
	poetry run pytest $(path) -s

lint: ## Run ruff linter
	poetry run ruff check .

format: ## Auto-format code with ruff
	poetry run ruff format .

typecheck: ## Run mypy type checker
	poetry run mypy src/doorman_agent

security: ## Run security scan (bandit + detect-secrets)
	poetry run bandit -c pyproject.toml -r src/doorman_agent
	poetry run detect-secrets-hook --baseline .secrets.baseline

check: lint typecheck security test ## Run lint + typecheck + security + tests (full CI gate)

# ── Build & Publish ──────────────────────────────────────────────────────────
build: ## Build wheel and sdist
	poetry build
	@echo "Built artifacts:"
	@ls -lh dist/

publish-test: build ## Publish to TestPyPI
	poetry config repositories.testpypi https://test.pypi.org/legacy/
	poetry publish -r testpypi

publish: build ## Publish to PyPI (use CI instead for production releases)
	poetry publish

# ── Run ──────────────────────────────────────────────────────────────────────
run: ## Run agent in API mode (requires DOORMAN_API_KEY)
	poetry run doorman-agent --config config.yaml

run-once: ## Run agent once in local mode
	poetry run doorman-agent --config config.yaml --local --once

run-local: ## Run agent continuously in local mode (no API calls)
	poetry run doorman-agent --config config.yaml --local

# ── Cleanup ──────────────────────────────────────────────────────────────────
clean: ## Remove build artifacts and cache files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -name ".coverage" -delete
	find . -name "htmlcov" -exec rm -rf {} +
	find . -name ".pytest_cache" -exec rm -rf {} +
	find . -name ".mypy_cache" -exec rm -rf {} +
	rm -rf dist/
	@echo "✅ Clean"
