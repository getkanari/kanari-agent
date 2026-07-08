"""
Tests for kanari_agent.audit module

Tests analysis logic — no external connections needed.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from kanari_agent.audit import (
    EXIT_CRITICAL,
    EXIT_HEALTHY,
    EXIT_WARNING,
    AuditResult,
    ConfigCheck,
    QueueTrend,
    _analyze_metrics,
    _build_checks_performed,
    _calculate_trends,
    _check_celery_config,
    _check_infrastructure,
    _check_redis_config,
    _is_queue_congested,
    _print_checks_summary,
    _print_md_report,
    _print_report,
    _run_config_checks,
    run_audit,
)
from kanari_agent.models import Config, QueueMetrics, SystemMetrics, WorkerMetrics


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
        first = _metrics(queues=[QueueMetrics(name=k, depth=v) for k, v in depths_start.items()])
        last = _metrics(queues=[QueueMetrics(name=k, depth=v) for k, v in depths_end.items()])
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

    def test_congested_queue_with_latency_in_recommendation(self, config):
        """When a congested queue has known latency, recommendation includes it"""
        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            alive_workers=1,
            total_workers=1,
            total_concurrency=4,
            queues=[QueueMetrics(name="celery", depth=2000, oldest_task_age_seconds=120.0)],
        )
        result = _analyze_metrics(metrics, [metrics], config, [])
        assert any("120" in r for r in result.recommendations)

    def test_config_check_critical_sets_exit_critical(self, config):
        metrics = _metrics(redis_connected=True, celery_connected=True)
        checks = [
            ConfigCheck(
                name="Some check",
                status="critical",
                message="Very bad",
                recommendation="Fix it now",
            )
        ]
        result = _analyze_metrics(metrics, [metrics], config, checks)
        assert result.exit_code == EXIT_CRITICAL


# ---------------------------------------------------------------------------
# _check_redis_config
# ---------------------------------------------------------------------------


def _make_redis_mock(maxmemory="0", policy="noeviction", save="3600 1", connected=12):
    client = MagicMock()

    def config_get(key):
        return {
            "maxmemory": {"maxmemory": maxmemory},
            "maxmemory-policy": {"maxmemory-policy": policy},
            "save": {"save": save},
            "maxclients": {"maxclients": "10000"},
        }.get(key, {})

    def info(section):
        mem = int(maxmemory) if maxmemory != "0" else 0
        return {
            "memory": {"used_memory": mem // 2 if mem else 500_000},
            "clients": {"connected_clients": connected},
        }.get(section, {})

    client.config_get.side_effect = config_get
    client.info.side_effect = info
    return client


class TestCheckRedisConfig:
    def test_no_maxmemory_warns(self):
        checks = _check_redis_config(_make_redis_mock(maxmemory="0"))
        check = next((c for c in checks if c.name == "Redis maxmemory"), None)
        assert check is not None
        assert check.status == "warning"

    def test_maxmemory_set_ok(self):
        checks = _check_redis_config(_make_redis_mock(maxmemory="2147483648"))
        check = next((c for c in checks if "memory" in c.name.lower()), None)
        assert check is not None
        assert check.status == "ok"

    def test_noeviction_policy_warns(self):
        checks = _check_redis_config(_make_redis_mock(policy="noeviction"))
        check = next((c for c in checks if c.name == "Redis eviction policy"), None)
        assert check is not None
        assert check.status == "warning"

    def test_good_eviction_policy_ok(self):
        checks = _check_redis_config(_make_redis_mock(policy="volatile-lru"))
        check = next((c for c in checks if c.name == "Redis eviction policy"), None)
        assert check is not None
        assert check.status == "ok"

    def test_no_persistence_warns(self):
        checks = _check_redis_config(_make_redis_mock(save=""))
        check = next((c for c in checks if c.name == "Redis persistence"), None)
        assert check is not None
        assert check.status == "warning"

    def test_persistence_enabled_ok(self):
        checks = _check_redis_config(_make_redis_mock(save="3600 1"))
        check = next((c for c in checks if c.name == "Redis persistence"), None)
        assert check is not None
        assert check.status == "ok"

    def test_connection_pool_ok(self):
        checks = _check_redis_config(_make_redis_mock(connected=100))
        check = next((c for c in checks if c.name == "Redis connection pool"), None)
        assert check is not None
        assert check.status == "ok"

    def test_connection_pool_high_warns(self):
        checks = _check_redis_config(_make_redis_mock(connected=9500))
        check = next((c for c in checks if c.name == "Redis connection pool"), None)
        assert check is not None
        assert check.status == "warning"

    def test_exception_skipped_gracefully(self):
        bad_client = MagicMock()
        bad_client.config_get.side_effect = Exception("no permission")
        bad_client.info.side_effect = Exception("no permission")
        checks = _check_redis_config(bad_client)
        assert isinstance(checks, list)


# ---------------------------------------------------------------------------
# _check_celery_config
# ---------------------------------------------------------------------------


def _make_celery_mock(acks_late=False, reject_on_lost=False, prefetch=4):
    app = MagicMock()
    inspector = MagicMock()
    inspector.conf.return_value = {
        "celery@worker-1": {
            "task_acks_late": acks_late,
            "task_reject_on_worker_lost": reject_on_lost,
            "worker_prefetch_multiplier": prefetch,
        }
    }
    app.control.inspect.return_value = inspector
    return app


class TestCheckCeleryConfig:
    @pytest.fixture
    def metrics(self):
        return _metrics()

    def test_acks_late_false_warns(self, metrics):
        checks = _check_celery_config(_make_celery_mock(acks_late=False), metrics)
        check = next((c for c in checks if c.name == "Celery task_acks_late"), None)
        assert check is not None
        assert check.status == "warning"

    def test_acks_late_true_ok(self, metrics):
        checks = _check_celery_config(_make_celery_mock(acks_late=True), metrics)
        check = next((c for c in checks if c.name == "Celery task_acks_late"), None)
        assert check is not None
        assert check.status == "ok"

    def test_reject_on_lost_false_warns(self, metrics):
        checks = _check_celery_config(_make_celery_mock(reject_on_lost=False), metrics)
        check = next((c for c in checks if c.name == "Celery task_reject_on_worker_lost"), None)
        assert check is not None
        assert check.status == "warning"

    def test_reject_on_lost_true_ok(self, metrics):
        checks = _check_celery_config(_make_celery_mock(reject_on_lost=True), metrics)
        check = next((c for c in checks if c.name == "Celery task_reject_on_worker_lost"), None)
        assert check is not None
        assert check.status == "ok"

    def test_high_prefetch_warns(self, metrics):
        checks = _check_celery_config(_make_celery_mock(prefetch=4), metrics)
        check = next((c for c in checks if c.name == "Celery prefetch_multiplier"), None)
        assert check is not None
        assert check.status == "warning"

    def test_prefetch_1_ok(self, metrics):
        checks = _check_celery_config(_make_celery_mock(prefetch=1), metrics)
        check = next((c for c in checks if c.name == "Celery prefetch_multiplier"), None)
        assert check is not None
        assert check.status == "ok"

    def test_empty_conf_returns_empty(self, metrics):
        app = MagicMock()
        inspector = MagicMock()
        inspector.conf.return_value = {}
        app.control.inspect.return_value = inspector
        checks = _check_celery_config(app, metrics)
        assert checks == []

    def test_exception_skipped_gracefully(self, metrics):
        app = MagicMock()
        app.control.inspect.side_effect = Exception("broker down")
        checks = _check_celery_config(app, metrics)
        assert isinstance(checks, list)


# ---------------------------------------------------------------------------
# _check_infrastructure
# ---------------------------------------------------------------------------


class TestCheckInfrastructure:
    def test_single_worker_warns(self):
        metrics = _metrics(alive_workers=1, total_workers=1)
        checks = _check_infrastructure(metrics)
        check = next((c for c in checks if c.name == "Worker redundancy"), None)
        assert check is not None
        assert check.status == "warning"

    def test_multiple_workers_ok(self):
        metrics = _metrics(alive_workers=3, total_workers=3)
        checks = _check_infrastructure(metrics)
        check = next((c for c in checks if c.name == "Worker redundancy"), None)
        assert check is not None
        assert check.status == "ok"

    def test_high_backlog_per_slot_warns(self):
        metrics = _metrics(
            alive_workers=1,
            total_workers=1,
            total_concurrency=4,
            total_pending_tasks=1000,
        )
        checks = _check_infrastructure(metrics)
        check = next((c for c in checks if c.name == "Queue backlog ratio"), None)
        assert check is not None
        assert check.status == "warning"


# ---------------------------------------------------------------------------
# _run_config_checks
# ---------------------------------------------------------------------------


class TestRunConfigChecks:
    def test_delegates_to_all_checkers(self):
        collector = MagicMock()
        collector.redis_client = _make_redis_mock()
        collector.celery_app = _make_celery_mock()
        metrics = _metrics(alive_workers=2, total_workers=2)

        checks = _run_config_checks(collector, metrics)
        assert len(checks) > 0
        names = [c.name for c in checks]
        assert any("Redis" in n for n in names)
        assert any("Celery" in n for n in names)

    def test_no_redis_skips_redis_checks(self):
        collector = MagicMock()
        collector.redis_client = None
        collector.celery_app = _make_celery_mock()
        metrics = _metrics()

        checks = _run_config_checks(collector, metrics)
        names = [c.name for c in checks]
        assert not any("Redis" in n for n in names)


# ---------------------------------------------------------------------------
# _print_report
# ---------------------------------------------------------------------------


def _console():
    from rich.console import Console

    return Console(file=io.StringIO(), force_terminal=False)


class TestPrintReport:
    def _call(self, result, metrics=None, samples=1):
        from kanari_agent.models import Config

        console = _console()
        m = metrics or _metrics(redis_connected=True, celery_connected=True)
        cfg = Config()
        _print_report(console, m, result, cfg, samples, 1.5)

    def test_healthy_report_no_error(self):
        result = AuditResult(exit_code=EXIT_HEALTHY)
        self._call(result)  # should not raise

    def test_warning_report_no_error(self):
        result = AuditResult(exit_code=EXIT_WARNING, warnings=["queue congested"])
        self._call(result)

    def test_critical_report_no_error(self):
        result = AuditResult(
            exit_code=EXIT_CRITICAL,
            criticals=["🔥 Possible Ghost Workers: 500 tasks pending but workers are 10% idle"],
        )
        self._call(result)

    def test_report_with_recommendations(self):
        result = AuditResult(
            exit_code=EXIT_WARNING,
            recommendations=["Scale workers for 'celery' queue"],
        )
        self._call(result)

    def test_report_with_trends(self):
        result = AuditResult(
            exit_code=EXIT_WARNING,
            queue_trends=[
                QueueTrend(
                    name="celery", depth_start=10, depth_end=30, depth_delta=20, trend="growing"
                )
            ],
        )
        self._call(result, samples=3)

    def test_report_with_config_checks_renders_table(self):
        result = AuditResult(
            exit_code=EXIT_WARNING,
            config_checks=[
                ConfigCheck(name="Redis maxmemory", status="warning", message="Not set"),
                ConfigCheck(name="Redis eviction", status="ok", message="volatile-lru"),
            ],
        )
        from kanari_agent.models import Config

        console = _console()
        m = _metrics(redis_connected=True, celery_connected=True)
        _print_report(console, m, result, Config(), 1, 1.5)
        output = console.file.getvalue()
        assert "Configuration Analysis" in output
        assert "Redis maxmemory" in output

    def test_report_with_workers_and_queues(self):
        from kanari_agent.models import QueueMetrics, WorkerMetrics

        metrics = _metrics(
            redis_connected=True,
            celery_connected=True,
            total_workers=2,
            alive_workers=1,
            total_concurrency=8,
            total_active_tasks=2,
            saturation_pct=25.0,
            max_latency_sec=15.0,
            workers=[
                WorkerMetrics(name="celery@w-1", is_alive=True, active_tasks=2, concurrency=4),
                WorkerMetrics(name="celery@w-2", is_alive=False, active_tasks=0, concurrency=4),
            ],
            queues=[
                QueueMetrics(name="celery", depth=5, oldest_task_age_seconds=15.0),
                QueueMetrics(name="emails", depth=2000),
            ],
            stuck_tasks=[
                {
                    "task_id": "t1",
                    "task_name": "slow_task",
                    "worker": "celery@w-1",
                    "runtime_seconds": 5400,
                }
            ],
        )
        result = AuditResult(exit_code=EXIT_CRITICAL, criticals=["1 stuck task(s) detected"])
        self._call(result, metrics=metrics)


# ---------------------------------------------------------------------------
# run_audit
# ---------------------------------------------------------------------------


class TestRunAudit:
    def _mock_collector(self, connect_ok=True, metrics=None):
        collector = MagicMock()
        collector.connect.return_value = connect_ok
        if connect_ok and metrics is not None:
            collector.collect.return_value = metrics
        return collector

    def test_connect_failure_returns_critical(self):
        from kanari_agent.models import Config

        config = Config()
        # MetricsCollector and Console are lazy-imported inside run_audit()
        with (
            patch("kanari_agent.collector.MetricsCollector") as mock_cls,
            patch("rich.console.Console", return_value=_console()),
        ):
            mock_cls.return_value = self._mock_collector(connect_ok=False)
            result = run_audit(config)

        assert result == EXIT_CRITICAL

    def test_healthy_system_returns_healthy(self):
        from kanari_agent.models import Config

        config = Config()
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            redis_connected=True,
            celery_connected=True,
            alive_workers=2,
            total_workers=2,
        )
        collector = self._mock_collector(connect_ok=True, metrics=metrics)
        collector.latency_available = False

        with (
            patch("kanari_agent.collector.MetricsCollector") as mock_cls,
            patch("rich.console.Console", return_value=_console()),
        ):
            mock_cls.return_value = collector
            result = run_audit(config)

        assert result == EXIT_HEALTHY

    def test_deep_mode_runs_config_checks(self):
        from kanari_agent.models import Config

        config = Config()
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            redis_connected=True,
            celery_connected=True,
        )
        collector = self._mock_collector(connect_ok=True, metrics=metrics)
        collector.redis_client = None
        collector.celery_app = None
        collector.latency_available = False

        with (
            patch("kanari_agent.collector.MetricsCollector") as mock_cls,
            patch("rich.console.Console", return_value=_console()),
        ):
            mock_cls.return_value = collector
            result = run_audit(config, deep=True)

        assert result in (EXIT_HEALTHY, EXIT_WARNING, EXIT_CRITICAL)

    def _healthy_setup(self):
        """Collector mock for a healthy 2-worker system with no real redis/celery clients."""
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            redis_connected=True,
            celery_connected=True,
            alive_workers=2,
            total_workers=2,
        )
        collector = self._mock_collector(connect_ok=True, metrics=metrics)
        collector.redis_client = None
        collector.celery_app = None
        collector.latency_available = False
        return collector

    def test_config_checks_run_by_default(self):
        from kanari_agent.models import Config

        collector = self._healthy_setup()
        with (
            patch("kanari_agent.collector.MetricsCollector") as mock_cls,
            patch("rich.console.Console", return_value=_console()),
            patch("kanari_agent.audit._run_config_checks", return_value=[]) as mock_checks,
        ):
            mock_cls.return_value = collector
            run_audit(Config())

        mock_checks.assert_called_once()

    def test_no_config_checks_skips(self):
        from kanari_agent.models import Config

        collector = self._healthy_setup()
        with (
            patch("kanari_agent.collector.MetricsCollector") as mock_cls,
            patch("rich.console.Console", return_value=_console()),
            patch("kanari_agent.audit._run_config_checks", return_value=[]) as mock_checks,
        ):
            mock_cls.return_value = collector
            run_audit(Config(), config_checks=False)

        mock_checks.assert_not_called()

    def test_config_warnings_do_not_change_exit_code(self):
        """Exit-code contract lock: config-check warnings never escalate the exit code."""
        from kanari_agent.models import Config

        warning_check = ConfigCheck(
            name="Redis maxmemory",
            status="warning",
            message="Not set (risk of OOM)",
            recommendation="CONFIG SET maxmemory 2gb",
        )
        collector = self._healthy_setup()
        with (
            patch("kanari_agent.collector.MetricsCollector") as mock_cls,
            patch("rich.console.Console", return_value=_console()),
            patch("kanari_agent.audit._run_config_checks", return_value=[warning_check]),
        ):
            mock_cls.return_value = collector
            result = run_audit(Config())

        assert result == EXIT_HEALTHY


# ---------------------------------------------------------------------------
# _build_checks_performed / _print_checks_summary
# ---------------------------------------------------------------------------


class TestBuildChecksPerformed:
    def test_combines_findings_families_and_config_checks(self):
        from kanari_agent.findings import CHECK_FAMILIES

        checks = [
            ConfigCheck(name="Redis maxmemory", status="warning", message="Not set"),
            ConfigCheck(name="Celery task_acks_late", status="ok", message="True (safe)"),
        ]
        summary = _build_checks_performed([], checks)

        assert len(summary) == len(CHECK_FAMILIES) + 2
        names = [entry["name"] for entry in summary]
        assert "redis connectivity" in names
        assert "Redis maxmemory" in names
        assert "Celery task_acks_late" in names

    def test_skipped_config_checks_never_appear(self):
        from kanari_agent.findings import CHECK_FAMILIES

        summary = _build_checks_performed([], [])
        assert len(summary) == len(CHECK_FAMILIES)


class TestPrintChecksSummary:
    def _summary_output(self, findings, config_checks):
        console = _console()
        checks_summary = _build_checks_performed(findings, config_checks)
        _print_checks_summary(console, findings, config_checks, checks_summary)
        return console.file.getvalue()

    def test_healthy_run_shows_checks_passed(self):
        checks = [ConfigCheck(name="Redis eviction policy", status="ok", message="volatile-lru")]
        output = self._summary_output([], checks)
        assert "checks passed" in output
        assert "redis connectivity" in output
        assert "Redis eviction policy" in output

    def test_summary_absent_when_findings_exist(self):
        from kanari_agent.findings import Finding, Severity

        findings = [
            Finding(id="NO_WORKERS", severity=Severity.CRITICAL, title="", impact="", evidence={})
        ]
        output = self._summary_output(findings, [])
        assert output == ""

    def test_summary_absent_when_config_warning_exists(self):
        checks = [ConfigCheck(name="Redis maxmemory", status="warning", message="Not set")]
        output = self._summary_output([], checks)
        assert output == ""

    def test_summary_notes_skipped_config_analysis(self):
        output = self._summary_output([], [])
        assert "checks passed" in output
        assert "config analysis skipped" in output


# ---------------------------------------------------------------------------
# _print_md_report — Checks Performed section
# ---------------------------------------------------------------------------


class TestMdChecksPerformed:
    def test_md_includes_checks_performed_section(self, capsys):
        from kanari_agent.findings import SystemStatus

        checks = [
            {"name": "redis connectivity", "status": "ok"},
            {"name": "Celery task_acks_late", "status": "warning"},
        ]
        _print_md_report(_metrics(), [], SystemStatus.OK, Config(), 1.0, checks)

        output = capsys.readouterr().out
        assert "## Checks Performed" in output
        assert "- ✓ redis connectivity" in output
        assert "- ✗ Celery task_acks_late" in output

    def test_md_omits_section_when_no_checks(self, capsys):
        from kanari_agent.findings import SystemStatus

        _print_md_report(_metrics(), [], SystemStatus.OK, Config(), 1.0, None)
        assert "## Checks Performed" not in capsys.readouterr().out
