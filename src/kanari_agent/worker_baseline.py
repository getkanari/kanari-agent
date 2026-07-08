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
from typing import Optional


class WorkerBaseline:
    def __init__(self, grace_seconds: int = 90, auto_resolve_seconds: Optional[int] = None):
        self.grace_seconds = grace_seconds
        self.auto_resolve_seconds = auto_resolve_seconds
        self.baseline = 0
        self.gap_since: Optional[datetime] = None

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
