"""
Configuration checks for Redis and Celery.

Detects config smells (missing maxmemory, noeviction, task_acks_late=False,
high prefetch, single-worker SPOF) that put queues at risk. Used by
`kanari audit` on every run and by the agent's startup audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kanari_agent.models import SystemMetrics

# Workers were already confirmed reachable by the collector (1s inspect timeout)
# by the time config checks run, so a matching short timeout is enough here.
CONFIG_INSPECT_TIMEOUT = 1.0


@dataclass
class ConfigCheck:
    """Result of a configuration check"""

    name: str
    status: str  # "ok", "warning", "critical"
    message: str
    recommendation: str | None = None


def _run_config_checks(collector: Any, metrics: SystemMetrics) -> list[ConfigCheck]:
    """Run configuration checks on Redis and Celery"""
    checks: list[ConfigCheck] = []

    # Redis checks
    if collector.redis_client:
        checks.extend(_check_redis_config(collector.redis_client))

    # Celery checks
    if collector.celery_app:
        checks.extend(_check_celery_config(collector.celery_app, metrics))

    # Infrastructure checks
    checks.extend(_check_infrastructure(metrics))

    return checks


def _check_redis_config(redis_client: Any) -> list[ConfigCheck]:
    """Check Redis configuration"""
    checks = []

    try:
        # Check maxmemory
        maxmemory = redis_client.config_get("maxmemory").get("maxmemory", "0")
        if maxmemory == "0" or maxmemory == 0:
            checks.append(
                ConfigCheck(
                    name="Redis maxmemory",
                    status="warning",
                    message="Not set (risk of OOM)",
                    recommendation="CONFIG SET maxmemory 2gb",
                )
            )
        else:
            # Check memory usage
            info = redis_client.info("memory")
            used = info.get("used_memory", 0)
            max_mem = int(maxmemory)
            if max_mem > 0:
                usage_pct = (used / max_mem) * 100
                if usage_pct > 80:
                    checks.append(
                        ConfigCheck(
                            name="Redis memory",
                            status="warning",
                            message=f"{usage_pct:.1f}% used (near capacity)",
                            recommendation="Consider increasing maxmemory or scaling",
                        )
                    )
                else:
                    checks.append(
                        ConfigCheck(
                            name="Redis memory",
                            status="ok",
                            message=f"{usage_pct:.1f}% used",
                        )
                    )
    except Exception:  # nosec B110 — Redis CONFIG GET may be disabled; skip gracefully
        pass  # Skip if no permission

    try:
        # Check maxmemory-policy
        policy = redis_client.config_get("maxmemory-policy").get("maxmemory-policy", "noeviction")
        if policy == "noeviction":
            checks.append(
                ConfigCheck(
                    name="Redis eviction policy",
                    status="warning",
                    message="noeviction (writes fail when full)",
                    recommendation="CONFIG SET maxmemory-policy volatile-lru",
                )
            )
        else:
            checks.append(
                ConfigCheck(
                    name="Redis eviction policy",
                    status="ok",
                    message=policy,
                )
            )
    except Exception:  # nosec B110 — CONFIG GET may be restricted; skip gracefully
        pass

    try:
        # Check persistence
        save_config = redis_client.config_get("save").get("save", "")
        if not save_config:
            checks.append(
                ConfigCheck(
                    name="Redis persistence",
                    status="warning",
                    message="Disabled (data loss on restart)",
                    recommendation="Consider enabling RDB or AOF persistence",
                )
            )
        else:
            checks.append(
                ConfigCheck(
                    name="Redis persistence",
                    status="ok",
                    message="Enabled",
                )
            )
    except Exception:  # nosec B110 — CONFIG GET may be restricted; skip gracefully
        pass

    try:
        # Check connection pool / max clients
        info = redis_client.info("clients")
        connected = info.get("connected_clients", 0)
        max_clients_raw = redis_client.config_get("maxclients").get("maxclients", "10000")
        max_clients = int(max_clients_raw)

        if max_clients > 0:
            client_usage_pct = (connected / max_clients) * 100
            if client_usage_pct > 80:
                checks.append(
                    ConfigCheck(
                        name="Redis connection pool",
                        status="warning",
                        message=f"{connected}/{max_clients} connections ({client_usage_pct:.1f}%)",
                        recommendation="Increase maxclients or review connection pooling",
                    )
                )
            else:
                checks.append(
                    ConfigCheck(
                        name="Redis connection pool",
                        status="ok",
                        message=f"{connected}/{max_clients} connections",
                    )
                )
    except Exception:  # nosec B110 — CONFIG GET may be restricted; skip gracefully
        pass

    return checks


def _check_celery_config(celery_app: Any, metrics: SystemMetrics) -> list[ConfigCheck]:
    """Check Celery configuration"""
    checks = []

    try:
        inspector = celery_app.control.inspect(timeout=CONFIG_INSPECT_TIMEOUT)
        conf = inspector.conf() or {}

        if conf:
            # Get first worker's config (they should all be the same)
            worker_conf: dict = next(iter(conf.values()), {})

            # Check task_acks_late
            acks_late = worker_conf.get("task_acks_late", False)
            if not acks_late:
                checks.append(
                    ConfigCheck(
                        name="Celery task_acks_late",
                        status="warning",
                        message="False (task loss if worker dies)",
                        recommendation="Set task_acks_late=True in Celery config",
                    )
                )
            else:
                checks.append(
                    ConfigCheck(
                        name="Celery task_acks_late",
                        status="ok",
                        message="True (safe)",
                    )
                )

            # Check task_reject_on_worker_lost
            reject_on_lost = worker_conf.get("task_reject_on_worker_lost", False)
            if not reject_on_lost:
                checks.append(
                    ConfigCheck(
                        name="Celery task_reject_on_worker_lost",
                        status="warning",
                        message="False (silent task loss possible)",
                        recommendation="Set task_reject_on_worker_lost=True",
                    )
                )
            else:
                checks.append(
                    ConfigCheck(
                        name="Celery task_reject_on_worker_lost",
                        status="ok",
                        message="True (safe)",
                    )
                )

            # Check prefetch_multiplier
            prefetch = worker_conf.get("worker_prefetch_multiplier", 4)
            if prefetch > 1:
                checks.append(
                    ConfigCheck(
                        name="Celery prefetch_multiplier",
                        status="warning",
                        message=f"{prefetch} (may cause uneven distribution)",
                        recommendation="Set worker_prefetch_multiplier=1 for long tasks",
                    )
                )
            else:
                checks.append(
                    ConfigCheck(
                        name="Celery prefetch_multiplier",
                        status="ok",
                        message=f"{prefetch} (optimized)",
                    )
                )

    except Exception:  # nosec B110 — Celery inspect may be unavailable; skip gracefully
        pass

    return checks


def _check_infrastructure(metrics: SystemMetrics) -> list[ConfigCheck]:
    """Check infrastructure-level concerns"""
    checks = []

    # Check for single point of failure
    if metrics.alive_workers == 1:
        checks.append(
            ConfigCheck(
                name="Worker redundancy",
                status="warning",
                message="Only 1 worker (single point of failure)",
                recommendation="Add redundant workers for high availability",
            )
        )
    elif metrics.alive_workers > 1:
        checks.append(
            ConfigCheck(
                name="Worker redundancy",
                status="ok",
                message=f"{metrics.alive_workers} workers (redundant)",
            )
        )

    # Check total concurrency vs queue depth
    if metrics.total_concurrency > 0 and metrics.total_pending_tasks > 0:
        tasks_per_slot = metrics.total_pending_tasks / metrics.total_concurrency
        if tasks_per_slot > 100:
            checks.append(
                ConfigCheck(
                    name="Queue backlog ratio",
                    status="warning",
                    message=f"{tasks_per_slot:.0f} pending tasks per slot",
                    recommendation="Consider scaling workers to reduce backlog",
                )
            )

    return checks
