"""
Tests for doorman_agent.audit module

Tests analysis logic — no external connections needed.
"""

from __future__ import annotations

import pytest

from doorman_agent.audit import (
    EXIT_CRITICAL,
    EXIT_HEALTHY,
    EXIT_WARNING,
    ConfigCheck,
    _analyze_metrics,
    _calculate_trends,
    _is_queue_congested,
)
from doorman_agent.models import Config, QueueMetrics, SystemMetrics, WorkerMetrics


@pytest.fixture
def config() -> Config:
    return Config()


def _metrics(**kwargs) -> SystemMetrics:
    """Helper: build a SystemMetrics with sensible defaults"""
    defaults: dict = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "redis_connected": True,
        "celery_connected": True,
    }
    defaults.update(kwargs)
    return SystemMetrics(**defaults)


# ---------------------------------------------------------------------------
# _is_queue_congested
# ---------------------------------------------------------------------------


class TestIsQueueCongested:
    def test_empty_queue_not_congested(self, config):
        q = QueueMetrics(name="celery", depth=0)
        assert _is_queue_congested(q, config) is False

    def test_depth_exceeds_threshold(self, config):
        q = QueueMetrics(name="celery", depth=1001)
        assert _is_queue_congested(q, config) is True

    def test_depth_at_threshold_not_congested(self, config):
        q = QueueMetrics(name="celery", depth=1000)
        assert _is_queue_congested(q, config) is False

    def test_latency_exceeds_threshold(self, config):
        q = QueueMetrics(name="celery", depth=5, oldest_task_age_seconds=61)
        assert _is_queue_congested(q, config) is True

    def test_latency_within_threshold(self, config):
        q = QueueMetrics(name="celery", depth=5, oldest_task_age_seconds=30)
        assert _is_queue_congested(q, config) is False

    def test_depth_exceeds_concurrency(self, config):
        q = QueueMetrics(name="celery", depth=10)
        # 10 pending tasks but only 4 concurrency slots
        assert _is_queue_congested(q, config, total_concurrency=4) is True

    def test_depth_within_concurrency_not_congested(self, config):
        q = QueueMetrics(name="celery", depth=3)
        assert _is_queue_congested(q, config, total_concurrency=10) is False

    def test_no_latency_no_false_positive(self, config):
        # depth=5, no latency known — should not be flagged by latency check
        q = QueueMetrics(name="celery", depth=5, oldest_task_age_seconds=None)
        assert _is_queue_congested(q, config, total_concurrency=100) is False


# ---------------------------------------------------------------------------
# _calculate_trends
# ---------------------------------------------------------------------------


class TestCalculateTrends:
    def _make_samples(self, depths_start: dict, depths_end: dict) -> list[SystemMetrics]:
        first = _metrics(
            queues=[QueueMetrics(name=k, depth=v) for k, v in depths_start.items()]
        )
        last = _metrics(
            queues=[QueueMetrics(name=k, depth=v) for k, v in depths_end.items()]
        )
        return [first, last]

    def test_empty_result_for_single_sample(self):
        samples = [_metrics()]
        assert _calculate_trends(samples) == []

    def test_growing_trend(self):
        samples = self._make_samples({"celery": 10}, {"celery": 25})
        trends = _calculate_trends(samples)
        assert len(trends) == 1
        assert trends[0].trend == "growing"
        assert trends[0].depth_delta == 15

    def test_shrinking_trend(self):
        samples = self._make_samples({"celery": 50}, {"celery": 20})
        trends = _calculate_trends(samples)
        assert trends[0].trend == "shrinking"
        assert trends[0].depth_delta == -30

    def test_stable_trend(self):
        samples = self._make_samples({"celery": 10}, {"celery": 10})
        trends = _calculate_trends(samples)
        assert trends[0].trend == "stable"
        assert trends[0].depth_delta == 0

    def test_multiple_queues(self):
        samples = self._make_samples(
            {"celery": 5, "notifications": 100},
            {"celery": 20, "notifications": 50},
        )
        trends = _calculate_trends(samples)
        by_name = {t.name: t for t in trends}

        assert by_name["celery"].trend == "growing"
        assert by_name["notifications"].trend == "shrinking"

    def test_new_queue_in_last_sample_starts_from_zero(self):
        first = _metrics(queues=[])
        last = _metrics(queues=[QueueMetrics(name="new-queue", depth=5)])
        trends = _calculate_trends([first, last])
        assert trends[0].depth_start == 0
        assert trends[0].depth_end == 5


