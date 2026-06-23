# Kanari Rename Design

**Date:** 2026-06-23
**Scope:** kanari-agent repo (formerly kanari-agent)

## Goal

Rename all `doorman*` references to `kanari*` across the Python package, CLI, env vars, config paths, and documentation. Clean rename — no backward compatibility layer.

## What Changes

### Package and entry points (`pyproject.toml`)
- `name`: `"kanari-agent"` → `"kanari-agent"`
- `packages`: `kanari_agent` → `kanari_agent`
- Entry points: `doorman` → `kanari`, `kanari-agent` → `kanari-agent`
- `repository` URL → `https://github.com/getkanari/kanari-agent`
- `[tool.bandit] targets`: `src/kanari_agent` → `src/kanari_agent`

### Module directory
- `src/kanari_agent/` → `src/kanari_agent/`
- All imports: `from kanari_agent.X import Y` → `from kanari_agent.X import Y`

### Public symbols
- `KanariAgent` → `KanariAgent`
- `KanariStampPlugin` → `KanariStampPlugin`
- `KANARI_TS_HEADER = "doorman_sent_ts"` → `KANARI_TS_HEADER = "kanari_sent_ts"`

### Internal functions and constants
- `load_doorman_config` → `load_kanari_config`
- `save_doorman_config` → `save_kanari_config`
- `KANARI_CONFIG_PATH` → `KANARI_CONFIG_PATH = Path.home() / ".kanari" / "config"`

### Environment variables
- `KANARI_API_KEY` → `KANARI_API_KEY`
- `KANARI_API_URL` → `KANARI_API_URL`
- `KANARI_LOCAL_MODE` → `KANARI_LOCAL_MODE`
- `KANARI_SANITIZE_TASK_SIGNATURES` → `KANARI_SANITIZE_TASK_SIGNATURES`

### CLI strings and User-Agent
- All `"kanari login"`, `"kanari agent"`, etc. → `"kanari login"`, `"kanari agent"`, etc.
- `f"kanari-agent {AGENT_VERSION}"` → `f"kanari-agent {AGENT_VERSION}"`
- `User-Agent` header: `kanari-agent/{version}` → `kanari-agent/{version}`

### Test files
- All `patch("kanari_agent.*")` → `patch("kanari_agent.*")`
- All `from kanari_agent.X import Y` → `from kanari_agent.X import Y`

### Documentation
- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `ASSISTANT.md`
- `config.example.yaml`: env var names

### Local filesystem (post-commit)
- `/Users/herchila/Projects/kanari-agent` → `/Users/herchila/Projects/kanari-agent`

## Constraints
- No backward compatibility (pre-launch, no real users)
- Python ≥ 3.9, all type hints preserved
- All 276 tests must pass after rename
- `pre-commit run --all-files` must pass
- Directory rename happens last (after all commits) to avoid breaking paths

## Out of Scope
- doorman-backend repo (internal, low priority)
- doorman-landing (separate repo)
- LemonSqueezy billing branch (merge separately after rename)
