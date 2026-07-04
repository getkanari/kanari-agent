"""
Tests for the paid/billing CLI commands: status, upgrade, manage-billing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


def _run_main(argv: list[str]):
    from kanari_agent.cli import main

    with patch("sys.argv", ["kanari"] + argv):
        main()


def _http_error(code: int, reason: str = "Error", body: bytes = b"") -> urllib.error.HTTPError:
    """Build a urllib HTTPError with a readable body."""
    err = urllib.error.HTTPError(
        url="https://api.getkanari.com/test",
        code=code,
        msg=reason,
        hdrs=MagicMock(),  # type: ignore[arg-type]
        fp=BytesIO(body),
    )
    return err


# ── kanari status ─────────────────────────────────────────────────────────────


class TestCliStatus:
    def test_status_shows_free_plan(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                return_value={"plan": "free", "subscribed": False},
            ),
        ):
            _run_main(["status"])

        out = capsys.readouterr().out
        assert "Plan:         free" in out
        assert "Subscription: none" in out

    def test_status_free_plan_shows_upgrade_hint(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                return_value={"plan": "free", "subscribed": False},
            ),
        ):
            _run_main(["status"])

        out = capsys.readouterr().out
        assert "kanari upgrade --plan solo" in out

    def test_status_paid_plan_no_upgrade_hint(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                return_value={"plan": "solo", "subscribed": True},
            ),
        ):
            _run_main(["status"])

        out = capsys.readouterr().out
        assert "Plan:         solo" in out
        assert "Subscription: active" in out
        assert "upgrade" not in out

    def test_status_unauthenticated_exits_1(self, capsys):
        with (
            patch("kanari_agent.login.load_kanari_config", return_value={}),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["status"])

        assert exc.value.code == 1
        assert "kanari login" in capsys.readouterr().out

    def test_status_http_error_exits_1(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                side_effect=_http_error(401, "Unauthorized"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["status"])

        assert exc.value.code == 1
        assert "401" in capsys.readouterr().out

    def test_status_network_error_exits_1(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                side_effect=Exception("Connection refused"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["status"])

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Could not reach" in out

    def test_status_uses_api_url_flag(self):
        captured_url: list[str] = []

        def fake_get_authed(url: str, api_key: str, **_) -> dict:
            captured_url.append(url)
            return {"plan": "free", "subscribed": False}

        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch("kanari_agent.login._get_authed", side_effect=fake_get_authed),
        ):
            _run_main(["status", "--api-url", "https://staging.getkanari.com"])

        assert captured_url[0].startswith("https://staging.getkanari.com")


# ── kanari upgrade ────────────────────────────────────────────────────────────


class TestCliUpgrade:
    def test_upgrade_opens_browser(self):
        mock_browser = MagicMock()
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._post",
                return_value={"checkout_url": "https://checkout.lemonsqueezy.com/abc"},
            ),
            patch("webbrowser.open", mock_browser),
        ):
            _run_main(["upgrade", "--plan", "solo"])

        mock_browser.assert_called_once_with("https://checkout.lemonsqueezy.com/abc")

    def test_upgrade_prints_plan_and_url(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._post",
                return_value={"checkout_url": "https://checkout.lemonsqueezy.com/abc"},
            ),
            patch("webbrowser.open"),
        ):
            _run_main(["upgrade", "--plan", "team"])

        out = capsys.readouterr().out
        assert "team" in out
        assert "https://checkout.lemonsqueezy.com/abc" in out

    def test_upgrade_default_plan_is_solo(self):
        captured: list[str] = []

        def fake_post(url: str, data: dict, **_) -> dict:
            captured.append(url)
            return {"checkout_url": "https://checkout.example.com/x"}

        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch("kanari_agent.login._post", side_effect=fake_post),
            patch("webbrowser.open"),
        ):
            _run_main(["upgrade"])

        assert "plan=solo" in captured[0]

    def test_upgrade_no_checkout_url_exits_1(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch("kanari_agent.login._post", return_value={}),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["upgrade"])

        assert exc.value.code == 1
        assert "No checkout URL" in capsys.readouterr().out

    def test_upgrade_unauthenticated_exits_1(self, capsys):
        with (
            patch("kanari_agent.login.load_kanari_config", return_value={}),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["upgrade"])

        assert exc.value.code == 1
        assert "kanari login" in capsys.readouterr().out

    def test_upgrade_http_error_with_json_detail(self, capsys):
        body = json.dumps({"detail": "Card declined"}).encode()
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._post",
                side_effect=_http_error(402, "Payment Required", body),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["upgrade"])

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Card declined" in out

    def test_upgrade_http_error_plain_body(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._post",
                side_effect=_http_error(500, "Internal Server Error", b"oops"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["upgrade"])

        assert exc.value.code == 1
        assert "oops" in capsys.readouterr().out

    def test_upgrade_network_error_exits_1(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch("kanari_agent.login._post", side_effect=Exception("timeout")),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["upgrade"])

        assert exc.value.code == 1
        assert "Could not reach" in capsys.readouterr().out

    @pytest.mark.parametrize("plan", ["solo", "team", "business"])
    def test_upgrade_accepts_all_valid_plans(self, plan: str):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._post",
                return_value={"checkout_url": "https://checkout.example.com/x"},
            ),
            patch("webbrowser.open"),
        ):
            _run_main(["upgrade", "--plan", plan])  # must not raise


# ── kanari manage-billing ─────────────────────────────────────────────────────


class TestCliManageBilling:
    def test_manage_billing_opens_portal(self):
        mock_browser = MagicMock()
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                return_value={"portal_url": "https://billing.lemonsqueezy.com/portal/xyz"},
            ),
            patch("webbrowser.open", mock_browser),
        ):
            _run_main(["manage-billing"])

        mock_browser.assert_called_once_with("https://billing.lemonsqueezy.com/portal/xyz")

    def test_manage_billing_no_portal_url_exits_1(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch("kanari_agent.login._get_authed", return_value={}),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["manage-billing"])

        assert exc.value.code == 1
        assert "No portal URL" in capsys.readouterr().out

    def test_manage_billing_unauthenticated_exits_1(self, capsys):
        with (
            patch("kanari_agent.login.load_kanari_config", return_value={}),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["manage-billing"])

        assert exc.value.code == 1
        assert "kanari login" in capsys.readouterr().out

    def test_manage_billing_http_400_no_subscription(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                side_effect=_http_error(400, "Bad Request"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["manage-billing"])

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "kanari upgrade --plan solo" in out

    def test_manage_billing_http_error_other(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                side_effect=_http_error(403, "Forbidden"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["manage-billing"])

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "403" in out

    def test_manage_billing_network_error_exits_1(self, capsys):
        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={"api_key": "sk_test123456789012"},  # pragma: allowlist secret
            ),
            patch(
                "kanari_agent.login._get_authed",
                side_effect=Exception("Connection refused"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            _run_main(["manage-billing"])

        assert exc.value.code == 1
        assert "Could not reach" in capsys.readouterr().out

    def test_manage_billing_uses_api_url_from_config(self):
        captured: list[str] = []

        def fake_get_authed(url: str, api_key: str, **_) -> dict:
            captured.append(url)
            return {"portal_url": "https://billing.example.com/portal/x"}

        with (
            patch(
                "kanari_agent.login.load_kanari_config",
                return_value={
                    "api_key": "sk_test123456789012",  # pragma: allowlist secret
                    "api_url": "https://custom.getkanari.com",
                },
            ),
            patch("kanari_agent.login._get_authed", side_effect=fake_get_authed),
            patch("webbrowser.open"),
        ):
            _run_main(["manage-billing"])

        assert captured[0].startswith("https://custom.getkanari.com")
