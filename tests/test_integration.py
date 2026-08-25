from __future__ import annotations

import datetime

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.views.generic import ListView

from redis_search_django import Q
from redis_search_django.exceptions import DocumentNotFound
from redis_search_django.index import IndexManager
from redis_search_django.signals import RealtimeSignalProcessor
from redis_search_django.views import SearchListViewMixin

from .helpers import is_redis_running
from .models import Category, Product, Tag, Vendor

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture
def live_category_doc(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])
    manager = IndexManager(doc)
    manager.create()
    processor = RealtimeSignalProcessor()
    processor.setup()
    yield doc
    processor.teardown()
    manager.drop(delete_docs=True)


@pytest.fixture
def live_product_doc(nested_document_class):
    doc, _ = nested_document_class
    manager = IndexManager(doc)
    manager.create()
    processor = RealtimeSignalProcessor()
    processor.setup()
    yield doc
    processor.teardown()
    manager.drop(delete_docs=True)


def test_object_create(live_category_doc):
    category = Category.objects.create(name="test")
    hit = live_category_doc.objects.get(pk=category.pk)
    assert hit.pk == str(category.pk)
    assert hit.name == category.name


def test_object_update(live_category_doc):
    category = Category.objects.create(name="test")
    category.name = "test2"
    category.save()
    hit = live_category_doc.objects.get(pk=category.pk)
    assert hit.name == "test2"


def test_object_delete(live_category_doc):
    category = Category.objects.create(name="test")
    live_category_doc.objects.get(pk=category.pk)
    category.delete()
    with pytest.raises(DocumentNotFound):
        live_category_doc.objects.get(pk=category.pk)


def test_search_and_filter(live_category_doc):
    Category.objects.create(name="alpha")
    Category.objects.create(name="beta")
    hits = list(live_category_doc.objects.filter(name="alpha"))
    assert len(hits) == 1
    assert hits[0].name == "alpha"
    assert live_category_doc.objects.filter(name="alpha").count() == 1


def test_related_object_add(live_product_doc):
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(name="Test", price=10.0, vendor=vendor)
    hit = live_product_doc.objects.get(pk=product.pk)
    assert hit.name == product.name
    assert hit.vendor.name == vendor.name
    assert getattr(hit, "category", None) is None
    assert hit.tags == []

    category = Category.objects.create(name="test")
    product.category = category
    product.save()
    hit = live_product_doc.objects.get(pk=product.pk)
    assert hit.category.name == category.name

    tag = Tag.objects.create(name="test")
    tag2 = Tag.objects.create(name="test2")
    product.tags.set([tag, tag2])
    hit = live_product_doc.objects.get(pk=product.pk)
    assert {item.pk for item in hit.tags} == {str(tag.pk), str(tag2.pk)}


def test_related_object_update(live_product_doc):
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(name="Test", price=10.0, vendor=vendor)
    vendor.name = "test2"
    vendor.save()
    hit = live_product_doc.objects.get(pk=product.pk)
    assert hit.vendor.name == "test2"

    category = Category.objects.create(name="test")
    product.category = category
    product.save()
    category.name = "test2"
    category.save()
    hit = live_product_doc.objects.get(pk=product.pk)
    assert hit.category.name == "test2"

    tag = Tag.objects.create(name="test")
    product.tags.add(tag)
    tag.name = "test3"
    tag.save()
    hit = live_product_doc.objects.get(pk=product.pk)
    assert hit.tags[0].name == "test3"


def test_related_object_delete(live_product_doc):
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(name="Test", price=10.0, vendor=vendor)
    category = Category.objects.create(name="test")
    product.category = category
    product.save()
    category.delete()
    hit = live_product_doc.objects.get(pk=product.pk)
    assert hit.category is None

    tag = Tag.objects.create(name="test")
    tag2 = Tag.objects.create(name="test2")
    product.tags.set([tag, tag2])
    tag.delete()
    hit = live_product_doc.objects.get(pk=product.pk)
    assert [item.pk for item in hit.tags] == [str(tag2.pk)]

    vendor.delete()
    with pytest.raises(DocumentNotFound):
        live_product_doc.objects.get(pk=product.pk)


