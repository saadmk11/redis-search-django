from __future__ import annotations

from unittest import mock

import pytest

from redis_search_django.enums import MigrateOutcome
from redis_search_django.exceptions import SchemaDrift
from redis_search_django.fields import Tag
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer

from .helpers import is_redis_running
from .models import Category, Product, Vendor

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


def _manager(document_cls) -> IndexManager:
    return IndexManager(document_cls)


def test_create_exists_info_drop_and_recreate(document_class):
    doc = document_class("CatIdx", Category, ["name"])
    doc._meta.language = "english"
    doc._meta.stopwords = ()
    manager = _manager(doc)
    assert manager.exists() is False
    assert manager.drift() is True
    assert manager.check() == 1
    physical = manager.create()
    assert physical
    assert manager.exists() is True
    assert manager.info()
    assert manager.drift() is False
    assert manager.check() == 0
    manager.mark_populated()
    assert manager.check() == 0
    doc._meta.language = "french"
    IndexManager(doc).create()
    manager.drop(delete_docs=True)
    assert manager.exists() is False
    manager.drop(delete_docs=True)


def test_migrate_created_noop_waiting_alter_swap_rebuild(document_class):
    vendor = Vendor.objects.create(
        name="Lab", establishment_date=__import__("datetime").date.today()
    )
    Product.objects.create(name="Widget", price=10, vendor=vendor)

    slim = document_class("ProdSlim", Product, ["name"])
    manager = _manager(slim)
    assert manager.migrate() is MigrateOutcome.CREATED
    assert manager.migrate() is MigrateOutcome.NO_OP

    meta = manager.load_meta()
    meta["populate_required"] = True
    meta["populated_fp"] = "stale"
    manager.save_meta(meta)
    assert manager.migrate() is MigrateOutcome.WAITING

    wide = document_class("ProdWide", Product, ["name", "price"])
    wide._meta.index_alias = slim._meta.index_alias
    wide._meta.key_prefix = slim._meta.key_prefix
    wide_manager = _manager(wide)
    assert wide_manager.migrate() is MigrateOutcome.ALTER
    assert wide_manager.check() == 1

    tagged = document_class(
        "ProdTagged",
        Product,
        extra_attrs={"name": Tag()},
    )
    tagged._meta.index_alias = slim._meta.index_alias
    tagged._meta.key_prefix = slim._meta.key_prefix
    assert _manager(tagged).migrate() is MigrateOutcome.REINDEX

    moved = document_class("ProdMoved", Product, ["name"])
    moved._meta.index_alias = slim._meta.index_alias
    moved._meta.key_prefix = "rsd:test.product.movedprefix:"
    assert _manager(moved).migrate() is MigrateOutcome.REINDEX

    manager.drop(delete_docs=True)
    _manager(tagged).drop(delete_docs=True)


def test_wait_loop_sees_indexing_then_ready(document_class):
    doc = document_class("CatWaitLoop", Category, ["name"])
    manager = _manager(doc)
    manager.create()
    calls = {"n": 0}
    real_client = manager._client

    raw = real_client()

    class Wrap:
        def ft(self, name):
            inner = raw.ft(name)

            class Info:
                def info(self):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return {"indexing": 1}
                    return inner.info()

            return Info() if name == manager.schema.physical_name else inner

        def delete(self, key):
            return raw.delete(key)

    manager._client = lambda: Wrap()  # type: ignore[method-assign]
    with mock.patch("redis_search_django.index.time.sleep"):
        manager._wait_until_ready(manager.schema.physical_name, timeout=2)
    assert calls["n"] >= 2
    manager._client = real_client
    manager.drop(delete_docs=True)


def test_wait_until_ready_times_out(document_class):
    doc = document_class("CatWait", Category, ["name"])
    manager = _manager(doc)
    manager.create()
    with pytest.raises(SchemaDrift, match="Timed out"):
        manager._wait_until_ready(manager.schema.physical_name, timeout=0)
    manager.drop(delete_docs=True)


