from __future__ import annotations

import pytest

from redis_search_django.indexer import Indexer

from .helpers import alive_index, is_redis_running, live_index
from .models import Category


@pytest.mark.django_db
def test_get_instances_from_related(
    nested_document_class, product_with_tag, category_obj
):
    product, tag = product_with_tag
    product.category = category_obj
    product.save()
    product_doc, _ = nested_document_class
    assert product_doc.get_instances_from_related(product.vendor) == product
    parents = list(product_doc.get_instances_from_related(tag))
    assert product in parents
    parents = list(product_doc.get_instances_from_related(category_obj))
    assert product in parents


def test_parent_list_empty_and_cascade_without_model(document_class):
    from redis_search_django.indexer import _parent_list

    doc = document_class("CatPar", Category, ["name"])
    assert _parent_list(doc, Category(name="x")) == []
    doc._meta.model = None
    assert Indexer()._cascade_deletes_parent(doc, Category(name="x")) is False


@pytest.mark.django_db(transaction=True)
def test_should_index_false_deletes_existing_key(category_obj):
    from redis_search_django.documents import Document

    class Filtered(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.filtered"
            prefix = "rsd:test.category.filtered:"

        @classmethod
        def should_index(cls, instance):
            return instance.name != "skip"

    with live_index(Filtered):
        indexer = Indexer()
        indexer.upsert(Filtered, category_obj)
        assert Filtered.objects.get(pk=category_obj.pk).name == category_obj.name
        category_obj.name = "skip"
        category_obj.save()
        indexer.upsert(Filtered, category_obj)
        with pytest.raises(Filtered.DoesNotExist):
            Filtered.objects.get(pk=category_obj.pk)


@pytest.mark.django_db(transaction=True)
async def test_aupsert_should_index_false_deletes_existing_key(category_obj):
    from redis_search_django.documents import Document

    class Filtered(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.afiltered"
            prefix = "rsd:test.category.afiltered:"

        @classmethod
        def should_index(cls, instance):
            return instance.name != "skip-async"

    async with alive_index(Filtered):
        indexer = Indexer()
        await indexer.aupsert(Filtered, category_obj)
        hit = await Filtered.objects.aget(pk=category_obj.pk)
        assert hit.name == category_obj.name
        category_obj.name = "skip-async"
        await category_obj.asave()
        await indexer.aupsert(Filtered, category_obj)
        with pytest.raises(Filtered.DoesNotExist):
            await Filtered.objects.aget(pk=category_obj.pk)


@pytest.mark.django_db(transaction=True)
def test_cascade_vendor_skips_parent_upsert(nested_document_class, product_obj):
    product_doc, _ = nested_document_class
    indexer = Indexer()
    assert indexer._cascade_deletes_parent(product_doc, product_obj.vendor) is True
    with live_index(product_doc):
        indexer.upsert(product_doc, product_obj)
        indexer.reindex_related(product_doc, product_obj.vendor, deleting=True)
        assert product_doc.objects.get(pk=product_obj.pk).name == product_obj.name


@pytest.mark.django_db(transaction=True)
async def test_areindex_related_cascade_skips(nested_document_class, product_obj):
    product_doc, _ = nested_document_class
    indexer = Indexer()
    async with alive_index(product_doc):
        await indexer.aupsert(product_doc, product_obj)
        await indexer.areindex_related(product_doc, product_obj.vendor, deleting=True)
        hit = await product_doc.objects.aget(pk=product_obj.pk)
        assert hit.name == product_obj.name


@pytest.mark.django_db(transaction=True)
def test_category_set_null_reindexes(nested_document_class, product_obj, category_obj):
    product_obj.category = category_obj
    product_obj.save()
    product_doc, _ = nested_document_class
    indexer = Indexer()
    assert indexer._cascade_deletes_parent(product_doc, category_obj) is False
    with live_index(product_doc):
        indexer.upsert(product_doc, product_obj)
        assert (
            product_doc.objects.get(pk=product_obj.pk).category.name
            == category_obj.name
        )
        indexer.reindex_related(product_doc, category_obj, exclude=category_obj)
        hit = product_doc.objects.get(pk=product_obj.pk)
        assert getattr(hit, "category", None) is None


@pytest.mark.django_db(transaction=True)
def test_upsert_queryset_and_rebuild_live(document_class, category_obj):
    doc = document_class("CatQs", Category, ["name"])
    with live_index(doc):
        indexer = Indexer()
        assert indexer.upsert_queryset(doc, Category.objects.all()) >= 1
        assert indexer.rebuild(doc) >= 1
        assert doc.objects.get(pk=category_obj.pk).name == category_obj.name


@pytest.mark.django_db(transaction=True)
async def test_aupsert_queryset_with_prefetch(nested_document_class, product_with_tag):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.index import IndexManager

    product_doc, _ = nested_document_class
    qs = product_doc.get_queryset()
    indexer = Indexer()
    count = await indexer.aupsert_queryset(product_doc, qs)
    assert count >= 1
    await IndexManager(product_doc).adrop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
def test_hash_upsert_and_should_index_false_queryset(document_class):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.documents import Document
    from redis_search_django.index import IndexManager

    category = Category.objects.create(name="HashMe")

    class HashDoc(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.category.hashup"
            prefix = "rsd:test.category.hashup:"

        @classmethod
        def should_index(cls, instance):
            return instance.name != "skip"

    indexer = Indexer()
    indexer.upsert(HashDoc, category)
    assert HashDoc.objects.get(pk=category.pk).name == "HashMe"
    skip = Category.objects.create(name="skip")
    indexer.upsert_queryset(HashDoc, Category.objects.all())
    with pytest.raises(HashDoc.DoesNotExist):
        HashDoc.objects.get(pk=skip.pk)
    IndexManager(HashDoc).drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
def test_upsert_queryset_chunks_and_prefetch(
    nested_document_class, product_with_tag, settings
):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.index import IndexManager

    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    product_doc, _ = nested_document_class
    Category.objects.create(name="Another")
    indexer = Indexer()
    assert indexer.upsert_queryset(product_doc, product_doc.get_queryset()) >= 1
    IndexManager(product_doc).drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
def test_upsert_queryset_resolves_write_prefixes_once(
    document_class, settings, monkeypatch
):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.index import IndexManager
    from redis_search_django.indexer import write_prefixes as real_write_prefixes

    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    Category.objects.create(name="P1")
    Category.objects.create(name="P2")
    doc = document_class("CatPrefixOnce", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    calls: list[int] = []

    def spy(document_cls):
        calls.append(1)
        return real_write_prefixes(document_cls)

    monkeypatch.setattr("redis_search_django.indexer.write_prefixes", spy)
    indexer.upsert_queryset(doc, Category.objects.all())
    assert calls == [1]
    IndexManager(doc).drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
def test_rebuild_when_schema_requires_rebuild(document_class, category_obj):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.index import IndexManager

    doc = document_class("CatReb", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    doc._meta.key_prefix = "rsd:test.category.rebuiltpref:"
    assert indexer.rebuild(doc) >= 1
    IndexManager(doc).drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
async def test_aupsert_queryset_hash_chunks_and_should_index_false(
    document_class, settings
):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.documents import Document
    from redis_search_django.index import IndexManager

    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    keep = await Category.objects.acreate(name="KeepHash")
    skip = await Category.objects.acreate(name="skip")

    class HashDoc(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.category.ahashqs"
            prefix = "rsd:test.category.ahashqs:"

        @classmethod
        def should_index(cls, instance):
            return instance.name != "skip"

    indexer = Indexer()
    await indexer.aupsert(HashDoc, keep)
    count = await indexer.aupsert_queryset(HashDoc, Category.objects.all())
    assert count >= 2
    hit = await HashDoc.objects.aget(pk=keep.pk)
    assert hit.name == "KeepHash"
    with pytest.raises(HashDoc.DoesNotExist):
        await HashDoc.objects.aget(pk=skip.pk)
    await IndexManager(HashDoc).adrop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
async def test_arebuild_when_schema_requires_rebuild(document_class, category_obj):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.index import IndexManager

    doc = document_class("CatAReb", Category, ["name"])
    indexer = Indexer()
    assert await indexer.apopulate(doc) >= 1
    assert await indexer.apopulate(doc) >= 1
    doc._meta.key_prefix = "rsd:test.category.arebuiltpref:"
    assert await indexer.arebuild(doc) >= 1
    await IndexManager(doc).adrop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
def test_populate_when_index_already_exists(document_class, category_obj):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django.index import IndexManager

    doc = document_class("CatPopExists", Category, ["name"])
    manager = IndexManager(doc)
    manager.create()
    try:
        assert Indexer().populate(doc) >= 1
        assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
        assert Indexer().populate(doc) >= 1
    finally:
        manager.drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
def test_reindex_related_should_index_false_and_hash(product_obj):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django import fields
    from redis_search_django.documents import Document
    from redis_search_django.index import IndexManager

    from .conftest import make_document
    from .models import Product, Vendor

    vendor_doc = make_document("VendRelH", Vendor, ["name"], embedded=True)

    class ProductHash(Document):
        vendor = fields.Object(vendor_doc)

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.product.hashrel"
            prefix = "rsd:test.product.hashrel:"

        @classmethod
        def should_index(cls, instance):
            return instance.name != "skip-me"

    indexer = Indexer()
    indexer.upsert(ProductHash, product_obj)
    assert ProductHash.objects.get(pk=product_obj.pk).name == product_obj.name
    indexer.reindex_related(ProductHash, product_obj.vendor)
    product_obj.name = "skip-me"
    product_obj.save()
    indexer.reindex_related(ProductHash, product_obj.vendor)
    with pytest.raises(ProductHash.DoesNotExist):
        ProductHash.objects.get(pk=product_obj.pk)
    IndexManager(ProductHash).drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
async def test_areindex_related_should_index_false_and_hash(product_obj):
    if not is_redis_running():
        pytest.skip("Redis is not running")
    from redis_search_django import fields
    from redis_search_django.documents import Document
    from redis_search_django.index import IndexManager

    from .conftest import make_document
    from .models import Product, Vendor

    vendor_doc = make_document("VendARelH", Vendor, ["name"], embedded=True)

    class ProductHash(Document):
        vendor = fields.Object(vendor_doc)

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.product.ahashrel"
            prefix = "rsd:test.product.ahashrel:"

        @classmethod
        def should_index(cls, instance):
            return instance.name != "skip-async"

    indexer = Indexer()
    await indexer.aupsert(ProductHash, product_obj)
    await indexer.areindex_related(ProductHash, product_obj.vendor)
    hit = await ProductHash.objects.aget(pk=product_obj.pk)
    assert hit.name == product_obj.name
    product_obj.name = "skip-async"
    await product_obj.asave()
    await indexer.areindex_related(ProductHash, product_obj.vendor)
    with pytest.raises(ProductHash.DoesNotExist):
        await ProductHash.objects.aget(pk=product_obj.pk)
    await IndexManager(ProductHash).adrop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
def test_upsert_and_delete_are_recorded(document_class, category_obj):
    from redis_search_django.query.instrument import capture_queries

    doc = document_class("CatWriteRec", Category, ["name"])
    with live_index(doc):
        indexer = Indexer()
        with capture_queries() as collector:
            indexer.upsert(doc, category_obj)
            indexer.delete(doc, category_obj.pk)
        kinds = [event.kind for event in collector.events]
        assert "write" in kinds
        assert "delete" in kinds
        assert any(event.query.startswith("JSON.SET") for event in collector.events)
        assert any(event.query.startswith("DEL") for event in collector.events)


@pytest.mark.django_db(transaction=True)
async def test_aupsert_and_adelete_are_recorded(document_class, category_obj):
    from redis_search_django.query.instrument import capture_queries

    doc = document_class("CatAWriteRec", Category, ["name"])
    async with alive_index(doc):
        indexer = Indexer()
        with capture_queries() as collector:
            await indexer.aupsert(doc, category_obj)
            await indexer.adelete(doc, category_obj.pk)
        assert any(event.query.startswith("JSON.SET") for event in collector.events)
        assert any(event.query.startswith("DEL") for event in collector.events)


@pytest.mark.django_db(transaction=True)
def test_pipeline_and_hash_writes_are_recorded(document_class, category_obj):
    from redis_search_django.documents import Document
    from redis_search_django.index import IndexManager
    from redis_search_django.query.instrument import capture_queries

    if not is_redis_running():
        pytest.skip("Redis is not running")

    json_doc = document_class("CatPipeRec", Category, ["name"])
    with live_index(json_doc):
        with capture_queries() as collector:
            Indexer().upsert_queryset(json_doc, Category.objects.all())
        assert any(event.query.startswith("PIPELINE") for event in collector.events)

    class HashDoc(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.category.hashrec"
            prefix = "rsd:test.category.hashrec:"

    indexer = Indexer()
    try:
        with capture_queries() as collector:
            indexer.upsert(HashDoc, category_obj)
        assert any(event.query.startswith("HSET") for event in collector.events)
    finally:
        IndexManager(HashDoc).drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
async def test_ahash_and_related_writes_are_recorded(product_obj):
    from redis_search_django import fields
    from redis_search_django.documents import Document
    from redis_search_django.index import IndexManager
    from redis_search_django.query.instrument import capture_queries

    if not is_redis_running():
        pytest.skip("Redis is not running")

    from .models import Vendor

    class VendorHash(Document):
        class Django:
            model = Vendor
            fields = ["name"]
            embedded = True

    class ProductHash(Document):
        vendor = fields.Object(VendorHash)

        class Django:
            model = type(product_obj)
            fields = ["name"]
            select_related_fields = ["vendor"]

        class Index:
            storage = "hash"
            name = "idx:test.product.hashrec"
            prefix = "rsd:test.product.hashrec:"

    indexer = Indexer()
    try:
        with capture_queries() as collector:
            await indexer.aupsert(ProductHash, product_obj)
            await indexer.areindex_related(ProductHash, product_obj.vendor)
            indexer.reindex_related(ProductHash, product_obj.vendor)
        assert any(event.query.startswith("HSET") for event in collector.events)
    finally:
        await IndexManager(ProductHash).adrop(delete_docs=True)
