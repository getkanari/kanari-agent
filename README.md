<p align="center">
  <img src="assets/logo.png" alt="Kanari" width="300" />
  <h3 align="center">Kanari</h3>
</p>

<p align="center">
  Privacy-first monitoring agent for Celery + Redis.
</p>

<p align="center">
  <a href="docs/introduction.mdx"><strong>Documentation</strong></a>
</p>
<br/>

[![PyPI version](https://img.shields.io/pypi/v/kanari.svg)](https://pypi.org/project/kanari/)
[![Python versions](https://img.shields.io/pypi/pyversions/kanari.svg)](https://pypi.org/project/kanari/)
[![CI](https://github.com/getkanari/kanari-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/getkanari/kanari-agent/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

## Problem addressed

A running Celery process does not guarantee that queued work is making progress.

Kanari combines Celery worker state with Redis queue depth, task wait time when timestamps
are available, active-task runtime, and relevant configuration. It reports conditions that
can block or delay background work and includes the evidence used to produce each finding.

Kanari checks:

- Redis and Celery connectivity
- Worker availability, active tasks, concurrency, and pool saturation
- Queue depth and the age of the oldest queued task
- Active tasks that exceed a configured runtime
- Celery acknowledgment and prefetch settings
- Redis memory, eviction, and persistence settings when configuration access is available

### Scope and limitations

- Queue inspection currently supports Celery with Redis as the broker.
- Queue wait time requires a timestamp in the queued message. Kanari can add one with
  `KanariStampPlugin`.
- A one-shot audit has no previous worker baseline. If one worker disappears while others remain,
  the audit may only observe the current worker count.
- Kanari reports execution-layer conditions. It does not determine whether a task result or a
  business operation is correct.
- Configuration checks that require Redis `CONFIG` or Celery `inspect conf` are skipped when the
  connected user does not have permission.

## Requirements

- Python 3.9–3.12 (tested in CI)
- Celery 5.2+
- Redis as the Celery broker

## Quick start

```bash
pip install kanari
kanari init
kanari doctor
kanari audit
```

`kanari init` writes `kanari.yaml`. `doctor`, `audit`, `watch`, and `agent` automatically
load that file from the current directory. The local commands do not require an account and do
not contact the Kanari API.

### Example output

The following is an abridged audit with `payments` configured as a critical queue:

```text
$ kanari audit

🔍 Kanari Audit
════════════════════════════════════════════════════════════

❌ System: CRITICAL

Infrastructure
  ✅ Redis: connected
  ✅ Celery: connected (4 workers)

Workers
  Status   Worker        Slots   Note
  ✅       api-worker    2/8     online
  ❌       payment-wrk   0/4     offline

Queues
  Status   Queue       Pending   Latency
  🔥       payments       1847     125.4s

Findings
  [CRITICAL]  WORKER_OFFLINE          Worker offline: payment-wrk
  [HIGH]      QUEUE_SLA_BREACH_PAYMENTS
  [HIGH]      QUEUE_BACKLOG_PAYMENTS

❌ Critical issues found
```

The report relates findings to the collected worker and queue metrics. The JSON format includes
an evidence summary for each top finding. Configuration warnings include candidate remediation
steps.

## Local commands

| Command | Purpose |
|---------|---------|
| [`kanari init`](docs/commands/init.mdx) | Generate a `kanari.yaml` file and probe Redis |
| [`kanari doctor`](docs/commands/doctor.mdx) | Check libraries, configuration, Redis, and Celery connectivity |
| [`kanari audit`](docs/commands/audit.mdx) | Run a one-shot assessment and exit with a status code |
| [`kanari watch`](docs/commands/watch.mdx) | Re-run the assessment on a refresh interval |
| [`kanari agent --local`](docs/commands/agent.mdx) | Collect continuously and write structured records to stdout |

Common audit formats:

```bash
kanari audit                  # terminal report
kanari audit --json           # machine-readable summary
kanari audit --md             # Markdown summary
kanari audit --no-config-checks
kanari audit --config /etc/kanari/prod.yaml
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | No findings or configuration warnings |
| `1` | Warning-level findings or configuration warnings |
| `2` | Degraded or critical state, including `HIGH` and `CRITICAL` findings |

## Queue latency

Celery messages in a Redis queue do not normally include a reliable publication timestamp.
Without one, Kanari reports queue depth but cannot determine how long the oldest task has waited.
Worker, active-task, saturation, and configuration checks continue to run.

Install the timestamp hook in the process that publishes tasks:

```python
from celery import Celery
from kanari_agent.stamps import KanariStampPlugin

app = Celery(...)
KanariStampPlugin.install(app)
```

The hook adds a `kanari_sent_ts` header before each task is published. Only tasks published after
the hook is installed have this timestamp.

See [Queue latency tracking](docs/guides/latency-tracking.mdx) for details.

## Configuration

Kanari reads runtime configuration from two locations:

| File | Purpose | Security note |
|------|---------|---------------|
| `kanari.yaml` | Redis/Celery URLs, queues, and local finding thresholds | May contain credentials in connection URLs; commit only a secret-free version |
| `~/.kanari/config` | API key and API URL written by `kanari login` | Contains secrets; do not commit |

A minimal local configuration:

```yaml
redis_url: redis://prod-redis:6379/1
celery_broker_url: redis://prod-redis:6379/1

# Empty means: discover queues through Celery inspect active_queues.
monitored_queues: []

# These thresholds control local findings.
thresholds:
  max_queue_size: 1000
  max_wait_time_seconds: 60
  max_task_runtime_seconds: 1800
  critical_queues:
    - payments
```

`REDIS_URL` and `CELERY_BROKER_URL` override values from `kanari.yaml`. See the
[configuration reference](docs/reference/configuration.mdx) for the full precedence and field list.

## Findings

A finding records severity, observed evidence, probable causes, confirmation steps, and candidate
remediation. Kanari does not execute remediation automatically.

| Finding | Default severity | Trigger |
|---------|------------------|---------|
| `REDIS_DOWN` | CRITICAL | Redis is unavailable during an established monitoring session |
| `NO_WORKERS` | CRITICAL | Celery is unavailable or no workers respond |
| `WORKER_OFFLINE` | CRITICAL | A worker appears in inspect data but does not return worker stats |
| `STUCK_TASK` | HIGH | An active task exceeds `max_task_runtime_seconds` |
| `QUEUE_BACKLOG_*` | HIGH/MEDIUM | Queue depth exceeds `max_queue_size`; critical queues use HIGH |
| `QUEUE_SLA_BREACH_*` | HIGH | Oldest queued task exceeds `max_wait_time_seconds` |
| `LATENCY_UNAVAILABLE` | MEDIUM | A non-empty queue has no usable timestamp |
| `HIGH_SATURATION` | MEDIUM | Active tasks exceed 80% of reported concurrency |

See the [findings reference](docs/reference/findings.mdx) for evidence fields and confirmation steps.

## Configuration analysis

`kanari audit` attempts these checks on every run:

| Check | Condition reported |
|-------|--------------------|
| Redis `maxmemory` | No limit is configured, or observed usage exceeds 80% of the configured limit |
| Redis eviction policy | `noeviction` will reject writes after the memory limit is reached |
| Redis persistence | The Redis `save` setting has no RDB snapshot schedule |
| `task_acks_late` | Early acknowledgment can lose an in-progress task if its worker exits |
| `task_reject_on_worker_lost` | Acknowledgment behavior can lose a task when a worker child exits unexpectedly |
| `worker_prefetch_multiplier` | Values above 1 can cause uneven distribution for long-running tasks |
| Worker redundancy | Only one worker is currently responding |

These checks describe risk; they do not prove that data loss or an outage has occurred. If the
connected Redis or Celery user cannot read configuration, unavailable checks are omitted rather
than reported as passing.

## Data and privacy model

Local commands and API mode handle identifiers differently:

| Field | Local collection and logs | API representation |
|-------|---------------------------|--------------------|
| Queue name | Raw queue name | Emails and UUIDs replaced with placeholders |
| Queue depth and oldest-task age | Numeric values | Numeric values |
| Worker name | Raw Celery worker name | Hashed worker ID plus a short display name with the domain removed |
| Active task ID | Present only for a detected stuck task | SHA-256-derived ID with a `t-` prefix |
| Active task name | Raw name for a detected stuck task | Emails, UUIDs, and common numeric IDs replaced with placeholders |
| Task arguments and results | Not extracted into Kanari metrics | Not serialized or transmitted |

Important details:

- `audit` and `watch` process their results locally and do not contact the Kanari API.
- `agent --local` writes collected records to stdout. Those records can contain raw queue names,
  worker names, task names, and task IDs, so the logs should be treated as operational data.
- API sanitization is pattern-based. Avoid putting sensitive data in task, queue, or worker names;
  sanitization cannot recognize every possible identifier.
- `kanari audit --json` is an aggregate audit summary, not a preview of the API payload.

The payload construction is implemented in
[`src/kanari_agent/api_client.py`](src/kanari_agent/api_client.py). See
[Privacy and data](docs/reference/privacy.mdx) for the field-level reference.

## CI/CD

`kanari audit` can be used as a CI check. Capture the exit code explicitly if the job needs to
publish the JSON report before failing:

```bash
set +e
kanari audit --json > kanari-audit.json
status=$?
set -e

cat kanari-audit.json

case "$status" in
  0) exit 0 ;;
  1) echo "Kanari reported warnings" >&2; exit 1 ;;
  2) echo "Kanari reported a degraded or critical state" >&2; exit 2 ;;
  *) echo "Kanari failed with exit code $status" >&2; exit "$status" ;;
esac
```

See the [CI/CD guide](docs/guides/cicd.mdx) for integration examples.

## Alerting

Local mode does not send notifications. Optional Slack and email notifications use API mode; see
[alert configuration](docs/commands/alerts.mdx).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
