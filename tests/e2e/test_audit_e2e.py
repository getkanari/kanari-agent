"""
E2E tests — require a real Redis instance and a live Celery worker.

Run locally:
    docker run -d --name kanari-e2e-redis --rm -p 6379:6379 redis:7-alpine   # start Redis
    E2E=true poetry run pytest tests/e2e/ -v

Override Redis URL if needed (defaults to localhost:6379):
    E2E=true E2E_REDIS_URL=redis://myhost:6379/0 poetry run pytest tests/e2e/ -v

Skipped in normal unit test runs unless E2E=true is set.
Tests that need a live Redis skip automatically if the instance is unreachable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Use E2E_REDIS_URL so we never accidentally inherit REDIS_URL from the shell
# (e.g. a Docker Compose env where redis://redis:6379 doesn't resolve locally).
REDIS_URL = os.environ.get("E2E_REDIS_URL", "redis://localhost:6379/0")
_E2E_ENABLED = os.environ.get("E2E", "false").lower() in ("true", "1", "yes")

pytestmark = pytest.mark.skipif(not _E2E_ENABLED, reason="Set E2E=true to run e2e tests")

_E2E_DIR = Path(__file__).parent

_DEAD_REDIS_URL = "redis://127.0.0.1:19999/0"


def _redis_reachable(url: str) -> bool:
    try:
        import redis

        client = redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


def _kanari(*args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run `python -m kanari_agent <args>` with isolated Redis env vars."""
    env = {
        **os.environ,
        "REDIS_URL": REDIS_URL,
        "CELERY_BROKER_URL": REDIS_URL,
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, "-m", "kanari_agent", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _parse_audit_json(stdout: str) -> dict[str, Any]:
    """
    Extract the audit result from multi-line output.

    audit --json writes structured log lines (StructuredLogger) before the
    final audit JSON. Scan in reverse for the first line with system_status.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if "system_status" in data:
                return data
        except json.JSONDecodeError:
            continue
    raise ValueError(f"No audit JSON result found in output:\n{stdout}")


@pytest.fixture(scope="module")
def live_redis() -> None:
    """Skip the entire module if Redis is not reachable at REDIS_URL."""
    if not _redis_reachable(REDIS_URL):
        pytest.skip(
            f"Redis not reachable at {REDIS_URL}. "
            f"Start it first: docker run -d --name kanari-e2e-redis --rm -p 6379:6379 redis:7-alpine"
        )


@pytest.fixture(scope="module")
def celery_worker(live_redis: None) -> Any:
    """Start a real Celery worker against the test Redis; tear down after module."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "celery_app",
            "worker",
            "--loglevel=warning",
            "--pool=solo",
            "--concurrency=1",
        ],
        cwd=_E2E_DIR,
        env={**os.environ, "CELERY_BROKER_URL": REDIS_URL},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(4)
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── Tests that need Redis + Celery worker ──────────────────────────────────────


