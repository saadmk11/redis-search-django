"""Opt-in hooks: a CBV mixin and an FBV decorator."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol, TypeVar, cast

from asgiref.sync import iscoroutinefunction
from django.http import HttpRequest, HttpResponse

from redis_search_django.query.instrument import (
    QueryEvent,
    RecordingCollector,
    reset_listener,
    set_listener,
)

from .carry import pop_carried_events
from .conf import should_show
from .overlay import finalize_response
from .views import explain_response, is_explain_request

_F = TypeVar("_F", bound=Callable[..., Any])


class _HasDispatch(Protocol):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any: ...


class SearchDebugMixin:
    """Record Redis Query Engine calls for this class-based view only.

    Put it first in the MRO::

        class ProductSearch(SearchDebugMixin, SearchListViewMixin, ListView):
            document_class = ProductDocument
    """

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        parent = cast(_HasDispatch, super())

        def run() -> Any:
            return parent.dispatch(request, *args, **kwargs)

        result = wrap_request(request, run)
        # Django marks the view callback as a coroutine when any handler is
        # async, then awaits dispatch(). Explain (and other short-circuits)
        # return a plain HttpResponse — wrap it so await does not TypeError.
        if getattr(self, "view_is_async", False) and not inspect.iscoroutine(result):
            return _as_coroutine(result)
        return result


def search_debug(view: _F) -> _F:
    """Record Redis Query Engine calls for this function view only.

    ::

        @search_debug
        def search(request):
            hits = ProductDocument.objects.search(q)[:20]
            return render(request, "search.html", {"hits": hits})
    """
    if iscoroutinefunction(view):

        @wraps(view)
        async def async_inner(
            request: HttpRequest, *args: Any, **kwargs: Any
        ) -> HttpResponse:
            result = wrap_request(request, lambda: view(request, *args, **kwargs))
            if inspect.iscoroutine(result):
                return cast(HttpResponse, await result)
            return cast(HttpResponse, result)

        return cast(_F, async_inner)

    @wraps(view)
    def inner(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        return wrap_request(request, lambda: view(request, *args, **kwargs))

    return cast(_F, inner)


def wrap_request(request: HttpRequest, run: Callable[[], Any]) -> Any:
    """Run *run* under a query listener when the overlay is allowed."""
    if is_explain_request(request):
        return explain_response(request)
    if not should_show(request):
        return run()
    collector = RecordingCollector()
    carried = pop_carried_events(request)
    token = set_listener(collector)
    try:
        result = run()
    except BaseException:
        reset_listener(token)
        raise
    if inspect.iscoroutine(result):
        # dispatch() only *created* the coroutine. Reset in this Context and
        # install the listener again inside the coroutine (WSGI async_to_sync
        # runs it in a different Context).
        reset_listener(token)
        return _finalize_async(request, result, collector, carried)
    return _finish(request, result, collector, carried, token)


async def _as_coroutine(result: Any) -> Any:
    return result


async def _finalize_async(
    request: HttpRequest,
    result: Awaitable[HttpResponse],
    collector: RecordingCollector,
    carried: list[QueryEvent],
) -> HttpResponse:
    token = set_listener(collector)
    try:
        response = await result
        return cast(
            HttpResponse, finalize_response(request, response, collector, carried)
        )
    finally:
        reset_listener(token)


def _finish(
    request: HttpRequest,
    response: Any,
    collector: RecordingCollector,
    carried: list[QueryEvent],
    token: Any,
) -> Any:
    try:
        return finalize_response(request, response, collector, carried)
    finally:
        reset_listener(token)
