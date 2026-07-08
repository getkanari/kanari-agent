"""
kanari doctor — diagnose local setup and connectivity issues
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


@dataclass
class CheckResult:
    label: str
    status: Status
    detail: str
    fix: Optional[str] = None


_ICON = {
    Status.OK: "✅",
    Status.WARN: "⚠️ ",
    Status.FAIL: "❌",
    Status.INFO: "ℹ️ ",
}

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BLUE = "\033[34m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_COLOR = {
    Status.OK: _GREEN,
    Status.WARN: _YELLOW,
    Status.FAIL: _RED,
    Status.INFO: _BLUE,
}


def _redact_url(url: str) -> str:
    return re.sub(r"(:)[^@/]+(@)", r"\1***\2", url)


def _fmt(result: CheckResult, use_color: bool) -> str:
    icon = _ICON[result.status]
    if use_color:
        label = f"{_BOLD}{result.label}{_RESET}"
        detail = f"{_COLOR[result.status]}{result.detail}{_RESET}"
    else:
        label = result.label
        detail = result.detail
    line = f"  {icon}  {label}: {detail}"
    if result.fix:
        fix_line = f"→ {result.fix}"
        line += f"\n       {_YELLOW + fix_line + _RESET if use_color else fix_line}"
    return line


# ── Individual checks ──────────────────────────────────────────────────────────


def _check_python() -> CheckResult:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 9):
        return CheckResult("Python", Status.OK, ver)
    return CheckResult(
        "Python",
        Status.FAIL,
        f"{ver} — too old",
        fix="Upgrade to Python 3.9 or newer",
    )


def _check_redis_lib() -> CheckResult:
    try:
        import redis as _  # noqa: F401

        return CheckResult("redis library", Status.OK, "Installed")
    except ImportError:
        return CheckResult("redis library", Status.FAIL, "Not installed", fix="pip install redis")


def _check_celery_lib() -> CheckResult:
    try:
        from celery import Celery as _  # noqa: F401

        return CheckResult("celery library", Status.OK, "Installed")
    except ImportError:
        return CheckResult("celery library", Status.FAIL, "Not installed", fix="pip install celery")


def _check_yaml_lib() -> CheckResult:
    try:
        import yaml as _  # noqa: F401

        return CheckResult("PyYAML", Status.OK, "Installed")
    except ImportError:
        return CheckResult(
            "PyYAML",
            Status.WARN,
            "Not installed — .yaml config files won't load",
            fix="pip install pyyaml",
        )


def _check_config_file(path: Optional[str]) -> CheckResult:
    label = "Config file"
    if not path:
        return CheckResult(label, Status.INFO, "Not specified — using env vars and defaults")
    import os

    if not os.path.exists(path):
        return CheckResult(
            label, Status.FAIL, f"Not found: {path}", fix=f"Check the path or create {path}"
        )
    try:
        from kanari_agent.config import load_config

        load_config(path)
        return CheckResult(label, Status.OK, f"Loaded: {path}")
    except Exception as exc:
        return CheckResult(
            label,
            Status.FAIL,
            f"Parse error: {exc}",
            fix="Check YAML syntax and field names against docs/reference/configuration",
        )


def _check_redis(url: str) -> CheckResult:
    label = f"Redis ({_redact_url(url)})"
    try:
        import redis

        client = redis.from_url(
            url, socket_timeout=3, socket_connect_timeout=3, decode_responses=True
        )
        client.ping()
        client.close()
        return CheckResult(label, Status.OK, "Connected")
    except ImportError:
        return CheckResult(
            label, Status.FAIL, "redis library not installed", fix="pip install redis"
        )
    except Exception as exc:
        return CheckResult(
            label,
            Status.FAIL,
            "Connection failed",
            fix=f"Check REDIS_URL — {exc}",
        )


def _check_celery_workers(broker_url: str, app_name: str) -> CheckResult:
    label = f"Celery workers ({_redact_url(broker_url)})"
    try:
        from celery import Celery

        app = Celery(app_name, broker=broker_url)
        try:
            app.config_from_object({"broker_connection_retry_on_startup": True})
        except Exception:  # nosec B110 — some Celery versions reject unknown keys
            pass
        pong = app.control.inspect(timeout=2.0).ping()
        app.close()
        if pong:
            return CheckResult(label, Status.OK, f"{len(pong)} worker(s) responding")
        return CheckResult(
            label,
            Status.WARN,
            "Broker reachable but no workers found",
            fix="celery -A <your_app> worker --loglevel=info",
        )
    except ImportError:
        return CheckResult(
            label, Status.FAIL, "celery library not installed", fix="pip install celery"
        )
    except Exception as exc:
        return CheckResult(
            label,
            Status.FAIL,
            "Cannot connect to broker",
            fix=f"Check CELERY_BROKER_URL — {exc}",
        )


def _check_api_key(api_key: Optional[str]) -> CheckResult:
    label = "API key"
    if not api_key:
        return CheckResult(
            label,
            Status.INFO,
            "Not set — needed only for alerts and API mode",
            fix="kanari login",
        )
    if api_key.startswith("sk_") and len(api_key) >= 20:
        return CheckResult(label, Status.OK, "Set (format valid)")
    return CheckResult(
        label,
        Status.WARN,
        "Set but format looks unexpected (expected sk_...)",
        fix="kanari login  # refresh your API key",
    )


# ── Runner ─────────────────────────────────────────────────────────────────────


def run_doctor(config_path: Optional[str] = None, no_color: bool = False) -> int:
    """Run all diagnostic checks. Returns 0 if all pass/warn, 1 if any fail."""
    import os

    from kanari_agent.config import load_config

    use_color = not no_color and sys.stdout.isatty()

    # Best-effort config load — fall back to env/defaults if it fails
    try:
        config = load_config(config_path)
        redis_url = config.redis_url
        broker_url = config.celery_broker_url
        app_name = config.celery_app_name
        api_key = config.api_key
    except Exception:
        config = None
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        broker_url = os.environ.get("CELERY_BROKER_URL", redis_url)
        app_name = "tasks"
        api_key = os.environ.get("KANARI_API_KEY")

    header = "Kanari Doctor — checking your setup"
    print(f"\n{_BOLD + header + _RESET if use_color else header}\n")

    checks = [
        _check_python(),
        _check_redis_lib(),
        _check_celery_lib(),
        _check_yaml_lib(),
        _check_config_file(config_path),
        _check_redis(redis_url),
        _check_celery_workers(broker_url, app_name),
        _check_api_key(api_key),
    ]

    for result in checks:
        print(_fmt(result, use_color))

    print()
    failures = [r for r in checks if r.status == Status.FAIL]
    warnings = [r for r in checks if r.status == Status.WARN]

    if failures:
        msg = f"❌  {len(failures)} error(s) found — fix them before running kanari audit"
        print(f"  {_RED + msg + _RESET if use_color else msg}")
        return 1
    if warnings:
        msg = f"⚠️   {len(warnings)} warning(s) — kanari will work but some features may be limited"
        print(f"  {_YELLOW + msg + _RESET if use_color else msg}")
        return 0
    msg = "✅  All checks passed"
    print(f"  {_GREEN + msg + _RESET if use_color else msg}")
    return 0
