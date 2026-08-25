from __future__ import annotations

import json
from collections import Counter
from typing import Any

from django.http import HttpRequest

from redis_search_django.debug.conf import EXPLAIN_PARAM, slow_ms
from redis_search_django.query.instrument import QueryEvent, sanitize_params

from .base import Panel


class QueriesPanel(Panel):
    title = "Queries"
    panel_id = "queries"
    template = "redis_search_django/debug/panels/queries.html"

    def nav_subtitle(self) -> str:
        count = self.stats.get("count", 0)
        total = self.stats.get("total_ms", 0.0)
        return f"{count} · {total:.1f}ms"

    def generate_stats(self, request: object, response: object) -> None:
        events = self.toolbar.collector.events
        threshold = slow_ms()
        counts = Counter(event.fingerprint() for event in events)
        store_id = self.toolbar.store_id
        request = self.toolbar.request
        rows = [
            _row(
                event,
                index,
                counts[event.fingerprint()] > 1,
                threshold,
                store_id,
                request,
            )
            for index, event in enumerate(events)
        ]
        live = [row for row in rows if not row["carried"]]
        kinds = Counter(row["kind"] for row in live)
        self.stats = {
            "count": len(live),
            "total_ms": sum(row["duration_ms"] for row in live),
            "slow": sum(1 for row in live if row["slow"]),
            "duplicates": sum(1 for row in live if row["duplicate"]),
            "carried": sum(1 for row in rows if row["carried"]),
            "kinds": [
                {"kind": kind, "count": kinds[kind]}
                for kind in ("search", "aggregate", "explain", "get", "write", "delete")
                if kinds[kind]
            ],
            "rows": rows,
        }


def _row(
    event: QueryEvent,
    index: int,
    duplicate: bool,
    threshold: float,
    store_id: str,
    request: HttpRequest,
) -> dict[str, Any]:
    return {
        "index": index,
        "kind": event.kind,
        "document": event.document,
        "index_name": event.index,
        "query": event.query,
        "command": event.redis_command(),
        "params": sanitize_params(event.params),
        "params_json": json.dumps(sanitize_params(event.params), indent=2, default=str),
        "duration_ms": event.duration_ms,
        "total": event.total,
        "offset": event.offset,
        "limit": event.limit,
        "sort": event.sort,
        "knn": event.knn,
        "extra": event.extra,
        "error": event.error,
        "key": event.key,
        "carried": event.carried,
        "origin": event.origin,
        "location": event.location,
        "stack": [frame.label() for frame in event.stack],
        "duplicate": duplicate,
        "slow": event.duration_ms >= threshold,
        "explainable": event.kind in {"search", "aggregate", "explain"},
        "explain_url": _explain_url(request, store_id, index),
    }


def _explain_url(request: HttpRequest, store_id: str, index: int) -> str:
    if not store_id:
        return ""
    params = request.GET.copy()
    params[EXPLAIN_PARAM] = f"{store_id}:{index}"
    return f"{request.path}?{params.urlencode()}"
