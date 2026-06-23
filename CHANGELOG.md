# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.1.0] — 2026-05-31

### Added
- **Python 3.9 support** — minimum version lowered from 3.10 to 3.9, enabling use on older production environments
- **Security scanning in CI** — `bandit` and `detect-secrets` added to the quality gate
- **Comprehensive test suite** — 272 tests across all modules (agent, api_client, audit, cli, collector, config, findings, privacy) with 80% coverage enforced

### Changed
- **Faster Celery inspection** — consolidated four separate inspector calls (`active_queues`, `active`, `reserved`, `stats`) into a single `_inspect_all()` call, reducing audit time on large clusters
- **Shorter connection timeouts** — Redis socket timeout reduced from 30s to 5s; Celery inspect timeout reduced to 1s, so failures are detected quickly rather than hanging the audit
- **Poetry pinned to 1.8.5** in CI workflows for reproducible builds

### Fixed
- `Optional[X]` type annotations made compatible with Python 3.9 across all modules
- `bare except:` clauses replaced with `except Exception:` in simulator cleanup

---

## [0.1.0b1] — 2026-04-13

Initial beta release.

### Added
- **`kanari audit`** — one-shot health check with TUI, JSON (`--json`), and Markdown (`--md`) output modes; exits with code 0/1/2 for CI/CD integration
- **`kanari watch`** — live dashboard that refreshes every N seconds (`--interval`)
- **`kanari agent`** — continuous monitoring daemon; local mode (`--local`) logs structured JSON, API mode sends metrics to api.getkanari.com
- **FindingsEngine** — structured health observations with severity, evidence, probable cause, confirmation steps, and safe fix for each issue:
  - `REDIS_DOWN` (CRITICAL) — Redis unreachable
  - `NO_WORKERS` (CRITICAL) — no Celery workers responding
  - `WORKER_OFFLINE` (CRITICAL) — individual worker not responding
  - `STUCK_TASK` (HIGH) — task running longer than configured threshold
  - `QUEUE_BACKLOG_*` (HIGH/MEDIUM) — queue depth exceeds threshold
  - `QUEUE_SLA_BREACH_*` (HIGH) — oldest task waiting longer than SLA
  - `LATENCY_UNAVAILABLE` (MEDIUM) — no timestamps in queue messages
  - `HIGH_SATURATION` (MEDIUM) — worker pool above 80% utilization
- **`kanari audit --deep`** — configuration analysis for Redis (`maxmemory`, eviction policy, persistence, connection pool) and Celery (`task_acks_late`, `task_reject_on_worker_lost`, `prefetch_multiplier`)
- **KanariStampPlugin** — optional one-line install that adds `kanari_sent_ts` timestamps to every published task, enabling accurate latency measurement
- **Privacy-first design** — worker names hashed (`w-a1b2c3d4`), task IDs hashed (`t-8f3a2b1c`), task signatures sanitized (emails, UUIDs, numeric IDs redacted), task arguments never accessed
- **Auto-discovery** — when `monitored_queues` is empty, queues are auto-discovered from Celery workers via `inspect.active_queues()`
- **Subparser-based CLI** — `kanari audit`, `kanari watch`, `kanari agent` replace the previous flat argument structure
- **Structured logging** — JSON-formatted logs via `StructuredLogger` throughout
- **GitHub Actions CI** — matrix testing on Python 3.9–3.12 with ruff, mypy, bandit, and pytest (80% coverage minimum)
- **GitHub Actions publish** — tag-triggered PyPI publish via OIDC trusted publishing

### Removed
- Simulator module (moved out of scope)
- Dockerfile and docker-compose.yml (moved out of scope)

---

[Unreleased]: https://github.com/herchila/kanari-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/herchila/kanari-agent/compare/v0.1.0b1...v0.1.0
[0.1.0b1]: https://github.com/herchila/kanari-agent/releases/tag/v0.1.0b1
