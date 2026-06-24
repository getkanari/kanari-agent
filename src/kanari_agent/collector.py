"""
Metrics collector for Redis and Celery
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from kanari_agent.logger import StructuredLogger
from kanari_agent.models import Config, QueueMetrics, SystemMetrics, WorkerMetrics
from kanari_agent.stamps import KANARI_TS_HEADER

# Optional dependencies - check at runtime
try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None  # type: ignore[assignment]
    REDIS_AVAILABLE = False

try:
    from celery import Celery

    CELERY_AVAILABLE = True
except ImportError:
    Celery = None
    CELERY_AVAILABLE = False


def _redact_url(url: str) -> str:
    """Redact password from URL. redis://:password@host -> redis://***@host"""
    return re.sub(r"(:)[^@/]+(@)", r"\1***\2", url)


INSPECT_TIMEOUT = 1.0


class MetricsCollector:
    """Collects metrics from Redis and Celery"""

    def __init__(self, config: Config, logger: StructuredLogger | None = None):
        self.config = config
        self.logger = logger or StructuredLogger("kanari-collector")
        self.redis_client: Any | None = None
        self.celery_app: Any | None = None
        self._discovered_queues: list[str] = []
        self.latency_available: bool = False

    def connect(self) -> bool:
        """Establishes connections with Redis and Celery"""
        success = True

        # Connect to Redis
        if REDIS_AVAILABLE:
            try:
                self.redis_client = redis.from_url(
                    self.config.redis_url,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                )
                self.redis_client.ping()
                self.logger.debug(
                    "Redis connection established", url=_redact_url(self.config.redis_url)
                )
            except Exception as e:
                self.logger.error("Redis connection failed", error=str(e))
                success = False
        else:
            self.logger.warning("Redis library not available. Install with: pip install redis")
            success = False

        # Connect to Celery
        if CELERY_AVAILABLE:
            try:
                self.celery_app = Celery(
                    self.config.celery_app_name, broker=self.config.celery_broker_url
                )
                self.celery_app.conf.update(
                    broker_connection_timeout=5, broker_connection_retry=False
                )
                self.logger.debug(
                    "Celery app initialized",
                    broker=_redact_url(self.config.celery_broker_url),
                )
            except Exception as e:
                self.logger.error("Celery initialization failed", error=str(e))
                success = False
        else:
            self.logger.warning("Celery library not available. Install with: pip install celery")
            success = False

        return success

    def _inspect_all(self) -> tuple[dict, dict, dict, dict]:
        """Single inspector call that fetches all Celery data at once.

        Returns (active_queues, active, reserved, stats) dicts.
        """
        active_queues: dict = {}
        active: dict = {}
        reserved: dict = {}
        stats: dict = {}

        if not self.celery_app:
            return active_queues, active, reserved, stats

        try:
            inspector = self.celery_app.control.inspect(timeout=INSPECT_TIMEOUT)
            active_queues = inspector.active_queues() or {}
            active = inspector.active() or {}
            reserved = inspector.reserved() or {}
            stats = inspector.stats() or {}
        except Exception as e:
            self.logger.error("Failed to inspect Celery workers", error=str(e))

        return active_queues, active, reserved, stats

    def _extract_queue_names(self, active_queues: dict) -> list[str]:
        """Extract queue names from active_queues inspector response."""
        queues: set[str] = set()

        for _worker_name, worker_queues in active_queues.items():
            for queue_info in worker_queues:
                if isinstance(queue_info, dict):
                    queue_name = queue_info.get("name")
                    if queue_name:
                        queues.add(queue_name)
                elif isinstance(queue_info, str):
                    queues.add(queue_info)

        if queues:
            self.logger.debug("Discovered queues from workers", queues=list(queues))
        elif active_queues:
            self.logger.warning("No queues discovered from workers")

        return list(queues)

    def discover_queues(self) -> list[str]:
        """Auto-discover queues from Celery workers."""
        active_queues, _, _, _ = self._inspect_all()
        return self._extract_queue_names(active_queues)

    def get_queues_to_monitor(self) -> list[str]:
        """
        Returns the list of queues to monitor.
        If monitored_queues is configured, use that.
        Otherwise, auto-discover from workers.
        """
        if self.config.monitored_queues:
            return self.config.monitored_queues

        # Auto-discover if not configured
        if not self._discovered_queues:
            self._discovered_queues = self.discover_queues()

        # Fallback to default "celery" queue if nothing discovered
        if not self._discovered_queues:
            self.logger.debug("No queues configured or discovered, using default 'celery' queue")
            return ["celery"]

        return self._discovered_queues

    def get_queue_depth(self, queue_name: str) -> int:
        """Gets the depth of a queue from Redis"""
        if not self.redis_client:
            return 0

        try:
            depth = self.redis_client.llen(queue_name)
            return depth or 0
        except Exception as e:
            self.logger.error("Failed to get queue depth", queue=queue_name, error=str(e))
            return 0

    def get_oldest_task_age(self, queue_name: str) -> tuple[float | None, str]:
        """
        Estimates the age of the oldest task in the queue.

        Celery messages may or may not have timestamps depending on configuration.
        We try multiple strategies:
        1. Check headers.kanari_sent_ts (Kanari stamp)
        2. Check headers.timestamp (if task_send_sent_event=True)
        3. Check properties.timestamp
        4. Check headers.eta (for scheduled tasks)

        Returns (age_seconds, latency_mode) where latency_mode is one of:
          "kanari"        — from kanari_sent_ts header
          "celery_event"  — from Celery's built-in timestamp header/property
          "none"          — no timestamp found
        """
        if not self.redis_client:
            return None, "none"

        try:
            # Get oldest message (last in list, since LPUSH adds to head)
            oldest_message = self.redis_client.lindex(queue_name, -1)
            if not oldest_message:
                return None, "none"

            try:
                if isinstance(oldest_message, str):
                    task_data = json.loads(oldest_message)
                else:
                    task_data = json.loads(oldest_message.decode("utf-8"))

                headers = task_data.get("headers", {})
                properties = task_data.get("properties", {})

                # Strategy 1: Kanari stamp (highest priority)
                if KANARI_TS_HEADER in headers:
                    ts = headers[KANARI_TS_HEADER]
                    if isinstance(ts, (int, float)):
                        age = time.time() - ts
                        return max(0, age), "kanari"

                # Strategy 2: Celery event timestamp in headers
                timestamp = None
                latency_mode = "none"

                if "timestamp" in headers:
                    timestamp = headers["timestamp"]
                    latency_mode = "celery_event"
                elif "timestamp" in properties:
                    timestamp = properties["timestamp"]
                    latency_mode = "celery_event"
                elif "published" in properties:
                    timestamp = properties["published"]
                    latency_mode = "celery_event"

                if timestamp is not None:
                    # Handle both float timestamps and ISO strings
                    if isinstance(timestamp, (int, float)):
                        task_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    else:
                        task_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))

                    age = (datetime.now(timezone.utc) - task_time).total_seconds()
                    return max(0, age), latency_mode

            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

            return None, "none"

        except Exception as e:
            self.logger.error("Failed to get oldest task age", queue=queue_name, error=str(e))
            return None, "none"

    def get_worker_stats(self) -> tuple[dict, dict, dict]:
        """Gets worker statistics via Celery inspect.

        Prefer calling _inspect_all() directly in collect() to avoid
        duplicate broadcasts. This method is kept for backward compatibility.
        """
        _, active, reserved, stats = self._inspect_all()
        return active, reserved, stats

    def collect(self) -> SystemMetrics:
        """Collects all system metrics"""
        metrics = SystemMetrics(timestamp=datetime.now(timezone.utc).isoformat())

        # Reset latency tracking
        self.latency_available = False

        # Verify Redis connection
        if self.redis_client:
            try:
                self.redis_client.ping()
                metrics.redis_connected = True
            except Exception:
                metrics.redis_connected = False

        # Single Celery inspect call for all data
        celery_queues, active, reserved, stats = self._inspect_all()

        # Determine queues to monitor
        if self.config.monitored_queues:
            queues_to_monitor = self.config.monitored_queues
        else:
            discovered = self._extract_queue_names(celery_queues)
            if discovered:
                self._discovered_queues = discovered
            if not self._discovered_queues:
                self.logger.debug(
                    "No queues configured or discovered, using default 'celery' queue"
                )
                self._discovered_queues = ["celery"]
            queues_to_monitor = self._discovered_queues

        # Track max latency across all queues
        max_latency: float | None = None

        # Collect queue metrics
        for queue_name in queues_to_monitor:
            depth = self.get_queue_depth(queue_name)
            oldest_age, latency_mode = self.get_oldest_task_age(queue_name)

            queue_metrics = QueueMetrics(
                name=queue_name,
                depth=depth,
                oldest_task_age_seconds=oldest_age,
                latency_mode=latency_mode,
            )
            metrics.queues.append(queue_metrics)
            metrics.total_pending_tasks += depth

            # Track max latency
            if oldest_age is not None:
                self.latency_available = True
                if max_latency is None or oldest_age > max_latency:
                    max_latency = oldest_age

        metrics.max_latency_sec = max_latency
        metrics.latency_available = self.latency_available

        # Process worker metrics (already fetched via _inspect_all above)
        if active or reserved or stats:
            metrics.celery_connected = True

        all_workers = set(active.keys()) | set(reserved.keys()) | set(stats.keys())
        metrics.total_workers = len(all_workers)

        total_concurrency = 0

        for worker_name in all_workers:
            worker_active = active.get(worker_name, [])
            worker_stats = stats.get(worker_name, {})

            # Get concurrency from pool stats
            pool_info = worker_stats.get("pool", {})
            worker_concurrency = pool_info.get("max-concurrency", 0)
            total_concurrency += worker_concurrency

            worker_metrics = WorkerMetrics(
                name=worker_name,
                active_tasks=len(worker_active),
                concurrency=worker_concurrency,
                is_alive=worker_name in stats,
            )
            metrics.workers.append(worker_metrics)

            if worker_metrics.is_alive:
                metrics.alive_workers += 1

            metrics.total_active_tasks += len(worker_active)

            # Detect stuck tasks (zombies)
            for task in worker_active:
                if isinstance(task, dict):
                    time_start = task.get("time_start")
                    if time_start:
                        runtime = time.time() - time_start
                        if runtime > self.config.thresholds.max_task_runtime_seconds:
                            metrics.stuck_tasks.append(
                                {
                                    "task_id": task.get("id", "unknown"),
                                    "task_name": task.get("name", "unknown"),
                                    "worker": worker_name,
                                    "runtime_seconds": runtime,
                                    "started_at": datetime.fromtimestamp(
                                        time_start, tz=timezone.utc
                                    ).isoformat(),
                                }
                            )

        # Calculate saturation percentage
        metrics.total_concurrency = total_concurrency
        if total_concurrency > 0:
            metrics.saturation_pct = (metrics.total_active_tasks / total_concurrency) * 100
        else:
            metrics.saturation_pct = 0.0

        return metrics