@pytest.mark.django_db(transaction=True)
async def test_async_index_lifecycle_and_migrate(document_class):
    doc = document_class("CatAidx", Category, ["name"])
    manager = _manager(doc)
    assert await manager.aexists() is False
    assert await manager.adrift() is True
    assert await manager.acheck() == 1
    await manager.acreate()
    assert await manager.aexists() is True
    assert await manager.ainfo()
    await manager.amark_populated()
    assert await manager.amigrate() is MigrateOutcome.NO_OP
    doc._meta.language = "french"
    await IndexManager(doc).acreate()
    await manager.adrop(delete_docs=True)
    assert await manager.aexists() is False
    await manager.adrop(delete_docs=True)

    slim = document_class("ProdASlim", Product, ["name"])
    slim_manager = _manager(slim)
    assert await slim_manager.amigrate() is MigrateOutcome.CREATED
    meta = await slim_manager.aload_meta()
    meta["populate_required"] = True
    meta["populated_fp"] = "stale"
    await slim_manager.asave_meta(meta)
    assert await slim_manager.amigrate() is MigrateOutcome.WAITING

    wide = document_class("ProdAWide", Product, ["name", "price"])
    wide._meta.index_alias = slim._meta.index_alias
    wide._meta.key_prefix = slim._meta.key_prefix
    assert await _manager(wide).amigrate() is MigrateOutcome.ALTER

    tagged = document_class(
        "ProdATagged",
        Product,
        extra_attrs={"name": Tag()},
    )
    tagged._meta.index_alias = slim._meta.index_alias
    tagged._meta.key_prefix = slim._meta.key_prefix
    assert await _manager(tagged).amigrate() is MigrateOutcome.REINDEX

    moved = document_class("ProdAMoved", Product, ["name"])
    moved._meta.index_alias = slim._meta.index_alias
    moved._meta.key_prefix = "rsd:test.product.amoved:"
    assert await _manager(moved).amigrate() is MigrateOutcome.REINDEX

    with pytest.raises(SchemaDrift, match="Timed out"):
        await slim_manager._await_until_ready(
            slim_manager.schema.physical_name, timeout=0
        )
    await slim_manager.adrop(delete_docs=True)


def test_migrate_alter_updates_meta_when_no_redis_fields(document_class):
    from dataclasses import replace

    from redis_search_django.schema import SchemaField

    doc = document_class("CatAlterEmpty", Category, ["name"])
    manager = _manager(doc)
    manager.create()
    extra = SchemaField(
        name="ghost",
        type="TAG",
        path="$.ghost",
        alias="ghost",
        sortable=False,
        index_missing=False,
        extra={},
    )
    manager.schema = replace(manager.schema, fields=(*manager.schema.fields, extra))
    assert manager.migrate() is MigrateOutcome.ALTER
    manager.drop(delete_docs=True)


def test_migrate_alter_rejected_falls_back_to_swap(document_class):
    from redis.exceptions import ResponseError

    vendor = Vendor.objects.create(
        name="AlterFail", establishment_date=__import__("datetime").date.today()
    )
    Product.objects.create(name="Gadget", price=5, vendor=vendor)
    slim = document_class("ProdAlterSlim", Product, ["name"])
    slim_manager = _manager(slim)
    slim_manager.create()
    wide = document_class("ProdAlterWide", Product, ["name", "price"])
    wide._meta.index_alias = slim._meta.index_alias
    wide._meta.key_prefix = slim._meta.key_prefix
    wide_manager = _manager(wide)
    raw = wide_manager._client()

    class Wrap:
        def ft(self, name):
            inner = raw.ft(name)

            class Proxy:
                def alter_schema_add(self, fields):
                    raise ResponseError("Duplicate field in schema")

                def __getattr__(self, item):
                    return getattr(inner, item)

            return Proxy()

        def __getattr__(self, item):
            return getattr(raw, item)

    wide_manager._client = lambda: Wrap()  # type: ignore[method-assign]
    assert wide_manager.migrate() is MigrateOutcome.REINDEX
    slim_manager.drop(delete_docs=True)


