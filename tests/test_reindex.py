from __future__ import annotations

import pytest
from django.core.management import call_command

from redis_search_django.client import get_redis_connection
from redis_search_django.enums import MigrateOutcome
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer
from redis_search_django.targets import generation_prefix, write_prefixes

from .helpers import is_redis_running
from .models import Category

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


def test_reindex_swaps_prefix_and_drops_old_keys(document_class, category_obj):
    doc = document_class("CatBlueGreen", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    old_key = f"{doc._meta.key_prefix}{category_obj.pk}"
    client = get_redis_connection()
    assert client.exists(old_key)
    assert doc.objects.get(pk=category_obj.pk).name == category_obj.name

    result = indexer.reindex(doc, blue_green=True, settle=False)
    assert result.verified
    assert result.dropped_old
    assert result.generation == 2
    assert result.new_prefix == generation_prefix(doc._meta.key_prefix, 2)
    assert not client.exists(old_key)
    new_key = f"{result.new_prefix}{category_obj.pk}"
    assert client.exists(new_key)
    hit = doc.objects.get(pk=category_obj.pk)
    assert hit.name == category_obj.name
    assert hit._v is None
    stored = client.json().get(new_key)
    assert stored and stored.get("_v")
    assert list(doc.objects.filter(name=category_obj.name))
    IndexManager(doc).drop(delete_docs=True)


def test_live_upsert_dual_writes_during_backfill(document_class, category_obj):
    doc = document_class("CatDual", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    new_prefix = generation_prefix(doc._meta.key_prefix, 2)
    new_physical = f"{doc._meta.index_alias}:g2:deadbeef"
    manager.begin_reindex(
        generation=2, target_physical=new_physical, target_prefix=new_prefix
    )
    manager._create_physical(new_physical, prefix=new_prefix, skip_initial_scan=True)
    try:
        assert write_prefixes(doc) == (doc._meta.key_prefix, new_prefix)
        category_obj.name = "BothSides"
        category_obj.save()
        indexer.upsert(doc, category_obj)
        client = get_redis_connection()
        assert client.exists(f"{doc._meta.key_prefix}{category_obj.pk}")
        assert client.exists(f"{new_prefix}{category_obj.pk}")
    finally:
        manager.abort_reindex()
        manager.drop(delete_docs=True)


def test_reindex_keep_old_and_abort(document_class, category_obj):
    doc = document_class("CatKeepOld", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    client = get_redis_connection()
    old_key = f"{doc._meta.key_prefix}{category_obj.pk}"
    kept = indexer.reindex(doc, blue_green=True, keep_old=True, settle=False)
    assert kept.dropped_old is False
    assert client.exists(old_key)
    assert client.exists(f"{kept.new_prefix}{category_obj.pk}")

    manager = IndexManager(doc)
    extra = generation_prefix(doc._meta.key_prefix, 3)
    manager.begin_reindex(
        generation=3,
        target_physical=f"{doc._meta.index_alias}:g3:cafebabe",
        target_prefix=extra,
    )
    aborted = indexer.reindex(doc, abort=True)
    assert aborted.aborted
    assert extra not in write_prefixes(doc)
    manager.drop(delete_docs=True)


def test_update_recommends_reindex_instead_of_swapping(document_class, capsys):
    from redis_search_django import fields

    slim = document_class("ProdUpdSlim", Category, ["name"])
    manager = IndexManager(slim)
    try:
        call_command("redisearch", "update")
        capsys.readouterr()
        tagged = document_class(
            "ProdUpdTag",
            Category,
            extra_attrs={"name": fields.Tag()},
        )
        tagged._meta.index_alias = slim._meta.index_alias
        tagged._meta.key_prefix = slim._meta.key_prefix
        assert IndexManager(tagged).migrate() is MigrateOutcome.REINDEX
        with pytest.raises(SystemExit):
            call_command("redisearch", "update")
        assert "redisearch reindex" in capsys.readouterr().out
    finally:
        manager.drop(delete_docs=True)


def test_reindex_command_and_second_generation(document_class, category_obj, capsys):
    doc = document_class("CatCmdRe", Category, ["name"])
    manager = IndexManager(doc)
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        call_command("redisearch", "reindex", "--blue-green")
        out = capsys.readouterr().out
        assert "reindexed" in out
        assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
        call_command("redisearch", "reindex", "--blue-green")
        again = capsys.readouterr().out
        assert "reindexed" in again
        assert "old index kept" not in again
        assert ".g3:" in IndexManager(doc).serving_prefix()
        idle = Indexer().reindex(doc, abort=True)
        assert idle.aborted
    finally:
        manager.drop(delete_docs=True)


def test_reindex_command_reports_verify_failure(
    document_class, category_obj, capsys, monkeypatch
):
    from redis_search_django.verify import VerifyReport

    doc = document_class("CatCmdFailV", Category, ["name"])
    manager = IndexManager(doc)
    try:
        Indexer().populate(doc)
        monkeypatch.setattr(
            "redis_search_django.indexer.Indexer.verify",
            lambda self, *args, **kwargs: VerifyReport(
                document=doc.__name__, prefix="x", missing=["1"]
            ),
        )
        with pytest.raises(SystemExit):
            call_command("redisearch", "reindex", "--blue-green")
        out = capsys.readouterr().out
        assert "verify failed" in out
        assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
    finally:
        manager.drop(delete_docs=True)


async def test_areindex_skip_verify_and_fail(document_class, category_obj, monkeypatch):
    from redis_search_django.verify import VerifyReport

    doc = document_class("CatASkip", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    skipped = await indexer.areindex(
        doc, blue_green=True, skip_verify=True, settle=False
    )
    assert skipped.verified
    monkeypatch.setattr(
        indexer,
        "averify",
        lambda *args, **kwargs: _async_report(
            VerifyReport(document=doc.__name__, prefix="x", missing=["1"])
        ),
    )
    failed = await indexer.areindex(doc, blue_green=True, settle=False)
    assert failed.verified is False
    await IndexManager(doc).adrop(delete_docs=True)


async def _async_report(report):
    return report


def test_reindex_stops_when_verify_fails(document_class, category_obj, monkeypatch):
    from redis_search_django.verify import VerifyReport

    doc = document_class("CatVerifyFail", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    monkeypatch.setattr(
        indexer,
        "verify",
        lambda *args, **kwargs: VerifyReport(
            document=doc.__name__, prefix="x", missing=["1"]
        ),
    )
    result = indexer.reindex(doc, blue_green=True, settle=False)
    assert result.verified is False
    assert result.dropped_old is False
    IndexManager(doc).drop(delete_docs=True)


async def test_areindex_roundtrip(document_class, category_obj):
    doc = document_class("CatARe", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    result = await indexer.areindex(doc, blue_green=True, settle=True)
    assert result.verified
    hit = await doc.objects.aget(pk=category_obj.pk)
    assert hit.name == category_obj.name
    extra = generation_prefix(doc._meta.key_prefix, result.generation + 1)
    await IndexManager(doc).abegin_reindex(
        generation=result.generation + 1,
        target_physical=f"{doc._meta.index_alias}:g{result.generation + 1}:asyncabort",
        target_prefix=extra,
    )
    aborted = await indexer.areindex(doc, abort=True)
    assert aborted.aborted
    idle = await indexer.areindex(doc, abort=True)
    assert idle.aborted
    await IndexManager(doc).adrop(delete_docs=True)


def test_settle_sleeps_only_when_enabled(document_class, monkeypatch):
    import redis_search_django.index as index_mod

    slept: list[float] = []
    monkeypatch.setattr(index_mod.time, "sleep", slept.append)
    manager = IndexManager(document_class("CatSettleOn", Category, ["name"]))

    monkeypatch.setattr(index_mod, "SETTLE_SECONDS", 0.0)
    manager.settle()
    assert slept == []

    monkeypatch.setattr(index_mod, "SETTLE_SECONDS", 0.25)
    manager.settle()
    assert slept == [0.25]


async def test_asettle_sleeps_only_when_enabled(document_class, monkeypatch):
    import redis_search_django.index as index_mod

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(index_mod.asyncio, "sleep", fake_sleep)
    manager = IndexManager(document_class("CatASettleOn", Category, ["name"]))

    monkeypatch.setattr(index_mod, "SETTLE_SECONDS", 0.0)
    await manager.asettle()
    assert slept == []

    monkeypatch.setattr(index_mod, "SETTLE_SECONDS", 0.25)
    await manager.asettle()
    assert slept == [0.25]


def test_reindex_skip_verify_promotes(document_class, category_obj, monkeypatch):
    from redis_search_django.verify import VerifyReport

    doc = document_class("CatSkipOk", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    verified: list[int] = []

    def fail_verify(*args, **kwargs):
        verified.append(1)
        return VerifyReport(document=doc.__name__, prefix="x", missing=["1"])

    monkeypatch.setattr(indexer, "verify", fail_verify)
    result = indexer.reindex(doc, blue_green=True, skip_verify=True, settle=False)
    assert verified == []
    assert result.verified
    assert result.dropped_old
    assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
    IndexManager(doc).drop(delete_docs=True)


def test_reindex_creates_when_missing(document_class, category_obj):
    doc = document_class("CatReNew", Category, ["name"])
    result = Indexer().reindex(doc, settle=False)
    assert result.created
    assert result.blue_green is False
    assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
    IndexManager(doc).drop(delete_docs=True)


def test_reindex_blue_green_creates_when_missing(document_class, category_obj):
    doc = document_class("CatReNewBg", Category, ["name"])
    result = Indexer().reindex(doc, blue_green=True, settle=False)
    assert result.created
    assert result.verified
    assert result.blue_green is True
    assert result.generation == 1
    assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
    IndexManager(doc).drop(delete_docs=True)


async def test_areindex_default_is_rebuild(document_class, category_obj):
    doc = document_class("CatAPlain", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    result = await indexer.areindex(doc)
    assert result.blue_green is False
    hit = await doc.objects.aget(pk=category_obj.pk)
    assert hit.name == category_obj.name
    await IndexManager(doc).adrop(delete_docs=True)


async def test_areindex_creates_when_missing(document_class, category_obj):
    doc = document_class("CatAReNew", Category, ["name"])
    result = await Indexer().areindex(doc, blue_green=True, settle=False)
    assert result.created
    hit = await doc.objects.aget(pk=category_obj.pk)
    assert hit.name == category_obj.name
    await IndexManager(doc).adrop(delete_docs=True)


def test_drop_physical_missing_and_serving_prefix_fallback(document_class):
    doc = document_class("CatDropPhys", Category, ["name"])
    manager = IndexManager(doc)
    manager.drop_physical("idx:does-not-exist-at-all")
    assert manager.serving_prefix({}) == doc._meta.key_prefix
    assert manager.serving_prefix({"prefix": "logical:"}) == "logical:"


async def test_adrop_physical_missing_and_areindex_create_failure(
    document_class, category_obj, monkeypatch
):
    doc = document_class("CatADropPhys", Category, ["name"])
    manager = IndexManager(doc)
    await manager.adrop_physical("idx:async-missing-physical")
    indexer = Indexer()
    await indexer.apopulate(doc)

    async def boom(*args, **kwargs):
        raise RuntimeError("acreate failed")

    monkeypatch.setattr(IndexManager, "_acreate_physical", boom)
    with pytest.raises(RuntimeError, match="acreate failed"):
        await indexer.areindex(doc, blue_green=True, settle=False)
    await manager.adrop(delete_docs=True)


def test_abort_reindex_with_partial_session(document_class, category_obj):
    doc = document_class("CatAbortPartial", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    meta = manager.load_meta()
    meta["reindex"] = {"state": "backfill"}
    manager.save_meta(meta)
    assert manager.abort_reindex() is None
    idle = manager.load_meta()
    assert idle.get("reindex") is None
    assert idle["write_prefixes"] == [doc._meta.key_prefix]
    manager.drop(delete_docs=True)


def test_reindex_plan_ignores_incomplete_session(document_class, category_obj):
    doc = document_class("CatBadSess", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    meta = manager.load_meta()
    meta["reindex"] = {"state": "backfill"}
    manager.save_meta(meta)
    result = indexer.reindex(doc, blue_green=True, settle=False)
    assert result.verified
    manager.drop(delete_docs=True)


def test_ensure_green_resumes_and_recreates(document_class, category_obj):
    doc = document_class("CatResume", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    new_prefix = generation_prefix(doc._meta.key_prefix, 2)
    new_physical = f"{doc._meta.index_alias}:g2:resume1"
    manager.begin_reindex(
        generation=2, target_physical=new_physical, target_prefix=new_prefix
    )
    result = indexer.reindex(doc, blue_green=True, settle=False)
    assert result.verified
    manager.drop(delete_docs=True)


async def test_areindex_resumes_and_keeps_old(document_class, category_obj):
    doc = document_class("CatAResume", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    manager = IndexManager(doc)
    new_prefix = generation_prefix(doc._meta.key_prefix, 2)
    new_physical = f"{doc._meta.index_alias}:g2:aresume"
    await manager.abegin_reindex(
        generation=2, target_physical=new_physical, target_prefix=new_prefix
    )
    await manager._acreate_physical(new_physical, prefix=new_prefix)
    result = await indexer.areindex(doc, blue_green=True, keep_old=True, settle=False)
    assert result.verified
    assert result.dropped_old is False
    await manager.adrop(delete_docs=True)


def test_begin_reindex_without_physical_name(document_class, category_obj):
    doc = document_class("CatNoPhys", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)
    meta = manager.load_meta()
    meta.pop("physical_name", None)
    manager.save_meta(meta)
    new_prefix = generation_prefix(doc._meta.key_prefix, 2)
    manager.begin_reindex(
        generation=2,
        target_physical=f"{doc._meta.index_alias}:g2:nophys",
        target_prefix=new_prefix,
    )
    started = manager.load_meta()
    assert started["write_prefixes"] == [doc._meta.key_prefix, new_prefix]
    assert started["reindex"]["target_prefix"] == new_prefix
    assert started["reindex"]["source_physical"] == manager.schema.physical_name

    assert manager.abort_reindex() == new_prefix
    idle = manager.load_meta()
    assert idle.get("reindex") is None
    assert idle["write_prefixes"] == [doc._meta.key_prefix]

    meta = manager.load_meta()
    meta.pop("physical_name", None)
    meta["reindex"] = {"target_prefix": "only-prefix:"}
    manager.save_meta(meta)
    assert manager.abort_reindex() == "only-prefix:"
    assert manager.load_meta().get("reindex") is None
    manager.drop(delete_docs=True)


async def test_abegin_reindex_without_physical_name(document_class, category_obj):
    doc = document_class("CatANoPhys", Category, ["name"])
    indexer = Indexer()
    await indexer.apopulate(doc)
    manager = IndexManager(doc)
    meta = await manager.aload_meta()
    meta.pop("physical_name", None)
    await manager.asave_meta(meta)
    new_prefix = generation_prefix(doc._meta.key_prefix, 2)
    await manager.abegin_reindex(
        generation=2,
        target_physical=f"{doc._meta.index_alias}:g2:anophys",
        target_prefix=new_prefix,
    )
    started = await manager.aload_meta()
    assert started["write_prefixes"] == [doc._meta.key_prefix, new_prefix]
    assert started["reindex"]["target_prefix"] == new_prefix
    assert started["reindex"]["source_physical"] == manager.schema.physical_name

    assert await manager.aabort_reindex() == new_prefix
    idle = await manager.aload_meta()
    assert idle.get("reindex") is None
    assert idle["write_prefixes"] == [doc._meta.key_prefix]

    meta = await manager.aload_meta()
    meta["reindex"] = {"target_physical": f"{doc._meta.index_alias}:g2:gone"}
    await manager.asave_meta(meta)
    assert await manager.aabort_reindex() is None
    assert (await manager.aload_meta()).get("reindex") is None

    meta = await manager.aload_meta()
    meta["reindex"] = {"target_prefix": "only-prefix:"}
    await manager.asave_meta(meta)
    assert await manager.aabort_reindex() == "only-prefix:"
    assert (await manager.aload_meta()).get("reindex") is None
    await manager.adrop(delete_docs=True)


def test_reindex_create_failure_aborts(document_class, category_obj, monkeypatch):
    doc = document_class("CatFailGreen", Category, ["name"])
    indexer = Indexer()
    indexer.populate(doc)
    manager = IndexManager(doc)

    def boom(*args, **kwargs):
        raise RuntimeError("create failed")

    monkeypatch.setattr(IndexManager, "_create_physical", boom)
    with pytest.raises(RuntimeError, match="create failed"):
        indexer.reindex(doc, blue_green=True, settle=False)
    assert IndexManager(doc).load_meta().get("reindex") is None
    manager.drop(delete_docs=True)
