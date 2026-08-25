from __future__ import annotations

import datetime
import uuid

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.views.generic import ListView

from redis_search_django import Aggregate, Q, aapply_index_action
from redis_search_django.actions import document_label
from redis_search_django.enums import IndexAction, MigrateOutcome
from redis_search_django.exceptions import DocumentNotFound
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer
from redis_search_django.views import SearchListViewMixin

from .helpers import is_redis_running
from .models import Category, Product, Tag, Vendor

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture
async def live_category_doc(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])
    manager = IndexManager(doc)
    await manager.acreate()
    yield doc
    await manager.adrop(delete_docs=True)


@pytest.fixture
async def live_hash_doc(document_class):
    uid = uuid.uuid4().hex[:8]
    doc = document_class(
        "CategoryHashDocument",
        Category,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "name": f"idx:test.category.hashasync.{uid}",
                    "prefix": f"rsd:test.category.hashasync.{uid}:",
                    "storage": "hash",
                },
            )
        },
    )
    manager = IndexManager(doc)
    await manager.acreate()
    yield doc
    await manager.adrop(delete_docs=True)


async def _aindex(document_cls, *instances) -> None:
    indexer = Indexer()
    for instance in instances:
        await indexer.aupsert(document_cls, instance)


async def test_async_get_search_count_and_iter(live_category_doc):
    alpha = await Category.objects.acreate(name="alpha")
    await Category.objects.acreate(name="beta")
    await _aindex(live_category_doc, alpha)
    hit = await live_category_doc.objects.aget(pk=alpha.pk)
    assert hit.pk == str(alpha.pk)
    assert hit.name == alpha.name
    assert await live_category_doc.objects.filter(name="alpha").acount() == 1
    assert await live_category_doc.objects.filter(name="alpha").aexists() is True
    hits = [item async for item in live_category_doc.objects.filter(name="alpha")]
    assert [item.name for item in hits] == ["alpha"]


async def test_async_get_missing_and_explain(live_category_doc):
    with pytest.raises(DocumentNotFound):
        await live_category_doc.objects.aget(pk=999_999)
    assert await live_category_doc.objects.filter(name="missing").aexplain()


async def test_async_to_queryset_preserves_order(live_category_doc):
    first = await Category.objects.acreate(name="zeta")
    second = await Category.objects.acreate(name="alpha")
    await _aindex(live_category_doc, first, second)
    qs = await live_category_doc.objects.order_by("name").ato_queryset()
    assert [obj.pk async for obj in qs] == [second.pk, first.pk]


async def test_async_facets_and_aggregate(live_category_doc):
    first = await Category.objects.acreate(name="alpha")
    second = await Category.objects.acreate(name="alpha")
    third = await Category.objects.acreate(name="beta")
    await _aindex(live_category_doc, first, second, third)
    facets = await live_category_doc.objects.afacets("name")
    values = {row["value"]: row["count"] for row in facets["name"]}
    assert values["alpha"] == 2
    assert values["beta"] == 1
    rows = await live_category_doc.objects.aaggregate(
        Aggregate().group_by("name").count("count")
    )
    counts = {row.get("name") or row.get("value"): int(row["count"]) for row in rows}
    assert counts["alpha"] == 2


async def test_amigrate_and_arebuild(live_category_doc):
    manager = IndexManager(live_category_doc)
    assert await manager.amigrate() is MigrateOutcome.NO_OP
    category = await Category.objects.acreate(name="rebuilt")
    count = await Indexer().arebuild(live_category_doc)
    assert count >= 1
    hit = await live_category_doc.objects.aget(pk=category.pk)
    assert hit.name == "rebuilt"


async def test_async_indexer_upsert_and_delete(live_category_doc):
    category = await Category.objects.acreate(name="indexed")
    indexer = Indexer()
    await indexer.aupsert(live_category_doc, category)
    hit = await live_category_doc.objects.aget(pk=category.pk)
    assert hit.name == "indexed"
    await indexer.adelete(live_category_doc, category.pk)
    with pytest.raises(DocumentNotFound):
        await live_category_doc.objects.aget(pk=category.pk)
    manager = IndexManager(live_category_doc)
    assert await manager.aexists() is True
    assert await manager.ainfo()


async def test_async_populate_hash_and_json(live_hash_doc, document_class):
    category = await Category.objects.acreate(name="hashed")
    indexer = Indexer()
    await indexer.aupsert(live_hash_doc, category)
    hit = await live_hash_doc.objects.aget_by_pk(category.pk)
    assert hit.name == "hashed"

    json_doc = document_class("CategoryAsyncPop", Category, ["name"])
    count = await indexer.apopulate(json_doc)
    assert count >= 1
    loaded = await json_doc.objects.aget(pk=category.pk)
    assert loaded.name == "hashed"
    await IndexManager(json_doc).adrop(delete_docs=True)


async def test_aapply_index_action_live(document_class):
    doc = document_class("CategoryAsyncAction", Category, ["name"])
    manager = IndexManager(doc)
    await manager.acreate()
    try:
        category = await Category.objects.acreate(name="via-action")
        await aapply_index_action(
            IndexAction.UPSERT,
            {"document": document_label(doc), "pk": category.pk},
        )
        hit = await doc.objects.aget(pk=category.pk)
        assert hit.name == "via-action"
        await aapply_index_action(
            IndexAction.DELETE,
            {"document": document_label(doc), "pk": category.pk},
        )
        with pytest.raises(DocumentNotFound):
            await doc.objects.aget(pk=category.pk)
    finally:
        await manager.adrop(delete_docs=True)


async def test_async_search_view(live_category_doc):
    match = await Category.objects.acreate(name="Redis Guide")
    other = await Category.objects.acreate(name="Other")
    await _aindex(live_category_doc, match, other)
    captured: dict = {}

    class View(SearchListViewMixin, ListView):
        document_class = live_category_doc
        convert_to_queryset = False

        async def get(self, request, *args, **kwargs):
            return await self.aget(request, *args, **kwargs)

        def get_search_queryset(self):
            qs = live_category_doc.objects.all()
            query = self.request.GET.get("query")
            if query:
                qs = qs.filter(Q(name__search=query))
            return qs

        async def afacets(self):
            return await self.get_search_queryset().afacets("name")

        def render_to_response(self, context, **response_kwargs):
            captured.clear()
            captured.update(context)
            return HttpResponse("ok")

    response = await View.as_view()(RequestFactory().get("/", {"query": "redis"}))
    assert response.status_code == 200
    assert {hit.pk for hit in captured["object_list"]} == {str(match.pk)}
    assert captured["facets"]["name"]


async def test_async_related_reindex(nested_document_class):
    product_doc, _ = nested_document_class
    manager = IndexManager(product_doc)
    await manager.acreate()
    try:
        vendor = await Vendor.objects.acreate(
            name="Lab", establishment_date=datetime.date.today()
        )
        product = await Product.objects.acreate(
            name="Widget", price=10.0, vendor=vendor
        )
        tag = await Tag.objects.acreate(name="sale")
        await product.tags.aadd(tag)
        indexer = Indexer()
        await indexer.aupsert(product_doc, product)
        vendor.name = "Lab Two"
        await vendor.asave()
        await indexer.areindex_related(product_doc, vendor)
        hit = await product_doc.objects.aget(pk=product.pk)
        assert hit.vendor.name == "Lab Two"
    finally:
        await manager.adrop(delete_docs=True)
