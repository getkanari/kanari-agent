# 🚪 Doorman Agent

**Privacy-first monitoring for Celery + Redis.** Zero PII exposure. One-line install.

```bash
pip install doorman-agent && doorman-agent --local
```

That's it. You're monitoring.

> ⚠️ **Alpha Notice:** Doorman is currently in alpha (`0.1.0a`). The API and dashboard at doorman.com are not yet publicly available.

---

## Why Doorman?

Your Celery workers are critical infrastructure. When they fail silently—stuck tasks, dead workers, growing queues—you find out from angry users, not your monitoring.

**Doorman fixes this.** Proactive alerts before users notice.

| Problem | Doorman Solution |
|---------|------------------|
| Worker dies but process stays alive | Detects zombie workers via heartbeat |
| Queue grows silently | Alerts on depth + latency thresholds |
| Task stuck for 2 hours | Flags anomalies with `stuck_task` alerts |
| "Is it my code or infra?" | `saturation_pct` tells you instantly |

---

## 🔒 Privacy by Design

**We never see your data.** The agent collects metrics only—task arguments and results are never accessed.

```
┌─────────────────────────────────────────────────────────────┐
│                    YOUR INFRASTRUCTURE                       │
│  ┌─────────┐    ┌─────────┐    ┌─────────────────────────┐  │
│  │  Redis  │◄───│ Celery  │◄───│  doorman-agent          │  │
│  │         │    │ Workers │    │  • Queue depth     ✓    │  │
│  └─────────┘    └─────────┘    │  • Worker status   ✓    │  │
│                                │  • Task latency    ✓    │  │
│                                │  • Task args       ✗    │  │
│                                │  • Task results    ✗    │  │
│                                │  • Task kwargs     ✗    │  │
│                                └───────────┬─────────────┘  │
└────────────────────────────────────────────┼────────────────┘
                                             │ HTTPS (metrics only)
                                             ▼
                                   ┌─────────────────┐
                                   │  doorman.com    │
                                   │  Analysis +     │
                                   │  Alerts         │
                                   └─────────────────┘
```

### What we collect vs. what we don't

| ✅ Collected | ❌ Never Collected |
|--------------|-------------------|
| Queue names | Task arguments |
| Queue depth | Task keyword arguments |
| Task latency | Task results |
| Worker count | Task payloads |
| Worker status | Database queries |
| Stuck task duration | User data |

### PII Sanitization

Even metadata is sanitized before leaving your infrastructure:

| Data | Raw | Sent to API |
|------|-----|-------------|
| Worker hostname | `celery@prod-db-worker-1.internal` | `w-a1b2c3d4` (hashed) |
| Task ID | `user-john@acme.com-12345` | `t-8f3a2b1c4d5e` (hashed) |
| Task name | `process_user_98765` | `process_user_[id]` (sanitized) |
| Queue name | `emails-john@acme.com` | `emails-[email]` (sanitized) |

**Verify it yourself:**

```bash
# Run in local mode - see exactly what would be sent
doorman-agent --local | jq '.'
```

---

## Quick Start

### 1. Install

```bash
pip install doorman-agent
```

### 2. Test locally (no API key, no data sent anywhere)

```bash
REDIS_URL=redis://localhost:6379/0 doorman-agent --local
```

### 3. Connect to Doorman (when ready)

```bash
export DOORMAN_API_KEY=your-api-key
doorman-agent --config config.yaml
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DOORMAN_API_KEY` | Your API key | Required (API mode) |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker URL | Same as `REDIS_URL` |
| `DOORMAN_LOCAL_MODE` | `true` = no API calls | `false` |
| `CHECK_INTERVAL` | Seconds between checks | `30` |

### Config File (optional)

Copy `config.example.yaml` and adjust:

