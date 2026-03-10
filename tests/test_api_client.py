"""
Tests for doorman_agent.api_client module

Focuses on privacy/sanitization logic — no external connections needed.
"""

from __future__ import annotations

import pytest

from doorman_agent.api_client import APIClient
from doorman_agent.models import Config, PrivacyConfig, QueueMetrics, SystemMetrics, WorkerMetrics


@pytest.fixture
def client() -> APIClient:
    """APIClient with sanitization enabled (default)"""
    return APIClient(api_key="test-key", api_url="https://api.example.com")


@pytest.fixture
def client_no_sanitize() -> APIClient:
    """APIClient with sanitization disabled"""
    config = Config(privacy=PrivacyConfig(sanitize_task_signatures=False))
    return APIClient(api_key="test-key", api_url="https://api.example.com", config=config)


@pytest.fixture
def minimal_metrics() -> SystemMetrics:
    return SystemMetrics(
        timestamp="2026-01-01T00:00:00+00:00",
        redis_connected=True,
        celery_connected=True,
    )


# ---------------------------------------------------------------------------
# Worker hashing
# ---------------------------------------------------------------------------


class TestHashWorkerId:
    def test_returns_w_prefix(self, client):
        result = client._hash_worker_id("celery@worker-1")
        assert result.startswith("w-")

    def test_returns_8_char_hash(self, client):
        result = client._hash_worker_id("celery@worker-1")
        # "w-" + 8 hex chars
        assert len(result) == 10

    def test_same_input_same_output(self, client):
        assert client._hash_worker_id("worker-1") == client._hash_worker_id("worker-1")

    def test_different_inputs_different_output(self, client):
        assert client._hash_worker_id("worker-1") != client._hash_worker_id("worker-2")


# ---------------------------------------------------------------------------
# Task ID hashing
# ---------------------------------------------------------------------------


class TestHashTaskId:
    def test_returns_t_prefix(self, client):
        result = client._hash_task_id("abc-123")
        assert result.startswith("t-")

    def test_returns_12_char_hash(self, client):
        result = client._hash_task_id("abc-123")
        # "t-" + 12 hex chars
        assert len(result) == 14

    def test_deterministic(self, client):
        assert client._hash_task_id("task-id-xyz") == client._hash_task_id("task-id-xyz")


# ---------------------------------------------------------------------------
# Display name sanitization
# ---------------------------------------------------------------------------


class TestSanitizeDisplayName:
    def test_strips_celery_prefix(self, client):
        assert client._sanitize_display_name("celery@worker-1") == "worker-1"

    def test_strips_domain_suffix(self, client):
        assert client._sanitize_display_name("celery@worker-1.prod.internal") == "worker-1"

    def test_no_prefix_no_change(self, client):
        assert client._sanitize_display_name("worker-1") == "worker-1"

    def test_ip_hostname(self, client):
        assert client._sanitize_display_name("celery@ip-10-0-1-234") == "ip-10-0-1-234"


# ---------------------------------------------------------------------------
# Task signature sanitization
# ---------------------------------------------------------------------------


class TestSanitizeTaskSignature:
    def test_plain_task_name_unchanged(self, client):
        assert client._sanitize_task_signature("app.tasks.send_email") == "app.tasks.send_email"

    def test_email_in_name_replaced(self, client):
        result = client._sanitize_task_signature("send_to_john@example.com")
        assert "[email]" in result
        assert "john" not in result

    def test_uuid_replaced(self, client):
        result = client._sanitize_task_signature(
            "order_550e8400-e29b-41d4-a716-446655440000"
        )
        assert "[uuid]" in result
        assert "550e8400" not in result

    def test_numeric_id_suffix_replaced(self, client):
        result = client._sanitize_task_signature("process_user_12345")
        assert "[id]" in result
        assert "12345" not in result

    def test_short_number_not_replaced(self, client):
        # Numbers with fewer than 4 digits should be kept
        result = client._sanitize_task_signature("retry_3")
        assert "3" in result

    def test_sanitization_disabled(self, client_no_sanitize):
        name = "process_user_12345"
        assert client_no_sanitize._sanitize_task_signature(name) == name

    def test_uuid_case_insensitive(self, client):
        result = client._sanitize_task_signature(
            "task_550E8400-E29B-41D4-A716-446655440000"
        )
        assert "[uuid]" in result

    @pytest.mark.parametrize(
        "input_name,expected_fragment",
        [
            ("send_to_john@acme.co", "[email]"),
            ("order_550e8400-e29b-41d4-a716-446655440000_process", "[uuid]"),
            ("process_user_99999", "[id]"),
        ],
    )
    def test_various_pii_patterns(self, client, input_name, expected_fragment):
        assert expected_fragment in client._sanitize_task_signature(input_name)


