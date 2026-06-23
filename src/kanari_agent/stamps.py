"""
Kanari Stamps — optional latency tracking for Celery tasks.

Usage in your Celery app:
    from kanari_agent.stamps import KanariStampPlugin

    app = Celery(...)
    KanariStampPlugin.install(app)

This adds a 'kanari_sent_ts' header to every published task.
The agent reads this header to compute queue age-of-oldest accurately.
"""

from __future__ import annotations

import time
from typing import Any

KANARI_TS_HEADER = "kanari_sent_ts"


def stamp_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Add kanari timestamp to task headers. Call before publish."""
    headers[KANARI_TS_HEADER] = time.time()
    return headers


class KanariStampPlugin:
    """Celery signal-based plugin that auto-stamps all published tasks."""

    _handler: Any = None  # prevent GC of signal handler

    @classmethod
    def install(cls, app: Any) -> None:
        """Install the stamp plugin into a Celery app."""
        from celery.signals import before_task_publish

        def add_kanari_stamp(headers: dict, **kwargs: Any) -> None:
            headers[KANARI_TS_HEADER] = time.time()

        cls._handler = add_kanari_stamp
        before_task_publish.connect(add_kanari_stamp)