```yaml
redis_url: redis://prod-redis:6379/1
celery_broker_url: redis://prod-redis:6379/1
check_interval_seconds: 15

# Queues to monitor (empty = auto-discover from workers)
monitored_queues: []

# Privacy settings
privacy:
  sanitize_task_signatures: true  # default: true

# Alert thresholds
thresholds:
  max_queue_size: 1000
  max_wait_time_seconds: 60
  max_task_runtime_seconds: 1800  # 30 minutes
```

```bash
doorman-agent --config config.yaml
```

---

## Key Metrics

### `saturation_pct` — The metric that matters

```
saturation_pct = (active_tasks / total_concurrency) × 100
```

| Saturation | Queue Depth | Diagnosis |
|------------|-------------|-----------|
| 🔴 >90% | Growing | **Need more workers** |
| 🟡 50-90% | Stable | **Normal** |
| 🟢 <30% | Growing | **Ghost workers** (network/config issue) |
| 🟢 <30% | Stable ~0 | **Healthy** |

### Full metrics payload

```json
{
  "metrics": {
    "total_pending": 1250,
    "total_active": 12,
    "saturation_pct": 75.0,
    "max_latency_sec": 125.7,
    "alive_workers": 3,
    "total_workers": 4
  },
  "queues": [
    {"name": "celery", "depth": 800, "latency_sec": 125.7}
  ],
  "workers": [
    {"id_hash": "w-a1b2c3d4", "status": "online", "concurrency": 4}
  ],
  "anomalies": [
    {"type": "stuck_task", "task_id_hash": "t-8f3a2b1c", "duration_sec": 2847}
  ],
  "privacy": {
    "args_accessed": false
  }
}
```

---

## CLI Reference

The CLI uses subcommands. Both `doorman` and `doorman-agent` work as the binary name.

```bash
# One-shot audit report (TUI)
doorman audit

# Audit with machine-readable JSON output (for CI/scripts)
doorman audit --json

# Deep audit: includes Redis/Celery configuration analysis
doorman audit --deep

# Live dashboard, refreshes every 5s
doorman watch
doorman watch --interval 10

# Daemon loop (local mode, no API calls)
doorman agent --local

# Daemon loop (production, sends to doorman.com)
doorman agent --token your-api-key
```

### Using env vars to connect to your infrastructure

`REDIS_URL` and `CELERY_BROKER_URL` override whatever is in `config.yaml`:

```bash
REDIS_URL=redis://localhost:6379/1 \
CELERY_BROKER_URL=redis://localhost:6379/1 \
  doorman audit
```

---

## Audit Mode

One-time health check with a formatted report. Perfect for CI/CD, debugging, or quick status checks.

```bash
doorman audit
```

**Sample output:**

```
🔍 Doorman Audit
════════════════════════════════════════════════════════════

Infrastructure
  ✅ Redis: connected
  ✅ Celery: connected (4 workers)

Workers
  ✅ worker-1: online (2/4 slots)
  ✅ worker-2: online (3/4 slots)
  ⚠️  worker-3: online (4/4 slots) — at capacity
  ❌ worker-4: offline

Queues
  ✅ celery: 12 pending, 2.1s latency
  ✅ notifications: empty
  🔥 emails: 847 pending, 125s latency — CONGESTED

Metrics
  📊 Saturation: 68.7% (11/16 slots)
  ⏱️  Max Latency: 125s (emails)
  📋 Total Pending: 859 tasks

════════════════════════════════════════════════════════════
💡 Recommendations:
  • Scale workers for 'emails' queue (847 pending, 125s latency)
  • Check 1 offline worker(s) — not responding to ping

════════════════════════════════════════════════════════════
⚠️  Warnings detected
```

**Exit codes (for CI/CD):**

| Code | Meaning |
|------|---------|
| `0` | ✅ Healthy |
| `1` | ⚠️ Warnings (congested queues, workers at capacity) |
| `2` | ❌ Critical (stuck tasks, dead workers) |

### Trend Detection