def _vendor(name: str) -> Vendor:
    return Vendor.objects.create(name=name, establishment_date=datetime.date.today())


def test_category_and_tag_text_in_filters(live_product_doc):
    books = Category.objects.create(name="Books")
    electronics = Category.objects.create(name="Electronics")
    wireless = Tag.objects.create(name="wireless")
    sale = Tag.objects.create(name="sale")
    internals = Product.objects.create(
        name="Redis Internals",
        description="Query engine handbook",
        price=42,
        category=books,
        vendor=_vendor("Paper Lantern"),
    )
    internals.tags.add(sale)
    headphones = Product.objects.create(
        name="Noise-Canceling Headphones",
        price=199,
        category=electronics,
        vendor=_vendor("Aether Labs"),
    )
    headphones.tags.add(wireless)
    Product.objects.create(
        name="Django at Scale",
        price=38,
        category=books,
        vendor=_vendor("Quiet Press"),
    )

    doc = live_product_doc
    assert doc.objects.filter(category__name__in=["Books"]).count() == 2
    assert doc.objects.filter(tags__name__in=["wireless"]).count() == 1
    assert (
        doc.objects.filter(
            category__name__in=["Books"], tags__name__in=["wireless"]
        ).count()
        == 0
    )
    search = doc.objects.filter(
        Q(name__search="redis") | Q(description__search="redis")
    )
    assert search.count() == 1
    assert search.explain()
    facets = search.facets("category__name", "tags__name")
    assert {row["value"] for row in facets["category__name"]} == {"Books"}
    books_facets = doc.objects.filter(category__name__in=["Books"]).facets(
        "category__name"
    )
    assert {row["value"] for row in books_facets["category__name"]} == {"Books"}


def test_search_view_category_query_and_tags(live_product_doc):
    books = Category.objects.create(name="Books")
    electronics = Category.objects.create(name="Electronics")
    wireless = Tag.objects.create(name="wireless")
    internals = Product.objects.create(
        name="Redis Internals",
        price=42,
        category=books,
        vendor=_vendor("Desk"),
    )
    headphones = Product.objects.create(
        name="Headphones",
        price=199,
        category=electronics,
        vendor=_vendor("Audio"),
    )
    headphones.tags.add(wireless)

    captured: dict = {}

    class View(SearchListViewMixin, ListView):
        document_class = live_product_doc
        convert_to_queryset = False

        def get_search_queryset(self):
            qs = live_product_doc.objects.all()
            query = self.request.GET.get("query")
            categories = list(filter(None, self.request.GET.getlist("category")))
            tags = list(filter(None, self.request.GET.getlist("tags")))
            if query:
                qs = qs.filter(Q(name__search=query) | Q(description__search=query))
            if categories:
                qs = qs.filter(category__name__in=categories)
            if tags:
                qs = qs.filter(tags__name__in=tags)
            return qs

        def facets(self):
            return self.get_search_queryset().facets("category__name", "tags__name")

        def render_to_response(self, context, **response_kwargs):
            captured.clear()
            captured.update(context)
            return HttpResponse("ok")

    factory = RequestFactory()
    books_response = View.as_view()(
        factory.get("/", {"category": "Books", "query": "", "min_price": ""})
    )
    assert books_response.status_code == 200
    assert {hit.pk for hit in captured["object_list"]} == {str(internals.pk)}
    assert {row["value"] for row in captured["facets"]["category__name"]} == {"Books"}

    search_response = View.as_view()(factory.get("/", {"query": "redis"}))
    assert search_response.status_code == 200
    assert {hit.pk for hit in captured["object_list"]} == {str(internals.pk)}

    tag_response = View.as_view()(factory.get("/", {"tags": "wireless"}))
    assert tag_response.status_code == 200
    assert {hit.pk for hit in captured["object_list"]} == {str(headphones.pk)}
