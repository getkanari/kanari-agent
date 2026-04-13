"""
Audit report generator for Doorman Agent

Uses rich library for beautiful terminal output.
"""

from __future__ import annotations

import json as json_module
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from doorman_agent.models import Config, SystemMetrics

# Exit codes
EXIT_HEALTHY = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2


@dataclass
class QueueTrend:
    """Trend data for a queue"""

    name: str
    depth_start: int
    depth_end: int
    depth_delta: int
    trend: str  # "growing", "shrinking", "stable"


@dataclass
class ConfigCheck:
    """Result of a configuration check"""

    name: str
    status: str  # "ok", "warning", "critical"
    message: str
    recommendation: str | None = None


@dataclass
class AuditResult:
    """Result of an audit check"""

    exit_code: int = EXIT_HEALTHY
    warnings: list[str] = field(default_factory=list)
    criticals: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    queue_trends: list[QueueTrend] = field(default_factory=list)
    config_checks: list[ConfigCheck] = field(default_factory=list)


def _status_to_exit_code(status: Any) -> int:
    """Map SystemStatus to exit code."""
    from doorman_agent.findings import SystemStatus

    mapping = {
        SystemStatus.OK: EXIT_HEALTHY,
        SystemStatus.WARNINGS: EXIT_WARNING,
        SystemStatus.DEGRADED: EXIT_CRITICAL,
        SystemStatus.CRITICAL: EXIT_CRITICAL,
    }
    return mapping.get(status, EXIT_CRITICAL)


def run_audit(
    config: Config,
    json_output: bool = False,
    md_output: bool = False,
    no_color: bool = False,
    deep: bool = False,
    timeout: float = 3.0,
    # Legacy positional/keyword args kept for backward compatibility with old tests
    samples: int = 1,
    interval: int = 10,
) -> int:
    """
    Run an audit check and print a formatted report.

    Args:
        config: Doorman configuration
        json_output: Print machine-readable JSON to stdout
        md_output: Print Markdown report
        no_color: Disable ANSI colors
        deep: Run deep configuration checks
        timeout: Maximum runtime in seconds
        samples: (legacy) Number of samples to collect
        interval: (legacy) Seconds between samples

    Returns:
        Exit code (0=healthy, 1=warning, 2=critical)
    """
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn

    from doorman_agent.collector import MetricsCollector
    from doorman_agent.findings import FindingsEngine, compute_system_status
    from doorman_agent.logger import StructuredLogger

    console = Console(no_color=no_color)
    logger = StructuredLogger("doorman-audit")
    collector = MetricsCollector(config, logger)

    start_time = time.time()

    if not json_output and not md_output:
        console.print()
        console.print("[bold cyan]🔍 Doorman Audit[/bold cyan]")
        console.print("[dim]═" * 60 + "[/dim]")
        console.print()

    # Connect with spinner
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(description="Connecting to infrastructure...", total=None)

        if not collector.connect():
            if json_output:
                print(
                    json_module.dumps(
                        {
                            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "system_status": "CRITICAL",
                            "exit_code": EXIT_CRITICAL,
                            "error": "Failed to connect to Redis/Celery",
                        }
                    )
                )
            else:
                console.print()
                console.print("[bold red]❌ Failed to connect to Redis/Celery[/bold red]")
                console.print("[dim]   Check REDIS_URL and CELERY_BROKER_URL[/dim]")
                console.print()
            return EXIT_CRITICAL

    # Collect metrics (respect timeout)
    metrics_samples: list[SystemMetrics] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        actual_samples = max(1, samples)
        for i in range(actual_samples):
            if actual_samples > 1:
                task = progress.add_task(
                    description=f"Collecting sample {i + 1}/{actual_samples}...", total=None
                )
            else:
                task = progress.add_task(description="Collecting metrics...", total=None)

            if i > 0:
                time.sleep(interval)

            metrics = collector.collect()
            metrics_samples.append(metrics)
            progress.remove_task(task)

    # Use the latest sample for the report
    metrics = metrics_samples[-1]

    # Run deep config checks if requested
    config_checks: list[ConfigCheck] = []
    if deep:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task(description="Running configuration analysis...", total=None)
            config_checks = _run_config_checks(collector, metrics)

    # Run findings engine
    engine = FindingsEngine()
    findings = engine.analyze(metrics, config, collector.latency_available)
    system_status = compute_system_status(findings)
    exit_code = _status_to_exit_code(system_status)

    # Also run legacy analysis for backward compat (combines config_checks into result)
    result = _analyze_metrics(metrics, metrics_samples, config, config_checks)
    # Use the higher exit code between findings-based and legacy analysis
    final_exit_code = max(exit_code, result.exit_code)

    elapsed = time.time() - start_time

    if json_output:
        _print_json_output(metrics, findings, system_status, final_exit_code)
    elif md_output:
        _print_md_report(metrics, findings, system_status, config, elapsed)
    else:
        _print_report(console, metrics, result, config, actual_samples, elapsed, deep)
        _print_findings_section(console, findings, system_status)

    return final_exit_code


