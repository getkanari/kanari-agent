"""
Tests for kanari_agent.doctor — all 8 checks and run_doctor() orchestration.
"""

from __future__ import annotations

import sys
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, patch

from kanari_agent.doctor import (
    Status,
    _check_api_key,
    _check_celery_lib,
    _check_celery_workers,
    _check_config_file,
    _check_python,
    _check_redis,
    _check_redis_lib,
    _check_yaml_lib,
    run_doctor,
)

# ── _check_python ─────────────────────────────────────────────────────────────


class TestCheckPython:
    def test_ok_on_supported_version(self):
        result = _check_python()
        # We're running on 3.9+, so it must pass
        assert result.status == Status.OK
        assert str(sys.version_info.major) in result.detail

    def test_fail_on_old_version(self):
        _VersionInfo = namedtuple(
            "version_info", ["major", "minor", "micro", "releaselevel", "serial"]
        )
        fake_vi = _VersionInfo(3, 8, 0, "final", 0)
        with patch.object(sys, "version_info", fake_vi):
            result = _check_python()
        assert result.status == Status.FAIL
        assert result.fix is not None


# ── _check_redis_lib ──────────────────────────────────────────────────────────


class TestCheckRedisLib:
    def test_ok_when_installed(self):
        result = _check_redis_lib()
        assert result.status == Status.OK

    def test_fail_when_not_installed(self):
        with patch.dict("sys.modules", {"redis": None}):
            result = _check_redis_lib()
        assert result.status == Status.FAIL
        assert "pip install redis" in (result.fix or "")


# ── _check_celery_lib ─────────────────────────────────────────────────────────


class TestCheckCeleryLib:
    def test_ok_when_installed(self):
        result = _check_celery_lib()
        assert result.status == Status.OK

    def test_fail_when_not_installed(self):
        with patch.dict("sys.modules", {"celery": None}):
            result = _check_celery_lib()
        assert result.status == Status.FAIL
        assert "pip install celery" in (result.fix or "")


# ── _check_yaml_lib ───────────────────────────────────────────────────────────


class TestCheckYamlLib:
    def test_ok_when_installed(self):
        result = _check_yaml_lib()
        assert result.status == Status.OK

    def test_warn_when_not_installed(self):
        with patch.dict("sys.modules", {"yaml": None}):
            result = _check_yaml_lib()
        assert result.status == Status.WARN
        assert "pip install pyyaml" in (result.fix or "")


# ── _check_config_file ────────────────────────────────────────────────────────


