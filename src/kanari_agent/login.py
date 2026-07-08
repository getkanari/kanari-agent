"""
Magic link authentication and alert configuration for kanari CLI.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

POLL_INTERVAL_SEC = 3
POLL_TIMEOUT_SEC = 300  # 5 minutes
KANARI_CONFIG_PATH = Path.home() / ".kanari" / "config"


# ── Config file helpers ───────────────────────────────────────────────────────


def load_kanari_config() -> dict:
    """Read ~/.kanari/config. Returns empty dict if file doesn't exist."""
    if not KANARI_CONFIG_PATH.exists():
        return {}
    try:
        import yaml  # type: ignore[import]

        with open(KANARI_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def save_kanari_config(api_key: str, api_url: str) -> None:
    """Write ~/.kanari/config with api_key and api_url."""
    KANARI_CONFIG_PATH.parent.mkdir(exist_ok=True)
    KANARI_CONFIG_PATH.write_text(f"api_key: {api_key}\napi_url: {api_url}\n")
    KANARI_CONFIG_PATH.chmod(0o600)  # owner read/write only — contains a secret


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _post(url: str, data: dict, api_key: Optional[str] = None, timeout: int = 10) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read())  # type: ignore[no-any-return]


def _get(url: str, timeout: int = 10) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read())  # type: ignore[no-any-return]


def _get_authed(url: str, api_key: str, timeout: int = 10) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read())  # type: ignore[no-any-return]


# ── kanari login ──────────────────────────────────────────────────────────────


def run_login(api_url: str) -> None:
    """Interactive magic link login. Saves API key to ~/.kanari/config on success."""
    try:
        email = input("Enter your email: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)

    if not email:
        print("❌ Email is required.")
        sys.exit(1)

    # Request magic link
    try:
        resp = _post(f"{api_url}/auth/request", {"email": email})
        poll_token = resp["token"]
    except urllib.error.HTTPError as e:
        print(f"❌ Server error: {e.code} {e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Could not reach {api_url}: {e}")
        print("   Check that the backend is running and try again.")
        sys.exit(1)

    print(f"✓ Magic link sent to {email}")
    print("  Check your inbox and click the link.")
    print("  Waiting for verification...  (Ctrl+C to cancel)\n")

    # Poll until verified, expired, or timeout
    deadline = time.time() + POLL_TIMEOUT_SEC
    try:
        while time.time() < deadline:
            try:
                resp = _get(f"{api_url}/auth/poll/{poll_token}")
                status = resp.get("status")

                if status == "verified":
                    api_key = resp["api_key"]
                    save_kanari_config(api_key, api_url)
                    masked = f"{api_key[:8]}...{api_key[-4:]}"
                    print("✓ Authenticated!")
                    print(f"  API key: {masked}")
                    print(f"  Saved to {KANARI_CONFIG_PATH}\n")
                    print("  Next step: kanari agent")
                    return

                elif status == "expired":
                    print("❌ Link expired. Run kanari login again.")
                    sys.exit(1)

                # status == "pending" or "consumed" → keep polling

            except urllib.error.HTTPError:
                pass  # token not found yet, keep polling
            except Exception:  # nosec B110
                pass  # network hiccup, keep polling

            time.sleep(POLL_INTERVAL_SEC)

        print("❌ Timed out waiting for verification (5 min).")
        sys.exit(1)

    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)


# ── kanari alerts configure ───────────────────────────────────────────────────


def run_alerts_configure(
    api_url: str,
    api_key: str,
    slack_webhook: Optional[str],
    alert_email: Optional[str],
) -> None:
    """POST /api/v1/settings/alerts to save alert channel config."""
    if not slack_webhook and not alert_email:
        print("❌ Provide at least --slack-webhook or --email.")
        sys.exit(1)

    payload: dict = {}
    if slack_webhook is not None:
        if not slack_webhook.startswith("https://"):
            print("❌ Slack webhook must start with https://")
            sys.exit(1)
        payload["slack_webhook"] = slack_webhook
    if alert_email is not None:
        payload["alert_email"] = alert_email

    try:
        _post(f"{api_url}/api/v1/settings/alerts", payload, api_key=api_key)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("❌ Invalid API key. Run kanari login first.")
        else:
            print(f"❌ Server error: {e.code} {e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Could not reach {api_url}: {e}")
        sys.exit(1)

    if slack_webhook:
        print("✓ Slack webhook saved.")
    if alert_email:
        print(f"✓ Alert email saved: {alert_email}")
    print("  You'll receive alerts when findings are HIGH or CRITICAL.")