def _print_json_output(
    metrics: SystemMetrics,
    findings: list[Any],
    system_status: Any,
    exit_code: int,
) -> None:
    """Print JSON summary to stdout."""
    from doorman_agent.findings import top_findings

    top = top_findings(findings, 3)
    top_list = []
    for f in top:
        evidence_summary = ", ".join(f"{k}={v}" for k, v in list(f.evidence.items())[:2])
        top_list.append(
            {
                "id": f.id,
                "severity": f.severity.value,
                "title": f.title,
                "evidence_summary": evidence_summary,
            }
        )

    output = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system_status": system_status.value,
        "exit_code": exit_code,
        "top_findings": top_list,
        "metrics": {
            "total_pending": metrics.total_pending_tasks,
            "total_active": metrics.total_active_tasks,
            "saturation_pct": round(metrics.saturation_pct, 1),
            "max_latency_sec": metrics.max_latency_sec,
            "redis_connected": metrics.redis_connected,
            "celery_connected": metrics.celery_connected,
        },
    }
    print(json_module.dumps(output))


def _print_md_report(
    metrics: SystemMetrics,
    findings: list[Any],
    system_status: Any,
    config: Config,
    elapsed: float,
) -> None:
    """Print Markdown report to stdout."""
    lines = [
        f"# Doorman Audit — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"**System Status:** {system_status.value}",
        f"**Duration:** {elapsed:.1f}s",
        "",
        "## Infrastructure",
        f"- Redis: {'connected' if metrics.redis_connected else 'disconnected'}",
        f"- Celery: {'connected' if metrics.celery_connected else 'disconnected'} ({metrics.total_workers} workers)",
        "",
        "## Findings",
    ]
    if findings:
        for f in findings:
            lines.append(f"- **[{f.severity.value}]** {f.title}")
    else:
        lines.append("- No findings — system healthy")

    print("\n".join(lines))


def _print_findings_section(console: Any, findings: list[Any], system_status: Any) -> None:
    """Print findings summary below the main report."""
    if not findings:
        return

    console.print()
    console.print("[bold]Findings[/bold]")
    for f in findings:
        sev = f.severity.value
        if sev == "CRITICAL":
            color = "red"
        elif sev == "HIGH":
            color = "yellow"
        elif sev == "MEDIUM":
            color = "blue"
        else:
            color = "dim"
        console.print(f"  [{color}][{sev}][/{color}]   {f.id:<30} {f.title}")


def run_watch(
    config: Config,
    interval: int = 5,
    no_color: bool = False,
    deep: bool = False,
) -> None:
    """Run the audit in a loop, refreshing every `interval` seconds."""
    import os

    while True:
        try:
            # Clear terminal
            os.system("clear" if os.name != "nt" else "cls")
            run_audit(config, no_color=no_color, deep=deep)
            time.sleep(interval)
        except KeyboardInterrupt:
            break


