"""
Tests for kanari_agent.findings, stamps, and audit JSON output modules
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from kanari_agent.audit import _status_to_exit_code
from kanari_agent.collector import _redact_url
from kanari_agent.findings import (
    CHECK_FAMILIES,
    Finding,
    FindingsEngine,
    Severity,
    SystemStatus,
    checks_performed,
    compute_system_status,
    top_findings,
)
from kanari_agent.models import Config, QueueMetrics, SystemMetrics, WorkerMetrics


def _metrics(**kwargs) -> SystemMetrics:
    defaults: dict = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "redis_connected": True,
        "celery_connected": True,
    }
    defaults.update(kwargs)
    return SystemMetrics(**defaults)


# ---------------------------------------------------------------------------
# compute_system_status
# ---------------------------------------------------------------------------


class TestComputeSystemStatus:
    def test_empty_findings_returns_ok(self):
        assert compute_system_status([]) == SystemStatus.OK

    def test_info_only_returns_ok(self):
        findings = [Finding(id="F1", severity=Severity.INFO, title="", impact="", evidence={})]
        assert compute_system_status(findings) == SystemStatus.OK

    def test_low_severity_returns_warnings(self):
        findings = [Finding(id="F1", severity=Severity.LOW, title="", impact="", evidence={})]
        assert compute_system_status(findings) == SystemStatus.WARNINGS

    def test_medium_severity_returns_warnings(self):
        findings = [Finding(id="F1", severity=Severity.MEDIUM, title="", impact="", evidence={})]
        assert compute_system_status(findings) == SystemStatus.WARNINGS

    def test_high_severity_returns_degraded(self):
        findings = [Finding(id="F1", severity=Severity.HIGH, title="", impact="", evidence={})]
        assert compute_system_status(findings) == SystemStatus.DEGRADED

    def test_critical_severity_returns_critical(self):
        findings = [Finding(id="F1", severity=Severity.CRITICAL, title="", impact="", evidence={})]
        assert compute_system_status(findings) == SystemStatus.CRITICAL

    def test_mixed_findings_uses_max(self):
        findings = [
            Finding(id="F1", severity=Severity.LOW, title="", impact="", evidence={}),
            Finding(id="F2", severity=Severity.HIGH, title="", impact="", evidence={}),
        ]
        assert compute_system_status(findings) == SystemStatus.DEGRADED


class TestSystemStatusCriticalForRedisDow:
    def test_system_status_critical_for_redis_down(self):
        findings = [
            Finding(
                id="REDIS_DOWN",
                severity=Severity.CRITICAL,
                title="",
                impact="",
                evidence={},
            )
        ]
        assert compute_system_status(findings) == SystemStatus.CRITICAL

    def test_system_status_ok_for_empty_findings(self):
        assert compute_system_status([]) == SystemStatus.OK


# ---------------------------------------------------------------------------
# top_findings
# ---------------------------------------------------------------------------


class TestTopFindings:
    def test_returns_top_n_by_severity(self):
        findings = [
            Finding(id="F1", severity=Severity.LOW, title="", impact="", evidence={}),
            Finding(id="F2", severity=Severity.CRITICAL, title="", impact="", evidence={}),
            Finding(id="F3", severity=Severity.MEDIUM, title="", impact="", evidence={}),
            Finding(id="F4", severity=Severity.HIGH, title="", impact="", evidence={}),
        ]
        result = top_findings(findings, 2)
        assert len(result) == 2
        assert result[0].severity == Severity.CRITICAL
        assert result[1].severity == Severity.HIGH

    def test_returns_all_if_fewer_than_n(self):
        findings = [Finding(id="F1", severity=Severity.LOW, title="", impact="", evidence={})]
        result = top_findings(findings, 5)
        assert len(result) == 1

    def test_empty_returns_empty(self):
        assert top_findings([], 3) == []


# ---------------------------------------------------------------------------
# checks_performed
# ---------------------------------------------------------------------------


class TestChecksPerformed:
    def test_no_findings_all_families_ok(self):
        result = checks_performed([])
        assert len(result) == len(CHECK_FAMILIES)
        assert all(entry["status"] == "ok" for entry in result)

    def test_family_names_match_registry(self):
        names = [entry["name"] for entry in checks_performed([])]
        assert names == [name for name, _ in CHECK_FAMILIES]

    def test_dynamic_queue_backlog_id_marks_family_failed(self):
        findings = [
            Finding(
                id="QUEUE_BACKLOG_EMAILS", severity=Severity.HIGH, title="", impact="", evidence={}
            )
        ]
        result = {entry["name"]: entry["status"] for entry in checks_performed(findings)}
        assert result["queue backlog"] == "failed"
        assert result["queue SLA latency"] == "ok"
        assert result["redis connectivity"] == "ok"

    def test_dynamic_sla_breach_id_marks_family_failed(self):
        findings = [
            Finding(
                id="QUEUE_SLA_BREACH_PAYMENTS",
                severity=Severity.HIGH,
                title="",
                impact="",
                evidence={},
            )
        ]
        result = {entry["name"]: entry["status"] for entry in checks_performed(findings)}
        assert result["queue SLA latency"] == "failed"
        assert result["queue backlog"] == "ok"

    def test_exact_id_marks_family_failed(self):
        findings = [
            Finding(id="REDIS_DOWN", severity=Severity.CRITICAL, title="", impact="", evidence={})
        ]
        result = {entry["name"]: entry["status"] for entry in checks_performed(findings)}
        assert result["redis connectivity"] == "failed"

    def test_multiple_findings_multiple_failures(self):
        findings = [
            Finding(id="NO_WORKERS", severity=Severity.CRITICAL, title="", impact="", evidence={}),
            Finding(
                id="HIGH_SATURATION", severity=Severity.MEDIUM, title="", impact="", evidence={}
            ),
        ]
        statuses = {entry["name"]: entry["status"] for entry in checks_performed(findings)}
        assert statuses["worker availability"] == "failed"
        assert statuses["worker saturation"] == "failed"
        assert sum(1 for s in statuses.values() if s == "failed") == 2


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — REDIS_DOWN
# ---------------------------------------------------------------------------


class TestRedisDownFinding:
    def test_redis_down_finding(self):
        metrics = _metrics(redis_connected=False)
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert any(f.id == "REDIS_DOWN" for f in findings)
        assert any(f.severity == Severity.CRITICAL for f in findings)

    def test_no_redis_down_when_connected(self):
        metrics = _metrics(redis_connected=True)
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert not any(f.id == "REDIS_DOWN" for f in findings)


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — NO_WORKERS
# ---------------------------------------------------------------------------


class TestNoWorkersFinding:
    def test_no_workers_when_celery_not_connected(self):
        metrics = _metrics(celery_connected=False)
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert any(f.id == "NO_WORKERS" for f in findings)
        assert any(f.severity == Severity.CRITICAL for f in findings)

    def test_no_workers_when_zero_total_workers(self):
        metrics = _metrics(celery_connected=True, total_workers=0)
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert any(f.id == "NO_WORKERS" for f in findings)

    def test_no_no_workers_when_workers_present(self):
        metrics = _metrics(celery_connected=True, total_workers=2, alive_workers=2)
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert not any(f.id == "NO_WORKERS" for f in findings)


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — WORKER_OFFLINE
# ---------------------------------------------------------------------------


class TestWorkerOfflineFinding:
    def test_offline_worker_generates_finding(self):
        metrics = _metrics(
            total_workers=2,
            alive_workers=1,
            workers=[
                WorkerMetrics(name="celery@w-1", is_alive=True),
                WorkerMetrics(name="celery@w-2", is_alive=False),
            ],
        )
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert any(f.id == "WORKER_OFFLINE" for f in findings)
        assert any(f.severity == Severity.CRITICAL for f in findings)

    def test_all_alive_no_offline_finding(self):
        metrics = _metrics(
            total_workers=2,
            alive_workers=2,
            workers=[
                WorkerMetrics(name="celery@w-1", is_alive=True),
                WorkerMetrics(name="celery@w-2", is_alive=True),
            ],
        )
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert not any(f.id == "WORKER_OFFLINE" for f in findings)


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — LATENCY_UNAVAILABLE
# ---------------------------------------------------------------------------


class TestLatencyUnavailableFinding:
    def test_latency_unavailable_finding_when_queue_has_depth(self):
        metrics = _metrics(queues=[QueueMetrics(name="celery", depth=5)])
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert any(f.id == "LATENCY_UNAVAILABLE" for f in findings)

    def test_no_latency_unavailable_when_queue_empty(self):
        metrics = _metrics(queues=[QueueMetrics(name="celery", depth=0)])
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert not any(f.id == "LATENCY_UNAVAILABLE" for f in findings)

    def test_no_latency_unavailable_when_latency_available(self):
        metrics = _metrics(
            queues=[QueueMetrics(name="celery", depth=5, oldest_task_age_seconds=10.0)]
        )
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=True)
        assert not any(f.id == "LATENCY_UNAVAILABLE" for f in findings)


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — QUEUE_BACKLOG
# ---------------------------------------------------------------------------


class TestQueueBacklogFinding:
    def test_backlog_finding_when_depth_exceeds_threshold(self):
        config = Config()
        config.thresholds.max_queue_size = 100
        metrics = _metrics(queues=[QueueMetrics(name="emails", depth=500)])
        findings = FindingsEngine().analyze(metrics, config, latency_available=False)
        assert any(f.id == "QUEUE_BACKLOG_EMAILS" for f in findings)

    def test_no_backlog_when_depth_within_threshold(self):
        config = Config()
        config.thresholds.max_queue_size = 1000
        metrics = _metrics(queues=[QueueMetrics(name="celery", depth=5)])
        findings = FindingsEngine().analyze(metrics, config, latency_available=False)
        assert not any("QUEUE_BACKLOG" in f.id for f in findings)

    def test_critical_queue_gives_high_severity(self):
        config = Config()
        config.thresholds.max_queue_size = 10
        config.thresholds.critical_queues = ["emails"]
        metrics = _metrics(queues=[QueueMetrics(name="emails", depth=100)])
        findings = FindingsEngine().analyze(metrics, config, latency_available=False)
        backlog = next((f for f in findings if f.id == "QUEUE_BACKLOG_EMAILS"), None)
        assert backlog is not None
        assert backlog.severity == Severity.HIGH

    def test_non_critical_queue_gives_medium_severity(self):
        config = Config()
        config.thresholds.max_queue_size = 10
        metrics = _metrics(queues=[QueueMetrics(name="celery", depth=100)])
        findings = FindingsEngine().analyze(metrics, config, latency_available=False)
        backlog = next((f for f in findings if f.id == "QUEUE_BACKLOG_CELERY"), None)
        assert backlog is not None
        assert backlog.severity == Severity.MEDIUM


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — QUEUE_SLA_BREACH
# ---------------------------------------------------------------------------


class TestQueueSlaBreachFinding:
    def test_sla_breach_when_latency_exceeds_threshold(self):
        config = Config()
        config.thresholds.max_wait_time_seconds = 60
        metrics = _metrics(
            queues=[QueueMetrics(name="celery", depth=1, oldest_task_age_seconds=120.0)]
        )
        findings = FindingsEngine().analyze(metrics, config, latency_available=True)
        assert any("QUEUE_SLA_BREACH" in f.id for f in findings)

    def test_no_sla_breach_within_threshold(self):
        config = Config()
        config.thresholds.max_wait_time_seconds = 60
        metrics = _metrics(
            queues=[QueueMetrics(name="celery", depth=1, oldest_task_age_seconds=30.0)]
        )
        findings = FindingsEngine().analyze(metrics, config, latency_available=True)
        assert not any("QUEUE_SLA_BREACH" in f.id for f in findings)


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — STUCK_TASK
# ---------------------------------------------------------------------------


class TestStuckTaskFinding:
    def test_stuck_task_generates_high_finding(self):
        metrics = _metrics(
            stuck_tasks=[
                {
                    "task_id": "t1",
                    "task_name": "slow_task",
                    "worker": "celery@w-1",
                    "runtime_seconds": 7200,
                }
            ]
        )
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert any(f.id == "STUCK_TASK" for f in findings)
        stuck = next(f for f in findings if f.id == "STUCK_TASK")
        assert stuck.severity == Severity.HIGH

    def test_no_stuck_task_when_none(self):
        metrics = _metrics()
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert not any(f.id == "STUCK_TASK" for f in findings)


# ---------------------------------------------------------------------------
# FindingsEngine.analyze — HIGH_SATURATION
# ---------------------------------------------------------------------------


class TestHighSaturationFinding:
    def test_high_saturation_finding(self):
        metrics = _metrics(saturation_pct=85.0, total_active_tasks=17, total_concurrency=20)
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert any(f.id == "HIGH_SATURATION" for f in findings)
        sat = next(f for f in findings if f.id == "HIGH_SATURATION")
        assert sat.severity == Severity.MEDIUM

    def test_no_high_saturation_when_below_threshold(self):
        metrics = _metrics(saturation_pct=50.0, total_active_tasks=5, total_concurrency=10)
        findings = FindingsEngine().analyze(metrics, Config(), latency_available=False)
        assert not any(f.id == "HIGH_SATURATION" for f in findings)


# ---------------------------------------------------------------------------
# _status_to_exit_code (exported from audit.py)
# ---------------------------------------------------------------------------


class TestStatusToExitCode:
    def test_ok_returns_0(self):
        assert _status_to_exit_code(SystemStatus.OK) == 0

    def test_warnings_returns_1(self):
        assert _status_to_exit_code(SystemStatus.WARNINGS) == 1

    def test_degraded_returns_2(self):
        assert _status_to_exit_code(SystemStatus.DEGRADED) == 2

    def test_critical_returns_2(self):
        assert _status_to_exit_code(SystemStatus.CRITICAL) == 2

    def test_exit_code_mapping(self):
        # Explicit mapping test as per spec
        assert _status_to_exit_code(SystemStatus.OK) == 0
        assert _status_to_exit_code(SystemStatus.WARNINGS) == 1
        assert _status_to_exit_code(SystemStatus.DEGRADED) == 2
        assert _status_to_exit_code(SystemStatus.CRITICAL) == 2


# ---------------------------------------------------------------------------
# URL redaction (_redact_url from collector.py)
# ---------------------------------------------------------------------------


class TestUrlRedaction:
    def test_redacts_password_in_redis_url(self):
        # redis://:secret@host — the leading colon is the user:pass separator, stays
        assert _redact_url("redis://:secret@host:6379/0") == "redis://:***@host:6379/0"

    def test_no_password_unchanged(self):
        assert _redact_url("redis://localhost:6379/0") == "redis://localhost:6379/0"

    def test_redacts_user_and_password(self):
        assert _redact_url("redis://user:pass@host:6379/1") == "redis://user:***@host:6379/1"

    def test_empty_string_unchanged(self):
        assert _redact_url("") == ""


# ---------------------------------------------------------------------------
# KanariStampPlugin (stamps.py)
# ---------------------------------------------------------------------------


class TestStampHeaders:
    def test_adds_kanari_ts_header(self):
        from kanari_agent.stamps import KANARI_TS_HEADER, stamp_headers

        headers: dict = {"lang": "py"}
        result = stamp_headers(headers)
        assert KANARI_TS_HEADER in result
        assert isinstance(result[KANARI_TS_HEADER], float)
        # Timestamp should be recent (within last 2 seconds)
        assert abs(result[KANARI_TS_HEADER] - time.time()) < 2

    def test_preserves_existing_headers(self):
        from kanari_agent.stamps import stamp_headers

        headers = {"lang": "py", "task": "my_task"}
        result = stamp_headers(headers)
        assert result["lang"] == "py"
        assert result["task"] == "my_task"


class TestKanariStampPluginInstall:
    def test_install_connects_before_task_publish(self):
        from celery.signals import before_task_publish

        from kanari_agent.stamps import KanariStampPlugin

        app = MagicMock()
        receivers_before = len(before_task_publish.receivers)
        KanariStampPlugin.install(app)
        assert len(before_task_publish.receivers) > receivers_before

    def test_signal_handler_stamps_headers(self):
        from celery.signals import before_task_publish

        from kanari_agent.stamps import KANARI_TS_HEADER, KanariStampPlugin

        app = MagicMock()
        KanariStampPlugin.install(app)

        # Use send() which dispatches to all connected receivers
        headers: dict = {"lang": "py"}
        before_task_publish.send(sender=None, headers=headers)

        assert KANARI_TS_HEADER in headers
        assert isinstance(headers[KANARI_TS_HEADER], float)
        assert headers[KANARI_TS_HEADER] > 1_000_000_000  # epoch sanity

    def test_handler_preserved_from_gc(self):
        from kanari_agent.stamps import KanariStampPlugin

        app = MagicMock()
        KanariStampPlugin.install(app)
        assert KanariStampPlugin._handler is not None


# ---------------------------------------------------------------------------
# kanari audit --json output contract
# ---------------------------------------------------------------------------


class TestAuditJsonOutput:
    def test_json_output_is_parseable_with_required_keys(self, capsys):
        from kanari_agent.audit import _print_json_output
        from kanari_agent.findings import Severity, SystemStatus

        metrics = _metrics(
            total_pending_tasks=10,
            total_active_tasks=3,
            saturation_pct=30.0,
            max_latency_sec=5.2,
            redis_connected=True,
            celery_connected=True,
        )
        findings = [
            Finding(
                id="QUEUE_BACKLOG_CELERY",
                severity=Severity.MEDIUM,
                title="Queue backlog",
                impact="Tasks accumulating",
                evidence={"queue": "celery", "depth": 500},
            )
        ]

        _print_json_output(metrics, findings, SystemStatus.WARNINGS, 1)

        captured = capsys.readouterr()
        data = json.loads(captured.out)

        # Required top-level keys
        assert "timestamp" in data
        assert "system_status" in data
        assert "exit_code" in data
        assert "top_findings" in data
        assert "metrics" in data

        # Verify values
        assert data["system_status"] == "WARNINGS"
        assert data["exit_code"] == 1
        assert len(data["top_findings"]) == 1
        assert data["top_findings"][0]["id"] == "QUEUE_BACKLOG_CELERY"

        # Metrics sub-keys
        m = data["metrics"]
        assert m["total_pending"] == 10
        assert m["total_active"] == 3
        assert m["redis_connected"] is True
        assert m["celery_connected"] is True

    def test_json_output_empty_findings(self, capsys):
        from kanari_agent.audit import _print_json_output
        from kanari_agent.findings import SystemStatus

        metrics = _metrics()
        _print_json_output(metrics, [], SystemStatus.OK, 0)

        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert data["system_status"] == "OK"
        assert data["exit_code"] == 0
        assert data["top_findings"] == []

    def test_json_output_includes_checks_performed(self, capsys):
        from kanari_agent.audit import _print_json_output
        from kanari_agent.findings import SystemStatus

        checks = [
            {"name": "redis connectivity", "status": "ok"},
            {"name": "Celery task_acks_late", "status": "warning"},
        ]
        _print_json_output(_metrics(), [], SystemStatus.OK, 0, checks)

        data = json.loads(capsys.readouterr().out)
        assert data["checks_performed"] == checks

    def test_json_output_checks_performed_defaults_to_empty_list(self, capsys):
        from kanari_agent.audit import _print_json_output
        from kanari_agent.findings import SystemStatus

        _print_json_output(_metrics(), [], SystemStatus.OK, 0)

        data = json.loads(capsys.readouterr().out)
        assert data["checks_performed"] == []
