from __future__ import annotations

import threading
from collections import OrderedDict
from uuid import uuid4

from redis_search_django.query.instrument import QueryEvent

from .conf import store_size


class ToolbarStore:
    """Process-local LRU of recorded request traces (for on-demand explain)."""

    def __init__(self) -> None:
        self._data: OrderedDict[str, list[QueryEvent]] = OrderedDict()
        self._lock = threading.Lock()

    def save(self, events: list[QueryEvent]) -> str:
        key = uuid4().hex
        with self._lock:
            self._data[key] = list(events)
            limit = store_size()
            while len(self._data) > limit:
                self._data.popitem(last=False)
        return key

    def get(self, key: str) -> list[QueryEvent] | None:
        with self._lock:
            events = self._data.get(key)
            if events is None:
                return None
            self._data.move_to_end(key)
            return list(events)

    def get_event(self, key: str, index: int) -> QueryEvent | None:
        events = self.get(key)
        if events is None or index < 0 or index >= len(events):
            return None
        return events[index]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


store = ToolbarStore()