def _run_config_checks(collector: Any, metrics: SystemMetrics) -> list[ConfigCheck]:
    """Run deep configuration checks on Redis and Celery"""
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
        inspector = celery_app.control.inspect(timeout=5)
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


def _calculate_trends(samples: list[SystemMetrics]) -> list[QueueTrend]:
    """Calculate queue trends from multiple samples"""
    if len(samples) < 2:
        return []

    first = samples[0]
    last = samples[-1]

    trends = []

    # Build lookup for first sample
    first_depths = {q.name: q.depth for q in first.queues}

    for queue in last.queues:
        start_depth = first_depths.get(queue.name, 0)
        end_depth = queue.depth
        delta = end_depth - start_depth

        if delta > 0:
            trend = "growing"
        elif delta < 0:
            trend = "shrinking"
        else:
            trend = "stable"

        trends.append(
            QueueTrend(
                name=queue.name,
                depth_start=start_depth,
                depth_end=end_depth,
                depth_delta=delta,
                trend=trend,
            )
        )

    return trends


def _is_queue_congested(queue: Any, config: Config, total_concurrency: int = 0) -> bool:
    """
    Determine if a queue is congested.

    A queue is congested if:
    - depth > max_queue_size threshold, OR
    - latency > max_wait_time threshold (if latency is known), OR
    - depth > total_concurrency (more pending than we can process at once)
    """
    # Check depth against absolute threshold
    if queue.depth > config.thresholds.max_queue_size:
        return True

    # Check latency threshold (only if latency is available)
    if (
        queue.oldest_task_age_seconds is not None
        and queue.oldest_task_age_seconds > config.thresholds.max_wait_time_seconds
    ):
        return True

    # Check depth against capacity - if more pending than total slots, it's backing up
    if total_concurrency > 0 and queue.depth > total_concurrency:
        return True

    return False