# ---------------------------------------------------------------------------
# Queue name sanitization
# ---------------------------------------------------------------------------


class TestSanitizeQueueName:
    def test_plain_queue_unchanged(self, client):
        assert client._sanitize_queue_name("celery") == "celery"

    def test_email_in_queue_replaced(self, client):
        result = client._sanitize_queue_name("emails-john@acme.com")
        assert "[email]" in result
        assert "john" not in result

    def test_uuid_in_queue_replaced(self, client):
        result = client._sanitize_queue_name(
            "tenant-550e8400-e29b-41d4-a716-446655440000"
        )
        assert "[uuid]" in result


# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------


class TestBuildPayload:
    def test_payload_structure(self, client, minimal_metrics):
        payload = client.build_payload(minimal_metrics)
        assert "timestamp" in payload
        assert "agent_version" in payload
        assert "metrics" in payload
        assert "queues" in payload
        assert "workers" in payload
        assert "anomalies" in payload
        assert "privacy" in payload

    def test_privacy_args_accessed_false(self, client, minimal_metrics):
        payload = client.build_payload(minimal_metrics)
        assert payload["privacy"]["args_accessed"] is False

    def test_worker_is_hashed(self, client):
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            workers=[WorkerMetrics(name="celery@prod-worker-1", active_tasks=2, concurrency=4)],
        )
        payload = client.build_payload(metrics)
        worker = payload["workers"][0]
        assert worker["id_hash"].startswith("w-")
        assert "prod-worker-1" not in worker["id_hash"]

    def test_queue_name_sanitized(self, client):
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            queues=[QueueMetrics(name="emails-john@acme.com", depth=5)],
        )
        payload = client.build_payload(metrics)
        # The email regex includes `-` in its char class, so `emails-john@acme.com`
        # is fully replaced — result contains [email] and no raw email data
        name = payload["queues"][0]["name"]
        assert "[email]" in name
        assert "@" not in name

    def test_stuck_task_hashed(self, client):
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            workers=[WorkerMetrics(name="celery@worker-1")],
            stuck_tasks=[
                {
                    "task_id": "real-task-uuid-1234",
                    "task_name": "process_user_99999",
                    "worker": "celery@worker-1",
                    "runtime_seconds": 3600,
                    "started_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        )
        payload = client.build_payload(metrics)
        anomaly = payload["anomalies"][0]
        assert anomaly["task_id_hash"].startswith("t-")
        assert "real-task-uuid-1234" not in anomaly["task_id_hash"]
        assert "[id]" in anomaly["task_signature"]

    def test_metrics_summary_values(self, client):
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            total_pending_tasks=100,
            total_active_tasks=5,
            total_workers=2,
            alive_workers=2,
            total_concurrency=8,
            saturation_pct=62.5,
        )
        payload = client.build_payload(metrics)
        m = payload["metrics"]
        assert m["total_pending"] == 100
        assert m["total_active"] == 5
        assert m["saturation_pct"] == 62.5

    def test_worker_status_online(self, client):
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            workers=[WorkerMetrics(name="celery@worker-1", is_alive=True)],
        )
        payload = client.build_payload(metrics)
        assert payload["workers"][0]["status"] == "online"

    def test_worker_status_offline(self, client):
        metrics = SystemMetrics(
            timestamp="2026-01-01T00:00:00+00:00",
            workers=[WorkerMetrics(name="celery@worker-1", is_alive=False)],
        )
        payload = client.build_payload(metrics)
        assert payload["workers"][0]["status"] == "offline"
