from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

from redis_search_django.client import get_redis_connection
from redis_search_django.redis import Query

from .conf import EXPLAIN_PARAM, resolve_show_toolbar
from .store import store


def is_explain_request(request: HttpRequest) -> bool:
    return EXPLAIN_PARAM in request.GET


def explain_response(request: HttpRequest) -> HttpResponse:
    """Serve FT.EXPLAIN for a stored query on the same view URL."""
    if not resolve_show_toolbar()(request):
        return JsonResponse({"error": "toolbar disabled"}, status=403)
    parsed = _parse_explain(request)
    if parsed is None:
        return JsonResponse({"error": "unknown query"}, status=404)
    store_id, index = parsed
    event = store.get_event(store_id, index)
    if event is None:
        return JsonResponse({"error": "unknown query"}, status=404)
    if event.kind not in {"search", "aggregate", "explain"}:
        return JsonResponse({"error": "cannot explain this operation"}, status=400)
    try:
        result = (
            get_redis_connection()
            .ft(event.index)
            .explain(
                Query(event.query).dialect(event.dialect),
                query_params=event.params or None,
            )
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"explain": str(result)})


def _parse_explain(request: HttpRequest) -> tuple[str, int] | None:
    raw = request.GET.get(EXPLAIN_PARAM, "")
    if not raw or ":" not in raw:
        return None
    store_id, _, index_s = raw.rpartition(":")
    if not store_id:
        return None
    try:
        return store_id, int(index_s)
    except ValueError:
        return None
