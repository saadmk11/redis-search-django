"""Live Redis end-to-end tests for every new ``redisearch`` command option.

Each test drives ``call_command`` through the full process against a real
Query Engine (no mocks): create/populate, run the option, then assert Redis
keys, alias reads, and search still match Django.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import CommandError, call_command

from redis_search_django.client import get_redis_connection
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer
from redis_search_django.targets import generation_prefix, read_prefix, write_prefixes

from .helpers import is_redis_running
from .models import Category, Product, Vendor

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


def _drop(*docs: type) -> None:
    for doc in docs:
        manager = IndexManager(doc)
        if manager.exists():
            manager.drop(delete_docs=True)


def test_command_reindex_default_is_in_place_rebuild(document_class, capsys):
    category = Category.objects.create(name="InPlace")
    doc = document_class("CmdRePlain", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        old_prefix = doc._meta.key_prefix
        old_key = f"{old_prefix}{category.pk}"
        assert get_redis_connection().exists(old_key)

        call_command("redisearch", "reindex")
        out = capsys.readouterr().out
        assert "reindexed" in out
        assert "→" not in out
        assert read_prefix(doc) == old_prefix
        assert get_redis_connection().exists(old_key)
        assert doc.objects.get(pk=category.pk).name == "InPlace"
    finally:
        _drop(doc)


def test_command_keep_old_requires_blue_green(document_class):
    document_class("CmdNeedBg", Category, ["name"])
    with pytest.raises(CommandError, match="--keep-old requires --blue-green"):
        call_command("redisearch", "reindex", "--keep-old")


def test_command_reindex_swaps_alias_and_drops_old_keys(document_class, capsys):
    category = Category.objects.create(name="LiveReindex")
    doc = document_class("CmdReDefault", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        old_key = f"{doc._meta.key_prefix}{category.pk}"
        assert get_redis_connection().exists(old_key)

        call_command("redisearch", "reindex", "--blue-green")
        out = capsys.readouterr().out
        assert "reindexed" in out
        assert not get_redis_connection().exists(old_key)
        assert read_prefix(doc) == generation_prefix(doc._meta.key_prefix, 2)
        assert get_redis_connection().exists(f"{read_prefix(doc)}{category.pk}")
        hit = doc.objects.get(pk=category.pk)
        assert hit.name == "LiveReindex"
        assert list(doc.objects.filter(name="LiveReindex"))
    finally:
        _drop(doc)


def test_command_reindex_keep_old_leaves_blue_keys(document_class, capsys):
    category = Category.objects.create(name="KeepOld")
    doc = document_class("CmdReKeep", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        old_key = f"{doc._meta.key_prefix}{category.pk}"

        call_command("redisearch", "reindex", "--blue-green", "--keep-old")
        out = capsys.readouterr().out
        assert "old index kept" in out
        client = get_redis_connection()
        assert client.exists(old_key)
        assert client.exists(f"{read_prefix(doc)}{category.pk}")
        assert doc.objects.get(pk=category.pk).name == "KeepOld"
        assert list(doc.objects.filter(name="KeepOld"))
    finally:
        _drop(doc)


def test_command_reindex_abort_restores_single_write(document_class, capsys):
    category = Category.objects.create(name="AbortMe")
    doc = document_class("CmdReAbort", Category, ["name"])
    manager = IndexManager(doc)
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        extra = generation_prefix(doc._meta.key_prefix, 2)
        physical = f"{doc._meta.index_alias}:g2:cmdabort"
        manager.begin_reindex(
            generation=2, target_physical=physical, target_prefix=extra
        )
        manager._create_physical(physical, prefix=extra)
        assert extra in write_prefixes(doc)

        call_command("redisearch", "reindex", "--abort")
        assert "aborted" in capsys.readouterr().out
        assert extra not in write_prefixes(doc)
        assert write_prefixes(doc) == (doc._meta.key_prefix,)
        assert get_redis_connection().exists(f"{doc._meta.key_prefix}{category.pk}")
        assert doc.objects.get(pk=category.pk).name == "AbortMe"
    finally:
        _drop(doc)


def test_command_reindex_models_only_touches_named_model(document_class, capsys):
    category = Category.objects.create(name="OnlyCat")
    vendor = Vendor.objects.create(
        name="OnlyVendor", establishment_date=__import__("datetime").date.today()
    )
    product = Product.objects.create(name="OnlyProd", price=3, vendor=vendor)
    cat_doc = document_class("CmdReCat", Category, ["name"])
    prod_doc = document_class("CmdReProd", Product, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        cat_old = cat_doc._meta.key_prefix
        prod_old = prod_doc._meta.key_prefix

        call_command(
            "redisearch", "reindex", "--blue-green", "--models", "tests.Category"
        )
        out = capsys.readouterr().out
        assert "CmdReCat" in out
        assert "CmdReProd" not in out
        assert read_prefix(cat_doc) == generation_prefix(cat_old, 2)
        assert read_prefix(prod_doc) == prod_old
        assert cat_doc.objects.get(pk=category.pk).name == "OnlyCat"
        assert prod_doc.objects.get(pk=product.pk).name == "OnlyProd"
    finally:
        _drop(cat_doc, prod_doc)


def test_command_reindex_creates_when_index_missing(document_class, capsys):
    category = Category.objects.create(name="FreshIdx")
    doc = document_class("CmdReCreate", Category, ["name"])
    try:
        call_command("redisearch", "reindex")
        out = capsys.readouterr().out
        assert "created and populated" in out
        assert doc.objects.get(pk=category.pk).name == "FreshIdx"
        assert list(doc.objects.filter(name="FreshIdx"))
    finally:
        _drop(doc)


def test_command_verify_ok_on_clean_index(document_class, capsys):
    Category.objects.create(name="Clean")
    doc = document_class("CmdVfOk", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        call_command("redisearch", "verify")
        out = capsys.readouterr().out
        assert "ok" in out
        assert "checked" in out
    finally:
        _drop(doc)


def test_command_verify_json_reports_missing_then_repair_fixes(document_class, capsys):
    category = Category.objects.create(name="JsonFix")
    doc = document_class("CmdVfJson", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        get_redis_connection().delete(doc.key_for(category.pk))

        with pytest.raises(SystemExit):
            call_command("redisearch", "verify", "--json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert str(category.pk) in payload["documents"][0]["missing"]

        call_command("redisearch", "verify", "--repair")
        assert "ok" in capsys.readouterr().out
        assert doc.objects.get(pk=category.pk).name == "JsonFix"
    finally:
        _drop(doc)


def test_command_verify_limit_lists_sample_pks(document_class, capsys):
    rows = [Category.objects.create(name=f"Lim{i}") for i in range(3)]
    doc = document_class("CmdVfLimit", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        client = get_redis_connection()
        for row in rows:
            client.delete(doc.key_for(row.pk))

        with pytest.raises(SystemExit):
            call_command("redisearch", "verify", "--limit", "1")
        out = capsys.readouterr().out
        assert "missing" in out
        assert " +2" in out
        assert "verify --repair" in out
    finally:
        _drop(doc)


def test_command_verify_models_only_checks_named_model(document_class, capsys):
    Category.objects.create(name="VfCat")
    vendor = Vendor.objects.create(
        name="VfVendor", establishment_date=__import__("datetime").date.today()
    )
    Product.objects.create(name="VfProd", price=4, vendor=vendor)
    cat_doc = document_class("CmdVfCat", Category, ["name"])
    prod_doc = document_class("CmdVfProd", Product, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        call_command("redisearch", "verify", "--models", "tests.Product")
        out = capsys.readouterr().out
        assert "CmdVfProd" in out
        assert "CmdVfCat" not in out
    finally:
        _drop(cat_doc, prod_doc)


def test_command_reindex_and_verify_hash_storage(document_class, capsys):
    category = Category.objects.create(name="HashLive")
    doc = document_class(
        "CmdReHash",
        Category,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "name": "idx:test.category.cmdrehash",
                    "prefix": "rsd:test.category.cmdrehash:",
                    "storage": "hash",
                },
            )
        },
    )
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        call_command("redisearch", "reindex", "--blue-green")
        assert "reindexed" in capsys.readouterr().out
        assert doc.objects.get(pk=category.pk).name == "HashLive"

        call_command("redisearch", "verify")
        assert "ok" in capsys.readouterr().out
        assert get_redis_connection().hget(doc.key_for(category.pk), "_v")
    finally:
        _drop(doc)


def test_command_reindex_then_live_upsert_uses_new_prefix(document_class, capsys):
    category = Category.objects.create(name="BeforeSwap")
    doc = document_class("CmdReLive", Category, ["name"])
    try:
        call_command("redisearch", "populate")
        capsys.readouterr()
        call_command("redisearch", "reindex", "--blue-green")
        capsys.readouterr()
        category.name = "AfterSwap"
        category.save()
        Indexer().upsert(doc, category)
        new_key = f"{read_prefix(doc)}{category.pk}"
        old_key = f"{doc._meta.key_prefix}{category.pk}"
        client = get_redis_connection()
        assert client.exists(new_key)
        assert not client.exists(old_key)
        assert doc.objects.get(pk=category.pk).name == "AfterSwap"
    finally:
        _drop(doc)
