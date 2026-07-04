"""
Tests for kanari_agent.login — config helpers, HTTP helpers, run_login, run_alerts_configure.
"""

from __future__ import annotations

import json
import stat
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanari_agent.login import (
    _get,
    _get_authed,
    _post,
    load_kanari_config,
    run_alerts_configure,
    run_login,
    save_kanari_config,
)


def _http_error(code: int, reason: str = "Error", body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.getkanari.com/test",
        code=code,
        msg=reason,
        hdrs=MagicMock(),  # type: ignore[arg-type]
        fp=BytesIO(body),
    )


# ── load_kanari_config ────────────────────────────────────────────────────────


class TestLoadKanariConfig:
    def test_returns_empty_dict_when_no_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", tmp_path / "config")
        result = load_kanari_config()
        assert result == {}

    def test_returns_parsed_yaml(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text(
            "api_key: sk_abc123\napi_url: https://api.example.com\n"
        )  # pragma: allowlist secret
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)
        result = load_kanari_config()
        assert result["api_key"] == "sk_abc123"  # pragma: allowlist secret
        assert result["api_url"] == "https://api.example.com"

    def test_returns_empty_on_parse_error(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text("bad: [unclosed yaml\n")
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)
        with patch("yaml.safe_load", side_effect=Exception("parse error")):
            result = load_kanari_config()
        assert result == {}

    def test_returns_empty_dict_on_empty_file(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text("")
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)
        result = load_kanari_config()
        assert result == {}


# ── save_kanari_config ────────────────────────────────────────────────────────


class TestSaveKanariConfig:
    def test_creates_file_with_correct_content(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / ".kanari" / "config"
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)
        save_kanari_config("sk_mykey123", "https://api.example.com")  # pragma: allowlist secret
        assert cfg.exists()
        content = cfg.read_text()
        assert "sk_mykey123" in content  # pragma: allowlist secret
        assert "https://api.example.com" in content

    def test_file_has_restricted_permissions(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / ".kanari" / "config"
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)
        save_kanari_config("sk_mykey123", "https://api.example.com")  # pragma: allowlist secret
        mode = stat.S_IMODE(cfg.stat().st_mode)
        assert mode == 0o600

    def test_creates_parent_directory(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / ".kanari" / "config"
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)
        save_kanari_config("sk_mykey123", "https://api.example.com")  # pragma: allowlist secret
        assert cfg.parent.is_dir()


# ── _post ─────────────────────────────────────────────────────────────────────


