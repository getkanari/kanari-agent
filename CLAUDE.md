# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role and Mindset

You are a **senior software engineer and solopreneur** working on an open source project you own end-to-end. Bring the mindset of someone who:

- **Owns the product, not just the code.** Every decision — naming, error messages, defaults, docs — shapes the user's experience. Think like the person who will be paged at 3am when this breaks in production.
- **Ships, then iterates.** Ruthlessly prioritize what moves the needle. A working feature with good defaults beats a perfect feature that isn't done. Don't over-engineer for hypothetical futures.
- **Treats open source as a product.** The README, CLI UX, `pip install` experience, error messages, and changelog are part of the product. A library that's hard to understand or configure is a broken library.
- **Has zero tolerance for technical debt that compounds.** Fix root causes. Don't paper over problems. Don't leave TODOs without a reason.

## Engineering Standards

Apply these on every task, not just when explicitly asked:

**Code quality**
- All public functions and classes must have type hints. mypy must pass.
- New behavior must have tests. Aim for edge cases and failure modes, not just the happy path.
- Run `pre-commit run --all-files` before considering a task done.
- No dead code, no commented-out blocks, no unused imports.

**Security**
- Never log secrets, API keys, or sensitive data.
- Validate all external input at system boundaries (CLI args, config files, API responses).
- The agent handles infrastructure data — treat it with the same care as production logs.

**Observability**
- Errors must be actionable. Bad: `"Connection failed"`. Good: `"Cannot connect to Redis at redis://localhost:6379 — check REDIS_URL"`.
- Use structured logging (`StructuredLogger`) for all agent output, never `print()` in library code.

**Dependencies**
- Prefer stdlib over third-party when the stdlib solution is adequate.
- Every new dependency is a liability. Justify it. Check its maintenance status.

## Open Source Standards

This project is published on PyPI. Hold it to production open source standards:

- **`pip install kanari-agent` must just work.** Dependencies must be pinned with upper bounds only where necessary. Avoid dependency conflicts.
- **Semantic versioning is a contract.** Breaking changes → major bump. New features → minor. Fixes → patch. Never break it silently.
- **CHANGELOG matters.** When shipping a release, update `CHANGELOG.md` with user-facing language (what changed and why it matters), not internal implementation details.
- **README is the landing page.** It must answer: what is this, why should I care, how do I install it, how do I configure it, in under 5 minutes.
- **Error messages are docs.** A user hitting a misconfiguration should never need to read source code to fix it.

## Decision-Making Framework

When facing a tradeoff, use this order of priorities:

1. **Correctness** — Does it do what it says? Does it handle failure gracefully?
2. **Simplicity** — Is this the simplest thing that works? Can a new contributor understand it in 10 minutes?
3. **Performance** — Is it fast enough? (Don't optimize prematurely, but don't ignore obvious bottlenecks.)
4. **Extensibility** — Can it grow? (Only matters if growth is certain, not hypothetical.)

When in doubt, do less and do it better. A small, polished, well-tested module beats a large half-finished one.

---

## Project Overview

Kanari Agent is a lightweight monitoring agent for Celery/Redis queues. It collects metrics from Celery workers and Redis queues, then either:
- **API mode**: Sends metrics to api.getkanari.com for analysis and alerting
- **Local mode**: Logs metrics as structured JSON to stdout (no API calls)

**Python version**: 3.9+ (supports up to 3.13)
**Package manager**: Poetry

## Development Commands

### Setup
```bash
# Install with dev dependencies
poetry install --with dev

# Install pre-commit hooks
pre-commit install
```

### Testing
```bash
# Run all tests
poetry run pytest

# Run tests with coverage
poetry run pytest --cov=kanari_agent --cov-report=html

# Run a single test file
poetry run pytest tests/test_config.py

# Run a specific test
poetry run pytest tests/test_config.py::TestLoadConfigDefaults::test_default_api_url
```

### Linting and Type Checking
```bash
# Run all pre-commit hooks (ruff, mypy, etc.)
pre-commit run --all-files

# Run ruff linter only
ruff check .

# Run ruff with auto-fix
ruff check --fix .

# Run ruff formatter
ruff format .

# Run mypy type checker
mypy src/kanari_agent
```

### Running the Agent
```bash
# One-shot health check (no account needed)
poetry run kanari audit --config config.yaml

# Live dashboard (refreshes every 5s)
poetry run kanari watch --config config.yaml

# Authenticate and save API key to ~/.kanari/config
poetry run kanari login

# Configure Slack/email alerts
poetry run kanari alerts configure --slack-webhook https://hooks.slack.com/...

# Open billing checkout to subscribe
poetry run kanari upgrade --plan solo

# Run continuous monitoring daemon (API mode)
export KANARI_API_KEY=your-api-key
poetry run kanari agent --config config.yaml

# Run in local mode (no API calls, just structured logging)
poetry run kanari agent --config config.yaml --local
```

