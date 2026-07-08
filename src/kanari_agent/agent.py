"""
Main Kanari Agent class
"""

from __future__ import annotations

import signal
import sys
import time

from kanari_agent.api_client import APIClient
from kanari_agent.collector import MetricsCollector, _redact_url
from kanari_agent.config import AGENT_VERSION
from kanari_agent.findings import FindingsEngine, compute_system_status
from kanari_agent.logger import StructuredLogger
from kanari_agent.models import Config, SystemMetrics


class KanariAgent:
    """
    The Kanari Agent - a lightweight metrics collector.

    In API mode: collects metrics and sends to getkanari.com API
    In local mode: collects metrics and logs them (no API calls)
    """

    def __init__(self, config: Config):
        self.config = config
        self.logger = StructuredLogger("kanari-agent")
        self.collector = MetricsCollector(config, self.logger)
        self.running = False
        self._consecutive_failures = 0
        self._max_consecutive_failures = 10
        self.api_client: APIClient | None = None

        # Initialize API client only if not in local mode and API key provided
        if not config.local_mode and config.api_key:
            self.api_client = APIClient(
                api_key=config.api_key, api_url=config.api_url, logger=self.logger
            )

    def setup_signal_handlers(self) -> None:
        """Configures handlers for graceful shutdown"""

        def handle_shutdown(signum: object, frame: object) -> None:
            self.logger.info("Shutdown signal received", signal=signum)
            self.running = False

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

    def _log_metrics_locally(self, metrics: SystemMetrics) -> None:
        """Logs metrics in structured format (for local mode)"""
        if self.api_client:
            payload = self.api_client.build_payload(metrics)
        else:
            # Build basic payload without API client
            payload = {
                "timestamp": metrics.timestamp,
                "agent_version": AGENT_VERSION,
                "metrics": {
                    "total_pending": metrics.total_pending_tasks,
                    "total_active": metrics.total_active_tasks,
                    "total_workers": metrics.total_workers,
                    "alive_workers": metrics.alive_workers,
                    "expected_workers": metrics.expected_workers,
                    "missing_workers": metrics.missing_workers,
                    "total_concurrency": metrics.total_concurrency,
                    "saturation_pct": round(metrics.saturation_pct, 2),
                    "max_latency_sec": metrics.max_latency_sec,
                },
                "infra_health": {
                    "redis": metrics.redis_connected,
                    "celery": metrics.celery_connected,
                },
                "queues": [
                    {
                        "name": q.name,
                        "depth": q.depth,
                        "latency_sec": q.oldest_task_age_seconds,
                    }
                    for q in metrics.queues
                ],
                "workers": [
                    {
                        "name": w.name,
                        "active_tasks": w.active_tasks,
                        "concurrency": w.concurrency,
                        "is_alive": w.is_alive,
                    }
                    for w in metrics.workers
                ],
                "anomalies": metrics.stuck_tasks,
            }

        self.logger.info("metrics_collected", mode="local", payload=payload)

    def startup_audit(self) -> None:
        """One full audit pass (findings + config smells) logged at daemon startup.

        Guarantees the first thing an operator sees is an assessment, not
        silence. Never sends anything to the API and never blocks the loop
        from starting.
        """
        try:
            from kanari_agent.config_checks import _run_config_checks

            metrics = self.collector.collect()
            engine = FindingsEngine()
            findings = engine.analyze(metrics, self.config, self.collector.latency_available)
            config_checks = _run_config_checks(self.collector, metrics)
            status = compute_system_status(findings)

            ok_checks = sum(1 for c in config_checks if c.status == "ok")
            self.logger.info(
                "startup_audit",
                system_status=status.value,
                findings=[
                    {"id": f.id, "severity": f.severity.value, "title": f.title} for f in findings
                ],
                config_checks=[
                    {
                        "name": c.name,
                        "status": c.status,
                        "message": c.message,
                        "recommendation": c.recommendation,
                    }
                    for c in config_checks
                ],
                checks_passed=ok_checks,
            )

            # Human-readable lines for log readers that don't parse JSON
            for f in findings:
                fix = f.safe_fix[0] if f.safe_fix else ""
                self.logger.warning(
                    f"[{f.severity.value}] {f.title}" + (f" — {fix}" if fix else "")
                )
            for c in config_checks:
                if c.status != "ok":
                    rec = f" — {c.recommendation}" if c.recommendation else ""
                    self.logger.warning(f"[CONFIG] {c.name}: {c.message}{rec}")
        except Exception as e:
            self.logger.error("startup_audit_failed", error=str(e))

    def check_once(self) -> SystemMetrics:
        """Executes one monitoring cycle"""
        metrics = self.collector.collect()

        # Generate findings
        engine = FindingsEngine()
        findings = engine.analyze(metrics, self.config, self.collector.latency_available)

        # Emit one-line health summary
        lat = f"{metrics.max_latency_sec:.0f}s" if metrics.max_latency_sec is not None else "?"
        self.logger.info(
            f"Health: {'OK' if not findings else findings[0].severity.value} "
            f"pending={metrics.total_pending_tasks} active={metrics.total_active_tasks} "
            f"workers={metrics.alive_workers}/{metrics.total_workers} "
            f"missing={metrics.missing_workers} "
            f"sat={metrics.saturation_pct:.0f}% max_age={lat} findings={len(findings)}"
        )

        # Local mode: just log metrics at DEBUG level
        if self.config.local_mode:
            self._log_metrics_locally(metrics)
            return metrics

        # API mode: send metrics
        if self.api_client:
            success = self.api_client.send_metrics(metrics)

            if success:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1

                if self._consecutive_failures >= self._max_consecutive_failures:
                    self.logger.error(
                        "Too many consecutive API failures",
                        count=self._consecutive_failures,
                        action="Agent will continue collecting but metrics are not being sent",
                    )
        else:
            self.logger.warning("No API key configured - metrics are only logged locally")
            self._log_metrics_locally(metrics)

        return metrics

    def run(self) -> None:
        """Runs the main agent loop"""
        mode = "local" if self.config.local_mode else "api"

        # Log startup banner first
        self.logger.info(
            "Kanari Agent starting",
            version=AGENT_VERSION,
            mode=mode,
            check_interval=self.config.check_interval_seconds,
            api_url=_redact_url(self.config.api_url) if not self.config.local_mode else "disabled",
        )

        # Validate API key if in API mode
        if not self.config.local_mode and self.api_client:
            self.logger.info("Validating API key...")
            if not self.api_client.validate_api_key():
                self.logger.error("API key validation failed. Please check your KANARI_API_KEY.")
                sys.exit(1)
            self.logger.info("API key validated successfully")
        elif self.config.local_mode:
            self.logger.info("Running in LOCAL MODE - metrics will be logged, not sent to API")

        # Connect to Redis/Celery (only here)
        if not self.collector.connect():
            self.logger.error("Failed to establish connections, exiting")
            sys.exit(1)

        # Log monitored queues after connection (may be auto-discovered)
        queues = self.collector.get_queues_to_monitor()
        self.logger.info("Monitoring queues", queues=queues)

        # Full assessment up front so the first thing seen is never silence
        self.startup_audit()

        self.setup_signal_handlers()
        self.running = True

        self.logger.info("Agent is now running. Press Ctrl+C to stop.")

        while self.running:
            try:
                self.check_once()
            except Exception as e:
                self.logger.error("Error in check cycle", error=str(e))

            # Sleep in small intervals to respond to signals
            for _ in range(self.config.check_interval_seconds):
                if not self.running:
                    break
                time.sleep(1)

        self.logger.info("Kanari Agent stopped")
