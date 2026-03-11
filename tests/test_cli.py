"""
Tests for doorman_agent.cli module
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from doorman_agent.models import Config, SystemMetrics


def _run_main(argv: list[str]):
    """Helper to run cli.main() with given arguments"""
    from doorman_agent.cli import main

    with patch("sys.argv", ["doorman-agent"] + argv):
        main()


def _minimal_metrics():
    return SystemMetrics(
        timestamp="2026-01-01T00:00:00+00:00",
        redis_connected=True,
        celery_connected=True,
    )


# cli.py uses lazy imports inside main(), so we patch at the source modules.


# ---------------------------------------------------------------------------
# --local --once
# ---------------------------------------------------------------------------


class TestCliLocalOnce:
    def test_local_once_runs_successfully(self):
        cfg = Config(local_mode=True)
        mock_agent = MagicMock()
        mock_agent.collector.connect.return_value = True
        mock_agent.check_once.return_value = _minimal_metrics()

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.agent.DoormanAgent", return_value=mock_agent),
        ):
            _run_main(["--local", "--once"])

        mock_agent.check_once.assert_called_once()

    def test_local_once_exits_if_connect_fails(self):
        cfg = Config(local_mode=True)
        mock_agent = MagicMock()
        mock_agent.collector.connect.return_value = False

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.agent.DoormanAgent", return_value=mock_agent),
        ):
            with pytest.raises(SystemExit) as exc:
                _run_main(["--local", "--once"])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# no API key
# ---------------------------------------------------------------------------


class TestCliNoApiKey:
    def test_exits_without_api_key_in_api_mode(self, capsys):
        cfg = Config(local_mode=False, api_key=None)

        with patch("doorman_agent.config.load_config", return_value=cfg):
            with pytest.raises(SystemExit) as exc:
                _run_main([])
        assert exc.value.code == 1

        captured = capsys.readouterr()
        assert "API key" in captured.out


# ---------------------------------------------------------------------------
# --audit
# ---------------------------------------------------------------------------


class TestCliAudit:
    def test_audit_mode_calls_run_audit_and_exits(self):
        cfg = Config()

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit) as exc:
                _run_main(["--audit", "--local"])
        assert exc.value.code == 0
        mock_audit.assert_called_once()

    def test_audit_deep_flag_passed(self):
        cfg = Config()

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.audit.run_audit", return_value=1) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["--audit", "--deep", "--local"])

        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs.get("deep") is True

    def test_config_check_is_alias_for_deep(self):
        cfg = Config()

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["--audit", "--config-check", "--local"])

        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs.get("deep") is True


# ---------------------------------------------------------------------------
# --simulate
# ---------------------------------------------------------------------------


class TestCliSimulate:
    def test_simulate_mode_calls_run_simulation(self):
        with patch("doorman_agent.simulator.run_simulation") as mock_sim:
            _run_main(["--simulate", "--workers", "2", "--enqueue", "5"])
            mock_sim.assert_called_once_with(2, 5)


# ---------------------------------------------------------------------------
# daemon mode (no --once)
# ---------------------------------------------------------------------------


class TestCliDaemonMode:
    def test_daemon_mode_calls_agent_run(self):
        cfg = Config(local_mode=True)
        mock_agent = MagicMock()

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.agent.DoormanAgent", return_value=mock_agent),
        ):
            _run_main(["--local"])

        mock_agent.run.assert_called_once()
