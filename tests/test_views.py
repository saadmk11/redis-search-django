from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from django.views.generic import ListView

from redis_search_django.indexer import Indexer
from redis_search_django.query.queryset import DocumentQuerySet
from redis_search_django.views import SearchListViewMixin

from .helpers import alive_index
from .models import Category


def test_requires_document_class():
    mixin = SearchListViewMixin()
    with pytest.raises(ImproperlyConfigured, match="document_class"):
        mixin.get_search_queryset()


def test_get_search_queryset_defaults_to_all(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin):
        document_class = doc

    qs = View().get_search_queryset()
    assert qs.document_cls is doc
    assert qs._none is False


def test_template_and_context_names(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc

    view = View()
    assert view.get_context_object_name([]) == "category_list"
    assert view.get_template_names() == ["tests/category_list.html"]


def test_get_converts_to_queryset(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        convert_to_queryset = True

        def get_search_queryset(self):
            return doc.objects.none()

        def render_to_response(self, context, **response_kwargs):
            converted = context["object_list"]
            assert isinstance(converted, QuerySet)
            assert not isinstance(converted, DocumentQuerySet)
            assert converted.model is Category
            return HttpResponse("ok")

    assert View.as_view()(RequestFactory().get("/")).status_code == 200


def test_get_renders_with_facets(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        convert_to_queryset = False

        def get_search_queryset(self):
            return []

        def facets(self):
            return {"name": []}

        def render_to_response(self, context, **response_kwargs):
            assert "facets" in context
            return HttpResponse("ok")

    request = RequestFactory().get("/")
    response = View.as_view()(request)
    assert response.status_code == 200
    assert response.content == b"ok"


async def test_async_get_renders_with_facets(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            return []

        async def afacets(self):
            return {"name": []}

        def render_to_response(self, context, **response_kwargs):
            assert context["facets"] == {"name": []}
            return HttpResponse("ok")

    request = RequestFactory().get("/")
    response = await View.as_view()(request)
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db(transaction=True)
async def test_async_view_paginates_and_rejects_bad_pages(document_class):
    names = ["page-a", "page-b", "page-c", "page-d", "page-e"]
    for name in names:
        await Category.objects.acreate(name=name)
    doc = document_class("CategoryDocument", Category, ["name"])
    captured: dict = {}

    class View(SearchListViewMixin, ListView):
        document_class = doc
        paginate_by = 2
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            return doc.objects.order_by("name")

        def render_to_response(self, context, **response_kwargs):
            captured.clear()
            captured.update(context)
            return HttpResponse("ok")

    async with alive_index(doc):
        await Indexer().aupsert_queryset(doc, Category.objects.all())
        response = await View.as_view()(RequestFactory().get("/"))
        assert response.status_code == 200
        assert captured["is_paginated"] is True
        assert captured["paginator"].count == 5
        assert [hit.name for hit in captured["object_list"]] == ["page-a", "page-b"]

        last = await View.as_view()(RequestFactory().get("/", {"page": "last"}))
        assert last.status_code == 200
        assert captured["page_obj"].number == 3
        assert [hit.name for hit in captured["object_list"]] == ["page-e"]

        with pytest.raises(Http404, match='not "last"'):
            await View.as_view()(RequestFactory().get("/", {"page": "abc"}))
        with pytest.raises(Http404, match="Invalid page"):
            await View.as_view()(RequestFactory().get("/", {"page": "99"}))


async def test_async_view_empty_raises_when_not_allowed(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        allow_empty = False
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            return []

    with pytest.raises(Http404):
        await View.as_view()(RequestFactory().get("/"))


async def test_afacets_default_does_not_call_sync_facets(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            return []

        def facets(self):
            raise AssertionError("sync facets() must not run on the async path")

        def render_to_response(self, context, **response_kwargs):
            assert context["facets"] is None
            return HttpResponse("ok")

    response = await View.as_view()(RequestFactory().get("/"))
    assert response.status_code == 200


def test_context_object_name_override_and_template_name(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        context_object_name = "hits"
        template_name = "shop/search.html"

    view = View()
    assert view.get_context_object_name([]) == "hits"
    assert view.get_template_names() == ["shop/search.html"]


def test_template_names_without_document_or_template():
    view = SearchListViewMixin()
    with pytest.raises(ImproperlyConfigured, match="template_name"):
        view.get_template_names()


def test_get_empty_raises(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        allow_empty = False
        convert_to_queryset = False

        def get_search_queryset(self):
            return []

    with pytest.raises(Http404):
        View.as_view()(RequestFactory().get("/"))


async def test_async_get_context_without_pagination(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        convert_to_queryset = True

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            return doc.objects.none()

        def render_to_response(self, context, **response_kwargs):
            converted = context["object_list"]
            assert isinstance(converted, QuerySet)
            assert not isinstance(converted, DocumentQuerySet)
            assert converted.model is Category
            assert list(converted) == []
            return HttpResponse("ok")

    response = await View.as_view()(RequestFactory().get("/"))
    assert response.status_code == 200


async def test_ais_empty_uses_aexists_on_queryset(document_class):
    doc = document_class("CatAEmpty", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        allow_empty = False
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            return doc.objects.none()

    with pytest.raises(Http404):
        await View.as_view()(RequestFactory().get("/"))


def test_default_facets_is_none():
    assert SearchListViewMixin().facets() is None


def test_context_object_name_without_document():
    view = SearchListViewMixin()
    assert view.get_context_object_name([]) is None


async def test_aget_context_uses_context_object_name(document_class):
    doc = document_class("CatCtx", Category, ["name"])

    class View(SearchListViewMixin, ListView):
        document_class = doc
        context_object_name = "hits"
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            self.object_list = []
            ctx = await self.aget_context_data(object_list=[])
            assert ctx["hits"] == []
            assert await self._ais_empty([]) is True
            return HttpResponse("ok")

    response = await View.as_view()(RequestFactory().get("/"))
    assert response.status_code == 200


async def test_aget_context_paginates_plain_list():
    class View(SearchListViewMixin, ListView):
        document_class = None
        template_name = "tests/category_list.html"
        convert_to_queryset = False
        paginate_by = 2

        async def get(self, request, *args, **kwargs):
            self.object_list = [1, 2, 3]
            ctx = await self.aget_context_data(object_list=[1, 2, 3])
            assert "category_list" not in ctx
            assert ctx["object_list"] == [1, 2]
            assert ctx["is_paginated"] is True
            assert await self._ais_empty([1]) is False
            return HttpResponse("ok")

    response = await View.as_view()(RequestFactory().get("/"))
    assert response.status_code == 200
