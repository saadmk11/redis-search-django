from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string

from redis_search_django.query.instrument import RecordingCollector

from .conf import load_panels
from .panels.base import Panel
from .store import store


class DebugToolbar:
    """Collects panel stats for one request and renders the overlay."""

    def __init__(self, request: HttpRequest, collector: RecordingCollector) -> None:
        self.request = request
        self.collector = collector
        self.store_id = ""
        self.panels: list[Panel] = []
        for cls in load_panels():
            panel = cls(self)
            if panel.enabled():
                self.panels.append(panel)

    def generate_stats(self, response: HttpResponse) -> None:
        if self.collector.events:
            self.store_id = store.save(self.collector.events)
        else:
            self.store_id = ""
        for panel in self.panels:
            try:
                panel.generate_stats(self.request, response)
            except Exception as exc:
                panel.error = f"{type(exc).__name__}: {exc}"

    def render(self) -> str:
        return render_to_string(
            "redis_search_django/debug/toolbar.html",
            {
                "toolbar": self,
                "panels": self.panels,
                "store_id": self.store_id,
                "request": self.request,
                "query_count": len(self.collector.live_events()),
                "total_ms": self.collector.total_ms,
            },
        )
