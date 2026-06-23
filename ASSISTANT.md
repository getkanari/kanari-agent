# Kanari Assistant

Kanari is a monitoring agent for Celery + Redis queues. It detects ghost workers, queue backlogs, stuck tasks, SLA breaches, and configuration risks.

## Key concepts

- **kanari audit** — one-shot health check, exits with code 0/1/2
- **kanari watch** — live dashboard, refreshes every N seconds
- **kanari agent** — continuous daemon, local mode or sends to api.getkanari.com
- **FindingsEngine** — produces structured findings with severity, cause, and fix
- **KanariStampPlugin** — adds timestamps to Celery tasks to enable latency tracking

## Common questions

**How do I install Kanari?**
`pip install kanari-agent`

**How do I run a health check?**
`kanari audit` — no config file or API key needed.

**Why does latency show as "unknown"?**
Celery + Redis doesn't timestamp tasks by default. Install `KanariStampPlugin` in your Celery app to enable latency tracking. See the Latency Tracking guide.

**What does WORKER_OFFLINE mean?**
A worker that was registered stopped responding to pings. Check for crashes, OOM kills, or network issues.

**What does QUEUE_SLA_BREACH mean?**
The oldest task in a queue has been waiting longer than `max_wait_time_seconds`. Workers may not be consuming that queue.

**How do I use Kanari in CI/CD?**
Use `kanari audit --json` — exits with code 2 if critical issues are found.

**Is my task data safe?**
Task arguments are never accessed. Worker names and task IDs are hashed. Task signatures are sanitized to remove emails, UUIDs, and numeric IDs.
