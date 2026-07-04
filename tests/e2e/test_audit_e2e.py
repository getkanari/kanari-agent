"""
E2E tests — require a real Redis instance and a live Celery worker.

Run locally:
    E2E=true poetry run pytest tests/e2e/ -v

Override Redis URL if needed (defaults to localhost:6379):
    E2E=true E2E_REDIS_URL=redis://myhost:6379/0 poetry run pytest tests/e2e/ -v

Skipped in normal unit test runs unless E2E=true is set.
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


def _kanari(*args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run `python -m kanari_agent <args>` with isolated Redis env vars."""
    env = {
        **os.environ,
        "REDIS_URL": REDIS_URL,
        "CELERY_BROKER_URL": REDIS_URL,
        # Suppress inherited env vars that could override REDIS_URL
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

    audit --json writes structured log lines (from StructuredLogger) followed
    by the final audit JSON. We scan in reverse for the first line that has
    a 'system_status' key, which is the canonical audit result object.
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
def celery_worker():
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
    # Give the worker time to register with the broker
    time.sleep(4)
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class TestAuditE2E:
    def test_audit_exits_zero_when_healthy(self, celery_worker: subprocess.Popen) -> None:
        result = _kanari("audit", "--json")
        assert result.returncode in (0, 1), (
            f"Expected 0 (healthy) or 1 (warnings), got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_audit_json_output_schema(self, celery_worker: subprocess.Popen) -> None:
        result = _kanari("audit", "--json")
        payload = _parse_audit_json(result.stdout)
        assert "system_status" in payload, f"Missing 'system_status' in: {payload}"
        assert "top_findings" in payload, f"Missing 'top_findings' in: {payload}"
        assert "metrics" in payload, f"Missing 'metrics' in: {payload}"
        assert "exit_code" in payload, f"Missing 'exit_code' in: {payload}"

    def test_audit_detects_workers(self, celery_worker: subprocess.Popen) -> None:
        result = _kanari("audit", "--json")
        payload = _parse_audit_json(result.stdout)
        assert payload["metrics"]["celery_connected"], (
            f"Expected celery_connected=True but got: {payload['metrics']}\n"
            f"Is the Celery worker fixture running?"
        )

    def test_audit_exits_two_on_redis_down(self) -> None:
        result = _kanari(
            "audit",
            "--json",
            extra_env={
                "REDIS_URL": "redis://127.0.0.1:19999/0",
                "CELERY_BROKER_URL": "redis://127.0.0.1:19999/0",
            },
        )
        assert result.returncode == 2, (
            f"Expected exit code 2 (critical) when Redis is unreachable, "
            f"got {result.returncode}.\nstdout: {result.stdout}"
        )

    def test_audit_json_redis_down_is_critical(self) -> None:
        result = _kanari(
            "audit",
            "--json",
            extra_env={
                "REDIS_URL": "redis://127.0.0.1:19999/0",
                "CELERY_BROKER_URL": "redis://127.0.0.1:19999/0",
            },
        )
        payload = _parse_audit_json(result.stdout)
        assert payload["system_status"] == "CRITICAL", (
            f"Expected system_status=CRITICAL when Redis is down, got: {payload}"
        )


class TestDoctorE2E:
    def test_doctor_passes_with_real_redis(self, celery_worker: subprocess.Popen) -> None:
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
            extra_env={
                "REDIS_URL": "redis://127.0.0.1:19999/0",
                "CELERY_BROKER_URL": "redis://127.0.0.1:19999/0",
            },
        )
        assert result.returncode == 1
        assert "Connection failed" in result.stdout
