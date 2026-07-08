"""
Tests for kanari_agent.agent module
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kanari_agent.agent import KanariAgent
from kanari_agent.models import Config, QueueMetrics, SystemMetrics, WorkerMetrics


@pytest.fixture
def local_config() -> Config:
    return Config(local_mode=True)


@pytest.fixture
def api_config() -> Config:
    return Config(local_mode=False, api_key="test-api-key")


@pytest.fixture
def minimal_metrics() -> SystemMetrics:
    return SystemMetrics(
        timestamp="2026-01-01T00:00:00+00:00",
        redis_connected=True,
        celery_connected=True,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestKanariAgentInit:
    def test_local_mode_no_api_client(self, local_config):
        agent = KanariAgent(local_config)
        assert agent.api_client is None

    def test_api_mode_with_key_creates_client(self, api_config):
        agent = KanariAgent(api_config)
        assert agent.api_client is not None

    def test_api_mode_without_key_no_client(self):
        config = Config(local_mode=False, api_key=None)
        agent = KanariAgent(config)
        assert agent.api_client is None

    def test_initial_failure_count_is_zero(self, local_config):
        agent = KanariAgent(local_config)
        assert agent._consecutive_failures == 0


# ---------------------------------------------------------------------------
# check_once — local mode
# ---------------------------------------------------------------------------


class TestCheckOnceLocalMode:
    def test_returns_system_metrics(self, local_config, minimal_metrics):
        agent = KanariAgent(local_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)

        result = agent.check_once()
        assert result is minimal_metrics

    def test_does_not_call_api_in_local_mode(self, local_config, minimal_metrics):
        agent = KanariAgent(local_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)

        # Ensure no api_client is accidentally called
        assert agent.api_client is None
        agent.check_once()  # should not raise

    def test_collector_is_called_once(self, local_config, minimal_metrics):
        agent = KanariAgent(local_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)

        agent.check_once()
        agent.collector.collect.assert_called_once()


# ---------------------------------------------------------------------------
# check_once — API mode
# ---------------------------------------------------------------------------


class TestCheckOnceApiMode:
    def test_sends_metrics_to_api_on_success(self, api_config, minimal_metrics):
        agent = KanariAgent(api_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=True)

        agent.check_once()
        agent.api_client.send_metrics.assert_called_once_with(minimal_metrics)

    def test_resets_failure_count_on_success(self, api_config, minimal_metrics):
        agent = KanariAgent(api_config)
        agent._consecutive_failures = 5
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=True)

        agent.check_once()
        assert agent._consecutive_failures == 0

    def test_increments_failure_count_on_api_error(self, api_config, minimal_metrics):
        agent = KanariAgent(api_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=False)

        agent.check_once()
        assert agent._consecutive_failures == 1

    def test_check_once_no_api_client_logs_locally(self, minimal_metrics):
        """API mode without api_client logs locally instead of sending"""
        config = Config(local_mode=False, api_key=None)
        agent = KanariAgent(config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.logger.info = MagicMock()

        agent.check_once()

        # Should have called logger.info with a payload (local log)
        calls = [str(c) for c in agent.logger.info.call_args_list]
        assert any("metrics_collected" in c for c in calls)

    def test_consecutive_failures_at_limit_logs_error(self, api_config, minimal_metrics):
        """After max consecutive failures, an error is logged"""
        agent = KanariAgent(api_config)
        agent._consecutive_failures = 9  # one below max (10)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=False)
        agent.logger.error = MagicMock()

        agent.check_once()

        # Now at 10 failures — error should be logged
        error_calls = [str(c) for c in agent.logger.error.call_args_list]
        assert any("consecutive" in c.lower() for c in error_calls)

    def test_multiple_failures_accumulate(self, api_config, minimal_metrics):
        agent = KanariAgent(api_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=False)

        for _ in range(3):
            agent.check_once()

        assert agent._consecutive_failures == 3


# ---------------------------------------------------------------------------
# _log_metrics_locally
# ---------------------------------------------------------------------------


class TestLogMetricsLocally:
    def test_uses_api_client_payload_when_available(self, api_config, minimal_metrics):
        agent = KanariAgent(api_config)
        agent.api_client.build_payload = MagicMock(return_value={"mocked": True})

        # Should not raise
        agent._log_metrics_locally(minimal_metrics)
        agent.api_client.build_payload.assert_called_once_with(minimal_metrics)

    def test_builds_basic_payload_without_api_client(self, local_config):
        agent = KanariAgent(local_config)
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            total_pending_tasks=10,
            total_active_tasks=2,
            total_workers=1,
            alive_workers=1,
            saturation_pct=50.0,
            redis_connected=True,
            celery_connected=True,
            queues=[QueueMetrics(name="celery", depth=10)],
            workers=[WorkerMetrics(name="celery@w-1", active_tasks=2, concurrency=4)],
        )

        # Patch logger to capture the call
        agent.logger.info = MagicMock()
        agent._log_metrics_locally(metrics)

        agent.logger.info.assert_called_once()
        call_kwargs = agent.logger.info.call_args
        payload = call_kwargs[1]["payload"]

        assert payload["metrics"]["total_pending"] == 10
        assert payload["metrics"]["total_active"] == 2
        assert len(payload["queues"]) == 1
        assert payload["queues"][0]["name"] == "celery"


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestRun:
    def _make_agent(self, config):
        agent = KanariAgent(config)
        agent.collector.connect = MagicMock(return_value=True)
        agent.collector.get_queues_to_monitor = MagicMock(return_value=["celery"])
        return agent

    def test_run_local_mode_executes_one_cycle_then_stops(self, local_config, minimal_metrics):
        agent = self._make_agent(local_config)

        def stop_after_first(*args, **kwargs):
            agent.running = False
            return minimal_metrics

        agent.check_once = MagicMock(side_effect=stop_after_first)

        with patch("time.sleep"):
            agent.run()

        agent.check_once.assert_called_once()

    def test_run_exits_if_connect_fails(self, local_config):
        agent = KanariAgent(local_config)
        agent.collector.connect = MagicMock(return_value=False)

        with pytest.raises(SystemExit) as exc_info:
            agent.run()
        assert exc_info.value.code == 1

    def test_run_api_mode_validates_key_and_exits_on_failure(self, api_config):
        agent = self._make_agent(api_config)
        agent.api_client.validate_api_key = MagicMock(return_value=False)

        with pytest.raises(SystemExit) as exc_info:
            agent.run()
        assert exc_info.value.code == 1

    def test_run_api_mode_validates_key_success(self, api_config, minimal_metrics):
        agent = self._make_agent(api_config)
        agent.api_client.validate_api_key = MagicMock(return_value=True)

        def stop_after_first(*args, **kwargs):
            agent.running = False
            return minimal_metrics

        agent.check_once = MagicMock(side_effect=stop_after_first)

        with patch("time.sleep"):
            agent.run()

        agent.api_client.validate_api_key.assert_called_once()

    def test_run_handles_exception_in_check_cycle(self, local_config):
        agent = self._make_agent(local_config)
        call_count = 0

        def raise_then_stop(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            agent.running = False

        agent.check_once = MagicMock(side_effect=raise_then_stop)
        agent.logger.error = MagicMock()

        with patch("time.sleep"):
            agent.run()

        # Should have continued after the exception
        assert call_count == 2
        error_calls = [str(c) for c in agent.logger.error.call_args_list]
        assert any("Error in check cycle" in c for c in error_calls)


# ---------------------------------------------------------------------------
# startup_audit
# ---------------------------------------------------------------------------


class TestStartupAudit:
    def _make_agent(self, config, metrics=None):
        agent = KanariAgent(config)
        agent.collector.connect = MagicMock(return_value=True)
        agent.collector.get_queues_to_monitor = MagicMock(return_value=["celery"])
        if metrics is not None:
            agent.collector.collect = MagicMock(return_value=metrics)
            agent.collector.latency_available = False
            agent.collector.redis_client = None
            agent.collector.celery_app = None
        return agent

    def test_run_performs_startup_audit_once_before_first_cycle(
        self, local_config, minimal_metrics
    ):
        agent = self._make_agent(local_config)
        call_order = []

        def record_audit(*args, **kwargs):
            call_order.append("startup_audit")

        def stop_after_first(*args, **kwargs):
            call_order.append("check_once")
            agent.running = False
            return minimal_metrics

        agent.startup_audit = MagicMock(side_effect=record_audit)
        agent.check_once = MagicMock(side_effect=stop_after_first)

        with patch("time.sleep"):
            agent.run()

        agent.startup_audit.assert_called_once()
        assert call_order[0] == "startup_audit"

    def test_startup_audit_logs_structured_event(self, local_config, minimal_metrics):
        agent = self._make_agent(local_config, metrics=minimal_metrics)
        agent.logger.info = MagicMock()

        agent.startup_audit()

        startup_calls = [
            c for c in agent.logger.info.call_args_list if c[0] and c[0][0] == "startup_audit"
        ]
        assert len(startup_calls) == 1
        kwargs = startup_calls[0][1]
        assert "system_status" in kwargs
        assert "findings" in kwargs
        assert "config_checks" in kwargs
        assert "checks_passed" in kwargs

    def test_startup_audit_warns_per_finding_and_smell(self, local_config):
        degraded = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            redis_connected=True,
            celery_connected=False,  # NO_WORKERS finding
        )
        agent = self._make_agent(local_config, metrics=degraded)
        agent.logger.warning = MagicMock()

        agent.startup_audit()

        warning_msgs = [str(c[0][0]) for c in agent.logger.warning.call_args_list]
        assert any("[CRITICAL]" in msg for msg in warning_msgs)

    def test_startup_audit_failure_does_not_crash(self, local_config):
        agent = self._make_agent(local_config)
        agent.collector.collect = MagicMock(side_effect=RuntimeError("redis exploded"))
        agent.logger.error = MagicMock()

        agent.startup_audit()  # must not raise

        assert any("startup_audit_failed" in str(c) for c in agent.logger.error.call_args_list)

    def test_startup_audit_never_sends_to_api(self, api_config, minimal_metrics):
        agent = self._make_agent(api_config, metrics=minimal_metrics)
        agent.api_client.send_metrics = MagicMock()

        agent.startup_audit()

        agent.api_client.send_metrics.assert_not_called()
