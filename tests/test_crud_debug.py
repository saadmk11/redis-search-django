"""Create / update / delete views must record index writes in the overlay.

Assertions inspect the stored QueryEvent list (kind, command, key), not
substrings that also appear in the overlay CSS.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.core import signing
from django.http import HttpResponse
from django.test import RequestFactory
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from redis_search_django.debug import SearchDebugMixin
from redis_search_django.debug.carry import CARRY_COOKIE, CARRY_SALT
from redis_search_django.debug.store import store
from redis_search_django.documents import Document
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer
from redis_search_django.query.instrument import QueryEvent
from redis_search_django.signals import RealtimeSignalProcessor

from .helpers import is_redis_running, live_index
from .models import Category, Product, Tag, Vendor

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]

HTML = "<html><body>page</body></html>"


class _List(SearchDebugMixin, ListView):
    model = Category

    def render_to_response(self, context, **response_kwargs):
        return HttpResponse(HTML)


class _Create(SearchDebugMixin, CreateView):
    model = Category
    fields = ["name"]
    success_url = "/crud/list/"


class _Update(SearchDebugMixin, UpdateView):
    model = Category
    fields = ["name"]
    success_url = "/crud/list/"


class _Delete(SearchDebugMixin, DeleteView):
    model = Category

    def get_success_url(self):
        return "/crud/list/"


class _ProductCreate(SearchDebugMixin, CreateView):
    model = Product
    fields = ["name", "description", "category", "vendor", "price"]
    success_url = "/crud/list/"


class _ProductUpdate(SearchDebugMixin, UpdateView):
    model = Product
    fields = ["name", "description", "category", "vendor", "price"]
    success_url = "/crud/list/"


class _ProductDelete(SearchDebugMixin, DeleteView):
    model = Product

    def get_success_url(self):
        return "/crud/list/"


def _events_from_redirect(response: HttpResponse) -> list[QueryEvent]:
    assert response.status_code == 302
    cookie = response.cookies.get(CARRY_COOKIE)
    assert cookie is not None
    store_id = signing.loads(cookie.value, salt=CARRY_SALT)
    events = store.get(store_id)
    assert events, "redirect cookie did not point at recorded events"
    assert response["X-RSD-Queries"] == str(len(events))
    return events


def _assert_write_event(events: list[QueryEvent], command: str) -> QueryEvent:
    matches = [
        event
        for event in events
        if event.query.startswith(command + " ") and event.kind in {"write", "delete"}
    ]
    assert matches, [(event.kind, event.query) for event in events]
    event = matches[0]
    assert event.key
    assert event.duration_ms >= 0
    assert event.query == f"{command} {event.key}"
    return event


def _follow(rf: RequestFactory, redirect: HttpResponse) -> HttpResponse:
    request = rf.get("/crud/list/")
    cookie = redirect.cookies.get(CARRY_COOKIE)
    if cookie is not None:
        request.COOKIES[CARRY_COOKIE] = cookie.value
    page = _List.as_view()(request)
    assert page["X-RSD-Queries"] == "0"
    cleared = page.cookies.get(CARRY_COOKIE)
    if cleared is not None:
        assert str(cleared["max-age"]) == "0"
    return page


def _assert_overlay_command(page: HttpResponse, command: str) -> None:
    assert page.status_code == 200
    assert b'id="rsd-debug"' in page.content
    marker = f'<pre class="rsd-code">{command} '.encode()
    assert marker in page.content
    assert b"previous request" in page.content


@pytest.fixture
def toolbar_on(settings):
    settings.DEBUG = True
    return settings


@pytest.fixture
def category_doc(document_class):
    doc = document_class("CatCrudDebug", Category, ["name"])
    proc = RealtimeSignalProcessor()
    proc.connect_document(doc)
    with live_index(doc):
        yield doc
    proc.teardown()


@pytest.fixture
def product_docs(nested_document_class):
    product_doc, _embedded = nested_document_class
    proc = RealtimeSignalProcessor()
    proc.connect_document(product_doc)
    with live_index(product_doc):
        yield product_doc
    proc.teardown()


def test_create_update_delete_category_overlay(toolbar_on, category_doc, rf):
    created = _Create.as_view()(rf.post("/crud/add/", {"name": "DebugCat"}))
    events = _events_from_redirect(created)
    write = _assert_write_event(events, "JSON.SET")
    assert write.document == "CatCrudDebug"
    page = _follow(rf, created)
    _assert_overlay_command(page, "JSON.SET")
    assert b"POST /crud/add/" in page.content
    obj = Category.objects.get(name="DebugCat")
    assert category_doc.objects.get(pk=obj.pk).name == "DebugCat"
    assert write.key == category_doc.key_for(obj.pk)

    updated = _Update.as_view()(
        rf.post(f"/crud/{obj.pk}/edit/", {"name": "DebugCat2"}), pk=obj.pk
    )
    events = _events_from_redirect(updated)
    _assert_write_event(events, "JSON.SET")
    page = _follow(rf, updated)
    _assert_overlay_command(page, "JSON.SET")
    assert category_doc.objects.get(pk=obj.pk).name == "DebugCat2"

    deleted = _Delete.as_view()(rf.post(f"/crud/{obj.pk}/delete/"), pk=obj.pk)
    events = _events_from_redirect(deleted)
    delete = _assert_write_event(events, "DEL")
    assert delete.kind == "delete"
    page = _follow(rf, deleted)
    _assert_overlay_command(page, "DEL")
    assert b'class="rsd-kind rsd-kind-delete"' in page.content
    with pytest.raises(category_doc.DoesNotExist):
        category_doc.objects.get(pk=obj.pk)


def test_create_update_delete_product_overlay(toolbar_on, product_docs, rf):
    vendor = Vendor.objects.create(
        name="CrudVendor", establishment_date=datetime.date(2020, 1, 1)
    )
    category = Category.objects.create(name="CrudCat")
    created = _ProductCreate.as_view()(
        rf.post(
            "/crud/products/add/",
            {
                "name": "Crud Product",
                "description": "from overlay test",
                "category": str(category.pk),
                "vendor": str(vendor.pk),
                "price": "12.50",
            },
        )
    )
    events = _events_from_redirect(created)
    write = _assert_write_event(events, "JSON.SET")
    page = _follow(rf, created)
    _assert_overlay_command(page, "JSON.SET")
    product = Product.objects.get(name="Crud Product")
    assert product_docs.objects.get(pk=product.pk).name == "Crud Product"
    assert write.key == product_docs.key_for(product.pk)

    updated = _ProductUpdate.as_view()(
        rf.post(
            f"/crud/products/{product.pk}/edit/",
            {
                "name": "Crud Product Edited",
                "description": "updated",
                "category": str(category.pk),
                "vendor": str(vendor.pk),
                "price": "15.00",
            },
        ),
        pk=product.pk,
    )
    _assert_write_event(_events_from_redirect(updated), "JSON.SET")
    page = _follow(rf, updated)
    _assert_overlay_command(page, "JSON.SET")
    assert product_docs.objects.get(pk=product.pk).name == "Crud Product Edited"

    deleted = _ProductDelete.as_view()(
        rf.post(f"/crud/products/{product.pk}/delete/"), pk=product.pk
    )
    delete = _assert_write_event(_events_from_redirect(deleted), "DEL")
    assert delete.key == product_docs.key_for(product.pk)
    page = _follow(rf, deleted)
    _assert_overlay_command(page, "DEL")
    with pytest.raises(product_docs.DoesNotExist):
        product_docs.objects.get(pk=product.pk)


def test_related_category_update_reindexes_product(toolbar_on, product_docs, rf):
    vendor = Vendor.objects.create(
        name="RelVendor", establishment_date=datetime.date(2020, 1, 1)
    )
    category = Category.objects.create(name="RelCat")
    product = Product.objects.create(
        name="Rel Product",
        vendor=vendor,
        category=category,
        price=9,
    )
    Indexer().upsert(product_docs, product)

    updated = _Update.as_view()(
        rf.post(f"/crud/{category.pk}/edit/", {"name": "RelCat2"}), pk=category.pk
    )
    write = _assert_write_event(_events_from_redirect(updated), "JSON.SET")
    assert write.document == product_docs.__name__
    assert write.key == product_docs.key_for(product.pk)
    page = _follow(rf, updated)
    _assert_overlay_command(page, "JSON.SET")
    hit = product_docs.objects.get(pk=product.pk)
    category_data = getattr(hit, "category", None)
    name = (
        category_data.get("name")
        if isinstance(category_data, dict)
        else getattr(category_data, "name", None)
    )
    assert name == "RelCat2"


def test_tag_hash_create_update_delete_overlay(toolbar_on, rf):
    uid = uuid.uuid4().hex[:8]

    class TagHash(Document):
        class Django:
            model = Tag
            fields = ["name"]

        class Index:
            storage = "hash"
            name = f"idx:test.tag.hashcrud.{uid}"
            prefix = f"rsd:test.tag.hashcrud.{uid}:"

    proc = RealtimeSignalProcessor()
    proc.connect_document(TagHash)
    manager = IndexManager(TagHash)
    manager.create()
    try:

        class TagCreate(SearchDebugMixin, CreateView):
            model = Tag
            fields = ["name"]
            success_url = "/crud/list/"

        class TagUpdate(SearchDebugMixin, UpdateView):
            model = Tag
            fields = ["name"]
            success_url = "/crud/list/"

        class TagDelete(SearchDebugMixin, DeleteView):
            model = Tag

            def get_success_url(self):
                return "/crud/list/"

        created = TagCreate.as_view()(rf.post("/crud/tags/add/", {"name": "hash-tag"}))
        write = _assert_write_event(_events_from_redirect(created), "HSET")
        page = _follow(rf, created)
        _assert_overlay_command(page, "HSET")
        tag = Tag.objects.get(name="hash-tag")
        assert TagHash.objects.get(pk=tag.pk).name == "hash-tag"
        assert write.key == TagHash.key_for(tag.pk)

        updated = TagUpdate.as_view()(
            rf.post(f"/crud/tags/{tag.pk}/edit/", {"name": "hash-tag-2"}), pk=tag.pk
        )
        _assert_write_event(_events_from_redirect(updated), "HSET")
        page = _follow(rf, updated)
        _assert_overlay_command(page, "HSET")
        assert TagHash.objects.get(pk=tag.pk).name == "hash-tag-2"

        deleted = TagDelete.as_view()(
            rf.post(f"/crud/tags/{tag.pk}/delete/"), pk=tag.pk
        )
        _assert_write_event(_events_from_redirect(deleted), "DEL")
        page = _follow(rf, deleted)
        _assert_overlay_command(page, "DEL")
        with pytest.raises(TagHash.DoesNotExist):
            TagHash.objects.get(pk=tag.pk)
    finally:
        proc.teardown()
        manager.drop(delete_docs=True)


def test_create_get_shows_empty_overlay(toolbar_on, rf):
    class FormPage(SearchDebugMixin, CreateView):
        model = Category
        fields = ["name"]
        success_url = "/crud/list/"

        def get(self, request, *args, **kwargs):
            return HttpResponse(HTML)

    page = FormPage.as_view()(rf.get("/crud/add/"))
    assert page.status_code == 200
    assert page["X-RSD-Queries"] == "0"
    assert b'id="rsd-debug"' in page.content
    assert b'<pre class="rsd-code">JSON.SET ' not in page.content
    assert (
        page.cookies.get(CARRY_COOKIE) is None
        or str(page.cookies[CARRY_COOKIE]["max-age"]) == "0"
    )
