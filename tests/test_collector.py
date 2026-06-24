"""
Tests for kanari_agent.collector module

Uses mocks for Redis and Celery — no external connections needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from kanari_agent.collector import MetricsCollector
from kanari_agent.models import Config


@pytest.fixture
def config() -> Config:
    return Config(
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/0",
        monitored_queues=["celery", "high-priority"],
    )


@pytest.fixture
def config_no_queues() -> Config:
    """Config with no queues configured (triggers auto-discovery)"""
    return Config(monitored_queues=[])


@pytest.fixture
def collector(config) -> MetricsCollector:
    return MetricsCollector(config)


@pytest.fixture
def collector_no_queues(config_no_queues) -> MetricsCollector:
    return MetricsCollector(config_no_queues)


def _make_celery_message(timestamp: float) -> str:
    """Helper: build a fake Celery Redis message with a timestamp"""
    return json.dumps(
        {
            "headers": {"timestamp": timestamp},
            "properties": {},
        }
    )


# ---------------------------------------------------------------------------
# get_queue_depth
# ---------------------------------------------------------------------------


class TestGetQueueDepth:
    def test_returns_depth_from_redis(self, collector):
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 42
        collector.redis_client = mock_redis

        assert collector.get_queue_depth("celery") == 42
        mock_redis.llen.assert_called_once_with("celery")

    def test_returns_zero_when_no_redis(self, collector):
        collector.redis_client = None
        assert collector.get_queue_depth("celery") == 0

    def test_returns_zero_on_redis_error(self, collector):
        mock_redis = MagicMock()
        mock_redis.llen.side_effect = Exception("connection error")
        collector.redis_client = mock_redis

        assert collector.get_queue_depth("celery") == 0

    def test_returns_zero_when_llen_returns_none(self, collector):
        mock_redis = MagicMock()
        mock_redis.llen.return_value = None
        collector.redis_client = mock_redis

        assert collector.get_queue_depth("celery") == 0


# ---------------------------------------------------------------------------
# get_oldest_task_age
# ---------------------------------------------------------------------------


class TestGetOldestTaskAge:
    def test_returns_none_when_no_redis(self, collector):
        collector.redis_client = None
        age, mode = collector.get_oldest_task_age("celery")
        assert age is None
        assert mode == "none"

    def test_returns_none_for_empty_queue(self, collector):
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = None
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is None

    def test_returns_age_from_header_timestamp(self, collector):
        # Message sent 100 seconds ago
        past_ts = datetime.now(timezone.utc).timestamp() - 100
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = _make_celery_message(past_ts)
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is not None
        assert 95 < age < 110  # allow some tolerance
        assert mode == "celery_event"

    def test_returns_none_for_invalid_json(self, collector):
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = "not-json"
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is None

    def test_returns_none_for_message_without_timestamp(self, collector):
        msg = json.dumps({"headers": {}, "properties": {}})
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = msg
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is None

    def test_age_is_non_negative(self, collector):
        # A future timestamp should clamp to 0
        future_ts = datetime.now(timezone.utc).timestamp() + 3600
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = _make_celery_message(future_ts)
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is not None
        assert age >= 0


# ---------------------------------------------------------------------------
# get_queues_to_monitor
# ---------------------------------------------------------------------------


class TestGetQueuesToMonitor:
    def test_returns_configured_queues(self, collector):
        assert collector.get_queues_to_monitor() == ["celery", "high-priority"]

    def test_discovers_from_workers_when_not_configured(self, collector_no_queues):
        mock_celery = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.active_queues.return_value = {
            "celery@worker-1": [{"name": "orders"}, {"name": "notifications"}]
        }
        mock_celery.control.inspect.return_value = mock_inspector
        collector_no_queues.celery_app = mock_celery

        queues = collector_no_queues.get_queues_to_monitor()
        assert set(queues) == {"orders", "notifications"}

    def test_falls_back_to_celery_queue_when_nothing_discovered(self, collector_no_queues):
        mock_celery = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.active_queues.return_value = {}
        mock_celery.control.inspect.return_value = mock_inspector
        collector_no_queues.celery_app = mock_celery

        queues = collector_no_queues.get_queues_to_monitor()
        assert queues == ["celery"]

    def test_caches_discovered_queues(self, collector_no_queues):
        mock_celery = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.active_queues.return_value = {"celery@worker-1": [{"name": "jobs"}]}
        mock_celery.control.inspect.return_value = mock_inspector
        collector_no_queues.celery_app = mock_celery

        collector_no_queues.get_queues_to_monitor()
        collector_no_queues.get_queues_to_monitor()

        # discover_queues should only be called once (cached after first call)
        assert mock_inspector.active_queues.call_count == 1


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


class TestCollect:
    def _setup_redis(self, collector, depths: dict, lindex_return=None):
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.llen.side_effect = lambda q: depths.get(q, 0)
        mock_redis.lindex.return_value = lindex_return
        collector.redis_client = mock_redis
        return mock_redis

    def _setup_celery(self, collector, active=None, reserved=None, stats=None):
        mock_celery = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.active.return_value = active or {}
        mock_inspector.reserved.return_value = reserved or {}
        mock_inspector.stats.return_value = stats or {}
        mock_celery.control.inspect.return_value = mock_inspector
        collector.celery_app = mock_celery
        return mock_celery

    def test_redis_connected_flag(self, collector):
        self._setup_redis(collector, {})
        self._setup_celery(collector)
        metrics = collector.collect()
        assert metrics.redis_connected is True

    def test_redis_not_connected_when_ping_fails(self, collector):
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = Exception("timeout")
        mock_redis.llen.return_value = 0
        mock_redis.lindex.return_value = None
        collector.redis_client = mock_redis
        self._setup_celery(collector)

        metrics = collector.collect()
        assert metrics.redis_connected is False

    def test_total_pending_tasks_summed_across_queues(self, collector):
        self._setup_redis(collector, {"celery": 10, "high-priority": 5})
        self._setup_celery(collector)

        metrics = collector.collect()
        assert metrics.total_pending_tasks == 15

    def test_queue_depths_in_metrics(self, collector):
        self._setup_redis(collector, {"celery": 7, "high-priority": 3})
        self._setup_celery(collector)

        metrics = collector.collect()
        depths = {q.name: q.depth for q in metrics.queues}
        assert depths["celery"] == 7
        assert depths["high-priority"] == 3

    def test_worker_count_and_saturation(self, collector):
        self._setup_redis(collector, {})
        self._setup_celery(
            collector,
            active={"celery@worker-1": [{"id": "t1", "name": "task"}]},
            stats={"celery@worker-1": {"pool": {"max-concurrency": 4}}},
        )

        metrics = collector.collect()
        assert metrics.total_workers == 1
        assert metrics.alive_workers == 1
        assert metrics.total_active_tasks == 1
        assert metrics.total_concurrency == 4
        assert metrics.saturation_pct == 25.0

    def test_no_workers_saturation_is_zero(self, collector):
        self._setup_redis(collector, {})
        self._setup_celery(collector)

        metrics = collector.collect()
        assert metrics.saturation_pct == 0.0

    def test_stuck_task_detected(self, collector):
        import time

        self._setup_redis(collector, {})
        # Task started 2 hours ago (exceeds 1800s threshold)
        old_start = time.time() - 7200
        self._setup_celery(
            collector,
            active={
                "celery@worker-1": [{"id": "stuck-1", "name": "long_task", "time_start": old_start}]
            },
            stats={"celery@worker-1": {"pool": {"max-concurrency": 4}}},
        )

        metrics = collector.collect()
        assert len(metrics.stuck_tasks) == 1
        assert metrics.stuck_tasks[0]["task_id"] == "stuck-1"
        assert metrics.stuck_tasks[0]["runtime_seconds"] > 7000

    def test_collect_with_latency_tracked(self, collector):
        import time

        past_ts = time.time() - 50
        msg = json.dumps({"headers": {"timestamp": past_ts}, "properties": {}})
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.llen.side_effect = lambda q: 5 if q == "celery" else 0
        mock_redis.lindex.return_value = msg
        collector.redis_client = mock_redis
        self._setup_celery(collector)

        metrics = collector.collect()
        assert metrics.max_latency_sec is not None
        assert metrics.max_latency_sec > 0


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_connect_success_both(self, collector):
        mock_redis_mod = MagicMock()
        mock_redis_instance = MagicMock()
        mock_redis_mod.from_url.return_value = mock_redis_instance
        mock_celery_class = MagicMock()

        with (
            patch("kanari_agent.collector.REDIS_AVAILABLE", True),
            patch("kanari_agent.collector.CELERY_AVAILABLE", True),
            patch("kanari_agent.collector.redis", mock_redis_mod),
            patch("kanari_agent.collector.Celery", mock_celery_class),
        ):
            result = collector.connect()

        assert result is True
        assert collector.redis_client is mock_redis_instance

    def test_connect_redis_failure(self, collector):
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.side_effect = Exception("connection refused")
        mock_celery_class = MagicMock()

        with (
            patch("kanari_agent.collector.REDIS_AVAILABLE", True),
            patch("kanari_agent.collector.CELERY_AVAILABLE", True),
            patch("kanari_agent.collector.redis", mock_redis_mod),
            patch("kanari_agent.collector.Celery", mock_celery_class),
        ):
            result = collector.connect()

        assert result is False

    def test_connect_celery_failure(self, collector):
        mock_redis_mod = MagicMock()
        mock_redis_instance = MagicMock()
        mock_redis_mod.from_url.return_value = mock_redis_instance
        mock_celery_class = MagicMock()
        mock_celery_class.side_effect = Exception("broker error")

        with (
            patch("kanari_agent.collector.REDIS_AVAILABLE", True),
            patch("kanari_agent.collector.CELERY_AVAILABLE", True),
            patch("kanari_agent.collector.redis", mock_redis_mod),
            patch("kanari_agent.collector.Celery", mock_celery_class),
        ):
            result = collector.connect()

        assert result is False

    def test_connect_redis_not_available(self, collector):
        mock_celery_class = MagicMock()
        with (
            patch("kanari_agent.collector.REDIS_AVAILABLE", False),
            patch("kanari_agent.collector.CELERY_AVAILABLE", True),
            patch("kanari_agent.collector.Celery", mock_celery_class),
        ):
            result = collector.connect()

        assert result is False

    def test_connect_celery_not_available(self, collector):
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.return_value = MagicMock()
        with (
            patch("kanari_agent.collector.REDIS_AVAILABLE", True),
            patch("kanari_agent.collector.CELERY_AVAILABLE", False),
            patch("kanari_agent.collector.redis", mock_redis_mod),
        ):
            result = collector.connect()

        assert result is False


# ---------------------------------------------------------------------------
# Additional get_oldest_task_age paths
# ---------------------------------------------------------------------------


class TestGetOldestTaskAgeExtended:
    def test_properties_timestamp_fallback(self, collector):
        past_ts = datetime.now(timezone.utc).timestamp() - 30
        msg = json.dumps({"headers": {}, "properties": {"timestamp": past_ts}})
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = msg
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is not None
        assert age > 25

    def test_published_timestamp_fallback(self, collector):
        past_ts = datetime.now(timezone.utc).timestamp() - 20
        msg = json.dumps({"headers": {}, "properties": {"published": past_ts}})
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = msg
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is not None
        assert age > 15

    def test_bytes_message_decoded(self, collector):
        past_ts = datetime.now(timezone.utc).timestamp() - 10
        msg = json.dumps({"headers": {"timestamp": past_ts}, "properties": {}}).encode("utf-8")
        mock_redis = MagicMock()
        mock_redis.lindex.return_value = msg
        collector.redis_client = mock_redis

        age, mode = collector.get_oldest_task_age("celery")
        assert age is not None
        assert age >= 0

    def test_get_worker_stats_exception_returns_empty(self, collector):
        mock_celery = MagicMock()
        mock_celery.control.inspect.side_effect = Exception("broker down")
        collector.celery_app = mock_celery

        active, reserved, stats = collector.get_worker_stats()
        assert active == {}
        assert reserved == {}
        assert stats == {}
