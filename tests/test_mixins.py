from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import cached_property
from django.views import View

from redis_search_django.documents import JsonDocument
from redis_search_django.mixins import (
    RediSearchMixin,
    RediSearchMultipleObjectMixin,
    RediSearchTemplateResponseMixin,
)
from redis_search_django.query import RediSearchQuery
from tests.models import Category


def test_redis_search_mixin(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()
    mixin = RediSearchMixin()

    with pytest.raises(ImproperlyConfigured):
        mixin.search()

    mixin.document_class = DocumentClass

    assert mixin.search_query_expression is None
    assert mixin.sort_by is None
    assert mixin.facets() is None
    assert isinstance(mixin.search(), RediSearchQuery)


def test_redis_search_mixin_with_view(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()

    class SearchView(RediSearchMixin, View):
        document_class = DocumentClass

        @cached_property
        def search_query_expression(self):
            return self.document_class.name % "test"

        @cached_property
        def sort_by(self):
            return "name"

    view = SearchView()
    search = view.search()

    assert isinstance(search, RediSearchQuery)
    assert search.sort_fields == ["name"]
    assert search.expressions


def test_redis_search_template_response_mixin(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()

    class SearchView(RediSearchMixin, RediSearchTemplateResponseMixin, View):
        document_class = DocumentClass
        template_name = "search.html"

    view = SearchView()

    assert view.get_template_names() == ["search.html"]


def test_redis_search_template_response_mixin_without_template_name(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()

    class SearchView(RediSearchMixin, RediSearchTemplateResponseMixin, View):
        document_class = DocumentClass

    view = SearchView()

    assert view.get_template_names() == ["tests/category_list.html"]


def test_redis_search_template_response_mixin_without_document_class(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()

    class SearchView(RediSearchMixin, RediSearchTemplateResponseMixin, View):
        document_class = None

    view = SearchView()

    with pytest.raises(ImproperlyConfigured):
        view.get_template_names()


def test_redis_search_multiple_object_mixin(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()

    class SearchView(RediSearchMixin, RediSearchMultipleObjectMixin, View):
        document_class = DocumentClass
        context_object_name = "categories"

    view = SearchView()

    assert view.get_context_object_name([]) == "categories"


def test_redis_search_multiple_object_mixin_without_object_name(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()

    class SearchView(RediSearchMixin, RediSearchMultipleObjectMixin, View):
        document_class = DocumentClass

    view = SearchView()

    assert view.get_context_object_name([]) == "category_list"


def test_redis_search_multiple_object_mixin_without_document_class(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])
    DocumentClass.db = mock.MagicMock()

    class SearchView(RediSearchMixin, RediSearchMultipleObjectMixin, View):
        document_class = None

    view = SearchView()

    assert view.get_context_object_name([]) is None
