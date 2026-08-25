"""Helpers for debug panels in the example app."""

from __future__ import annotations

import json
from typing import Any

from redis_search_django.query.results import SearchHit


def display_value(value: Any) -> Any:
    """Replace vectors / blobs so templates and JSON dumps stay readable."""
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)}-byte blob>"
    if isinstance(value, list):
        if value and all(isinstance(item, (int, float)) for item in value):
            return f"<{len(value)}-float vector>"
        return [display_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): display_value(item) for key, item in value.items()}
    return value


def display_params(params: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (params or {}).items():
        out[str(key)] = display_value(value)
    return out


def hit_payload(hit: SearchHit | None) -> dict[str, Any]:
    if hit is None:
        return {}
    return {
        "pk": hit.pk,
        "score": hit.score,
        "data": display_value(hit.data),
    }


def hits_payload(hits: list[SearchHit]) -> list[dict[str, Any]]:
    return [hit_payload(hit) for hit in hits]


def pretty(value: Any) -> str:
    return json.dumps(display_value(value), indent=2, default=str)
