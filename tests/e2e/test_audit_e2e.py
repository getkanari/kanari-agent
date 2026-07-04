"""
E2E tests — require a real Redis instance and a live Celery worker.

Run locally:
    E2E=true REDIS_URL=redis://localhost:6379/0 poetry run pytest tests/e2e/ -v

Skipped in normal unit test runs unless E2E=true is set.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_E2E_ENABLED = os.environ.get("E2E", "false").lower() in ("true", "1", "yes")

pytestmark = pytest.mark.skipif(not _E2E_ENABLED, reason="Set E2E=true to run e2e tests")

_E2E_DIR = Path(__file__).parent


def _kanari(*args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run `python -m kanari_agent <args>` with test Redis env vars."""
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
        payload = json.loads(result.stdout)
        assert "status" in payload, f"Missing 'status' key in: {payload}"
        assert "findings" in payload, f"Missing 'findings' key in: {payload}"
        assert "queues" in payload, f"Missing 'queues' key in: {payload}"
        assert "workers" in payload, f"Missing 'workers' key in: {payload}"

    def test_audit_detects_workers(self, celery_worker: subprocess.Popen) -> None:
        result = _kanari("audit", "--json")
        payload = json.loads(result.stdout)
        assert payload["workers"] > 0, (
            f"Expected at least 1 worker but got {payload['workers']}.\n"
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

    def test_audit_json_redis_down_has_finding(self) -> None:
        result = _kanari(
            "audit",
            "--json",
            extra_env={
                "REDIS_URL": "redis://127.0.0.1:19999/0",
                "CELERY_BROKER_URL": "redis://127.0.0.1:19999/0",
            },
        )
        payload = json.loads(result.stdout)
        codes = [f.get("code", "") for f in payload.get("findings", [])]
        assert "REDIS_DOWN" in codes, f"Expected REDIS_DOWN finding, got: {codes}"


class TestDoctorE2E:
    def test_doctor_passes_with_real_redis(self, celery_worker: subprocess.Popen) -> None:
        result = _kanari("doctor", "--no-color")
        # Exit 0 = all ok, exit 1 = failures — with a worker up we expect 0
        assert result.returncode == 0, (
            f"kanari doctor returned {result.returncode}.\n{result.stdout}"
        )
        assert "Redis" in result.stdout
        assert "✅" in result.stdout or "All checks passed" in result.stdout

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
        assert "Connection failed" in result.stdout or "error" in result.stdout.lower()