def _analyze_metrics(
    metrics: SystemMetrics,
    samples: list[SystemMetrics],
    config: Config,
    config_checks: list[ConfigCheck],
) -> AuditResult:
    """Analyze metrics and generate audit result"""
    result = AuditResult()
    result.config_checks = config_checks

    # Calculate trends if multiple samples
    if len(samples) > 1:
        result.queue_trends = _calculate_trends(samples)

    # Check infrastructure
    if not metrics.redis_connected:
        result.criticals.append("Redis not connected")
        result.exit_code = EXIT_CRITICAL

    if not metrics.celery_connected:
        result.criticals.append("No Celery workers responding")
        result.exit_code = EXIT_CRITICAL

    # Check workers
    dead_workers = metrics.total_workers - metrics.alive_workers
    if dead_workers > 0:
        capacity_lost = (
            (dead_workers / metrics.total_workers) * 100 if metrics.total_workers > 0 else 0
        )
        result.criticals.append(
            f"{dead_workers} worker(s) offline = {capacity_lost:.0f}% capacity lost"
        )
        result.recommendations.append(
            f"Check {dead_workers} offline worker(s) — not responding to ping"
        )
        result.exit_code = EXIT_CRITICAL

    # Check for workers at capacity
    workers_at_capacity = [
        w
        for w in metrics.workers
        if w.is_alive and w.concurrency > 0 and w.active_tasks >= w.concurrency
    ]
    if workers_at_capacity:
        result.warnings.append(f"{len(workers_at_capacity)} worker(s) at full capacity")
        if result.exit_code < EXIT_WARNING:
            result.exit_code = EXIT_WARNING

    # Check queues for congestion
    congested_queues = []
    for q in metrics.queues:
        if _is_queue_congested(q, config, metrics.total_concurrency):
            congested_queues.append(q)

            # Build recommendation message
            parts = [f"'{q.name}' queue ({q.depth} pending"]
            if q.oldest_task_age_seconds:
                parts.append(f", {q.oldest_task_age_seconds:.0f}s latency")
            else:
                parts.append(", latency unknown")
            parts.append(")")

            result.recommendations.append(f"Scale workers for {''.join(parts)}")

    if congested_queues:
        result.warnings.append(f"{len(congested_queues)} queue(s) congested")
        if result.exit_code < EXIT_WARNING:
            result.exit_code = EXIT_WARNING

    # Check for ghost workers (low saturation but significant backlog)
    has_significant_backlog = (
        metrics.total_concurrency > 0 and metrics.total_pending_tasks > metrics.total_concurrency
    )
    low_saturation = metrics.saturation_pct < 50

    if has_significant_backlog and low_saturation and metrics.alive_workers > 0:
        # Find which queues have backlog
        backlogged_queues = [q for q in metrics.queues if q.depth > 0]
        if backlogged_queues:
            queue_names = ", ".join(q.name for q in backlogged_queues[:3])
            result.criticals.append(
                f"🔥 Possible Ghost Workers: {metrics.total_pending_tasks} tasks pending but workers are {metrics.saturation_pct:.0f}% idle"
            )
            result.recommendations.append(
                f"Investigate: workers not picking up tasks from [{queue_names}] (check network/broker config)"
            )
            result.exit_code = EXIT_CRITICAL

    # Check for trends indicating growing queues
    for trend in result.queue_trends:
        if trend.trend == "growing" and trend.depth_delta > 10:
            result.warnings.append(f"Queue '{trend.name}' growing: +{trend.depth_delta} tasks")
            if result.exit_code < EXIT_WARNING:
                result.exit_code = EXIT_WARNING

    # Check stuck tasks
    if metrics.stuck_tasks:
        result.criticals.append(f"{len(metrics.stuck_tasks)} stuck task(s) detected")
        result.exit_code = EXIT_CRITICAL

        for stuck in metrics.stuck_tasks:
            runtime_min = stuck.get("runtime_seconds", 0) / 60
            result.recommendations.append(
                f"Investigate stuck task '{stuck.get('task_name', 'unknown')}' on {stuck.get('worker', 'unknown')} ({runtime_min:.0f}min)"
            )

    # Check saturation
    if metrics.saturation_pct > 90:
        result.warnings.append(f"High saturation ({metrics.saturation_pct:.1f}%)")
        result.recommendations.append("Consider adding more workers — saturation above 90%")
        if result.exit_code < EXIT_WARNING:
            result.exit_code = EXIT_WARNING

    # Add config check warnings/criticals
    for check in config_checks:
        if check.status == "warning" and check.recommendation:
            result.recommendations.append(f"{check.name}: {check.recommendation}")
        elif check.status == "critical" and check.recommendation:
            result.recommendations.append(f"{check.name}: {check.recommendation}")
            result.exit_code = EXIT_CRITICAL

    return result


