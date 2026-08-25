from __future__ import annotations

import pytest

from redis_search_django.client import get_redis_connection, json_set
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer
from redis_search_django.targets import read_prefix

from .helpers import is_redis_running
from .models import Category

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


def _docs(document_class, name: str):
    return document_class(name, Category, ["name"])


def test_verify_reports_missing_stale_orphaned_and_repairs(document_class):
    keep = Category.objects.create(name="Keep")
    stale = Category.objects.create(name="Stale")
    gone = Category.objects.create(name="Gone")
    doc = _docs(document_class, "CatVerify")
    indexer = Indexer()
    indexer.populate(doc)
    client = get_redis_connection()
    prefix = read_prefix(doc)

    client.delete(doc.key_for(keep.pk))
    json_set(
        client,
        doc.key_for(stale.pk),
        {"pk": str(stale.pk), "name": "Stale", "_v": "old"},
    )
    gone_pk = str(gone.pk)
    gone_key = doc.key_for(gone.pk)
    gone.delete()
    extra_key = f"{prefix}999001"
    json_set(client, extra_key, {"pk": "999001", "name": "ghost", "_v": "x"})

    report = indexer.verify(doc)
    assert str(keep.pk) in report.missing
    assert str(stale.pk) in report.stale
    assert gone_pk in report.orphaned
    assert "999001" in report.orphaned
    assert report.ok is False

    fixed = indexer.verify(doc, repair=True)
    assert fixed.ok
    assert fixed.repaired_missing >= 1
    assert fixed.repaired_stale >= 1
    assert fixed.repaired_orphaned >= 1
    assert doc.objects.get(pk=keep.pk).name == "Keep"
    assert doc.objects.get(pk=stale.pk).name == "Stale"
    assert not client.exists(gone_key)
    assert not client.exists(extra_key)
    IndexManager(doc).drop(delete_docs=True)


def test_verify_unversioned_and_ghost_repair(document_class, settings):
    from redis_search_django.verify import VerifyReport, _repair

    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    one = Category.objects.create(name="One")
    two = Category.objects.create(name="Two")
    doc = _docs(document_class, "CatUnver")
    indexer = Indexer()
    indexer.populate(doc)
    client = get_redis_connection()
    json_set(client, doc.key_for(one.pk), {"pk": str(one.pk), "name": "One"})
    report = indexer.verify(doc)
    assert str(one.pk) in report.stale
    assert report.checked >= 2
    ghost = VerifyReport(
        document=doc.__name__,
        prefix=read_prefix(doc),
        missing=["999999"],
        stale=[str(two.pk)],
    )
    _repair(doc, ghost, read_prefix(doc))
    assert ghost.repaired_stale >= 1
    assert "999999" in ghost.missing
    IndexManager(doc).drop(delete_docs=True)


def test_verify_skips_should_index_false(document_class):
    keep = Category.objects.create(name="KeepMe")
    skip = Category.objects.create(name="skip")
    doc = document_class(
        "CatShouldV",
        Category,
        ["name"],
        extra_attrs={
            "should_index": classmethod(lambda cls, instance: instance.name != "skip")
        },
    )
    indexer = Indexer()
    indexer.populate(doc)
    report = indexer.verify(doc)
    assert str(keep.pk) not in report.missing
    assert str(skip.pk) not in report.missing
    IndexManager(doc).drop(delete_docs=True)


def test_load_versions_exists_false(document_class):
    from redis_search_django.verify import _load_versions

    doc = document_class(
        "CatHashExists",
        Category,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "name": "idx:test.category.hashexists",
                    "prefix": "rsd:test.category.hashexists:",
                    "storage": "hash",
                },
            )
        },
    )

    class Fake:
        def pipeline(self, transaction=False):
            return self

        def hget(self, key, field):
            return None

        def execute(self, raise_on_error=True):
            return [None]

    found = _load_versions(Fake(), doc, "p:", ["p:1"])
    assert found == {"1": None}


def test_verify_helpers_and_scan_filters():
    from redis_search_django.verify import _coerce_pks, _scan_keys

    class FakeClient:
        def scan_iter(self, match, count):
            yield f"{match[:-1]}1"
            yield b"rsd:x:2"
            yield f"{match[:-1]}g2:9"
            yield 12

    keys = list(_scan_keys(FakeClient(), "rsd:x:"))
    assert "rsd:x:1" in keys
    assert "rsd:x:2" in keys
    assert all("g2" not in key for key in keys)

    class Field:
        def to_python(self, raw):
            if raw == "bad":
                raise ValueError("nope")
            return int(raw)

    class Model:
        class _meta:
            pk = Field()

    assert _coerce_pks(Model, ["1", "bad"]) == [1, "bad"]


def test_write_reindex_without_report():
    from io import StringIO

    from redis_search_django.indexer import ReindexResult
    from redis_search_django.management.commands.redisearch import Command

    command = Command(stdout=StringIO())
    command._write_reindex(
        ReindexResult(
            document="Demo",
            generation=2,
            old_physical="old",
            new_physical="new",
            old_prefix="a:",
            new_prefix="b:",
            count=3,
            verified=False,
        )
    )
    assert "verify failed" in command.stdout.getvalue()


def test_write_verify_partial_repair():
    from io import StringIO

    from redis_search_django.management.commands.redisearch import Command
    from redis_search_django.verify import VerifyReport

    command = Command(stdout=StringIO())
    report = VerifyReport(
        document="Demo",
        prefix="p:",
        missing=["1", "2"],
        repaired_missing=1,
    )
    command._write_verify([report], as_json=False, limit=1)
    out = command.stdout.getvalue()
    assert "repaired" in out
    assert "missing" in out


def test_hash_verify_and_stamp(document_class):
    category = Category.objects.create(name="HashV")
    doc = document_class(
        "CatHashVerify",
        Category,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "name": "idx:test.category.hashverify",
                    "prefix": "rsd:test.category.hashverify:",
                    "storage": "hash",
                },
            )
        },
    )
    indexer = Indexer()
    indexer.populate(doc)
    client = get_redis_connection()
    assert client.hget(doc.key_for(category.pk), "_v")
    client.hdel(doc.key_for(category.pk), "_v")
    stale = indexer.verify(doc)
    assert str(category.pk) in stale.stale
    client.delete(doc.key_for(category.pk))
    report = indexer.verify(doc)
    assert str(category.pk) in report.missing
    assert indexer.verify(doc, repair=True).ok
    IndexManager(doc).drop(delete_docs=True)
