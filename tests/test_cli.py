"""
Tests for doorman_agent.cli module (subcommand-based CLI)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from doorman_agent.models import Config, SystemMetrics


def _run_main(argv: list[str]):
    """Helper to run cli.main() with given arguments"""
    from doorman_agent.cli import main

    with patch("sys.argv", ["doorman"] + argv):
        main()


def _minimal_metrics():
    return SystemMetrics(
        timestamp="2026-01-01T00:00:00+00:00",
        redis_connected=True,
        celery_connected=True,
    )


# ---------------------------------------------------------------------------
# No subcommand → help + exit 0
# ---------------------------------------------------------------------------


class TestNoSubcommand:
    def test_no_subcommand_exits_zero(self):
        with pytest.raises(SystemExit) as exc:
            _run_main([])
        assert exc.value.code == 0

    def test_version_flag_exits(self):
        with pytest.raises(SystemExit) as exc:
            _run_main(["--version"])
        # argparse exits with 0 for --version
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# doorman audit
# ---------------------------------------------------------------------------


class TestCliAudit:
    def test_audit_calls_cmd_audit_and_exits(self):
        cfg = Config()
        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.cli.cmd_audit") as mock_cmd,
        ):
            mock_cmd.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                _run_main(["audit"])
        mock_cmd.assert_called_once()

    def test_audit_with_json_flag(self):
        cfg = Config()
        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["audit", "--json"])
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs.get("json_output") is True

    def test_audit_with_deep_flag(self):
        cfg = Config()
        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["audit", "--deep"])
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs.get("deep") is True

    def test_audit_exit_code_propagated(self):
        cfg = Config()
        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.audit.run_audit", return_value=2),
        ):
            with pytest.raises(SystemExit) as exc:
                _run_main(["audit"])
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# doorman watch
# ---------------------------------------------------------------------------


class TestCliWatch:
    def test_watch_calls_cmd_watch(self):
        cfg = Config()
        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.cli.cmd_watch") as mock_cmd,
        ):
            _run_main(["watch"])
        mock_cmd.assert_called_once()

    def test_watch_with_interval(self):
        cfg = Config()
        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.audit.run_watch") as mock_watch,
        ):
            _run_main(["watch", "--interval", "10"])
        mock_watch.assert_called_once()
        call_kwargs = mock_watch.call_args[1]
        assert call_kwargs.get("interval") == 10


# ---------------------------------------------------------------------------
# doorman agent
# ---------------------------------------------------------------------------


class TestCliAgent:
    def test_agent_local_mode_calls_run(self):
        cfg = Config(local_mode=True)
        mock_agent = MagicMock()
        mock_agent.collector.connect.return_value = True

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.agent.DoormanAgent", return_value=mock_agent),
        ):
            _run_main(["agent", "--local"])

        mock_agent.run.assert_called_once()

    def test_agent_connect_failure_exits(self):
        cfg = Config(local_mode=True)
        mock_agent = MagicMock()
        mock_agent.collector.connect.return_value = False

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.agent.DoormanAgent", return_value=mock_agent),
        ):
            with pytest.raises(SystemExit) as exc:
                _run_main(["agent", "--local"])
        assert exc.value.code == 1

    def test_agent_with_token(self):
        cfg = Config()
        mock_agent = MagicMock()
        mock_agent.collector.connect.return_value = True

        with (
            patch("doorman_agent.config.load_config", return_value=cfg),
            patch("doorman_agent.agent.DoormanAgent", return_value=mock_agent),
        ):
            _run_main(["agent", "--token", "my-secret-key", "--local"])

        mock_agent.run.assert_called_once()
