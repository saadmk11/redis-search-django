"""Attach the overlay to a single view response."""

from __future__ import annotations

import asyncio
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.utils.encoding import force_str

from redis_search_django.query.instrument import QueryEvent, RecordingCollector

from .carry import clear_carry_cookie, is_redirect, persist_redirect
from .conf import insert_before
from .toolbar import DebugToolbar


def finalize_response(
    request: HttpRequest,
    response: Any,
    collector: RecordingCollector,
    carried: list[QueryEvent] | None = None,
) -> Any:
    """Persist a redirect trace, or inject the overlay (plus carried POST rows)."""
    if not isinstance(response, HttpResponse):
        return response
    if is_redirect(response):
        persist_redirect(request, response, collector)
        _set_headers(response, collector)
        return response
    if carried:
        collector.events[:0] = carried
    rendered = attach_overlay(request, response, collector)
    clear_carry_cookie(rendered)
    return rendered


def attach_overlay(
    request: HttpRequest, response: Any, collector: RecordingCollector
) -> Any:
    if not isinstance(response, HttpResponse):
        return response

    def apply(rendered: HttpResponse) -> HttpResponse:
        return _stamp_and_inject(request, rendered, collector)

    render = getattr(response, "render", None)
    unrendered = callable(render) and not getattr(response, "is_rendered", True)
    if unrendered:
        if _in_async() and hasattr(response, "add_post_render_callback"):
            # TemplateResponse.render() may touch the ORM. Django will render
            # it later via sync_to_async / the WSGI handler.
            response.add_post_render_callback(apply)
            _set_headers(response, collector)
            return response
        assert callable(render)
        response = render()
    return apply(response)


def _in_async() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _set_headers(response: HttpResponse, collector: RecordingCollector) -> None:
    response["X-RSD-Queries"] = str(len(collector.live_events()))
    response["X-RSD-Query-Time"] = f"{collector.total_ms:.3f}"


def _stamp_and_inject(
    request: HttpRequest, response: HttpResponse, collector: RecordingCollector
) -> HttpResponse:
    _set_headers(response, collector)
    if _can_inject(response):
        if not apps.is_installed("redis_search_django.debug"):
            raise ImproperlyConfigured(
                "Add 'redis_search_django.debug' to INSTALLED_APPS to use "
                "SearchDebugMixin or @search_debug."
            )
        toolbar = DebugToolbar(request, collector)
        toolbar.generate_stats(response)
        _inject(response, toolbar.render())
    return response


def can_inject(response: HttpResponse) -> bool:
    return _can_inject(response)


def _can_inject(response: HttpResponse) -> bool:
    if getattr(response, "streaming", False):
        return False
    if response.get("Content-Encoding"):
        return False
    content_type = response.get("Content-Type", "")
    if "text/html" not in content_type:
        return False
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)):
        return False
    if b'id="rsd-debug"' in content:
        return False
    return True


def _inject(response: HttpResponse, html: str) -> None:
    charset = getattr(response, "charset", None) or "utf-8"
    try:
        body = force_str(response.content, encoding=charset)
    except (UnicodeDecodeError, LookupError, TypeError):
        return
    marker = insert_before()
    index = body.lower().rfind(marker.lower())
    if index == -1:
        return
    body = body[:index] + html + body[index:]
    try:
        response.content = body.encode(charset)
    except UnicodeEncodeError:
        return
    if "Content-Length" in response:
        response["Content-Length"] = str(len(response.content))
