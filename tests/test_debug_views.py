"""Sync and async coverage for SearchDebugMixin and @search_debug."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from django.http import HttpResponse
from django.test import AsyncClient, Client, RequestFactory
from django.views import View
from django.views.generic import ListView

from redis_search_django.debug import SearchDebugMixin, search_debug
from redis_search_django.query.instrument import current_listener
from redis_search_django.views import SearchListViewMixin

from .models import Category


def _request(path="/"):
    return RequestFactory().get(path)


def _html(body: str = "ok") -> HttpResponse:
    return HttpResponse(f"<html><body>{body}</body></html>")


def _fake_search(*, total: int = 2) -> Mock:
    result = Mock()
    result.total = total
    result.docs = []
    ft = Mock()
    ft.search.return_value = result
    client = Mock()
    client.ft.return_value = ft
    return client


def _fake_async_search(*, total: int = 2) -> Mock:
    result = Mock()
    result.total = total
    result.docs = []
    ft = AsyncMock()
    ft.search.return_value = result
    client = Mock()
    client.ft.return_value = ft
    return client


@pytest.fixture
def toolbar_on(settings):
    settings.DEBUG = True
    return settings


# --- Django test clients -------------------------------------------------


def test_sync_client_mixin_and_decorator(toolbar_on):
    client = Client()
    mixin = client.get("/")
    assert mixin.status_code == 200
    assert b'id="rsd-debug"' in mixin.content
    assert mixin["X-RSD-Queries"] == "0"

    decorated = client.get("/fn/")
    assert b'id="rsd-debug"' in decorated.content
    assert decorated["X-RSD-Queries"] == "0"

    plain = client.get("/plain/")
    assert b"rsd-debug" not in plain.content
    assert "X-RSD-Queries" not in plain

    # WSGI + async view (runserver path). Must not raise ContextVar errors.
    async_page = client.get("/async/")
    assert async_page.status_code == 200
    assert b'id="rsd-debug"' in async_page.content

    async_tpl = client.get("/async-tpl/")
    assert async_tpl.status_code == 200
    assert b"async-tpl" in async_tpl.content
    assert b'id="rsd-debug"' in async_tpl.content


@pytest.mark.asyncio
async def test_async_client_mixin_and_decorator(toolbar_on):
    client = AsyncClient()
    mixin = await client.get("/async/")
    assert mixin.status_code == 200
    assert b'id="rsd-debug"' in mixin.content
    assert mixin["X-RSD-Queries"] == "0"

    decorated = await client.get("/afn/")
    assert b'id="rsd-debug"' in decorated.content
    assert decorated["X-RSD-Queries"] == "0"


def test_post_redirect_carries_writes_to_next_page(toolbar_on):
    client = Client()
    posted = client.post("/save/")
    assert posted.status_code == 302
    assert posted["X-RSD-Queries"] == "1"
    after = client.get("/after/")
    assert after.status_code == 200
    assert after["X-RSD-Queries"] == "0"
    assert b'<pre class="rsd-code">JSON.SET rsd:cat:1' in after.content
    assert b"previous request" in after.content
    assert b"POST /save/" in after.content
    again = client.get("/after/")
    assert again["X-RSD-Queries"] == "0"
    assert b'<pre class="rsd-code">JSON.SET rsd:cat:1' not in again.content


@pytest.mark.asyncio
async def test_async_post_redirect_carries_writes(toolbar_on):
    client = AsyncClient()
    posted = await client.post("/save/")
    assert posted.status_code == 302
    assert posted["X-RSD-Queries"] == "1"
    after = await client.get("/after/")
    assert after["X-RSD-Queries"] == "0"
    assert b'<pre class="rsd-code">JSON.SET rsd:cat:1' in after.content
    assert b"previous request" in after.content
    again = await client.get("/after/")
    assert again["X-RSD-Queries"] == "0"
    assert b'<pre class="rsd-code">JSON.SET rsd:cat:1' not in again.content


# --- Query recording through views ---------------------------------------


def test_sync_mixin_records_search(document_class, monkeypatch, toolbar_on):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatMixinSync", Category, ["name"])
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: _fake_search(total=4))

    class Page(SearchDebugMixin, View):
        def get(self, request):
            count = doc.objects.filter(name="X").count()
            return _html(str(count))

    response = Page.as_view()(_request("/search/"))
    assert response.status_code == 200
    assert response["X-RSD-Queries"] == "1"
    assert b"CatMixinSync" in response.content
    assert b"FT.SEARCH" in response.content
    assert b"4" in response.content


def test_sync_decorator_records_search(document_class, monkeypatch, toolbar_on):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatDecoSync", Category, ["name"])
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: _fake_search(total=3))

    @search_debug
    def page(request):
        count = doc.objects.filter(name="Y").count()
        return _html(str(count))

    response = page(_request("/fn-search/"))
    assert response["X-RSD-Queries"] == "1"
    assert b"CatDecoSync" in response.content
    assert page.__name__ == "page"


@pytest.mark.asyncio
async def test_async_mixin_records_search(document_class, monkeypatch, toolbar_on):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatMixinAsync", Category, ["name"])
    monkeypatch.setattr(
        qs_mod, "get_async_redis_connection", lambda: _fake_async_search(total=5)
    )

    class Page(SearchDebugMixin, View):
        async def get(self, request):
            count = await doc.objects.filter(name="X").acount()
            return _html(str(count))

    response = await Page.as_view()(_request("/async-search/"))
    assert response.status_code == 200
    assert response["X-RSD-Queries"] == "1"
    assert b"CatMixinAsync" in response.content
    assert b"FT.SEARCH" in response.content


@pytest.mark.asyncio
async def test_async_decorator_records_search(document_class, monkeypatch, toolbar_on):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatDecoAsync", Category, ["name"])
    monkeypatch.setattr(
        qs_mod, "get_async_redis_connection", lambda: _fake_async_search(total=7)
    )

    @search_debug
    async def page(request):
        count = await doc.objects.filter(name="Z").acount()
        return _html(str(count))

    response = await page(_request("/afn-search/"))
    assert response["X-RSD-Queries"] == "1"
    assert b"CatDecoAsync" in response.content
    assert page.__name__ == "page"


# --- SearchListViewMixin composition -------------------------------------


def test_sync_search_list_mixin_composition(document_class, toolbar_on):
    doc = document_class("CatListSync", Category, ["name"])

    class Page(SearchDebugMixin, SearchListViewMixin, ListView):
        document_class = doc
        convert_to_queryset = False

        def get_search_queryset(self):
            return doc.objects.none()

        def render_to_response(self, context, **response_kwargs):
            return _html("list")

    response = Page.as_view()(_request("/list/"))
    assert response.status_code == 200
    assert b"rsd-debug" in response.content
    assert response["X-RSD-Queries"] == "0"


@pytest.mark.asyncio
async def test_async_search_list_mixin_composition(document_class, toolbar_on):
    doc = document_class("CatListAsync", Category, ["name"])

    class Page(SearchDebugMixin, SearchListViewMixin, ListView):
        document_class = doc
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            return doc.objects.none()

        async def afacets(self):
            return {}

        def render_to_response(self, context, **response_kwargs):
            return _html("alist")

    response = await Page.as_view()(_request("/alist/"))
    assert response.status_code == 200
    assert b"rsd-debug" in response.content
    assert response["X-RSD-Queries"] == "0"


# --- Explain intercept does not run the view -----------------------------


def test_sync_mixin_explain_skips_view(toolbar_on):
    ran = {"n": 0}

    class Page(SearchDebugMixin, View):
        def get(self, request):
            ran["n"] += 1
            return _html("ran")

    response = Page.as_view()(RequestFactory().get("/", {"_rsd_explain": "missing:0"}))
    assert ran["n"] == 0
    assert response.status_code == 404
    assert json.loads(response.content)["error"] == "unknown query"


@pytest.mark.asyncio
async def test_async_mixin_explain_skips_view(toolbar_on):
    ran = {"n": 0}

    class Page(SearchDebugMixin, View):
        async def get(self, request):
            ran["n"] += 1
            return _html("ran")

    # Django awaits dispatch() on async CBVs. The explain short-circuit must
    # return an awaitable JsonResponse, not a bare HttpResponse.
    response = await Page.as_view()(
        RequestFactory().get("/", {"_rsd_explain": "missing:0"})
    )
    assert ran["n"] == 0
    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/json")
    assert json.loads(response.content)["error"] == "unknown query"


@pytest.mark.asyncio
async def test_async_client_explain_on_async_probe(toolbar_on):
    client = AsyncClient()
    response = await client.get("/async/", {"_rsd_explain": "missing:0"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown query"


def test_sync_decorator_explain_skips_view(toolbar_on):
    ran = {"n": 0}

    @search_debug
    def page(request):
        ran["n"] += 1
        return _html("ran")

    response = page(RequestFactory().get("/", {"_rsd_explain": "missing:0"}))
    assert ran["n"] == 0
    assert response.status_code == 404


# --- Errors reset the listener -------------------------------------------


def test_sync_mixin_error_resets_listener(toolbar_on):
    class Page(SearchDebugMixin, View):
        def get(self, request):
            raise RuntimeError("sync boom")

    with pytest.raises(RuntimeError, match="sync boom"):
        Page.as_view()(_request("/"))
    assert current_listener() is None


@pytest.mark.asyncio
async def test_async_mixin_error_resets_listener(toolbar_on):
    class Page(SearchDebugMixin, View):
        async def get(self, request):
            raise RuntimeError("async boom")

    with pytest.raises(RuntimeError, match="async boom"):
        await Page.as_view()(_request("/"))
    assert current_listener() is None


@pytest.mark.asyncio
async def test_async_mixin_hidden_when_debug_off(settings):
    settings.DEBUG = False

    class Page(SearchDebugMixin, View):
        async def get(self, request):
            return _html("off")

    response = await Page.as_view()(_request("/"))
    assert b"rsd-debug" not in response.content
    assert "X-RSD-Queries" not in response


def test_already_rendered_template_response_is_not_rerendered(toolbar_on):
    from django.template import engines
    from django.template.response import SimpleTemplateResponse

    template = engines["django"].from_string("<html><body>ready</body></html>")
    response = SimpleTemplateResponse(template)
    response.render()
    assert response.is_rendered

    class Page(SearchDebugMixin, View):
        def get(self, request):
            return response

    out = Page.as_view()(_request("/"))
    assert out is response
    assert b"rsd-debug" in out.content