Use `doorman watch` to detect if queues are growing or shrinking over time:

```bash
doorman watch --interval 10
```

**Sample output with trends:**

```
Queues
  ✅ celery: 45 pending ↑+15
  🔥 emails: 847 pending, 125s latency ↑+203 — CONGESTED
  ✅ notifications: 10 pending ↓-5

Trends (over 3 samples)
  ↑ emails: +203 tasks (644 → 847)
  ↓ notifications: -5 tasks (15 → 10)

💡 Recommendations:
  • Possible ghost workers: 'emails' growing but saturation is low (25.0%)
```

### Deep Configuration Analysis

```bash
doorman audit --deep
```

**Checks included:**

| Category | Check | Risk |
|----------|-------|------|
| Redis | `maxmemory` not set | OOM kill |
| Redis | `maxmemory-policy = noeviction` | Writes fail when full |
| Redis | Persistence disabled | Data loss on restart |
| Redis | Connection pool > 80% | Connection exhaustion |
| Celery | `task_acks_late = False` | Task loss if worker dies |
| Celery | `task_reject_on_worker_lost = False` | Silent task loss |
| Celery | `prefetch_multiplier > 1` | Uneven task distribution |
| Infra | Single worker | Single point of failure |

---

## Security

- **No inbound connections** — Agent pushes to API, never listens
- **TLS only** — All API communication over HTTPS
- **API key scoped** — Keys are project-specific, revocable
- **No shell access** — Agent has no remote execution capability
- **Auditable** — Run `--local` to inspect all collected data before sending anything

### Disabling task signature sanitization

If your task names are guaranteed PII-free:

```yaml
privacy:
  sanitize_task_signatures: false
```

---

## Requirements

- Python 3.9+
- Redis
- Celery 5.2+

---

## Development

### Setup

```bash
git clone https://github.com/herchila/doorman-agent
cd doorman-agent
poetry install --with dev
pre-commit install
```

### Commands

```bash
make test        # Run tests with coverage (minimum 80%)
make lint        # Ruff linter
make format      # Auto-format with ruff
make typecheck   # mypy type checker
make security    # bandit + detect-secrets
make check       # Full CI gate (lint + typecheck + security + tests)
```

### Running against real infrastructure

Use [doorman-chaosmonkey](https://github.com/herchila/doorman-chaosmonkey) to spin up a local Celery + Redis environment with configurable failure scenarios:

```bash
# In doorman-chaosmonkey: start Redis + 4 workers
make up

# In doorman-agent: point to that environment (chaosmonkey uses DB 1)
REDIS_URL=redis://localhost:6379/1 \
CELERY_BROKER_URL=redis://localhost:6379/1 \
  make audit

# or with make targets:
REDIS_URL=redis://localhost:6379/1 CELERY_BROKER_URL=redis://localhost:6379/1 make watch
REDIS_URL=redis://localhost:6379/1 CELERY_BROKER_URL=redis://localhost:6379/1 make audit-deep
```

Available `make` targets for dev:

| Target | Description |
|--------|-------------|
| `make audit` | One-shot TUI report |
| `make audit-json` | JSON output (for CI) |
| `make audit-deep` | Includes Redis/Celery config analysis |
| `make watch` | Live dashboard (refreshes every 5s) |
| `make agent` | Daemon loop in local mode |

### CI/CD

GitHub Actions runs on every push:
- Lint, format check, type check, security scan
- Tests with coverage (fails below 80%)
- Matrix: Python 3.9, 3.10, 3.11, 3.12

Publishing to PyPI is triggered by `v*` tags via Trusted Publishing (OIDC) — no API tokens stored in GitHub Secrets.

---

## Alerts & Notifications

The agent collects metrics. The Doorman API analyzes them and sends alerts.

**Planned integrations:**
- Slack
- PagerDuty
- Email
- Webhooks

---

## License

Apache License 2.0 — See [LICENSE](LICENSE) for details.
