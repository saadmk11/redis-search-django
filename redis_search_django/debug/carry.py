"""Carry recorded events across a POST-redirect-GET.

Django Debug Toolbar uses a History store (and used to intercept redirects
with an interstitial page) so a 302 does not hide the request that ran the
writes. This package has no middleware, so the mixin stores the POST trace
in :mod:`store` and hands the id to the next opted-in GET via a signed cookie.
"""

from __future__ import annotations

from dataclasses import replace

from django.core import signing
from django.http import HttpRequest, HttpResponse

from redis_search_django.query.instrument import QueryEvent, RecordingCollector

from .store import store

CARRY_COOKIE = "rsd_debug_carry"
CARRY_SALT = "redis_search_django.debug.carry"
CARRY_MAX_AGE = 60


def request_origin(request: HttpRequest) -> str:
    return f"{request.method} {request.path}"


def is_redirect(response: object) -> bool:
    status = getattr(response, "status_code", None)
    return isinstance(status, int) and 300 <= status < 400


def persist_redirect(
    request: HttpRequest,
    response: HttpResponse,
    collector: RecordingCollector,
) -> str | None:
    """Save this request's events and set the carry cookie on a 3xx."""
    if not collector.events:
        return None
    events = [
        replace(event, origin=event.origin or request_origin(request))
        for event in collector.events
    ]
    store_id = store.save(events)
    signed = signing.dumps(store_id, salt=CARRY_SALT)
    response.set_cookie(
        CARRY_COOKIE,
        signed,
        max_age=CARRY_MAX_AGE,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return store_id


def pop_carried_events(request: HttpRequest) -> list[QueryEvent]:
    """Load events saved by the previous redirect, if the cookie is valid."""
    raw = request.COOKIES.get(CARRY_COOKIE)
    if not raw:
        return []
    try:
        store_id = signing.loads(raw, salt=CARRY_SALT, max_age=CARRY_MAX_AGE)
    except signing.BadSignature:
        return []
    if not isinstance(store_id, str):
        return []
    events = store.get(store_id)
    if not events:
        return []
    return [replace(event, carried=True) for event in events]


def clear_carry_cookie(response: HttpResponse) -> None:
    response.delete_cookie(CARRY_COOKIE, path="/", samesite="Lax")