def _print_report(
    console: Any,
    metrics: SystemMetrics,
    result: AuditResult,
    config: Config,
    samples: int,
    elapsed: float,
    deep: bool,
) -> None:
    """Print the formatted audit report using rich"""
    from rich.panel import Panel
    from rich.table import Table

    # Critical banner if needed
    critical_banners = [c for c in result.criticals if c.startswith("🔥")]
    if critical_banners:
        for banner in critical_banners:
            console.print(Panel(f"[bold red]{banner}[/bold red]", border_style="red"))
        console.print()

    # System status header
    if result.exit_code == EXIT_HEALTHY:
        status_text = "[bold green]✅ System: HEALTHY[/bold green]"
    elif result.exit_code == EXIT_WARNING:
        status_text = "[bold yellow]⚠️  System: DEGRADED[/bold yellow]"
    else:
        status_text = "[bold red]❌ System: CRITICAL[/bold red]"

    console.print(status_text)
    console.print()

    # Infrastructure section
    console.print("[bold]Infrastructure[/bold]")
    if metrics.redis_connected:
        console.print("  [green]✅ Redis: connected[/green]")
    else:
        console.print("  [red]❌ Redis: not connected[/red]")

    if metrics.celery_connected:
        worker_word = "worker" if metrics.total_workers == 1 else "workers"
        console.print(
            f"  [green]✅ Celery: connected ({metrics.total_workers} {worker_word})[/green]"
        )
    else:
        console.print("  [red]❌ Celery: no workers responding[/red]")

    # Workers table
    console.print()
    console.print("[bold]Workers[/bold]")

    if not metrics.workers:
        console.print("  [dim]No workers found[/dim]")
    else:
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Status")
        table.add_column("Worker")
        table.add_column("Slots")
        table.add_column("Note")

        for w in metrics.workers:
            slots = (
                f"{w.active_tasks}/{w.concurrency}" if w.concurrency > 0 else f"{w.active_tasks}"
            )

            if not w.is_alive:
                table.add_row("[red]❌[/red]", w.name, slots, "[red]offline[/red]")
            elif w.concurrency > 0 and w.active_tasks >= w.concurrency:
                table.add_row("[yellow]⚠️[/yellow]", w.name, slots, "[yellow]at capacity[/yellow]")
            else:
                table.add_row("[green]✅[/green]", w.name, slots, "[green]online[/green]")

        console.print(table)

    # Queues table
    console.print()
    console.print("[bold]Queues[/bold]")

    if not metrics.queues:
        console.print("  [dim]No queues found[/dim]")
    else:
        # Build trend lookup
        trend_lookup = {t.name: t for t in result.queue_trends}

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Status")
        table.add_column("Queue")
        table.add_column("Pending", justify="right")
        table.add_column("Latency", justify="right")
        table.add_column("Trend")

        for q in metrics.queues:
            is_congested = _is_queue_congested(q, config, metrics.total_concurrency)

            # Status icon
            if is_congested:
                status = "[red]🔥[/red]"
            else:
                status = "[green]✅[/green]"

            # Latency display
            if q.oldest_task_age_seconds is not None:
                if q.oldest_task_age_seconds > config.thresholds.max_wait_time_seconds:
                    latency = f"[red]{q.oldest_task_age_seconds:.1f}s[/red]"
                else:
                    latency = f"{q.oldest_task_age_seconds:.1f}s"
            elif q.depth > 0:
                # Has tasks but no latency info
                latency = "[yellow]unknown[/yellow]"
            else:
                latency = "[dim]0s[/dim]"

            # Trend
            trend_str = ""
            if q.name in trend_lookup:
                trend = trend_lookup[q.name]
                if trend.trend == "growing":
                    trend_str = f"[red]↑+{trend.depth_delta}[/red]"
                elif trend.trend == "shrinking":
                    trend_str = f"[green]↓{trend.depth_delta}[/green]"
                else:
                    trend_str = "[dim]→[/dim]"

            # Depth with congestion coloring
            if is_congested:
                depth_str = f"[red]{q.depth}[/red]"
            else:
                depth_str = str(q.depth) if q.depth > 0 else "[dim]0[/dim]"

            table.add_row(status, q.name, depth_str, latency, trend_str)

        console.print(table)

    # Metrics section
    console.print()
    console.print("[bold]Metrics[/bold]")

    # Saturation with color and headroom
    sat = metrics.saturation_pct
    headroom = metrics.total_concurrency - metrics.total_active_tasks

    if sat > 90:
        sat_color = "red"
    elif sat > 70:
        sat_color = "yellow"
    else:
        sat_color = "green"

    slots_str = (
        f"{metrics.total_active_tasks}/{metrics.total_concurrency} slots"
        if metrics.total_concurrency > 0
        else ""
    )
    headroom_str = f", headroom: {headroom} slots" if headroom > 0 else ""

    console.print(
        f"  📊 Saturation: [{sat_color}]{sat:.1f}%[/{sat_color}] ({slots_str}{headroom_str})"
    )

    # Max latency - handle unknown case properly
    if metrics.max_latency_sec is not None:
        max_latency_queue = next(
            (
                q.name
                for q in metrics.queues
                if q.oldest_task_age_seconds == metrics.max_latency_sec
            ),
            "unknown",
        )
        if metrics.max_latency_sec > config.thresholds.max_wait_time_seconds:
            console.print(
                f"  ⏱️  Max Latency: [red]{metrics.max_latency_sec:.1f}s[/red] ({max_latency_queue})"
            )
        else:
            console.print(f"  ⏱️  Max Latency: {metrics.max_latency_sec:.1f}s ({max_latency_queue})")
    elif metrics.total_pending_tasks > 0:
        # Has pending tasks but can't measure latency — show the fix inline
        console.print(
            "  ⏱️  Max Latency: [yellow]unavailable[/yellow] — "
            "enable with: [dim]DoormanStampPlugin.install(app)[/dim]  "
            "[dim](see LATENCY_UNAVAILABLE finding)[/dim]"
        )
    else:
        console.print("  ⏱️  Max Latency: [green]0s (SLA Safe ✓)[/green]")

    # Total pending
    pending_color = "red" if metrics.total_pending_tasks > 100 else "default"
    console.print(
        f"  📋 Total Pending: [{pending_color}]{metrics.total_pending_tasks:,}[/{pending_color}] tasks"
    )

    # Anomalies section
    if metrics.stuck_tasks:
        console.print()
        console.print("[bold]Anomalies[/bold]")
        console.print(
            f"  [red]⚠️  {len(metrics.stuck_tasks)} stuck task(s) (>{config.thresholds.max_task_runtime_seconds // 60}min)[/red]"
        )
        for stuck in metrics.stuck_tasks:
            runtime_min = stuck.get("runtime_seconds", 0) / 60
            console.print(
                f"     └─ {stuck.get('task_name', 'unknown')} on {stuck.get('worker', 'unknown')} ({runtime_min:.0f}min)"
            )

    # Trends section (only if multiple samples)
    if samples > 1 and result.queue_trends:
        growing = [t for t in result.queue_trends if t.trend == "growing"]
        shrinking = [t for t in result.queue_trends if t.trend == "shrinking"]

        if growing or shrinking:
            console.print()
            console.print(f"[bold]Trends[/bold] [dim](over {samples} samples)[/dim]")
            for t in growing:
                console.print(
                    f"  [red]↑ {t.name}: +{t.depth_delta} tasks ({t.depth_start} → {t.depth_end})[/red]"
                )
            for t in shrinking:
                console.print(
                    f"  [green]↓ {t.name}: {t.depth_delta} tasks ({t.depth_start} → {t.depth_end})[/green]"
                )

    # Configuration Analysis (only if deep mode)
    if deep and result.config_checks:
        console.print()
        console.print("[bold]Configuration Analysis[/bold]")

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Status")
        table.add_column("Check")
        table.add_column("Result")

        for check in result.config_checks:
            if check.status == "ok":
                status = "[green]✅[/green]"
                message = f"[green]{check.message}[/green]"
            elif check.status == "warning":
                status = "[yellow]⚠️[/yellow]"
                message = f"[yellow]{check.message}[/yellow]"
            else:
                status = "[red]❌[/red]"
                message = f"[red]{check.message}[/red]"

            table.add_row(status, check.name, message)

        console.print(table)

    # Recommendations
    if result.recommendations:
        from rich.markup import escape

        console.print()
        console.print("[dim]═" * 60 + "[/dim]")
        console.print("[bold]💡 Recommendations:[/bold]")
        for rec in result.recommendations:
            console.print(f"  • {escape(rec)}")

    # Final status footer
    console.print()
    console.print("[dim]═" * 60 + "[/dim]")

    if result.exit_code == EXIT_HEALTHY:
        console.print("[bold green]✅ All systems healthy[/bold green]")
    elif result.exit_code == EXIT_WARNING:
        console.print("[bold yellow]⚠️  Warnings detected[/bold yellow]")
    else:
        console.print("[bold red]❌ Critical issues found[/bold red]")

    console.print(f"[dim]Audit completed in {elapsed:.1f}s[/dim]")
    console.print()
