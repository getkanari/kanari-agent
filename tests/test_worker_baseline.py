"""Tests for the WorkerBaseline state machine (pure logic, no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kanari_agent.worker_baseline import WorkerBaseline

_BASE = datetime(2026, 7, 7, 10, 0, 0, tzinfo=timezone.utc)


def _t(seconds: int) -> datetime:
    return _BASE + timedelta(seconds=seconds)


def test_establishes_baseline_from_first_observation():
    b = WorkerBaseline(grace_seconds=90)
    assert b.update(4, _t(0)) == (4, 0)


def test_grows_baseline_when_fleet_grows():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    assert b.update(6, _t(30)) == (6, 0)


def test_no_missing_during_grace():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    assert b.update(3, _t(60)) == (4, 0)  # gap starts, in grace
    assert b.update(3, _t(120)) == (4, 0)  # 60s elapsed < 90s, still grace


def test_confirms_missing_after_grace():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    b.update(3, _t(60))  # gap starts at t=60
    assert b.update(3, _t(160)) == (4, 1)  # 100s elapsed >= 90s


def test_fail_loud_missing_persists():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    b.update(3, _t(60))
    assert b.update(3, _t(160)) == (4, 1)
    assert b.update(3, _t(600)) == (4, 1)  # still missing, indefinitely


def test_recovery_clears_missing():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    b.update(3, _t(60))
    b.update(3, _t(160))
    assert b.update(4, _t(200)) == (4, 0)  # fleet complete again


def test_auto_resolve_rebaselines_after_window():
    b = WorkerBaseline(grace_seconds=90, auto_resolve_seconds=300)
    b.update(4, _t(0))
    b.update(3, _t(60))  # gap starts at t=60
    assert b.update(3, _t(160)) == (4, 1)  # firing during grace..auto window
    assert b.update(3, _t(400)) == (3, 0)  # 340s >= 300s -> re-baseline to 3