async def test_amigrate_alter_rejected_falls_back_to_swap(document_class):
    from redis.exceptions import ResponseError

    vendor = await Vendor.objects.acreate(
        name="AAlterFail", establishment_date=__import__("datetime").date.today()
    )
    await Product.objects.acreate(name="AGadget", price=5, vendor=vendor)
    slim = document_class("ProdAAlterSlim", Product, ["name"])
    slim_manager = _manager(slim)
    await slim_manager.acreate()
    wide = document_class("ProdAAlterWide", Product, ["name", "price"])
    wide._meta.index_alias = slim._meta.index_alias
    wide._meta.key_prefix = slim._meta.key_prefix
    wide_manager = _manager(wide)
    raw = wide_manager._aclient()

    class Wrap:
        def ft(self, name):
            inner = raw.ft(name)

            class Proxy:
                async def alter_schema_add(self, fields):
                    raise ResponseError("Duplicate field in schema")

                def __getattr__(self, item):
                    return getattr(inner, item)

            return Proxy()

        def __getattr__(self, item):
            return getattr(raw, item)

    wide_manager._aclient = lambda: Wrap()  # type: ignore[method-assign]
    assert await wide_manager.amigrate() is MigrateOutcome.REINDEX
    await slim_manager.adrop(delete_docs=True)


async def test_amigrate_alter_updates_meta_when_no_redis_fields(document_class):
    from dataclasses import replace

    from redis_search_django.schema import SchemaField

    doc = document_class("CatAAlterEmpty", Category, ["name"])
    manager = _manager(doc)
    await manager.acreate()
    extra = SchemaField(
        name="ghost",
        type="TAG",
        path="$.ghost",
        alias="ghost",
        sortable=False,
        index_missing=False,
        extra={},
    )
    manager.schema = replace(manager.schema, fields=(*manager.schema.fields, extra))
    assert await manager.amigrate() is MigrateOutcome.ALTER
    await manager.adrop(delete_docs=True)


async def test_await_until_ready_times_out_while_indexing(document_class):
    doc = document_class("CatAWaitSleep", Category, ["name"])
    manager = _manager(doc)
    await manager.acreate()

    class Wrap:
        def ft(self, name):
            class Info:
                async def info(self):
                    return {"indexing": 1}

            return Info()

    manager._aclient = lambda: Wrap()  # type: ignore[method-assign]
    with (
        mock.patch("redis_search_django.index.asyncio.sleep", new=mock.AsyncMock()),
        pytest.raises(SchemaDrift, match="Timed out"),
    ):
        await manager._await_until_ready("idx:busy", timeout=0.01)
    manager.drop(delete_docs=True)


def test_indexer_populate_rebuild_hash_and_index_all(document_class):
    category = Category.objects.create(name="Indexed")
    json_doc = document_class("CatPop", Category, ["name"])
    indexer = Indexer()
    assert indexer.populate(json_doc) >= 1
    assert json_doc.objects.get(pk=category.pk).name == "Indexed"
    assert json_doc.index_all() >= 1

    hash_doc = document_class(
        "CatHashPop",
        Category,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "name": "idx:test.category.hashpop",
                    "prefix": "rsd:test.category.hashpop:",
                    "storage": "hash",
                },
            )
        },
    )
    indexer.upsert(hash_doc, category)
    assert hash_doc.objects.get(pk=category.pk).name == "Indexed"
    IndexManager(json_doc).drop(delete_docs=True)
    IndexManager(hash_doc).drop(delete_docs=True)


async def test_aindex_all_and_hash_aupsert(document_class):
    category = await Category.objects.acreate(name="AIndexed")
    doc = document_class("CatAPop", Category, ["name"])
    assert await doc.aindex_all() >= 1
    hit = await doc.objects.aget(pk=category.pk)
    assert hit.name == "AIndexed"
    await IndexManager(doc).adrop(delete_docs=True)
