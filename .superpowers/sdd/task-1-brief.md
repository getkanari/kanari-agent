### Task 1: `WorkerBaseline` state machine (agent)

**Files:**
- Create: `~/Projects/kanari-agent/src/kanari_agent/worker_baseline.py`
- Test: `~/Projects/kanari-agent/tests/test_worker_baseline.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `WorkerBaseline(grace_seconds: int = 90, auto_resolve_seconds: int | None = None)` with method `update(alive: int, now: datetime) -> tuple[int, int]` returning `(expected_workers, missing_workers)`. Mutable attributes `baseline: int` and `gap_since: datetime | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_worker_baseline.py`:

```python
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
    assert b.update(3, _t(60)) == (4, 0)   # gap starts, in grace
    assert b.update(3, _t(120)) == (4, 0)  # 60s elapsed < 90s, still grace


def test_confirms_missing_after_grace():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    b.update(3, _t(60))                     # gap starts at t=60
    assert b.update(3, _t(160)) == (4, 1)   # 100s elapsed >= 90s


def test_fail_loud_missing_persists():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    b.update(3, _t(60))
    assert b.update(3, _t(160)) == (4, 1)
    assert b.update(3, _t(600)) == (4, 1)   # still missing, indefinitely


def test_recovery_clears_missing():
    b = WorkerBaseline(grace_seconds=90)
    b.update(4, _t(0))
    b.update(3, _t(60))
    b.update(3, _t(160))
    assert b.update(4, _t(200)) == (4, 0)   # fleet complete again


def test_auto_resolve_rebaselines_after_window():
    b = WorkerBaseline(grace_seconds=90, auto_resolve_seconds=300)
    b.update(4, _t(0))
    b.update(3, _t(60))                      # gap starts at t=60
    assert b.update(3, _t(160)) == (4, 1)    # firing during grace..auto window
    assert b.update(3, _t(400)) == (3, 0)    # 340s >= 300s -> re-baseline to 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/kanari-agent && poetry run pytest tests/test_worker_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kanari_agent.worker_baseline'`.

- [ ] **Step 3: Write the implementation**

Create `src/kanari_agent/worker_baseline.py`:

```python
"""Worker-offline detection: a count-based baseline state machine.

The agent is a stateful daemon, so it can remember the worker fleet across
cycles. Celery's ``inspect`` only reports *live* workers, so a crashed worker
simply vanishes and ``alive`` drops. This component turns that raw count into
two stable numbers the backend can alert on:

- ``expected_workers``: high-water mark of alive workers seen this session.
- ``missing_workers``: confirmed shortfall, once a gap outlives the grace period.

Design notes / known limitations:
- Baseline lives in memory only. Restarting the agent re-baselines from the
  currently-alive workers (a worker down during downtime is not detected until
  the fleet is seen complete again). Persisting to disk is a deliberate non-goal.
- Count-based, not name-based: robust to ephemeral worker names (k8s/autoscaling)
  but cannot name the specific missing worker.
- Cannot distinguish a crash from an intentional scale-down without an external
  signal. Default policy is fail-loud; opt into auto-resolve via
  ``auto_resolve_seconds``.
"""

from __future__ import annotations

from datetime import datetime


class WorkerBaseline:
    def __init__(self, grace_seconds: int = 90, auto_resolve_seconds: int | None = None):
        self.grace_seconds = grace_seconds
        self.auto_resolve_seconds = auto_resolve_seconds
        self.baseline = 0
        self.gap_since: datetime | None = None

    def update(self, alive: int, now: datetime) -> tuple[int, int]:
        """Feed one cycle's alive-worker count; return (expected, missing)."""
        # Fleet complete or grown: reset and adopt the new high-water mark.
        if alive >= self.baseline:
            self.baseline = alive
            self.gap_since = None
            return self.baseline, 0

        # Capacity is missing.
        if self.gap_since is None:
            self.gap_since = now
            return self.baseline, 0

        elapsed = (now - self.gap_since).total_seconds()

        # Auto-resolve (opt-in) wins once its (longer) window elapses.
        if self.auto_resolve_seconds is not None and elapsed >= self.auto_resolve_seconds:
            self.baseline = alive
            self.gap_since = None
            return self.baseline, 0

        if elapsed >= self.grace_seconds:
            return self.baseline, self.baseline - alive

        return self.baseline, 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Projects/kanari-agent && poetry run pytest tests/test_worker_baseline.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/kanari-agent
git add src/kanari_agent/worker_baseline.py tests/test_worker_baseline.py
git commit -m "feat: add WorkerBaseline state machine for worker-offline detection"
```

---
