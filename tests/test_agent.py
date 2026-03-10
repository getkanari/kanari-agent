"""
Tests for doorman_agent.agent module
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from doorman_agent.agent import DoormanAgent
from doorman_agent.models import Config, QueueMetrics, SystemMetrics, WorkerMetrics


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


class TestDoormanAgentInit:
    def test_local_mode_no_api_client(self, local_config):
        agent = DoormanAgent(local_config)
        assert agent.api_client is None

    def test_api_mode_with_key_creates_client(self, api_config):
        agent = DoormanAgent(api_config)
        assert agent.api_client is not None

    def test_api_mode_without_key_no_client(self):
        config = Config(local_mode=False, api_key=None)
        agent = DoormanAgent(config)
        assert agent.api_client is None

    def test_initial_failure_count_is_zero(self, local_config):
        agent = DoormanAgent(local_config)
        assert agent._consecutive_failures == 0


# ---------------------------------------------------------------------------
# check_once — local mode
# ---------------------------------------------------------------------------


class TestCheckOnceLocalMode:
    def test_returns_system_metrics(self, local_config, minimal_metrics):
        agent = DoormanAgent(local_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)

        result = agent.check_once()
        assert result is minimal_metrics

    def test_does_not_call_api_in_local_mode(self, local_config, minimal_metrics):
        agent = DoormanAgent(local_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)

        # Ensure no api_client is accidentally called
        assert agent.api_client is None
        agent.check_once()  # should not raise

    def test_collector_is_called_once(self, local_config, minimal_metrics):
        agent = DoormanAgent(local_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)

        agent.check_once()
        agent.collector.collect.assert_called_once()


# ---------------------------------------------------------------------------
# check_once — API mode
# ---------------------------------------------------------------------------


class TestCheckOnceApiMode:
    def test_sends_metrics_to_api_on_success(self, api_config, minimal_metrics):
        agent = DoormanAgent(api_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=True)

        agent.check_once()
        agent.api_client.send_metrics.assert_called_once_with(minimal_metrics)

    def test_resets_failure_count_on_success(self, api_config, minimal_metrics):
        agent = DoormanAgent(api_config)
        agent._consecutive_failures = 5
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=True)

        agent.check_once()
        assert agent._consecutive_failures == 0

    def test_increments_failure_count_on_api_error(self, api_config, minimal_metrics):
        agent = DoormanAgent(api_config)
        agent.collector.collect = MagicMock(return_value=minimal_metrics)
        agent.api_client.send_metrics = MagicMock(return_value=False)

        agent.check_once()
        assert agent._consecutive_failures == 1

    def test_multiple_failures_accumulate(self, api_config, minimal_metrics):
        agent = DoormanAgent(api_config)
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
        agent = DoormanAgent(api_config)
        agent.api_client.build_payload = MagicMock(return_value={"mocked": True})

        # Should not raise
        agent._log_metrics_locally(minimal_metrics)
        agent.api_client.build_payload.assert_called_once_with(minimal_metrics)

    def test_builds_basic_payload_without_api_client(self, local_config):
        agent = DoormanAgent(local_config)
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
