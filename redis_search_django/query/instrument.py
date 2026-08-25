"""Optional per-request query recording.

The debug overlay installs a listener via :func:`capture_queries`. With no
listener (the default) :func:`observe` is a no-op: one ``ContextVar`` read.
"""

from __future__ import annotations

import inspect
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

_STACK_LIMIT = 24
_HIDE_PATH_PARTS = (
    "/redis_search_django/query/",
    "/redis_search_django/debug/",
    "/site-packages/",
    "/dist-packages/",
    "/_pytest/",
    "/pluggy/",
)
_HIDE_PATH_SUFFIXES = (
    "/redis_search_django/indexer.py",
    "/redis_search_django/signals.py",
    "/redis_search_django/actions.py",
    "/contextlib.py",
)
_HIDE_MODULES = (
    "django.",
    "redis.",
    "asgiref.",
    "_pytest.",
    "pytest.",
    "pluggy.",
    "coverage.",
)
_HIDE_BASENAMES = frozenset({"pytest", "py.test"})


class QueryListener(Protocol):
    def record(self, event: QueryEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class StackFrame:
    """One filtered stack frame for the debug overlay."""

    filename: str
    lineno: int
    function: str

    def label(self) -> str:
        return f"{_short_path(self.filename)}:{self.lineno} in {self.function}"


def hidden_stack_frame(filename: str, module: str = "") -> bool:
    """Return True if this frame is library, test-runner, or framework internals."""
    if not filename or filename.startswith("<"):
        return True
    path = filename.replace("\\", "/")
    if any(part in path for part in _HIDE_PATH_PARTS):
        return True
    if any(path.endswith(suffix) for suffix in _HIDE_PATH_SUFFIXES):
        return True
    if path.rsplit("/", 1)[-1] in _HIDE_BASENAMES:
        return True
    return _module_hidden(module)


def _module_hidden(module: str) -> bool:
    if not module:
        return False
    return any(
        module == prefix.rstrip(".") or module.startswith(prefix)
        for prefix in _HIDE_MODULES
    )


def _short_path(filename: str) -> str:
    path = filename.replace("\\", "/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else filename


def capture_call_site() -> tuple[str | None, tuple[StackFrame, ...]]:
    """Walk the stack, skip hidden frames, return (location, filtered stack)."""
    found: list[StackFrame] = []
    for info in inspect.stack(context=0):
        if len(found) >= _STACK_LIMIT:
            break
        filename = info.filename
        module = str(info.frame.f_globals.get("__name__", "") or "")
        if hidden_stack_frame(filename, module):
            continue
        found.append(StackFrame(filename, info.lineno, info.function))
    if not found:
        return None, ()
    return found[0].label(), tuple(found)


def _stacktraces_enabled() -> bool:
    from django.conf import settings

    user = getattr(settings, "REDIS_SEARCH_DEBUG", None) or {}
    if isinstance(user, dict) and "STACKTRACES" in user:
        return bool(user["STACKTRACES"])
    return True


_listener: ContextVar[QueryListener | None] = ContextVar(
    "redis_search_query_listener", default=None
)


@dataclass(slots=True)
class QueryEvent:
    """One Redis Query Engine round-trip observed during a request."""

    kind: str
    document: str
    index: str
    query: str
    duration_ms: float
    params: dict[str, Any] = field(default_factory=dict)
    total: int | None = None
    offset: int | None = None
    limit: int | None = None
    sort: str | None = None
    knn: bool = False
    extra: bool = False
    error: str | None = None
    key: str | None = None
    dialect: int = 2
    carried: bool = False
    origin: str | None = None
    location: str | None = None
    stack: tuple[StackFrame, ...] = ()

    def fingerprint(self) -> tuple[object, ...]:
        items = tuple(sorted((str(k), repr(v)) for k, v in self.params.items()))
        return (self.kind, self.index, self.query, items)

    def redis_command(self) -> str:
        if self.kind == "search":
            parts = [f"FT.SEARCH {self.index} {self.query!r}"]
            if self.offset is not None and self.limit is not None:
                parts.append(f"LIMIT {self.offset} {self.limit}")
            if self.sort:
                descending = self.sort.startswith("-")
                name = self.sort[1:] if descending else self.sort
                parts.append(f"SORTBY {name} {'DESC' if descending else 'ASC'}")
            if self.dialect:
                parts.append(f"DIALECT {self.dialect}")
            return " ".join(parts)
        if self.kind == "aggregate":
            return f"FT.AGGREGATE {self.index} {self.query!r}"
        if self.kind == "explain":
            return f"FT.EXPLAIN {self.index} {self.query!r}"
        return self.query


class RecordingCollector:
    """In-memory listener used by the toolbar and by tests."""

    def __init__(self) -> None:
        self.events: list[QueryEvent] = []

    def record(self, event: QueryEvent) -> None:
        self.events.append(event)

    def live_events(self) -> list[QueryEvent]:
        """Events from this request only (exclude POST-redirect carry)."""
        return [event for event in self.events if not event.carried]

    @property
    def total_ms(self) -> float:
        return sum(event.duration_ms for event in self.live_events())


def observe_write(
    document_cls: Any,
    command: str,
    key: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    """Time a JSON.SET / HSET / DEL when a listener is installed."""
    meta = getattr(document_cls, "_meta", None)
    return observe(
        kind="delete" if command == "DEL" else "write",
        document=getattr(document_cls, "__name__", str(document_cls)),
        index=getattr(meta, "index_alias", "") if meta is not None else "",
        query=f"{command} {key}",
        key=key,
        params=params,
        dialect=getattr(meta, "dialect", 2) if meta is not None else 2,
    )


def observe_pipeline(document_cls: Any, count: int) -> Any:
    """Time one pipeline ``execute()`` of index writes."""
    meta = getattr(document_cls, "_meta", None)
    return observe(
        kind="write",
        document=getattr(document_cls, "__name__", str(document_cls)),
        index=getattr(meta, "index_alias", "") if meta is not None else "",
        query=f"PIPELINE {count}",
        params={"commands": count},
        dialect=getattr(meta, "dialect", 2) if meta is not None else 2,
    )


def current_listener() -> QueryListener | None:
    return _listener.get()


def set_listener(listener: QueryListener | None) -> Token[QueryListener | None]:
    return _listener.set(listener)


def reset_listener(token: Token[QueryListener | None]) -> None:
    try:
        _listener.reset(token)
    except ValueError:
        # Token came from another Context (WSGI async_to_sync).
        _listener.set(None)


@contextmanager
def capture_queries() -> Generator[RecordingCollector, None, None]:
    collector = RecordingCollector()
    token = set_listener(collector)
    try:
        yield collector
    finally:
        reset_listener(token)


def query_text(query: object) -> str:
    """Best-effort RediSearch query string from a redis-py ``Query`` or ``str``."""
    if query is None:
        return ""
    if isinstance(query, str):
        return query
    getter = getattr(query, "query_string", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:
            pass
    return str(query)


def sanitize(value: object) -> object:
    """Replace vectors / blobs so JSON and HTML stay readable."""
    if isinstance(value, memoryview):
        return f"<{len(value)}-byte blob>"
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)}-byte blob>"
    if isinstance(value, list):
        if value and all(isinstance(item, (int, float)) for item in value):
            return f"<{len(value)}-float vector>"
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {str(key): sanitize(value) for key, value in (params or {}).items()}


@contextmanager
def observe(
    *,
    kind: str,
    document: str,
    index: str,
    query: str,
    params: dict[str, Any] | None = None,
    offset: int | None = None,
    limit: int | None = None,
    sort: str | None = None,
    knn: bool = False,
    extra: bool = False,
    key: str | None = None,
    dialect: int = 2,
) -> Generator[dict[str, Any] | None, None, None]:
    """Time a Redis call and record it when a listener is installed.

    Yields a mutable dict the caller may set ``total`` on, or ``None`` when
    nothing is listening (callers must not assume a dict).
    """
    listener = current_listener()
    if listener is None:
        yield None
        return
    location: str | None = None
    stack: tuple[StackFrame, ...] = ()
    if _stacktraces_enabled():
        location, stack = capture_call_site()
    start = perf_counter()
    patch: dict[str, Any] = {}
    error: str | None = None
    try:
        yield patch
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        listener.record(
            QueryEvent(
                kind=kind,
                document=document,
                index=index,
                query=query,
                duration_ms=(perf_counter() - start) * 1000.0,
                params=dict(params or {}),
                total=patch.get("total"),
                offset=offset,
                limit=limit,
                sort=sort,
                knn=knn,
                extra=extra,
                error=error,
                key=key,
                dialect=dialect,
                location=location,
                stack=stack,
            )
        )