class TestCheckConfigFile:
    def test_info_when_not_specified(self):
        result = _check_config_file(None)
        assert result.status == Status.INFO

    def test_fail_when_file_not_found(self):
        result = _check_config_file("/nonexistent/path/config.yaml")
        assert result.status == Status.FAIL
        assert result.fix is not None

    def test_ok_when_file_valid(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("redis_url: redis://localhost:6379/0\n")
        with patch("kanari_agent.config.load_config"):
            result = _check_config_file(str(cfg))
        assert result.status == Status.OK
        assert str(cfg) in result.detail

    def test_fail_when_file_parse_error(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("bad: [unclosed\n")
        with patch("kanari_agent.config.load_config", side_effect=ValueError("bad yaml")):
            result = _check_config_file(str(cfg))
        assert result.status == Status.FAIL
        assert result.fix is not None


# ── _check_redis ──────────────────────────────────────────────────────────────


class TestCheckRedis:
    def test_ok_when_connected(self):
        mock_client = MagicMock()
        with patch("redis.from_url", return_value=mock_client):
            result = _check_redis("redis://localhost:6379/0")
        assert result.status == Status.OK
        mock_client.ping.assert_called_once()
        mock_client.close.assert_called_once()

    def test_fail_when_connection_refused(self):
        with patch("redis.from_url", side_effect=Exception("Connection refused")):
            result = _check_redis("redis://localhost:6379/0")
        assert result.status == Status.FAIL
        assert result.fix is not None

    def test_fail_when_redis_not_installed(self):
        with patch.dict("sys.modules", {"redis": None}):
            result = _check_redis("redis://localhost:6379/0")
        assert result.status == Status.FAIL

    def test_redacts_password_in_label(self):
        mock_client = MagicMock()
        with patch("redis.from_url", return_value=mock_client):
            result = _check_redis("redis://:mysecret@localhost:6379/0")
        assert "mysecret" not in result.label
        assert "***" in result.label


# ── _check_celery_workers ─────────────────────────────────────────────────────


class TestCheckCeleryWorkers:
    def _make_app(self, ping_return):
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.return_value = ping_return
        return mock_app

    def test_ok_when_workers_found(self):
        mock_app = self._make_app({"worker1": {"ok": "pong"}, "worker2": {"ok": "pong"}})
        with patch("celery.Celery", return_value=mock_app):
            result = _check_celery_workers("redis://localhost:6379/0", "tasks")
        assert result.status == Status.OK
        assert "2" in result.detail

    def test_warn_when_no_workers(self):
        mock_app = self._make_app(None)
        with patch("celery.Celery", return_value=mock_app):
            result = _check_celery_workers("redis://localhost:6379/0", "tasks")
        assert result.status == Status.WARN
        assert result.fix is not None

    def test_fail_when_broker_unreachable(self):
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.side_effect = Exception("timeout")
        with patch("celery.Celery", return_value=mock_app):
            result = _check_celery_workers("redis://localhost:6379/0", "tasks")
        assert result.status == Status.FAIL
        assert result.fix is not None

    def test_fail_when_celery_not_installed(self):
        with patch.dict("sys.modules", {"celery": None}):
            result = _check_celery_workers("redis://localhost:6379/0", "tasks")
        assert result.status == Status.FAIL

    def test_config_from_object_error_is_swallowed(self):
        mock_app = MagicMock()
        mock_app.config_from_object.side_effect = Exception("unknown key")
        mock_app.control.inspect.return_value.ping.return_value = {"w1": {}}
        with patch("celery.Celery", return_value=mock_app):
            result = _check_celery_workers("redis://localhost:6379/0", "tasks")
        assert result.status == Status.OK


# ── _check_api_key ────────────────────────────────────────────────────────────


class TestCheckApiKey:
    def test_info_when_not_set(self):
        result = _check_api_key(None)
        assert result.status == Status.INFO
        assert result.fix is not None

    def test_ok_when_valid_format(self):
        result = _check_api_key("sk_" + "x" * 20)  # pragma: allowlist secret
        assert result.status == Status.OK

    def test_warn_when_unexpected_format(self):
        result = _check_api_key("bad_key")  # pragma: allowlist secret
        assert result.status == Status.WARN
        assert result.fix is not None

    def test_warn_when_sk_too_short(self):
        result = _check_api_key("sk_short")  # pragma: allowlist secret
        assert result.status == Status.WARN


# ── run_doctor ────────────────────────────────────────────────────────────────


def _patch_all_checks_ok():
    """Context manager that makes every connectivity check pass."""
    mock_redis_client = MagicMock()
    mock_celery_app = MagicMock()
    mock_celery_app.control.inspect.return_value.ping.return_value = {"w1": {}}
    return (
        patch("redis.from_url", return_value=mock_redis_client),
        patch("celery.Celery", return_value=mock_celery_app),
    )


class TestRunDoctor:
    def test_exits_0_when_all_pass(self, capsys):
        mock_client = MagicMock()
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.return_value = {"w1": {}}

        with (
            patch("kanari_agent.config.load_config") as mock_cfg,
            patch("redis.from_url", return_value=mock_client),
            patch("celery.Celery", return_value=mock_app),
        ):
            mock_cfg.return_value = MagicMock(
                redis_url="redis://localhost:6379/0",
                celery_broker_url="redis://localhost:6379/0",
                celery_app_name="tasks",
                api_key="sk_" + "x" * 20,  # pragma: allowlist secret
            )
            code = run_doctor(no_color=True)

        assert code == 0
        out = capsys.readouterr().out
        assert "All checks passed" in out

    def test_exits_1_when_redis_down(self, capsys):
        with (
            patch("kanari_agent.config.load_config") as mock_cfg,
            patch("redis.from_url", side_effect=Exception("refused")),
            patch(
                "celery.Celery",
                return_value=MagicMock(
                    **{"control.inspect.return_value.ping.side_effect": Exception("refused")}
                ),
            ),
        ):
            mock_cfg.return_value = MagicMock(
                redis_url="redis://localhost:6379/0",
                celery_broker_url="redis://localhost:6379/0",
                celery_app_name="tasks",
                api_key=None,
            )
            code = run_doctor(no_color=True)

        assert code == 1
        assert "error" in capsys.readouterr().out.lower()

    def test_exits_0_when_only_warnings(self, capsys):
        mock_client = MagicMock()
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.return_value = None  # no workers = WARN

        with (
            patch("kanari_agent.config.load_config") as mock_cfg,
            patch("redis.from_url", return_value=mock_client),
            patch("celery.Celery", return_value=mock_app),
        ):
            mock_cfg.return_value = MagicMock(
                redis_url="redis://localhost:6379/0",
                celery_broker_url="redis://localhost:6379/0",
                celery_app_name="tasks",
                api_key=None,
            )
            code = run_doctor(no_color=True)

        assert code == 0
        assert "warning" in capsys.readouterr().out.lower()

    def test_falls_back_to_env_vars_on_config_error(self, monkeypatch, capsys):
        monkeypatch.setenv("REDIS_URL", "redis://envhost:6379/0")
        monkeypatch.setenv("CELERY_BROKER_URL", "redis://envhost:6379/0")

        mock_client = MagicMock()
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.return_value = {"w": {}}

        with (
            patch("kanari_agent.config.load_config", side_effect=Exception("bad config")),
            patch("redis.from_url", return_value=mock_client),
            patch("celery.Celery", return_value=mock_app),
        ):
            code = run_doctor(no_color=True)

        # Should still run — falls back to env vars
        assert isinstance(code, int)

    def test_with_config_path(self, tmp_path: Path, capsys):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("redis_url: redis://localhost:6379/0\n")

        mock_client = MagicMock()
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.return_value = {"w": {}}

        with (
            patch("kanari_agent.config.load_config") as mock_load,
            patch("redis.from_url", return_value=mock_client),
            patch("celery.Celery", return_value=mock_app),
        ):
            mock_load.return_value = MagicMock(
                redis_url="redis://localhost:6379/0",
                celery_broker_url="redis://localhost:6379/0",
                celery_app_name="tasks",
                api_key=None,
            )
            code = run_doctor(config_path=str(cfg_file), no_color=True)

        out = capsys.readouterr().out
        assert "Config file" in out
        assert isinstance(code, int)

    def test_color_output_contains_header(self, capsys):
        mock_client = MagicMock()
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.return_value = {"w": {}}

        with (
            patch("kanari_agent.config.load_config") as mock_cfg,
            patch("redis.from_url", return_value=mock_client),
            patch("celery.Celery", return_value=mock_app),
            patch("sys.stdout.isatty", return_value=True),
        ):
            mock_cfg.return_value = MagicMock(
                redis_url="redis://localhost:6379/0",
                celery_broker_url="redis://localhost:6379/0",
                celery_app_name="tasks",
                api_key=None,
            )
            run_doctor(no_color=False)

        out = capsys.readouterr().out
        assert "Kanari Doctor" in out

    def test_prints_each_check_label(self, capsys):
        mock_client = MagicMock()
        mock_app = MagicMock()
        mock_app.control.inspect.return_value.ping.return_value = {"w": {}}

        with (
            patch("kanari_agent.config.load_config") as mock_cfg,
            patch("redis.from_url", return_value=mock_client),
            patch("celery.Celery", return_value=mock_app),
        ):
            mock_cfg.return_value = MagicMock(
                redis_url="redis://localhost:6379/0",
                celery_broker_url="redis://localhost:6379/0",
                celery_app_name="tasks",
                api_key=None,
            )
            run_doctor(no_color=True)

        out = capsys.readouterr().out
        for label in ["Python", "redis library", "celery library", "PyYAML", "Redis", "API key"]:
            assert label in out, f"Expected '{label}' in doctor output"
