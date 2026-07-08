"""
Tests for kanari_agent.api_client module

Focuses on privacy/sanitization logic — no external connections needed.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from kanari_agent.api_client import APIClient
from kanari_agent.models import Config, PrivacyConfig, QueueMetrics, SystemMetrics, WorkerMetrics


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
        result = client._sanitize_task_signature("order_550e8400-e29b-41d4-a716-446655440000")
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
        result = client._sanitize_task_signature("task_550E8400-E29B-41D4-A716-446655440000")
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
        result = client._sanitize_queue_name("tenant-550e8400-e29b-41d4-a716-446655440000")
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


# ---------------------------------------------------------------------------
# _make_request
# ---------------------------------------------------------------------------


def _make_http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(url="", code=code, msg="", hdrs=None, fp=None)  # type: ignore[arg-type]
    err.read = lambda size=-1: body  # type: ignore[method-assign, assignment]
    return err


class TestMakeRequest:
    def _mock_urlopen(self, response_body: dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_body).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_successful_get_returns_true_and_data(self, client):
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen({"ok": True})):
            success, data = client._make_request("GET", "/api/v1/test")
        assert success is True
        assert data == {"ok": True}

    def test_successful_post_sends_payload(self, client):
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen({})) as mock_open:
            client._make_request("POST", "/api/v1/metrics", {"key": "val"})
            mock_open.assert_called_once()

    def test_http_401_returns_false(self, client):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(401)):
            success, _ = client._make_request("GET", "/api/v1/test")
        assert success is False

    def test_http_403_returns_false(self, client):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(403)):
            success, _ = client._make_request("GET", "/api/v1/test")
        assert success is False

    def test_http_429_returns_false(self, client):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(429)):
            success, _ = client._make_request("GET", "/api/v1/test")
        assert success is False

    def test_http_error_with_json_body_returns_body(self, client):
        err = _make_http_error(400, body=b'{"error": "bad request"}')
        with patch("urllib.request.urlopen", side_effect=err):
            success, data = client._make_request("GET", "/api/v1/test")
        assert success is False
        assert data == {"error": "bad request"}

    def test_url_error_returns_false(self, client):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            success, data = client._make_request("GET", "/api/v1/test")
        assert success is False
        assert data is None

    def test_unexpected_exception_returns_false(self, client):
        with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
            success, data = client._make_request("GET", "/api/v1/test")
        assert success is False
        assert data is None


# ---------------------------------------------------------------------------
# validate_api_key
# ---------------------------------------------------------------------------


class TestValidateApiKey:
    def test_returns_true_on_success(self, client):
        with patch.object(
            client,
            "_make_request",
            return_value=(True, {"organization": "acme", "plan": "pro"}),
        ):
            assert client.validate_api_key() is True

    def test_returns_false_on_failure(self, client):
        with patch.object(client, "_make_request", return_value=(False, None)):
            assert client.validate_api_key() is False

    def test_returns_false_when_response_is_none(self, client):
        with patch.object(client, "_make_request", return_value=(True, None)):
            assert client.validate_api_key() is False


# ---------------------------------------------------------------------------
# send_metrics / send_heartbeat
# ---------------------------------------------------------------------------


class TestSendMetrics:
    def test_returns_true_on_success(self, client, minimal_metrics):
        with patch.object(client, "_make_request", return_value=(True, {"alerts_triggered": 0})):
            assert client.send_metrics(minimal_metrics) is True

    def test_returns_false_on_api_failure(self, client, minimal_metrics):
        with patch.object(client, "_make_request", return_value=(False, None)):
            assert client.send_metrics(minimal_metrics) is False

    def test_calls_build_payload(self, client, minimal_metrics):
        with patch.object(client, "_make_request", return_value=(True, {})):
            with patch.object(client, "build_payload", wraps=client.build_payload) as mock_build:
                client.send_metrics(minimal_metrics)
                mock_build.assert_called_once_with(minimal_metrics)


class TestSendHeartbeat:
    def test_returns_true_on_success(self, client):
        with patch.object(client, "_make_request", return_value=(True, {})):
            assert client.send_heartbeat() is True

    def test_returns_false_on_failure(self, client):
        with patch.object(client, "_make_request", return_value=(False, None)):
            assert client.send_heartbeat() is False


def test_build_payload_includes_worker_baseline_fields():
    from kanari_agent.api_client import APIClient
    from kanari_agent.models import SystemMetrics

    client = APIClient(api_key="sk_test_placeholder", api_url="https://example.test")
    metrics = SystemMetrics(
        timestamp="2026-07-07T10:00:00Z",
        total_workers=3,
        alive_workers=3,
        expected_workers=4,
        missing_workers=1,
    )
    payload = client.build_payload(metrics)
    assert payload["metrics"]["expected_workers"] == 4
    assert payload["metrics"]["missing_workers"] == 1
