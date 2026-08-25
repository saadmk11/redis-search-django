from __future__ import annotations

from django.conf import settings

from .types import SettingValue

DEFAULTS: dict[str, SettingValue] = {
    "URL": "redis://localhost:6379/0",
    "PREFIX": "rsd",
    "AUTO_INDEX": True,
    "DIALECT": 2,
    "DEFAULT_STORAGE": "json",
    "CHUNK_SIZE": 2000,
    "SOCKET_TIMEOUT": 5,
    "SIGNAL_PROCESSOR": "redis_search_django.signals.RealtimeSignalProcessor",
    "SIGNAL_ERRORS": "raise",
    "TO_QUERYSET_WARN": 1000,
    "TO_QUERYSET_MAX": 5000,
    "CONNECTION": None,
    "ASYNC_CONNECTION": None,
}


def redis_search_setting(key: str) -> object:
    """Return a REDIS_SEARCH setting, falling back to package defaults.

    A key present in ``REDIS_SEARCH`` is returned as-is, even if the type is
    wrong. :func:`setting_str` / :func:`setting_int` / :func:`setting_bool`
    enforce types for callers that need a specific shape.
    """
    user = getattr(settings, "REDIS_SEARCH", None) or {}
    if key in user:
        return user[key]
    return DEFAULTS[key]


def setting_str(key: str) -> str:
    value = redis_search_setting(key)
    if not isinstance(value, str):
        raise TypeError(f"REDIS_SEARCH[{key!r}] must be a string.")
    return value


def setting_int(key: str) -> int:
    value = redis_search_setting(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"REDIS_SEARCH[{key!r}] must be an int.")
    return value


def setting_bool(key: str) -> bool:
    value = redis_search_setting(key)
    if not isinstance(value, bool):
        raise TypeError(f"REDIS_SEARCH[{key!r}] must be a bool.")
    return value
