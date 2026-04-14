"""
Finding system for Doorman Agent — structured health observations with severity, evidence, and remediation guidance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from doorman_agent.models import Config, SystemMetrics


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class SystemStatus(str, Enum):
    OK = "OK"
    WARNINGS = "WARNINGS"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


@dataclass
class Finding:
    id: str  # e.g. "QUEUE_BACKLOG_EMAILS", "LATENCY_UNAVAILABLE"
    severity: Severity
    title: str
    impact: str
    evidence: dict[str, Any]
    confidence: float = 1.0
    probable_cause: list[str] = field(default_factory=list)
    confirm_steps: list[str] = field(default_factory=list)
    safe_fix: list[str] = field(default_factory=list)
    docs_links: list[str] = field(default_factory=list)


def compute_system_status(findings: list[Finding]) -> SystemStatus:
    """CRITICAL finding => CRITICAL, HIGH => DEGRADED, MEDIUM/LOW => WARNINGS, none => OK"""
    if not findings:
        return SystemStatus.OK
    max_sev = max(findings, key=lambda f: SEVERITY_RANK[f.severity]).severity
    if max_sev == Severity.CRITICAL:
        return SystemStatus.CRITICAL
    elif max_sev == Severity.HIGH:
        return SystemStatus.DEGRADED
    elif max_sev in (Severity.MEDIUM, Severity.LOW):
        return SystemStatus.WARNINGS
    return SystemStatus.OK


def top_findings(findings: list[Finding], n: int = 3) -> list[Finding]:
    """Return top N findings by severity rank"""
    return sorted(findings, key=lambda f: SEVERITY_RANK[f.severity], reverse=True)[:n]


class FindingsEngine:
    """Analyzes SystemMetrics and produces a list of Finding objects."""

    def analyze(
        self,
        metrics: SystemMetrics,
        config: Config,
        latency_available: bool,
    ) -> list[Finding]:
        """Run all checks and return findings."""
        findings: list[Finding] = []

        # 1. REDIS_DOWN — must check first
        if not metrics.redis_connected:
            findings.append(
                Finding(
                    id="REDIS_DOWN",
                    severity=Severity.CRITICAL,
                    title="Redis is unreachable",
                    impact="No queue metrics available; agent cannot monitor queues.",
                    evidence={"redis_connected": False},
                    probable_cause=[
                        "Redis process is down",
                        "Network/firewall blocking connection",
                    ],
                    confirm_steps=["redis-cli ping", "Check REDIS_URL environment variable"],
                    safe_fix=[
                        "Restart Redis: systemctl restart redis",
                        "Verify REDIS_URL is correct",
                    ],
                )
            )

        # 2. NO_WORKERS — celery not connected or zero workers
        if not metrics.celery_connected or metrics.total_workers == 0:
            findings.append(
                Finding(
                    id="NO_WORKERS",
                    severity=Severity.CRITICAL,
                    title="No Celery workers available",
                    impact="Tasks will queue indefinitely with no processing capacity.",
                    evidence={
                        "celery_connected": metrics.celery_connected,
                        "total_workers": metrics.total_workers,
                    },
                    probable_cause=[
                        "All workers have crashed or been stopped",
                        "Celery broker connection string is wrong",
                    ],
                    confirm_steps=[
                        "celery -A your_app inspect ping",
                        "Check worker logs for startup errors",
                    ],
                    safe_fix=[
                        "Start workers: celery -A your_app worker --loglevel=info",
                        "Check CELERY_BROKER_URL matches running broker",
                    ],
                )
            )

        # 3. WORKER_OFFLINE — individual workers that are not alive
        for worker in metrics.workers:
            if not worker.is_alive:
                findings.append(
                    Finding(
                        id="WORKER_OFFLINE",
                        severity=Severity.CRITICAL,
                        title=f"Worker offline: {worker.name}",
                        impact=f"Capacity reduced by {worker.concurrency} slots.",
                        evidence={"worker": worker.name, "is_alive": False},
                        probable_cause=[
                            "Worker process crashed",
                            "OOM kill or system resource exhaustion",
                        ],
                        confirm_steps=[
                            f"celery -A your_app inspect ping -d {worker.name}",
                            "Check system logs: journalctl -u celery",
                        ],
                        safe_fix=[
                            f"Restart worker: celery -A your_app worker --hostname={worker.name}",
                            "Check for OOM: dmesg | grep -i killed",
                        ],
                    )
                )

        # 4. STUCK_TASK — tasks running longer than threshold
        for stuck in metrics.stuck_tasks:
            task_name = stuck.get("task_name", "unknown")
            runtime = stuck.get("runtime_seconds", 0)
            worker = stuck.get("worker", "unknown")
            findings.append(
                Finding(
                    id="STUCK_TASK",
                    severity=Severity.HIGH,
                    title=f"Stuck task: {task_name}",
                    impact=f"Worker slot blocked for {runtime:.0f}s, reducing throughput.",
                    evidence={
                        "task_name": task_name,
                        "runtime_seconds": runtime,
                        "worker": worker,
                    },
                    probable_cause=[
                        "Task is deadlocked or waiting on an external resource",
                        "Infinite loop or missing timeout",
                    ],
                    confirm_steps=[
                        f"celery -A your_app inspect active -d {worker}",
                        "Check task logs for the task_id",
                    ],
                    safe_fix=[
                        "Add a task timeout: @app.task(time_limit=300)",
                        "Revoke stuck task: celery -A your_app control revoke <task_id> --terminate",
                    ],
                )
            )

        # 5. QUEUE_BACKLOG_{QUEUE} and QUEUE_SLA_BREACH_{QUEUE}
        for queue in metrics.queues:
            queue_id = queue.name.upper().replace("-", "_").replace(".", "_")
            threshold = config.thresholds.max_queue_size
            is_critical_queue = queue.name in config.thresholds.critical_queues

            if queue.depth > threshold:
                severity = Severity.HIGH if is_critical_queue else Severity.MEDIUM
                findings.append(
                    Finding(
                        id=f"QUEUE_BACKLOG_{queue_id}",
                        severity=severity,
                        title=f"Queue '{queue.name}' backlog: {queue.depth} tasks pending",
                        impact="Tasks are accumulating faster than workers can process them.",
                        evidence={
                            "queue": queue.name,
                            "depth": queue.depth,
                            "threshold": threshold,
                            "is_critical": is_critical_queue,
                        },
                        probable_cause=[
                            "Insufficient worker capacity",
                            "Spike in task production",
                            "Worker(s) offline or slow",
                        ],
                        confirm_steps=[
                            f"redis-cli LLEN {queue.name}",
                            "celery -A your_app inspect active",
                        ],
                        safe_fix=[
                            "Scale workers: add more worker processes",
                            "Check worker health and concurrency settings",
                        ],
                    )
                )

            # SLA breach check
            wait_threshold = config.thresholds.max_wait_time_seconds
            if (
                queue.oldest_task_age_seconds is not None
                and queue.oldest_task_age_seconds > wait_threshold
            ):
                findings.append(
                    Finding(
                        id=f"QUEUE_SLA_BREACH_{queue_id}",
                        severity=Severity.HIGH,
                        title=f"SLA breach on queue '{queue.name}'",
                        impact=f"Oldest task has been waiting {queue.oldest_task_age_seconds:.0f}s, exceeding {wait_threshold}s SLA.",
                        evidence={
                            "queue": queue.name,
                            "latency_sec": queue.oldest_task_age_seconds,
                            "threshold": wait_threshold,
                        },
                        probable_cause=[
                            "Workers not consuming from this queue",
                            "Queue routing misconfiguration",
                        ],
                        confirm_steps=[
                            f"redis-cli LINDEX {queue.name} -1 | python3 -m json.tool",
                            "celery -A your_app inspect active_queues",
                        ],
                        safe_fix=[
                            f"Ensure workers are subscribed to queue '{queue.name}'",
                            "Scale workers dedicated to this queue",
                        ],
                    )
                )

        # 6. LATENCY_UNAVAILABLE — no timestamps and queues have depth > 0
        any_queue_has_depth = any(q.depth > 0 for q in metrics.queues)
        if not latency_available and any_queue_has_depth:
            findings.append(
                Finding(
                    id="LATENCY_UNAVAILABLE",
                    severity=Severity.MEDIUM,
                    title="Latency unavailable — install DoormanStampPlugin to enable",
                    impact="SLA breach detection is blind; tasks may wait for minutes without alerting.",
                    evidence={
                        "latency_available": False,
                        "queues_with_depth": [q.name for q in metrics.queues if q.depth > 0],
                    },
                    probable_cause=[
                        "Celery + Redis does not add timestamps to queue messages by default",
                        "task_send_sent_event=True does NOT help — it only emits events to the Celery event stream, not to queue messages",
                    ],
                    confirm_steps=[
                        "Inspect a raw message — look for 'doorman_sent_ts' in headers:",
                        "redis-cli -n 1 LINDEX <queue_name> -1 | python3 -m json.tool | grep doorman_sent_ts",
                    ],
                    safe_fix=[
                        "Add DoormanStampPlugin to your Celery app (one line):\n"
                        "  from doorman_agent.stamps import DoormanStampPlugin\n"
                        "  DoormanStampPlugin.install(app)  # before any task is published",
                        "This uses the before_task_publish signal and adds 'doorman_sent_ts' to every message header.",
                        "Note: only NEW tasks published after install will have timestamps.",
                    ],
                )
            )

        # 7. HIGH_SATURATION — saturation_pct > 80
        if metrics.saturation_pct > 80:
            findings.append(
                Finding(
                    id="HIGH_SATURATION",
                    severity=Severity.MEDIUM,
                    title=f"High worker saturation: {metrics.saturation_pct:.1f}%",
                    impact="Workers are near capacity; new tasks will queue rather than execute immediately.",
                    evidence={
                        "saturation_pct": metrics.saturation_pct,
                        "active": metrics.total_active_tasks,
                        "concurrency": metrics.total_concurrency,
                    },
                    probable_cause=[
                        "Insufficient worker concurrency for current load",
                        "Long-running tasks consuming all slots",
                    ],
                    confirm_steps=[
                        "celery -A your_app inspect active",
                        "Check worker prefetch_multiplier setting",
                    ],
                    safe_fix=[
                        "Add more worker processes or increase concurrency",
                        "Set worker_prefetch_multiplier=1 to prevent hoarding",
                    ],
                )
            )

        return findings