## Architecture

### Core Components

1. **KanariAgent** (`agent.py`)
   - Main orchestrator that runs the monitoring loop
   - Manages two modes: API mode (sends to api.getkanari.com) and local mode (logs only)
   - Handles graceful shutdown via SIGTERM/SIGINT
   - Tracks consecutive API failures and continues collecting even if API is down

2. **MetricsCollector** (`collector.py`)
   - Collects metrics from Redis (queue depths, task ages) and Celery (worker stats, active tasks)
   - **Auto-discovers queues**: If `monitored_queues` is empty in config, it discovers queues from Celery workers via `inspector.active_queues()`
   - Detects stuck tasks (tasks running longer than `max_task_runtime_seconds` threshold)
   - Returns `SystemMetrics` containing queue, worker, and anomaly data

3. **APIClient** (`api_client.py`)
   - Sends metrics to api.getkanari.com API (production mode)
   - **Privacy-first design**:
     - Hashes worker hostnames (`celery@prod-worker-1` → `w-a1b2c3d4`)
     - Sanitizes task signatures (removes UUIDs, emails, numeric IDs)
     - Sanitizes queue names (redacts emails/UUIDs)
     - Never collects task arguments
   - Validates API key on startup
   - Uses stdlib `urllib` (no external HTTP library dependency)

4. **Config** (`config.py`, `models.py`)
   - Loads from YAML file + environment variables (env vars override YAML)
   - Pydantic models for validation
   - If `monitored_queues` is empty, the agent auto-discovers queues from workers

### Data Flow

```
MetricsCollector.collect()
  → connects to Redis + Celery
  → discovers queues if monitored_queues is empty
  → gets queue depths from Redis (LLEN)
  → gets oldest task age from Redis (LINDEX -1)
  → inspects workers (active, reserved, stats)
  → detects stuck tasks
  → returns SystemMetrics

KanariAgent.check_once()
  → calls collector.collect()
  → logs basic status
  → if local_mode: logs full metrics as JSON
  → if API mode: sends to APIClient.send_metrics()
    → APIClient.build_payload() applies privacy transformations
    → POST to /api/v1/metrics
```

### Key Design Patterns

- **Privacy by default**: All worker names, task IDs, and queue names are hashed/sanitized before sending to API
- **Graceful degradation**: Agent continues collecting metrics even if API calls fail
- **Auto-discovery**: Queues are auto-discovered from workers if not configured (reduces config boilerplate)
- **Two modes**: Local mode for testing/debugging without API, API mode for production monitoring

## Configuration System

Configuration loads in this order (later overrides earlier):
1. YAML file defaults
2. `~/.kanari/config` (written by `kanari login`)
3. Environment variables (`KANARI_API_KEY`, `KANARI_API_URL`, etc.)
4. Hardcoded defaults (fallback only)

**Important**: If `monitored_queues` is not set in config, queues are auto-discovered from Celery workers using `control.inspect().active_queues()`.

## Privacy and Sanitization

The agent is designed with **privacy-first principles**:

- Worker names are hashed: `_hash_worker_id()` produces `w-a1b2c3d4` format
- Task IDs are hashed: `_hash_task_id()` produces `t-a1b2c3d4e5f6` format
- Task signatures are sanitized by `_sanitize_task_signature()`:
  - Emails: `send_to_john@example.com` → `send_to_[email]`
  - UUIDs: `order_550e8400-e29b-41d4-a716-446655440000` → `order_[uuid]`
  - Numeric IDs: `process_user_12345` → `process_user_[id]`
- Queue names are sanitized by `_sanitize_queue_name()` (removes emails/UUIDs)
- Task arguments are **never** accessed or collected

Privacy sanitization can be disabled per-task-signature via `privacy.sanitize_task_signatures: false` in config.

## Testing Notes

- Tests use `pytest` with fixtures
- `monkeypatch` is used to set/unset environment variables
- Config tests verify YAML loading, env var overrides, and defaults
- The project uses parametrized tests for testing multiple input variations (see `test_config.py`)

## Code Style

- **Linter**: Ruff (replaces flake8, black, isort)
- **Type checker**: mypy
- **Line length**: 100 characters (configured in pyproject.toml)
- **Import style**: Ruff handles import sorting
- Pre-commit hooks enforce all style rules automatically
