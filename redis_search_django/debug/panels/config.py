from __future__ import annotations

from typing import Any

from django.conf import settings

from redis_search_django.conf import DEFAULTS, redis_search_setting
from redis_search_django.debug.conf import DEFAULTS as DEBUG_DEFAULTS
from redis_search_django.debug.conf import debug_setting, redact_url

from .base import Panel


class ConfigPanel(Panel):
    title = "Config"
    panel_id = "config"
    template = "redis_search_django/debug/panels/config.html"

    def generate_stats(self, request: object, response: object) -> None:
        self.stats = {
            "redis_search": _pairs(DEFAULTS, redis_search_setting, redact_urls=True),
            "debug": _pairs(DEBUG_DEFAULTS, debug_setting),
            "debug_enabled": bool(getattr(settings, "DEBUG", False)),
        }


def _pairs(
    defaults: dict[str, Any],
    getter: Any,
    *,
    redact_urls: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in defaults:
        value = getter(key)
        text = _display(value)
        if redact_urls and key == "URL" and isinstance(value, str):
            text = redact_url(value)
        rows.append({"key": key, "value": text})
    return rows


def _display(value: object) -> str:
    if callable(value):
        name = getattr(value, "__qualname__", type(value).__name__)
        module = getattr(value, "__module__", "")
        return f"{module}.{name}" if module else str(name)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        if all(isinstance(item, str) for item in value):
            return ", ".join(value)
    return repr(value)
