# Doorman Agent

Monitoring agent for Celery + Redis queues. Detects stuck tasks, dead workers, queue backlogs, and SLA breaches. Privacy-first: task arguments are never collected, worker names and task IDs are hashed before leaving your infrastructure.

```bash
pip install doorman-agent
doorman audit
```

---

## Requirements

- Python 3.9+
- Redis
- Celery 5.2+

---

## Quick Start (production)

**1. Install**

```bash
pip install doorman-agent
```

**2. Point to your infrastructure**

```bash
export REDIS_URL=redis://your-redis:6379/0
export CELERY_BROKER_URL=redis://your-redis:6379/0
```

**3. Run a health check**

```bash
doorman audit
```

That's it. No config file needed, no API key, no external dependencies.

### Optional: enable latency tracking

By default Celery + Redis doesn't timestamp queued tasks, so Doorman can't measure how long tasks wait. Add one line to your Celery app to fix this:

```python
from doorman_agent.stamps import DoormanStampPlugin

app = Celery(...)
DoormanStampPlugin.install(app)  # adds doorman_sent_ts header to every task
```

---

## Commands

### `doorman audit`

One-shot health check. Prints a report and exits with a code (`0` = healthy, `1` = warnings, `2` = critical).

```bash
doorman audit                # TUI report
doorman audit --json         # machine-readable JSON (for CI/scripts)
doorman audit --deep         # includes Redis/Celery configuration analysis
```

Sample output:

```
Infrastructure
  Redis: connected
  Celery: connected (4 workers)

Workers
  worker-1: online (2/4 slots)
  worker-2: online (3/4 slots)
  worker-3: online (4/4 slots) — at capacity
  worker-4: offline

Queues
  celery: 12 pending, 2.1s latency
  notifications: empty
  emails: 847 pending, 125s latency — CONGESTED

Findings
  [CRITICAL]  WORKER_OFFLINE                 Worker offline: worker-4
  [HIGH]      QUEUE_BACKLOG_EMAILS           Queue 'emails' backlog: 847 tasks pending
  [MEDIUM]    HIGH_SATURATION                High worker saturation: 68.7%
```

### `doorman watch`

Live dashboard that refreshes periodically.

```bash
doorman watch                # refreshes every 5s
doorman watch --interval 10  # refreshes every 10s
```

### `doorman agent`

Continuous monitoring loop. In local mode it logs metrics; in API mode it sends them to doorman.com.

```bash
doorman agent --local                # log only, no API calls
doorman agent --token your-api-key   # sends metrics to doorman.com
```

---

## Configuration

Create a `config.yaml` file:

```yaml
# API Connection
api_key: null  # Use DOORMAN_API_KEY env var instead
api_url: "https://api.doorman.com"

# Local mode (no API calls, just logging)
local_mode: false

# Redis/Celery
redis_url: "redis://localhost:6379/0"
celery_broker_url: "redis://localhost:6379/0"

# Behavior
check_interval_seconds: 30

# Queues to monitor
monitored_queues:
  - celery
  - default
  - emails
```

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker URL | same as REDIS_URL default |
| `DOORMAN_API_KEY` | API key (for `doorman agent` API mode) | — |
| `DOORMAN_LOCAL_MODE` | `true` to disable API calls | `false` |
| `CHECK_INTERVAL` | Seconds between checks | `30` |

### Config file (optional)

```yaml
redis_url: redis://prod-redis:6379/1
celery_broker_url: redis://prod-redis:6379/1
check_interval_seconds: 15

# Empty = auto-discover queues from workers
monitored_queues: []

thresholds:
  max_queue_size: 1000
  max_wait_time_seconds: 60
  max_task_runtime_seconds: 1800

privacy:
  sanitize_task_signatures: true
```

```bash
doorman audit --config config.yaml
```

---

## Findings

Doorman doesn't just show metrics — it tells you what's wrong and how to fix it.

| Finding | Severity | What it means |
|---------|----------|---------------|
| `REDIS_DOWN` | CRITICAL | Cannot connect to Redis |
| `NO_WORKERS` | CRITICAL | No Celery workers responding |
| `WORKER_OFFLINE` | CRITICAL | A specific worker is not responding |
| `STUCK_TASK` | HIGH | A task has been running longer than the threshold |
| `QUEUE_BACKLOG_*` | HIGH/MEDIUM | Queue depth exceeds threshold |
| `QUEUE_SLA_BREACH_*` | HIGH | Oldest task waiting longer than SLA |
| `LATENCY_UNAVAILABLE` | MEDIUM | No timestamps in queue — install `DoormanStampPlugin` |
| `HIGH_SATURATION` | MEDIUM | Workers near capacity (>80%) |

Each finding includes probable cause, confirmation steps, and a safe fix.

---

## Privacy

The agent never accesses task arguments, results, or payloads. Metadata that could contain PII is sanitized before it leaves your infrastructure:

| Data | Example | What Doorman sees |
|------|---------|-------------------|
| Worker hostname | `celery@prod-worker-1.internal` | `w-a1b2c3d4` |
| Task ID | `550e8400-e29b-41d4-a716-446655440000` | `t-8f3a2b1c4d5e` |
| Task name | `process_user_98765` | `process_user_[id]` |
| Queue name | `emails-john@acme.com` | `emails-[email]` |

Run in local mode to verify exactly what gets collected:

```bash
doorman audit --json | python3 -m json.tool
```

---

## Deep audit

`doorman audit --deep` checks Redis and Celery configuration for common production issues:

| Check | Risk if misconfigured |
|-------|----------------------|
| `maxmemory` not set | Redis OOM kill |
| `maxmemory-policy = noeviction` | Writes fail when full |
| Persistence disabled | Data loss on restart |
| `task_acks_late = False` | Task loss if worker dies mid-execution |
| `task_reject_on_worker_lost = False` | Silent task loss |
| `prefetch_multiplier > 1` | Uneven task distribution |
| Single worker | Single point of failure |

---

## Exit codes

For CI/CD integration:

| Code | Meaning |
|------|---------|
| `0` | Healthy |
| `1` | Warnings |
| `2` | Critical |

```bash
doorman audit --json
if [ $? -eq 2 ]; then echo "Critical issues found"; fi
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

Apache License 2.0 — See [LICENSE](LICENSE).
