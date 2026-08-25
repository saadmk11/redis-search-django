"""In-process stand-in for a Celery task + signal processor.

The package does not depend on Celery. User projects copy this pattern and
replace ``DummyTask.delay`` with ``@shared_task`` / ``.delay()``.
"""

from __future__ import annotations

import json
from typing import Any

from redis_search_django import apply_index_action
from redis_search_django.signals import BaseSignalProcessor


class DummyTask:
    """Queue ``(action, payload)`` pairs the way Celery ``.delay()`` would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def delay(self, action: str, payload: dict[str, Any]) -> DummyTask:
        serialized = json.loads(json.dumps(payload, default=str))
        self.calls.append((action, serialized))
        return self

    def apply(self) -> int:
        """Worker: run queued actions with ``apply_index_action``."""
        count = 0
        for action, payload in self.calls:
            apply_index_action(action, payload)
            count += 1
        self.calls.clear()
        return count


class DummyCelerySignalProcessor(BaseSignalProcessor):
    def __init__(self, task: DummyTask | None = None) -> None:
        super().__init__()
        self.task = task or DummyTask()

    def dispatch(self, action: str, payload: dict[str, Any]) -> None:
        self.task.delay(action, payload)