class TestAuditE2E:
    def test_audit_exits_zero_when_healthy(self, celery_worker: Any) -> None:
        result = _kanari("audit", "--json")
        assert result.returncode in (0, 1), (
            f"Expected 0 (healthy) or 1 (warnings), got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_audit_json_output_schema(self, celery_worker: Any) -> None:
        result = _kanari("audit", "--json")
        payload = _parse_audit_json(result.stdout)
        assert "system_status" in payload, f"Missing 'system_status' in: {payload}"
        assert "top_findings" in payload, f"Missing 'top_findings' in: {payload}"
        assert "metrics" in payload, f"Missing 'metrics' in: {payload}"
        assert "exit_code" in payload, f"Missing 'exit_code' in: {payload}"

    def test_audit_detects_workers(self, celery_worker: Any) -> None:
        result = _kanari("audit", "--json")
        payload = _parse_audit_json(result.stdout)
        assert payload["metrics"]["celery_connected"], (
            f"Expected celery_connected=True but got: {payload['metrics']}\n"
            f"Is the Celery worker fixture running?"
        )

    # ── Tests that only need dead Redis (no fixture required) ──────────────────

    def test_audit_exits_two_on_redis_down(self) -> None:
        result = _kanari(
            "audit",
            "--json",
            extra_env={"REDIS_URL": _DEAD_REDIS_URL, "CELERY_BROKER_URL": _DEAD_REDIS_URL},
        )
        assert result.returncode == 2, (
            f"Expected exit code 2 (critical) when Redis is unreachable, "
            f"got {result.returncode}.\nstdout: {result.stdout}"
        )

    def test_audit_json_redis_down_is_critical(self) -> None:
        result = _kanari(
            "audit",
            "--json",
            extra_env={"REDIS_URL": _DEAD_REDIS_URL, "CELERY_BROKER_URL": _DEAD_REDIS_URL},
        )
        payload = _parse_audit_json(result.stdout)
        assert payload["system_status"] == "CRITICAL", (
            f"Expected system_status=CRITICAL when Redis is down, got: {payload}"
        )


class TestConfigChecksE2E:
    """Default-on config analysis — the first-run "aha" guarantee."""

    def test_audit_default_includes_checks_performed(self, celery_worker: Any) -> None:
        result = _kanari("audit", "--json")
        payload = _parse_audit_json(result.stdout)
        checks = payload.get("checks_performed", [])
        assert checks, f"Expected non-empty checks_performed, got: {payload}"
        names = [c["name"] for c in checks]
        assert "redis connectivity" in names

    def test_audit_default_detects_config_smells(self, celery_worker: Any) -> None:
        """Stock docker Redis has maxmemory=0 — a smell must surface with zero flags."""
        result = _kanari("audit", "--json")
        payload = _parse_audit_json(result.stdout)
        checks = {c["name"]: c["status"] for c in payload.get("checks_performed", [])}
        assert checks.get("Redis maxmemory") == "warning", (
            f"Expected 'Redis maxmemory' warning on stock Redis, got: {checks}"
        )
        # Stock Celery defaults task_acks_late=False; entry present iff inspector.conf() answered
        if "Celery task_acks_late" in checks:
            assert checks["Celery task_acks_late"] == "warning"

    def test_audit_no_config_checks_flag(self, celery_worker: Any) -> None:
        result = _kanari("audit", "--json", "--no-config-checks")
        payload = _parse_audit_json(result.stdout)
        names = [c["name"] for c in payload.get("checks_performed", [])]
        assert "Redis maxmemory" not in names
        assert "redis connectivity" in names  # findings families always reported

    def test_config_warnings_do_not_change_exit_code(self, celery_worker: Any) -> None:
        with_checks = _kanari("audit", "--json")
        without_checks = _kanari("audit", "--json", "--no-config-checks")
        assert with_checks.returncode == without_checks.returncode, (
            f"Config checks changed the exit code: {with_checks.returncode} vs "
            f"{without_checks.returncode}"
        )

    def test_deep_flag_still_accepted_with_deprecation_notice(self, celery_worker: Any) -> None:
        result = _kanari("audit", "--json", "--deep")
        assert result.returncode in (0, 1)
        assert "deprecated" in result.stderr
        # stdout stays parseable despite the stderr notice
        _parse_audit_json(result.stdout)


class TestDoctorE2E:
    def test_doctor_passes_with_real_redis(self, celery_worker: Any) -> None:
        result = _kanari("doctor", "--no-color")
        assert result.returncode == 0, (
            f"kanari doctor returned {result.returncode}.\n{result.stdout}"
        )
        assert "Redis" in result.stdout
        assert "All checks passed" in result.stdout

    def test_doctor_fails_gracefully_when_redis_down(self) -> None:
        result = _kanari(
            "doctor",
            "--no-color",
            extra_env={"REDIS_URL": _DEAD_REDIS_URL, "CELERY_BROKER_URL": _DEAD_REDIS_URL},
        )
        assert result.returncode == 1
        assert "Connection failed" in result.stdout