class TestPost:
    def test_sends_json_body(self):
        response_data = {"token": "abc123"}
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(response_data).encode()

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = _post("https://api.example.com/auth", {"email": "x@y.com"})

        assert result == response_data
        req = mock_open.call_args[0][0]
        assert req.get_method() == "POST"
        assert b"x@y.com" in req.data

    def test_includes_auth_header_when_api_key_given(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"{}"

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            _post(
                "https://api.example.com/endpoint",
                {},
                api_key="sk_mykey",  # pragma: allowlist secret
            )

        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer sk_mykey"  # pragma: allowlist secret

    def test_no_auth_header_when_no_api_key(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"{}"

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            _post("https://api.example.com/endpoint", {})

        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") is None


# ── _get ──────────────────────────────────────────────────────────────────────


class TestGet:
    def test_returns_parsed_json(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps({"status": "pending"}).encode()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _get("https://api.example.com/auth/poll/token123")

        assert result == {"status": "pending"}

    def test_uses_get_method(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"{}"

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            _get("https://api.example.com/resource")

        req = mock_open.call_args[0][0]
        assert req.get_method() == "GET"


# ── _get_authed ───────────────────────────────────────────────────────────────


class TestGetAuthed:
    def test_includes_auth_header(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"plan": "solo"}'

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = _get_authed(
                "https://api.example.com/billing/status",
                api_key="sk_key123",  # pragma: allowlist secret
            )

        assert result == {"plan": "solo"}
        req = mock_open.call_args[0][0]
        assert "Bearer sk_key123" in req.get_header("Authorization")  # pragma: allowlist secret


# ── run_login ─────────────────────────────────────────────────────────────────


class TestRunLogin:
    def test_cancels_on_eof(self, capsys):
        with (
            patch("builtins.input", side_effect=EOFError),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1
        assert "Cancelled" in capsys.readouterr().out

    def test_cancels_on_keyboard_interrupt(self, capsys):
        with (
            patch("builtins.input", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1

    def test_exits_on_empty_email(self, capsys):
        with (
            patch("builtins.input", return_value=""),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1
        assert "required" in capsys.readouterr().out

    def test_exits_on_http_error_requesting_link(self, capsys):
        with (
            patch("builtins.input", return_value="user@example.com"),
            patch("kanari_agent.login._post", side_effect=_http_error(422, "Unprocessable")),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1
        assert "Server error" in capsys.readouterr().out

    def test_exits_on_network_error_requesting_link(self, capsys):
        with (
            patch("builtins.input", return_value="user@example.com"),
            patch("kanari_agent.login._post", side_effect=Exception("refused")),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Could not reach" in out

    def test_successful_verification_saves_config(self, tmp_path: Path, monkeypatch, capsys):
        cfg = tmp_path / "config"
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)

        with (
            patch("builtins.input", return_value="user@example.com"),
            patch("kanari_agent.login._post", return_value={"token": "poll-token-xyz"}),
            patch(
                "kanari_agent.login._get",
                return_value={
                    "status": "verified",
                    "api_key": "sk_newkey12345678",  # pragma: allowlist secret
                },
            ),
            patch("time.sleep"),
        ):
            run_login("https://api.example.com")

        assert cfg.exists()
        out = capsys.readouterr().out
        assert "Authenticated" in out

    def test_expired_link_exits_1(self, capsys):
        with (
            patch("builtins.input", return_value="user@example.com"),
            patch("kanari_agent.login._post", return_value={"token": "poll-token-xyz"}),
            patch("kanari_agent.login._get", return_value={"status": "expired"}),
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1
        assert "expired" in capsys.readouterr().out

    def test_poll_ignores_http_errors(self, tmp_path: Path, monkeypatch, capsys):
        cfg = tmp_path / "config"
        monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", cfg)

        # First poll raises HTTPError, second returns verified
        poll_responses = [
            _http_error(404, "Not Found"),
            {"status": "verified", "api_key": "sk_newkey12345678"},  # pragma: allowlist secret
        ]
        call_count = 0

        def fake_get(url: str, **_):
            nonlocal call_count
            r = poll_responses[call_count]
            call_count += 1
            if isinstance(r, Exception):
                raise r
            return r

        def fake_get_raising(url: str, **_):
            nonlocal call_count
            item = poll_responses[call_count]
            call_count += 1
            if isinstance(item, urllib.error.HTTPError):
                raise item
            return item

        with (
            patch("builtins.input", return_value="user@example.com"),
            patch("kanari_agent.login._post", return_value={"token": "t"}),
            patch("kanari_agent.login._get", side_effect=fake_get_raising),
            patch("time.sleep"),
        ):
            run_login("https://api.example.com")

        assert "Authenticated" in capsys.readouterr().out

    def test_timeout_exits_1(self, capsys):
        import time as _time

        # Make time.time() return a value past the deadline immediately
        start = _time.time()
        with (
            patch("builtins.input", return_value="user@example.com"),
            patch("kanari_agent.login._post", return_value={"token": "t"}),
            patch("kanari_agent.login._get", return_value={"status": "pending"}),
            patch("time.sleep"),
            patch("time.time", side_effect=[start, start + 9999]),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1
        assert "Timed out" in capsys.readouterr().out

    def test_keyboard_interrupt_during_poll_exits_1(self, capsys):
        with (
            patch("builtins.input", return_value="user@example.com"),
            patch("kanari_agent.login._post", return_value={"token": "t"}),
            patch("kanari_agent.login._get", side_effect=KeyboardInterrupt),
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc,
        ):
            run_login("https://api.example.com")
        assert exc.value.code == 1
        assert "Cancelled" in capsys.readouterr().out


# ── run_alerts_configure ──────────────────────────────────────────────────────


class TestRunAlertsConfigure:
    API_URL = "https://api.example.com"
    API_KEY = "sk_testkey12345678"  # pragma: allowlist secret

    def test_exits_1_when_no_channel(self, capsys):
        with pytest.raises(SystemExit) as exc:
            run_alerts_configure(self.API_URL, self.API_KEY, None, None)
        assert exc.value.code == 1
        assert "--slack-webhook" in capsys.readouterr().out

    def test_exits_1_on_invalid_webhook_url(self, capsys):
        with pytest.raises(SystemExit) as exc:
            run_alerts_configure(self.API_URL, self.API_KEY, "http://not-https.com", None)
        assert exc.value.code == 1
        assert "https://" in capsys.readouterr().out

    def test_success_with_slack_only(self, capsys):
        with patch("kanari_agent.login._post", return_value={}):
            run_alerts_configure(self.API_URL, self.API_KEY, "https://hooks.slack.com/abc", None)
        out = capsys.readouterr().out
        assert "Slack webhook saved" in out

    def test_success_with_email_only(self, capsys):
        with patch("kanari_agent.login._post", return_value={}):
            run_alerts_configure(self.API_URL, self.API_KEY, None, "user@example.com")
        out = capsys.readouterr().out
        assert "Alert email saved" in out
        assert "user@example.com" in out

    def test_success_with_both_channels(self, capsys):
        with patch("kanari_agent.login._post", return_value={}):
            run_alerts_configure(
                self.API_URL,
                self.API_KEY,
                "https://hooks.slack.com/abc",
                "user@example.com",
            )
        out = capsys.readouterr().out
        assert "Slack webhook saved" in out
        assert "Alert email saved" in out

    def test_http_401_invalid_key(self, capsys):
        with (
            patch("kanari_agent.login._post", side_effect=_http_error(401, "Unauthorized")),
            pytest.raises(SystemExit) as exc,
        ):
            run_alerts_configure(self.API_URL, self.API_KEY, "https://hooks.slack.com/abc", None)
        assert exc.value.code == 1
        assert "Invalid API key" in capsys.readouterr().out

    def test_http_error_other(self, capsys):
        with (
            patch(
                "kanari_agent.login._post", side_effect=_http_error(500, "Internal Server Error")
            ),
            pytest.raises(SystemExit) as exc,
        ):
            run_alerts_configure(self.API_URL, self.API_KEY, "https://hooks.slack.com/abc", None)
        assert exc.value.code == 1
        assert "Server error" in capsys.readouterr().out

    def test_network_error(self, capsys):
        with (
            patch("kanari_agent.login._post", side_effect=Exception("refused")),
            pytest.raises(SystemExit) as exc,
        ):
            run_alerts_configure(self.API_URL, self.API_KEY, "https://hooks.slack.com/abc", None)
        assert exc.value.code == 1
        assert "Could not reach" in capsys.readouterr().out
