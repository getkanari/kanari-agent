# Doorman Assistant

Doorman is a monitoring agent for Celery + Redis queues. It detects ghost workers, queue backlogs, stuck tasks, SLA breaches, and configuration risks.

## Key concepts

- **doorman audit** — one-shot health check, exits with code 0/1/2
- **doorman watch** — live dashboard, refreshes every N seconds  
- **doorman agent** — continuous daemon, local mode or sends to doorman.com
- **FindingsEngine** — produces structured findings with severity, cause, and fix
- **DoormanStampPlugin** — adds timestamps to Celery tasks to enable latency tracking

## Common questions

**How do I install Doorman?**
`pip install doorman-agent`

**How do I run a health check?**
`doorman audit` — no config file or API key needed.

**Why does latency show as "unknown"?**
Celery + Redis doesn't timestamp tasks by default. Install `DoormanStampPlugin` in your Celery app to enable latency tracking. See the Latency Tracking guide.

**What does WORKER_OFFLINE mean?**
A worker that was registered stopped responding to pings. Check for crashes, OOM kills, or network issues.

**What does QUEUE_SLA_BREACH mean?**
The oldest task in a queue has been waiting longer than `max_wait_time_seconds`. Workers may not be consuming that queue.

**How do I use Doorman in CI/CD?**
Use `doorman audit --json` — exits with code 2 if critical issues are found.

**Is my task data safe?**
Task arguments are never accessed. Worker names and task IDs are hashed. Task signatures are sanitized to remove emails, UUIDs, and numeric IDs.
