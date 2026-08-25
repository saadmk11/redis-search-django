from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.http import HttpRequest
from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from .panels.base import Panel

DEFAULT_PANELS: tuple[str, ...] = (
    "redis_search_django.debug.panels.queries.QueriesPanel",
    "redis_search_django.debug.panels.indexes.IndexesPanel",
    "redis_search_django.debug.panels.config.ConfigPanel",
)

EXPLAIN_PARAM = "_rsd_explain"

_show_toolbar_cached: tuple[object, Callable[[HttpRequest], bool]] | None = None
_panels_cached: tuple[tuple[object, ...], list[type[Panel]]] | None = None

DEFAULTS: dict[str, Any] = {
    "SHOW_TOOLBAR": "redis_search_django.debug.conf.show_toolbar",
    "PANELS": list(DEFAULT_PANELS),
    "SLOW_MS": 10.0,
    "INSERT_BEFORE": "</body>",
    "STORE_SIZE": 25,
    "STACKTRACES": True,
}


def debug_setting(key: str) -> Any:
    user = getattr(settings, "REDIS_SEARCH_DEBUG", None)
    if isinstance(user, dict) and key in user:
        return user[key]
    return DEFAULTS[key]


def show_toolbar(request: HttpRequest) -> bool:
    """Default callback: on when ``DEBUG`` is True.

    If ``INTERNAL_IPS`` is a non-empty sequence, the client address must
    also be in that list (same idea as Django Debug Toolbar, but empty
    ``INTERNAL_IPS`` does not hide the toolbar).
    """
    if not settings.DEBUG:
        return False
    internal = getattr(settings, "INTERNAL_IPS", None)
    if internal:
        return request.META.get("REMOTE_ADDR", "") in set(internal)
    return True


def resolve_show_toolbar() -> Callable[[HttpRequest], bool]:
    global _show_toolbar_cached
    value = debug_setting("SHOW_TOOLBAR")
    cached = _show_toolbar_cached
    if cached is not None and cached[0] == value:
        return cached[1]
    if callable(value):
        loaded: Callable[[HttpRequest], bool] = value
    elif isinstance(value, str):
        imported = import_string(value)
        if not callable(imported):
            raise TypeError("REDIS_SEARCH_DEBUG['SHOW_TOOLBAR'] must be callable.")
        loaded = imported
    else:
        raise TypeError(
            "REDIS_SEARCH_DEBUG['SHOW_TOOLBAR'] must be a callable or import path."
        )
    _show_toolbar_cached = (value, loaded)
    return loaded


def load_panels() -> list[type[Panel]]:
    global _panels_cached
    raw = debug_setting("PANELS")
    if not isinstance(raw, (list, tuple)):
        raise TypeError("REDIS_SEARCH_DEBUG['PANELS'] must be a list of import paths.")
    key = tuple(raw)
    cached = _panels_cached
    if cached is not None and cached[0] == key:
        return list(cached[1])
    panels: list[type[Panel]] = []
    for item in raw:
        if isinstance(item, str):
            panels.append(import_string(item))
        else:
            panels.append(item)
    _panels_cached = (key, panels)
    return list(panels)


def slow_ms() -> float:
    value = debug_setting("SLOW_MS")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(DEFAULTS["SLOW_MS"])
    return float(value)


def store_size() -> int:
    value = debug_setting("STORE_SIZE")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return int(DEFAULTS["STORE_SIZE"])
    return value


def insert_before() -> str:
    value = debug_setting("INSERT_BEFORE")
    if not isinstance(value, str) or not value:
        return str(DEFAULTS["INSERT_BEFORE"])
    return value


def should_show(request: HttpRequest) -> bool:
    return bool(resolve_show_toolbar()(request))


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.password:
        return url
    host = parts.hostname or ""
    if parts.username:
        netloc = f"{parts.username}:***@{host}"
    else:
        netloc = f"***@{host}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
