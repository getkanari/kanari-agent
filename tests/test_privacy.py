"""
Privacy guarantee tests for Kanari Agent.

These tests verify that no PII escapes through the API payload under any
realistic scenario. They complement the unit tests in test_api_client.py by
testing end-to-end invariants rather than individual sanitization functions.

Invariants enforced:
  1. No raw email addresses in any string field of the payload
  2. No raw UUIDs in any string field of the payload
  3. No raw hostnames in worker identifiers
  4. Task IDs are always hashed (never original)
  5. Worker names are always hashed (never original)
  6. privacy.args_accessed is always False
  7. Sanitization cannot be accidentally bypassed
"""

from __future__ import annotations

import json
import re

import pytest

from kanari_agent.api_client import APIClient
from kanari_agent.models import Config, PrivacyConfig, QueueMetrics, SystemMetrics, WorkerMetrics

# ---------------------------------------------------------------------------
# Patterns that must NEVER appear in any payload string value
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
# Raw hostname format: celery@hostname (the @ is the giveaway)
CELERY_HOSTNAME_PATTERN = re.compile(r"celery@[\w\-\.]+")


def _all_strings(obj, path="") -> list[tuple[str, str]]:
    """Recursively extract all string values from a dict/list with their path."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            results.extend(_all_strings(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            results.extend(_all_strings(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        results.append((path, obj))
    return results


def _assert_no_pattern(payload: dict, pattern: re.Pattern, label: str):
    """Assert that no string value in the payload matches the given pattern."""
    violations = []
    for path, value in _all_strings(payload):
        if pattern.search(value):
            violations.append(f"  {path} = {value!r}")
    if violations:
        raise AssertionError(f"PII LEAK: {label} found in payload:\n" + "\n".join(violations))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> APIClient:
    return APIClient(api_key="test-key", api_url="https://api.example.com")


@pytest.fixture
def client_no_sanitize() -> APIClient:
    config = Config(privacy=PrivacyConfig(sanitize_task_signatures=False))
    return APIClient(api_key="test-key", api_url="https://api.example.com", config=config)


def _realistic_metrics(**overrides) -> SystemMetrics:
    """Build realistic metrics with PII embedded in every sensitive field."""
    defaults: dict = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "redis_connected": True,
        "celery_connected": True,
        "total_pending_tasks": 500,
        "total_active_tasks": 8,
        "total_workers": 3,
        "alive_workers": 3,
        "total_concurrency": 12,
        "saturation_pct": 66.7,
        "workers": [
            WorkerMetrics(
                name="celery@prod-worker-1.internal.acme.com",
                active_tasks=4,
                concurrency=4,
                is_alive=True,
            ),
            WorkerMetrics(
                name="celery@prod-worker-2.internal.acme.com",
                active_tasks=4,
                concurrency=4,
                is_alive=True,
            ),
        ],
        "queues": [
            QueueMetrics(name="emails-john@acme.com", depth=100, oldest_task_age_seconds=15.0),
            QueueMetrics(name="tenant-550e8400-e29b-41d4-a716-446655440000", depth=50),
            QueueMetrics(name="celery", depth=350, oldest_task_age_seconds=5.0),
        ],
        "stuck_tasks": [
            {
                "task_id": "550e8400-e29b-41d4-a716-446655440000",
                "task_name": "app.tasks.process_user_98765",
                "worker": "celery@prod-worker-1.internal.acme.com",
                "runtime_seconds": 7200,
                "started_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "task_id": "send_to_john@acme.com-order-12345",
                "task_name": "app.tasks.send_email_to_john@acme.com",
                "worker": "celery@prod-worker-2.internal.acme.com",
                "runtime_seconds": 3600,
                "started_at": "2026-01-01T01:00:00+00:00",
            },
        ],
    }
    defaults.update(overrides)
    return SystemMetrics(**defaults)


# ---------------------------------------------------------------------------
# Core invariants
# ---------------------------------------------------------------------------


class TestNoEmailsInPayload:
    def test_no_email_in_queue_names(self, client):
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        _assert_no_pattern(payload, EMAIL_PATTERN, "raw email address")

    def test_no_email_in_worker_ids(self, client):
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        _assert_no_pattern(payload, EMAIL_PATTERN, "raw email address")

    def test_no_email_in_task_signatures(self, client):
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        _assert_no_pattern(payload, EMAIL_PATTERN, "raw email address")

    def test_no_email_in_task_id_hashes(self, client):
        """Even if a task_id contains an email, the hash must not"""
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        _assert_no_pattern(payload, EMAIL_PATTERN, "raw email address")

    def test_no_email_even_with_sanitize_disabled(self, client_no_sanitize):
        """Queue names are always sanitized regardless of privacy config"""
        metrics = _realistic_metrics()
        payload = client_no_sanitize.build_payload(metrics)
        # Queue names are ALWAYS sanitized — _sanitize_queue_name is not
        # gated by sanitize_task_signatures
        queue_names = [q["name"] for q in payload["queues"]]
        for name in queue_names:
            assert not EMAIL_PATTERN.search(name), (
                f"Email leaked in queue name even with sanitization disabled: {name!r}"
            )


class TestNoUUIDsInPayload:
    def test_no_uuid_in_queue_names(self, client):
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        queue_names = [q["name"] for q in payload["queues"]]
        for name in queue_names:
            assert not UUID_PATTERN.search(name), f"UUID leaked in queue name: {name!r}"

    def test_no_uuid_in_task_id_fields(self, client):
        """Task IDs must be hashed, not raw UUIDs"""
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        for anomaly in payload["anomalies"]:
            task_id_hash = anomaly["task_id_hash"]
            assert not UUID_PATTERN.search(task_id_hash), (
                f"Raw UUID in task_id_hash: {task_id_hash!r}"
            )

    def test_no_uuid_in_task_signatures(self, client):
        metrics = _realistic_metrics(
            stuck_tasks=[
                {
                    "task_id": "t1",
                    "task_name": "process_order_550e8400-e29b-41d4-a716-446655440000",
                    "worker": "celery@w-1",
                    "runtime_seconds": 7200,
                    "started_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        )
        payload = client.build_payload(metrics)
        for anomaly in payload["anomalies"]:
            sig = anomaly["task_signature"]
            assert not UUID_PATTERN.search(sig), f"Raw UUID in task_signature: {sig!r}"


class TestNoHostnamesInPayload:
    def test_worker_ids_are_hashed(self, client):
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        for worker in payload["workers"]:
            assert CELERY_HOSTNAME_PATTERN.search(worker["id_hash"]) is None, (
                f"Raw hostname in id_hash: {worker['id_hash']!r}"
            )
            assert worker["id_hash"].startswith("w-"), (
                f"Worker hash missing 'w-' prefix: {worker['id_hash']!r}"
            )

    def test_stuck_task_worker_ref_is_hashed(self, client):
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        for anomaly in payload["anomalies"]:
            ref = anomaly["worker_ref"]
            assert CELERY_HOSTNAME_PATTERN.search(ref) is None, (
                f"Raw hostname in worker_ref: {ref!r}"
            )
            assert ref.startswith("w-"), f"worker_ref missing 'w-' prefix: {ref!r}"

    def test_display_name_strips_domain(self, client):
        """Display name may show short hostname but never the full FQDN"""
        metrics = _realistic_metrics()
        payload = client.build_payload(metrics)
        for worker in payload["workers"]:
            display = worker["display_name"]
            assert ".internal" not in display, (
                f"Internal domain leaked in display_name: {display!r}"
            )
            assert "acme.com" not in display, f"Company domain leaked in display_name: {display!r}"


class TestArgsNeverAccessed:
    def test_args_accessed_is_always_false(self, client):
        payload = client.build_payload(_realistic_metrics())
        assert payload["privacy"]["args_accessed"] is False

    def test_args_accessed_false_with_no_sanitize(self, client_no_sanitize):
        payload = client_no_sanitize.build_payload(_realistic_metrics())
        assert payload["privacy"]["args_accessed"] is False

    def test_args_accessed_false_for_empty_metrics(self, client):
        metrics = SystemMetrics(timestamp="2026-01-01T00:00:00+00:00")
        payload = client.build_payload(metrics)
        assert payload["privacy"]["args_accessed"] is False


# ---------------------------------------------------------------------------
# Full payload scan — catch-all for any PII pattern
# ---------------------------------------------------------------------------


class TestFullPayloadScan:
    def test_complete_payload_free_of_emails(self, client):
        """Scan every string in the full payload for email patterns"""
        payload = client.build_payload(_realistic_metrics())
        _assert_no_pattern(payload, EMAIL_PATTERN, "email address")

    def test_complete_payload_free_of_raw_uuids_in_hashed_fields(self, client):
        """
        UUIDs may appear in timestamps/started_at (ISO format contains hyphens
        but not UUID format), so we target only fields that should be hashed.
        """
        payload = client.build_payload(_realistic_metrics())
        for anomaly in payload["anomalies"]:
            for field in ("task_id_hash", "worker_ref", "task_signature"):
                value = anomaly.get(field, "")
                assert not UUID_PATTERN.search(value), f"UUID found in anomaly.{field}: {value!r}"

    def test_payload_is_json_serializable(self, client):
        """Payload must be JSON-serializable (no non-serializable objects)"""
        payload = client.build_payload(_realistic_metrics())
        serialized = json.dumps(payload)
        assert len(serialized) > 0

    def test_no_celery_hostname_anywhere_in_payload(self, client):
        """The string 'celery@' must never appear in any payload value"""
        payload = client.build_payload(_realistic_metrics())
        _assert_no_pattern(payload, CELERY_HOSTNAME_PATTERN, "raw Celery hostname")


# ---------------------------------------------------------------------------
# Numeric ID sanitization
# ---------------------------------------------------------------------------


class TestNumericIdSanitization:
    @pytest.mark.parametrize(
        "task_name",
        [
            "process_user_12345",
            "send_notification_99999",
            "generate_report_10000",
            "sync_account_55555",
        ],
    )
    def test_numeric_ids_replaced_in_task_signatures(self, client, task_name):
        metrics = _realistic_metrics(
            stuck_tasks=[
                {
                    "task_id": "t1",
                    "task_name": task_name,
                    "worker": "celery@w-1",
                    "runtime_seconds": 7200,
                    "started_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        )
        payload = client.build_payload(metrics)
        sig = payload["anomalies"][0]["task_signature"]
        assert not re.search(r"\d{4,}", sig), (
            f"Numeric ID not sanitized in task_signature: {sig!r} (original: {task_name!r})"
        )