# ---------------------------------------------------------------------------
# _analyze_metrics
# ---------------------------------------------------------------------------


class TestAnalyzeMetrics:
    def test_healthy_system(self, config):
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            alive_workers=2,
            total_workers=2,
            saturation_pct=50.0,
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_HEALTHY

    def test_redis_disconnected_is_critical(self, config):
        metrics = _metrics(redis_connected=False, celery_connected=True)
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_CRITICAL
        assert any("Redis" in c for c in result.criticals)

    def test_no_celery_workers_is_critical(self, config):
        metrics = _metrics(redis_connected=True, celery_connected=False)
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_CRITICAL

    def test_dead_worker_is_critical(self, config):
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            total_workers=2,
            alive_workers=1,
            workers=[
                WorkerMetrics(name="celery@w-1", is_alive=True),
                WorkerMetrics(name="celery@w-2", is_alive=False),
            ],
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_CRITICAL

    def test_worker_at_capacity_is_warning(self, config):
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            total_workers=1,
            alive_workers=1,
            workers=[
                WorkerMetrics(name="celery@w-1", is_alive=True, active_tasks=4, concurrency=4)
            ],
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_WARNING
        assert any("capacity" in w for w in result.warnings)

    def test_congested_queue_is_warning(self, config):
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            alive_workers=1,
            total_workers=1,
            total_concurrency=4,
            queues=[QueueMetrics(name="celery", depth=2000)],
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_WARNING
        assert any("congested" in w for w in result.warnings)

    def test_stuck_task_is_critical(self, config):
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            stuck_tasks=[
                {
                    "task_id": "t1",
                    "task_name": "slow_task",
                    "worker": "celery@w-1",
                    "runtime_seconds": 7200,
                }
            ],
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_CRITICAL
        assert any("stuck" in c for c in result.criticals)

    def test_high_saturation_is_warning(self, config):
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            saturation_pct=95.0,
            total_active_tasks=19,
            total_concurrency=20,
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_WARNING
        assert any("saturation" in w.lower() for w in result.warnings)

    def test_growing_queue_trend_is_warning(self, config):
        first = _metrics(
            redis_connected=True,
            celery_connected=True,
            queues=[QueueMetrics(name="celery", depth=10)],
        )
        last = _metrics(
            redis_connected=True,
            celery_connected=True,
            queues=[QueueMetrics(name="celery", depth=30)],
        )
        result = _analyze_metrics(last, [first, last], config, [])
        assert result.exit_code >= EXIT_WARNING
        assert any("growing" in w for w in result.warnings)

    def test_config_check_warning_adds_recommendation(self, config):
        metrics = _metrics(redis_connected=True, celery_connected=True)
        checks = [
            ConfigCheck(
                name="Redis maxmemory",
                status="warning",
                message="Not set",
                recommendation="CONFIG SET maxmemory 2gb",
            )
        ]
        result = _analyze_metrics(metrics, [metrics], config, checks)
        assert any("maxmemory" in r for r in result.recommendations)

    def test_recommendations_present_for_dead_workers(self, config):
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            total_workers=2,
            alive_workers=1,
            workers=[
                WorkerMetrics(name="celery@w-1", is_alive=True),
                WorkerMetrics(name="celery@w-2", is_alive=False),
            ],
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert len(result.recommendations) > 0

    def test_ghost_workers_detected(self, config):
        """Workers alive but not processing tasks despite a large backlog"""
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            alive_workers=2,
            total_workers=2,
            total_concurrency=8,
            total_active_tasks=1,
            saturation_pct=12.5,  # very low
            total_pending_tasks=500,
            queues=[QueueMetrics(name="celery", depth=500)],
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert result.exit_code == EXIT_CRITICAL
        assert any("Ghost" in c for c in result.criticals)
