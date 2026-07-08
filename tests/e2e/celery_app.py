"""Minimal Celery app used exclusively by E2E tests."""

from __future__ import annotations

import os

from celery import Celery

broker = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")

app = Celery("e2e_worker", broker=broker, backend=broker)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
)


@app.task
def add(x: int, y: int) -> int:
    return x + y
