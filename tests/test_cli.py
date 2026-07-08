"""
Tests for kanari_agent.cli module (subcommand-based CLI)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanari_agent.models import Config, SystemMetrics


def _run_main(argv: list[str]):
    """Helper to run cli.main() with given arguments"""
    from kanari_agent.cli import main

    with patch("sys.argv", ["kanari"] + argv):
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

    def test_no_subcommand_shows_quick_start(self, capsys):
        with pytest.raises(SystemExit):
            _run_main([])
        out = capsys.readouterr().out
        assert "kanari init" in out
        assert "kanari doctor" in out
        assert "kanari audit" in out
        assert "kanari agent --local" in out

    def test_no_subcommand_shows_version(self, capsys):
        with pytest.raises(SystemExit):
            _run_main([])
        out = capsys.readouterr().out
        assert "Kanari" in out

    def test_version_flag_exits(self):
        with pytest.raises(SystemExit) as exc:
            _run_main(["--version"])
        # argparse exits with 0 for --version
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# kanari audit
# ---------------------------------------------------------------------------


class TestCliAudit:
    def test_audit_calls_cmd_audit_and_exits(self):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.cli.cmd_audit") as mock_cmd,
        ):
            mock_cmd.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                _run_main(["audit"])
        mock_cmd.assert_called_once()

    def test_audit_with_json_flag(self):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["audit", "--json"])
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs.get("json_output") is True

    def test_audit_deep_flag_is_accepted_and_noop(self, capsys):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["audit", "--deep"])
        call_kwargs = mock_audit.call_args[1]
        # --deep no longer forwards; config checks run by default
        assert "deep" not in call_kwargs
        assert call_kwargs.get("config_checks") is True
        assert "deprecated" in capsys.readouterr().err

    def test_audit_no_config_checks_flag(self):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["audit", "--no-config-checks"])
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs.get("config_checks") is False

    def test_audit_config_checks_default_on_without_deprecation_notice(self, capsys):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.audit.run_audit", return_value=0) as mock_audit,
        ):
            with pytest.raises(SystemExit):
                _run_main(["audit"])
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs.get("config_checks") is True
        assert "deprecated" not in capsys.readouterr().err

    def test_audit_exit_code_propagated(self):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.audit.run_audit", return_value=2),
        ):
            with pytest.raises(SystemExit) as exc:
                _run_main(["audit"])
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# kanari watch
# ---------------------------------------------------------------------------


class TestCliWatch:
    def test_watch_calls_cmd_watch(self):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.cli.cmd_watch") as mock_cmd,
        ):
            _run_main(["watch"])
        mock_cmd.assert_called_once()

    def test_watch_with_interval(self):
        cfg = Config()
        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.audit.run_watch") as mock_watch,
        ):
            _run_main(["watch", "--interval", "10"])
        mock_watch.assert_called_once()
        call_kwargs = mock_watch.call_args[1]
        assert call_kwargs.get("interval") == 10


# ---------------------------------------------------------------------------
# kanari agent
# ---------------------------------------------------------------------------


class TestCliAgent:
    def test_agent_local_mode_calls_run(self):
        cfg = Config(local_mode=True)
        mock_agent = MagicMock()

        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.agent.KanariAgent", return_value=mock_agent),
        ):
            _run_main(["agent", "--local"])

        mock_agent.run.assert_called_once()

    def test_agent_connect_failure_exits(self):
        # Connection error handling lives in agent.run(); cmd_agent delegates entirely to run().
        cfg = Config(local_mode=True)
        mock_agent = MagicMock()
        mock_agent.run.side_effect = SystemExit(1)

        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.agent.KanariAgent", return_value=mock_agent),
        ):
            with pytest.raises(SystemExit) as exc:
                _run_main(["agent", "--local"])

        mock_agent.run.assert_called_once()
        assert exc.value.code == 1

    def test_agent_with_token(self):
        cfg = Config()
        mock_agent = MagicMock()

        with (
            patch("kanari_agent.config.load_config", return_value=cfg),
            patch("kanari_agent.agent.KanariAgent", return_value=mock_agent),
        ):
            _run_main(["agent", "--token", "my-secret-key", "--local"])

        mock_agent.run.assert_called_once()


# ---------------------------------------------------------------------------
# kanari init
# ---------------------------------------------------------------------------


class TestCmdInit:
    def test_creates_config_yaml_in_tmp(self, tmp_path: Path):
        output = tmp_path / "config.yaml"
        _run_main(["init", "--output", str(output)])
        assert output.exists()

    def test_output_contains_required_keys(self, tmp_path: Path):
        output = tmp_path / "config.yaml"
        _run_main(["init", "--output", str(output)])
        content = output.read_text()
        for key in ("redis_url", "celery_broker_url", "celery_app_name", "thresholds"):
            assert key in content

    def test_output_is_valid_yaml(self, tmp_path: Path):
        import yaml

        output = tmp_path / "config.yaml"
        _run_main(["init", "--output", str(output)])
        data = yaml.safe_load(output.read_text())
        assert isinstance(data, dict)
        assert "redis_url" in data

    def test_exits_1_if_file_exists_without_force(self, tmp_path: Path):
        output = tmp_path / "config.yaml"
        output.write_text("existing content")
        with pytest.raises(SystemExit) as exc:
            _run_main(["init", "--output", str(output)])
        assert exc.value.code == 1
        assert output.read_text() == "existing content"

    def test_force_overwrites_existing_file(self, tmp_path: Path):
        output = tmp_path / "config.yaml"
        output.write_text("old content")
        _run_main(["init", "--output", str(output), "--force"])
        assert output.read_text() != "old content"
        assert "redis_url" in output.read_text()

    def test_default_output_filename(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _run_main(["init"])
        assert (tmp_path / "config.yaml").exists()

    def test_prints_next_step_hint(self, tmp_path: Path, capsys):
        output = tmp_path / "config.yaml"
        _run_main(["init", "--output", str(output)])
        out = capsys.readouterr().out
        assert "audit" in out
        assert str(output) in out
