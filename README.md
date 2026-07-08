# Kanari Agent

[![PyPI version](https://img.shields.io/pypi/v/kanari-agent.svg)](https://pypi.org/project/kanari-agent/)
[![Python versions](https://img.shields.io/pypi/pyversions/kanari-agent.svg)](https://pypi.org/project/kanari-agent/)
[![Tests](https://github.com/getkanari/kanari-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/getkanari/kanari-agent/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Monitoring agent for Celery + Redis queues. One command to know if your workers are healthy, your queues are draining, and your tasks aren't stuck.

```bash
pip install kanari-agent
kanari audit
```

No account needed. No external service. Point it at your Redis and get a full health report in seconds.

---

## Why Kanari?

**Your workers show as running. Your queue keeps growing. Nobody knows why.**

This is the most common Celery production problem — and the hardest to debug with generic monitoring tools. Datadog and Grafana can tell you CPU and memory, but they don't understand Celery's queue model, worker pool, or task acknowledgment semantics.

Kanari is built specifically for Celery shops. It knows:

- **Ghost workers** — workers that are alive but stopped consuming after a Redis reconnect
- **Queue latency** — how long the oldest task has been waiting (not just queue depth)
- **Stuck tasks** — tasks running beyond your threshold that are blocking worker slots
- **Silent task loss** — configurations (`task_acks_late=False`) that drop tasks on worker crash
- **Capacity headroom** — how close you are to saturation before new tasks start queuing

```
🔍 Kanari Audit
════════════════════════════════════════════════════════════

✅ System: HEALTHY

Infrastructure
  ✅ Redis: connected
  ✅ Celery: connected (4 workers)

Workers
  Status   Worker       Slots   Note
  ✅       api-worker   2/8     online
  ✅       email-wrk    1/4     online
  ⚠️       job-worker   4/4     at capacity
  ❌       beat-wrk     0/4     offline

Queues
  Status   Queue          Pending   Latency    Trend
  ✅       celery         3         1.2s
  ✅       default        0         0s         →
  🔥       emails         847       125.4s     ↑+203
  ✅       notifications  12        4.8s

Metrics
  📊 Saturation: 43.8% (7/16 slots, headroom: 9 slots)
  ⏱️  Max Latency: 125.4s (emails)
  📋 Total Pending: 862 tasks

════════════════════════════════════════════════════════════
Findings
  [CRITICAL]   WORKER_OFFLINE           Worker offline: beat-wrk
  [HIGH]       QUEUE_SLA_BREACH_EMAILS  SLA breach on queue 'emails'
  [HIGH]       QUEUE_BACKLOG_EMAILS     Queue 'emails' backlog: 847 tasks pending

💡 Recommendations:
  • Restart worker: celery -A your_app worker --hostname=beat-wrk
  • Scale workers for 'emails' queue (847 pending, 125.4s latency)

❌ Critical issues found
Audit completed in 1.3s
```

---

## Requirements

- Python 3.9+
- Redis
- Celery 5.2+

---

## Quick Start

**1. Install**

```bash
pip install kanari-agent
```

**2. Generate a config file**

```bash
kanari init
```

Creates `config.yaml` with sensible defaults. If `REDIS_URL` or `CELERY_BROKER_URL` are set in your environment, they're picked up automatically. The command also pings Redis and tells you whether it's reachable.

**3. Verify your setup**

```bash
kanari doctor --config config.yaml
```

Checks that Redis is reachable, Celery workers are responding, and all required libraries are installed. Tells you exactly what to fix if something is wrong.

**4. Run a health check**

```bash
kanari audit --config config.yaml
```

That's it. No account, no API key, no external dependencies beyond Redis and Celery.

> **Tip:** `kanari audit` also works without a config file — it connects to `redis://localhost:6379/0` by default.

---

## Commands

### `kanari init`

Generate a starter `config.yaml` with sensible defaults. Reads `REDIS_URL` and `CELERY_BROKER_URL` from the environment if set, and probes Redis to confirm connectivity.

```bash
kanari init                        # creates config.yaml in the current directory
kanari init --output /etc/kanari/config.yaml  # custom path
kanari init --force                # overwrite existing file
```

### `kanari doctor`

Diagnose your setup before running anything else. Checks Python version, required libraries, Redis connectivity, Celery workers, and API key format.

```bash
kanari doctor                         # check default setup
kanari doctor --config config.yaml    # also validate a config file
```

Returns exit code `0` if everything passes (or only warnings), `1` if any check fails.

### `kanari audit`

One-shot health check. Prints a report and exits with a status code.

```bash
kanari audit                        # rich TUI report + Redis/Celery config analysis
kanari audit --json                 # machine-readable JSON (for CI/scripts)
kanari audit --md                   # Markdown report
kanari audit --no-config-checks    # skip config analysis (e.g. restricted Redis)
kanari audit --config config.yaml   # use config file
```

Configuration analysis (acks_late, eviction policy, prefetch, and more) runs on every audit. On a healthy system the report shows a `✓ N checks passed` summary of everything verified. The JSON output includes a `checks_performed` array for CI assertions.

**Exit codes** — integrate directly into CI/CD:

| Code | Meaning |
|------|---------|
| `0` | Healthy |
| `1` | Warnings |
| `2` | Critical |

```bash
kanari audit --json
if [ $? -eq 2 ]; then
  echo "Critical Celery issues found — paging on-call"
  exit 1
fi
```

### `kanari watch`

Live dashboard that clears and refreshes periodically. Useful when debugging an incident.

```bash
kanari watch                # refreshes every 5s
kanari watch --interval 10  # refreshes every 10s
kanari watch --deep         # includes config analysis on each refresh
```

### `kanari agent`

Continuous monitoring loop. Runs until stopped. In local mode it logs structured JSON; in API mode it sends metrics to api.getkanari.com.

```bash
kanari agent --local                        # log only, no API calls
kanari agent --config config.yaml --local   # with config file
kanari agent --token your-api-key           # sends metrics to api.getkanari.com
```

---

## Enable Latency Tracking

By default, Celery + Redis doesn't timestamp tasks when they're queued. Without timestamps, Kanari can't measure how long tasks wait — it can only see queue depth.

Add one line to your Celery app to unlock accurate latency:

```python
from celery import Celery
from kanari_agent.stamps import KanariStampPlugin

app = Celery(...)
KanariStampPlugin.install(app)  # adds kanari_sent_ts header to every task
```

After this, `kanari audit` shows real wait times per queue and triggers `QUEUE_SLA_BREACH` findings when tasks wait longer than your configured threshold.

> **Note:** Only tasks published _after_ installing the plugin will have timestamps. Tasks already in the queue will show `latency: unknown` until they're consumed and new ones are enqueued.

---

## Configuration

All settings can be set via environment variables. A config file is optional.

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker URL | same as `REDIS_URL` |
| `KANARI_API_KEY` | API key for `kanari agent` API mode | — |
| `KANARI_LOCAL_MODE` | `true` to disable API calls | `false` |
| `CHECK_INTERVAL` | Seconds between checks (agent mode) | `30` |

### Config file (optional)

```yaml
# redis and celery connections
redis_url: redis://prod-redis:6379/1
celery_broker_url: redis://prod-redis:6379/1

# check interval in agent mode
check_interval_seconds: 15

# leave empty to auto-discover queues from workers
monitored_queues: []

# alert thresholds
thresholds:
  max_queue_size: 1000          # tasks — triggers QUEUE_BACKLOG finding
  max_wait_time_seconds: 60     # seconds — triggers QUEUE_SLA_BREACH finding
  max_task_runtime_seconds: 1800  # 30 min — triggers STUCK_TASK finding
  critical_queues:              # these get HIGH severity (vs MEDIUM) on backlog
    - emails
    - payments

# privacy: set false only if task names contain no PII
privacy:
  sanitize_task_signatures: true
```

```bash
kanari audit --config config.yaml
```

---

## Findings

Kanari doesn't just show metrics — it tells you what's wrong and how to fix it. Each finding includes the probable cause, commands to confirm it, and a safe fix.

| Finding | Severity | What it means |
|---------|----------|---------------|
| `REDIS_DOWN` | CRITICAL | Cannot connect to Redis — no queue metrics available |
| `NO_WORKERS` | CRITICAL | No Celery workers responding — tasks queue indefinitely |
| `WORKER_OFFLINE` | CRITICAL | A specific worker stopped responding |
| `STUCK_TASK` | HIGH | A task has been running longer than `max_task_runtime_seconds` |
| `QUEUE_BACKLOG_*` | HIGH/MEDIUM | Queue depth exceeds `max_queue_size` |
| `QUEUE_SLA_BREACH_*` | HIGH | Oldest task waiting longer than `max_wait_time_seconds` |
| `LATENCY_UNAVAILABLE` | MEDIUM | No timestamps in queue — install `KanariStampPlugin` |
| `HIGH_SATURATION` | MEDIUM | Worker pool above 80% utilization |

---

## Configuration Analysis

Every `kanari audit` inspects your Redis and Celery configuration for common production misconfigurations (`--deep` is no longer needed and is kept only as a deprecated no-op):

| Check | Risk if wrong |
|-------|---------------|
| Redis `maxmemory` not set | OOM kill wipes your queue |
| Redis eviction policy `noeviction` | Writes fail silently when Redis is full |
| Redis persistence disabled | Tasks lost on Redis restart |
| `task_acks_late = False` | Tasks lost if worker crashes mid-execution |
| `task_reject_on_worker_lost = False` | Silent task loss on sudden worker death |
| `worker_prefetch_multiplier > 1` | Uneven task distribution, fast tasks stuck behind slow ones |
| Single worker running | Single point of failure — one crash = full outage |

---

## Privacy

The agent never accesses task arguments, results, or payloads. All metadata that could contain PII is sanitized before it leaves your infrastructure:

| Data | Original | What Kanari sees |
|------|----------|-------------------|
| Worker hostname | `celery@prod-worker-1.internal` | `w-a1b2c3d4` |
| Task ID | `550e8400-e29b-41d4-a716-446655440000` | `t-8f3a2b1c4d5e` |
| Task name | `process_user_98765` | `process_user_[id]` |
| Task name | `send_to_john@acme.com` | `send_to_[email]` |
| Queue name | `emails-jane@acme.com` | `emails-[email]` |
| Task arguments | `{"user_id": 123, "token": "sk_..."}` | _never accessed_ |

To inspect exactly what the agent collects in your environment:

```bash
kanari audit --json | python3 -m json.tool
```

---

## CI/CD Integration

```yaml
# .github/workflows/health-check.yml
- name: Celery health check
  env:
    REDIS_URL: ${{ secrets.REDIS_URL }}
    CELERY_BROKER_URL: ${{ secrets.CELERY_BROKER_URL }}
  run: |
    pip install kanari-agent
    kanari audit --json
```

Or in a shell script:

```bash
#!/bin/bash
kanari audit --json
STATUS=$?
if [ $STATUS -eq 2 ]; then
  echo "CRITICAL: Celery issues detected"
  # trigger PagerDuty, Slack, etc.
  exit 1
fi
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests welcome.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

Apache License 2.0 — See [LICENSE](LICENSE).
