from __future__ import annotations

import pytest

from redis_search_django.exceptions import ConfigurationError
from redis_search_django.serializer import Serializer
from redis_search_django.targets import generation_prefix, physical_name_for
from redis_search_django.versioning import default_version, stamp_payload

from .models import Category


def test_public_payload_strips_stamp():
    from redis_search_django.versioning import public_payload

    assert public_payload({"pk": "1", "name": "A", "_v": "x"}) == {
        "pk": "1",
        "name": "A",
    }
    assert public_payload({"pk": "1"}) == {"pk": "1"}


def test_generation_prefix_is_sibling_not_child():
    base = "rsd:shop.product.productdocument:"
    assert generation_prefix(base, 1) == base
    assert generation_prefix(base, 2) == "rsd:shop.product.productdocument.g2:"
    assert not generation_prefix(base, 2).startswith(base)


def test_physical_name_includes_generation_after_first():
    alias = "idx:shop.product.productdocument"
    fp = "rsd-schema-v1:abcdef0123456789"
    assert physical_name_for(alias, fp, 1) == f"{alias}:abcdef01"
    assert physical_name_for(alias, fp, 2) == f"{alias}:g2:abcdef01"


@pytest.mark.django_db
def test_default_stamp_is_stable_and_changes_with_payload(document_class, category_obj):
    doc = document_class("CatVer", Category, ["name"])
    serializer = Serializer()
    payload = serializer.to_document(doc, category_obj)
    first = default_version(doc, payload)
    assert first == default_version(doc, payload)
    payload["name"] = "Other"
    assert default_version(doc, payload) != first


@pytest.mark.django_db
def test_default_version_reuses_cached_schema(
    document_class, category_obj, monkeypatch
):
    from redis_search_django import schema as schema_mod

    doc = document_class("CatVerCache", Category, ["name"])
    payload = Serializer().to_document(doc, category_obj)
    default_version(doc, payload)
    builds: list[int] = []
    real = schema_mod._build_schema

    def spy(cls):
        builds.append(1)
        return real(cls)

    monkeypatch.setattr(schema_mod, "_build_schema", spy)
    default_version(doc, payload)
    default_version(doc, {**payload, "name": "Other"})
    assert builds == []


@pytest.mark.django_db
def test_stamp_payload_adds_v(document_class, category_obj):
    doc = document_class("CatStamp", Category, ["name"])
    payload = Serializer().to_document(doc, category_obj)
    stamped = stamp_payload(doc, category_obj, payload)
    assert "_v" in stamped
    assert stamped["name"] == "Test"
    assert payload == {"pk": str(category_obj.pk), "name": "Test"}


@pytest.mark.django_db
def test_get_index_version_hook(document_class, category_obj):
    doc = document_class(
        "CatHookV",
        Category,
        ["name"],
        extra_attrs={
            "get_index_version": classmethod(lambda cls, instance, payload: "v-custom")
        },
    )
    stamped = stamp_payload(
        doc, category_obj, Serializer().to_document(doc, category_obj)
    )
    assert stamped["_v"] == "v-custom"


@pytest.mark.django_db
def test_empty_hook_skips_stamp(document_class, category_obj):
    from redis_search_django.versioning import payload_version

    assert payload_version(None) is None
    assert payload_version({"pk": "1"}) is None
    assert payload_version({"_v": 3}) == "3"

    doc = document_class(
        "CatSkipV",
        Category,
        ["name"],
        extra_attrs={
            "get_index_version": classmethod(lambda cls, instance, payload: "")
        },
    )
    payload = Serializer().to_document(doc, category_obj)
    assert stamp_payload(doc, category_obj, payload) == payload


def test_reserved_stamp_field_is_rejected():
    from redis_search_django import fields
    from redis_search_django.documents import Document

    with pytest.raises(ConfigurationError, match="reserved"):

        class Collides(Document):
            _v = fields.Tag()

            class Django:
                model = Category
                fields = ["name"]

            class Index:
                name = "idx:test.category.vcollide"
                prefix = "rsd:test.category.vcollide:"


def test_load_targets_falls_back_when_redis_errors(document_class, monkeypatch):
    from redis_search_django import targets

    doc = document_class("CatTgtFail", Category, ["name"])
    targets.invalidate_targets(doc)

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(targets, "get_redis_connection", boom)
    loaded = targets.load_targets(doc, fresh=True)
    assert loaded.read_prefix == doc._meta.key_prefix
    again = targets.load_targets(doc)
    assert again.read_prefix == doc._meta.key_prefix
    import time

    targets._cache[doc._meta.index_alias] = (time.monotonic() - 10, again)
    refreshed = targets.load_targets(doc)
    assert refreshed.read_prefix == doc._meta.key_prefix


def test_load_targets_keeps_stale_cache_when_redis_errors(document_class, monkeypatch):
    from redis_search_django import targets
    from redis_search_django.targets import WriteTargets

    doc = document_class("CatTgtStale", Category, ["name"])
    known = WriteTargets(
        read_prefix="green:",
        write_prefixes=("blue:", "green:"),
        generation=2,
        physical_name="idx:g2",
        reindex={"state": "backfill"},
    )
    targets._cache[doc._meta.index_alias] = (__import__("time").monotonic() - 10, known)

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(targets, "get_redis_connection", boom)
    loaded = targets.load_targets(doc, fresh=True)
    assert loaded.read_prefix == "green:"
    assert loaded.write_prefixes == ("blue:", "green:")


def test_load_targets_invalidate_race(document_class):
    from concurrent.futures import ThreadPoolExecutor

    from redis_search_django import targets

    doc = document_class("CatTgtRace", Category, ["name"])
    targets.invalidate_targets()
    errors: list[BaseException] = []

    def worker(_i: int) -> None:
        try:
            targets.load_targets(doc)
            targets.invalidate_targets(doc)
            targets.load_targets(doc, fresh=True)
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(32)))
    assert errors == []


def test_load_targets_does_not_cache_after_invalidate(document_class, monkeypatch):
    from redis_search_django import targets

    doc = document_class("CatTgtEpoch", Category, ["name"])
    targets.invalidate_targets(doc)

    class _Client:
        def get(self, _key: str) -> None:
            targets.invalidate_targets(doc)
            return None

    monkeypatch.setattr(targets, "get_redis_connection", lambda: _Client())
    loaded = targets.load_targets(doc, fresh=True)
    assert loaded.read_prefix == doc._meta.key_prefix
    assert doc._meta.index_alias not in targets._cache


def test_targets_cache_and_meta_shapes(document_class):
    from redis_search_django.targets import invalidate_targets, load_targets

    doc = document_class("CatTgt", Category, ["name"])
    invalidate_targets()
    loaded = load_targets(doc)
    assert loaded.read_prefix == doc._meta.key_prefix


def test_decode_meta_and_generation_string():
    from redis_search_django.targets import _decode_meta, _targets_from_meta

    assert _decode_meta(None) == {}
    assert _decode_meta(b'{"generation": "3"}')["generation"] == "3"
    assert _decode_meta(12) == {}
    targets = _targets_from_meta(
        {
            "generation": "4",
            "write_prefixes": [1, "p:"],
            "physical_name": "idx",
            "reindex": {"state": "backfill", "n": 1},
        },
        "base:",
    )
    assert targets.generation == 4
    assert targets.write_prefixes == ("p:",)
    assert targets.reindex == {"state": "backfill"}
    empty = _targets_from_meta({}, "base:")
    assert empty.read_prefix == "base:"
    assert empty.generation == 1
