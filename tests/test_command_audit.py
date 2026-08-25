"""Concurrency, memory, and performance edge cases for management commands."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from redis_search_django.client import get_redis_connection
from redis_search_django.exceptions import ReindexInProgress
from redis_search_django.index import (
    IndexManager,
    _first_prefix,
    _info_prefix,
    _info_serves,
    _session_target,
)
from redis_search_django.indexer import Indexer, _aiter_records, _iter_records
from redis_search_django.targets import generation_prefix, read_prefix, write_prefixes
from redis_search_django.verify import VerifyReport, _load_versions, _stamp_from_raw

from .helpers import is_redis_running
from .models import Category, Product, Vendor

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


def test_info_prefix_and_session_helpers():
    assert _session_target(None) == (None, "")
    assert _session_target({"state": "backfill"}) == (None, "")
    assert _session_target({"target_physical": "idx:g2", "target_prefix": "p.g2:"}) == (
        "p.g2:",
        "idx:g2",
    )

    assert _first_prefix(None) is None
    assert _first_prefix("") is None
    assert _first_prefix(1) is None
    assert _first_prefix(b"rsd:a:") == "rsd:a:"
    assert _first_prefix(["rsd:a:", "rsd:b:"]) == "rsd:a:"
    assert _first_prefix((b"rsd:a:",)) == "rsd:a:"

    assert _info_prefix({}) is None
    assert _info_prefix({"index_definition": "nope"}) is None
    assert _info_prefix({"index_definition": {"prefixes": ["live:"]}}) == "live:"
    assert _info_prefix({"index_definition": {"prefix": b"live:"}}) == "live:"
    assert (
        _info_prefix({"index_definition": ["key_type", "JSON", "prefixes", ["live:"]]})
        == "live:"
    )
    assert (
        _info_prefix({"index_definition": [b"prefix", b"live:", "key_type", "JSON"]})
        == "live:"
    )
    assert _info_prefix({"index_definition": ["prefixes"]}) is None

    assert _info_serves({"index_name": "idx:g2"}, "idx:g2", "p:")
    assert _info_serves({"index_name": b"idx:g2"}, "idx:g2", "p:")
    assert _info_serves(
        {"index_name": "alias", "index_definition": {"prefixes": ["p.g2:"]}},
        "idx:g2",
        "p.g2:",
    )
    assert not _info_serves({"index_name": "alias"}, "idx:g2", "p.g2:")


def test_stamp_from_raw_shapes():
    assert _stamp_from_raw(None) is None
    assert _stamp_from_raw([]) is None
    assert _stamp_from_raw(["abc"]) == "abc"
    assert _stamp_from_raw(b"hash") == "hash"
    assert _stamp_from_raw({"_v": "from-doc"}) == "from-doc"
    assert _stamp_from_raw(12) == "12"


def test_load_versions_json_fetches_stamp_path_only():
    from redis_search_django.enums import Storage

    seen: list[tuple[object, ...]] = []

    class FakeJson:
        def get(self, key, path=None):
            seen.append((key, path))
            return "stamp"

    class Fake:
        def pipeline(self, transaction=False):
            return self

        def json(self):
            return FakeJson()

        def execute(self, raise_on_error=True):
            return ["stamp"]

    class Doc:
        _meta = type("M", (), {"storage": Storage.JSON})()

    found = _load_versions(Fake(), Doc, "p:", ["p:1"])
    assert found == {"1": "stamp"}
    assert seen == [("p:1", "._v")]

    class ErrPipe(Fake):
        def execute(self, raise_on_error=True):
            return [RuntimeError("missing path")]

    assert _load_versions(ErrPipe(), Doc, "p:", ["p:9"]) == {"9": None}


def test_iter_records_prefetches_in_chunks(product_with_tag, settings, monkeypatch):
    product, _tag = product_with_tag
    other = Vendor.objects.create(
        name="Other", establishment_date=__import__("datetime").date.today()
    )
    Product.objects.create(name="Second", price=2, vendor=other)
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    calls: list[int] = []
    original = __import__(
        "django.db.models", fromlist=["prefetch_related_objects"]
    ).prefetch_related_objects

    def spy(batch, *lookups):
        calls.append(len(batch))
        return original(batch, *lookups)

    monkeypatch.setattr("django.db.models.prefetch_related_objects", spy)
    qs = Product.objects.prefetch_related("tags")
    rows = list(_iter_records(qs, 1))
    assert len(rows) >= 2
    assert calls
    assert all(size == 1 for size in calls)
    leftover = list(_iter_records(Product.objects.prefetch_related("tags"), 10))
    assert len(leftover) >= 2
    assert product.pk in {row.pk for row in leftover}


async def test_aiter_records_prefetches_in_chunks(
    product_with_tag, settings, monkeypatch
):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    calls: list[int] = []
    module = __import__("django.db.models", fromlist=["aprefetch_related_objects"])
    original = module.aprefetch_related_objects

    async def spy(batch, *lookups):
        calls.append(len(batch))
        return await original(batch, *lookups)

    monkeypatch.setattr("django.db.models.aprefetch_related_objects", spy)
    qs = Product.objects.prefetch_related("tags")
    rows = [row async for row in _aiter_records(qs, 1)]
    leftover = [row async for row in _aiter_records(qs, 10)]
    assert rows
    assert leftover
    assert calls
    assert 1 in calls


def test_reindex_rejects_second_holder(document_class, category_obj):
    doc = document_class("AuditLock", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    token = manager.acquire_reindex_lock()
    try:
        with pytest.raises(ReindexInProgress, match="in progress"):
            indexer.reindex(doc, blue_green=True, settle=False)
        with pytest.raises(ReindexInProgress, match="in progress"):
            indexer.rebuild(doc)
        with pytest.raises(CommandError, match="in progress"):
            call_command("redisearch", "reindex", "--blue-green")
        with pytest.raises(CommandError, match="in progress"):
            call_command("redisearch", "rebuild")
        with pytest.raises(CommandError, match="in progress"):
            call_command("redisearch", "drop")
        with pytest.raises(CommandError, match="in progress"):
            call_command("redisearch", "populate")
        with pytest.raises(CommandError, match="in progress"):
            call_command("redisearch", "verify", "--repair")
        with pytest.raises(ReindexInProgress, match="in progress"):
            indexer.populate(doc)
    finally:
        manager.release_reindex_lock(token)
    result = indexer.reindex(doc, blue_green=True, settle=False)
    assert result.verified
    manager.drop(delete_docs=True)


async def test_areindex_rejects_second_holder(document_class, category_obj):
    doc = document_class("AuditALock", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    manager = IndexManager(doc)
    token = await manager.aacquire_reindex_lock()
    try:
        with pytest.raises(ReindexInProgress, match="in progress"):
            await indexer.areindex(doc, blue_green=True, settle=False)
        with pytest.raises(ReindexInProgress, match="in progress"):
            await indexer.arebuild(doc)
        with pytest.raises(ReindexInProgress, match="in progress"):
            await indexer.apopulate(doc)
    finally:
        await manager.arelease_reindex_lock(token)
    result = await indexer.areindex(doc, blue_green=True, settle=False)
    assert result.verified
    await manager.adrop(delete_docs=True)


def test_regular_reindex_refuses_open_blue_green_session(document_class, category_obj):
    doc = document_class("AuditOpenSess", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    extra = generation_prefix(doc._meta.key_prefix, 2)
    manager.begin_reindex(
        generation=2,
        target_physical=f"{doc._meta.index_alias}:g2:open",
        target_prefix=extra,
    )
    try:
        with pytest.raises(ReindexInProgress, match="--abort"):
            indexer.reindex(doc, settle=False)
        with pytest.raises(ReindexInProgress, match="--abort"):
            indexer.rebuild(doc)
        with pytest.raises(CommandError, match="--abort"):
            call_command("redisearch", "drop")
        with pytest.raises(CommandError, match="--abort"):
            call_command("redisearch", "rebuild")
    finally:
        indexer.reindex(doc, abort=True)
        manager.drop(delete_docs=True)


async def test_aregular_reindex_refuses_open_session(document_class, category_obj):
    doc = document_class("AuditAOpen", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    manager = IndexManager(doc)
    extra = generation_prefix(doc._meta.key_prefix, 2)
    await manager.abegin_reindex(
        generation=2,
        target_physical=f"{doc._meta.index_alias}:g2:aopen",
        target_prefix=extra,
    )
    try:
        with pytest.raises(ReindexInProgress, match="--abort"):
            await indexer.areindex(doc, settle=False)
        with pytest.raises(ReindexInProgress, match="--abort"):
            await indexer.arebuild(doc)
    finally:
        await indexer.areindex(doc, abort=True)
        await manager.adrop(delete_docs=True)


def test_abort_after_promote_does_not_drop_live_keys(document_class, category_obj):
    doc = document_class("AuditAbortLive", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    result = indexer.reindex(doc, blue_green=True, keep_old=True, settle=False)
    manager = IndexManager(doc)
    live_key = f"{result.new_prefix}{category_obj.pk}"
    client = get_redis_connection()
    assert client.exists(live_key)
    meta = manager.load_meta()
    meta["reindex"] = {
        "state": "backfill",
        "target_physical": result.new_physical,
        "target_prefix": result.new_prefix,
    }
    manager.save_meta(meta)
    aborted = indexer.reindex(doc, abort=True)
    assert aborted.aborted
    assert client.exists(live_key)
    assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
    assert write_prefixes(doc) == (result.new_prefix,)
    manager.drop(delete_docs=True)


async def test_aabort_after_promote_does_not_drop_live_keys(
    document_class, category_obj
):
    doc = document_class("AuditAAbortLive", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    result = await indexer.areindex(doc, blue_green=True, keep_old=True, settle=False)
    manager = IndexManager(doc)
    live_key = f"{result.new_prefix}{category_obj.pk}"
    assert get_redis_connection().exists(live_key)
    meta = await manager.aload_meta()
    meta["reindex"] = {
        "state": "backfill",
        "target_physical": result.new_physical,
        "target_prefix": result.new_prefix,
    }
    await manager.asave_meta(meta)
    aborted = await indexer.areindex(doc, abort=True)
    assert aborted.aborted
    assert get_redis_connection().exists(live_key)
    await manager.adrop(delete_docs=True)


def test_reindex_stops_if_aborted_during_backfill(
    document_class, category_obj, monkeypatch
):
    doc = document_class("AuditAbortMid", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    original = Indexer.upsert_queryset

    def abort_then_write(self, document_cls, qs, prefixes=None, heartbeat=None):
        manager.abort_reindex()
        return original(self, document_cls, qs, prefixes=prefixes, heartbeat=heartbeat)

    monkeypatch.setattr(Indexer, "upsert_queryset", abort_then_write)
    result = indexer.reindex(doc, blue_green=True, settle=False)
    assert result.aborted
    assert result.new_prefix
    assert get_redis_connection().exists(f"{doc._meta.key_prefix}{category_obj.pk}")
    assert write_prefixes(doc) == (doc._meta.key_prefix,)
    manager.drop(delete_docs=True)


async def test_areindex_stops_if_aborted_during_backfill(
    document_class, category_obj, monkeypatch
):
    doc = document_class("AuditAAbortMid", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    manager = IndexManager(doc)
    original = Indexer.aupsert_queryset

    async def abort_then_write(self, document_cls, qs, prefixes=None, heartbeat=None):
        await manager.aabort_reindex()
        return await original(
            self, document_cls, qs, prefixes=prefixes, heartbeat=heartbeat
        )

    monkeypatch.setattr(Indexer, "aupsert_queryset", abort_then_write)
    result = await indexer.areindex(doc, blue_green=True, settle=False)
    assert result.aborted
    await manager.adrop(delete_docs=True)


async def test_areindex_aborts_when_session_cleared(
    document_class, category_obj, monkeypatch
):
    doc = document_class("AuditASessGone", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    manager = IndexManager(doc)
    original = Indexer.aupsert_queryset

    async def clear_session(self, document_cls, qs, prefixes=None, heartbeat=None):
        meta = await manager.aload_meta()
        meta["reindex"] = None
        await manager.asave_meta(meta)
        return await original(
            self, document_cls, qs, prefixes=prefixes, heartbeat=heartbeat
        )

    monkeypatch.setattr(Indexer, "aupsert_queryset", clear_session)
    result = await indexer.areindex(doc, blue_green=True, settle=False)
    assert result.aborted
    await manager.adrop(delete_docs=True)


def test_abort_clears_stuck_lock(document_class, category_obj):
    doc = document_class("AuditStuck", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    get_redis_connection().set(manager.reindex_lock_key(), "stale")
    with pytest.raises(ReindexInProgress):
        indexer.reindex(doc, settle=False)
    idle = indexer.reindex(doc, abort=True)
    assert idle.aborted
    result = indexer.reindex(doc, blue_green=True, settle=False)
    assert result.verified
    manager.drop(delete_docs=True)


def test_renew_lock_extends_ttl_and_lost_lock_raises(document_class, category_obj):
    doc = document_class("AuditRenew", Category, ["name"])
    manager = IndexManager(doc)
    Indexer().populate(doc)
    token = manager.acquire_reindex_lock()
    try:
        client = get_redis_connection()
        client.expire(manager.reindex_lock_key(), 30)
        manager.heartbeat()
        assert client.ttl(manager.reindex_lock_key()) > 30
        client.delete(manager.reindex_lock_key())
        with pytest.raises(ReindexInProgress, match="lost the reindex lock"):
            manager.heartbeat()
    finally:
        manager.release_reindex_lock(token)
    manager.drop(delete_docs=True)


async def test_arenew_lock_lost_raises(document_class, category_obj):
    doc = document_class("AuditARenew", Category, ["name"])
    manager = IndexManager(doc)
    await Indexer().apopulate(doc)
    token = await manager.aacquire_reindex_lock()
    try:
        await manager.aheartbeat()
        get_redis_connection().delete(manager.reindex_lock_key())
        with pytest.raises(ReindexInProgress, match="lost the reindex lock"):
            await manager.aheartbeat()
    finally:
        await manager.arelease_reindex_lock(token)
    await manager.adrop(delete_docs=True)


def test_heartbeat_skips_renew_without_token(document_class, monkeypatch):
    manager = IndexManager(document_class("AuditNoBeat", Category, ["name"]))
    renewed: list[str] = []
    monkeypatch.setattr(manager, "renew_reindex_lock", renewed.append)
    assert manager._lock_token is None
    manager.heartbeat()
    assert renewed == []


async def test_aheartbeat_skips_renew_without_token(document_class, monkeypatch):
    manager = IndexManager(document_class("AuditANoBeat", Category, ["name"]))
    renewed: list[str] = []

    async def capture(token: str) -> None:
        renewed.append(token)

    monkeypatch.setattr(manager, "arenew_reindex_lock", capture)
    assert manager._lock_token is None
    await manager.aheartbeat()
    assert renewed == []


def test_upsert_queryset_heartbeats_each_chunk(document_class, settings):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    Category.objects.create(name="H1")
    Category.objects.create(name="H2")
    doc = document_class("AuditHeart", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    beats: list[int] = []
    indexer.upsert_queryset(
        doc, Category.objects.all(), heartbeat=lambda: beats.append(1)
    )
    assert beats
    IndexManager(doc).drop(delete_docs=True)


async def test_aupsert_queryset_heartbeats_each_chunk(document_class, settings):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    await Category.objects.acreate(name="AH1")
    await Category.objects.acreate(name="AH2")
    doc = document_class("AuditAHeart", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    beats: list[int] = []
    await indexer.aupsert_queryset(
        doc, Category.objects.all(), heartbeat=lambda: beats.append(1)
    )
    assert beats
    await IndexManager(doc).adrop(delete_docs=True)


def test_verify_heartbeats_each_chunk(document_class, settings):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    Category.objects.create(name="V1")
    Category.objects.create(name="V2")
    doc = document_class("AuditVHeart", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    beats: list[int] = []
    indexer.verify(doc, heartbeat=lambda: beats.append(1))
    assert beats
    IndexManager(doc).drop(delete_docs=True)


def test_get_pk_retries_after_stale_prefix_cache(document_class, category_obj):
    from redis_search_django.targets import WriteTargets, _cache

    doc = document_class("AuditStaleGet", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    result = indexer.reindex(doc, blue_green=True, settle=False)
    old_prefix = result.old_prefix
    _cache[doc._meta.index_alias] = (
        -1.0,
        WriteTargets(
            read_prefix=old_prefix,
            write_prefixes=(old_prefix,),
            generation=1,
            physical_name=result.old_physical,
            reindex=None,
        ),
    )
    assert not get_redis_connection().exists(f"{old_prefix}{category_obj.pk}")
    assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
    IndexManager(doc).drop(delete_docs=True)


async def test_aget_pk_retries_after_stale_prefix_cache(document_class, category_obj):
    from redis_search_django.targets import WriteTargets, _cache

    doc = document_class("AuditAStaleGet", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    result = await indexer.areindex(doc, blue_green=True, settle=False)
    _cache[doc._meta.index_alias] = (
        -1.0,
        WriteTargets(
            read_prefix=result.old_prefix,
            write_prefixes=(result.old_prefix,),
            generation=1,
            physical_name=result.old_physical,
            reindex=None,
        ),
    )
    hit = await doc.objects.aget(pk=category_obj.pk)
    assert hit.name == category_obj.name
    await IndexManager(doc).adrop(delete_docs=True)


def test_release_lock_ignores_foreign_token(document_class, category_obj):
    doc = document_class("AuditToken", Category, ["name"])
    manager = IndexManager(doc)
    Indexer().populate(doc)
    token = manager.acquire_reindex_lock()
    manager.release_reindex_lock("not-the-token")
    with pytest.raises(ReindexInProgress):
        manager.acquire_reindex_lock()
    manager.release_reindex_lock(token)
    again = manager.acquire_reindex_lock()
    manager.release_reindex_lock(again)
    manager.drop(delete_docs=True)


def test_alias_serves_missing_index(document_class):
    doc = document_class("AuditNoAlias", Category, ["name"])
    manager = IndexManager(doc)
    assert manager._alias_serves("idx:missing", "p:") is False


async def test_aalias_serves_missing_index(document_class):
    doc = document_class("AuditANoAlias", Category, ["name"])
    manager = IndexManager(doc)
    assert await manager._aalias_serves("idx:missing", "p:") is False


def test_verify_repair_batches_upserts_and_orphan_deletes(document_class, settings):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    rows = [Category.objects.create(name=f"Batch{i}") for i in range(3)]
    extra = Category.objects.create(name="Ghost")
    doc = document_class("AuditRepair", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    client = get_redis_connection()
    for row in rows:
        client.delete(doc.key_for(row.pk))
    extra_key = doc.key_for(extra.pk)
    extra.delete()

    upserts: list[int] = []
    original = Indexer.upsert_queryset

    def counted(self, document_cls, qs, prefixes=None):
        upserts.append(qs.count())
        return original(self, document_cls, qs, prefixes=prefixes)

    Indexer.upsert_queryset = counted  # type: ignore[method-assign]
    try:
        report = indexer.verify(doc, repair=True)
    finally:
        Indexer.upsert_queryset = original  # type: ignore[method-assign]
    assert report.ok
    assert report.repaired_missing == 3
    assert report.repaired_orphaned == 1
    assert len(upserts) == 3
    assert all(size == 1 for size in upserts)
    assert not client.exists(extra_key)
    for row in rows:
        assert doc.objects.get(pk=row.pk).name == row.name
    IndexManager(doc).drop(delete_docs=True)


def test_repair_skips_pks_not_in_django(document_class):
    from redis_search_django.verify import _repair

    Category.objects.create(name="Keep")
    doc = document_class("AuditGhostPk", Category, ["name"])
    Indexer().populate(doc)
    report = VerifyReport(
        document=doc.__name__,
        prefix=read_prefix(doc),
        missing=["999888"],
    )
    _repair(doc, report, read_prefix(doc))
    assert report.missing == ["999888"]
    assert report.repaired_missing == 0
    IndexManager(doc).drop(delete_docs=True)


def test_command_drop_after_clean_reindex(document_class, capsys):
    Category.objects.create(name="DropMe")
    doc = document_class("AuditDropOk", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        call_command("redisearch", "drop")
        assert "dropped" in capsys.readouterr().out
        assert not IndexManager(doc).exists()
    finally:
        IndexManager(doc).drop(delete_docs=True)
