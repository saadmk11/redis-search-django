from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import models

from redis_search_django.exceptions import ConfigurationError
from redis_search_django.fields import Boolean, Numeric, Tag, Text
from redis_search_django.mapping import field_from_django

from .models import Category, Product, Vendor


def test_base_field_to_index_value_coerces_remaining_types():
    from redis_search_django.fields import Field

    field = Field()
    assert field.to_index_value(None) is None
    assert field.to_index_value("x") == "x"
    assert field.to_index_value(bytearray(b"ab")) == b"ab"
    assert field.to_index_value(memoryview(b"cd")) == b"cd"

    class File:
        name = "cover.png"

    assert field.to_index_value(File()) == "cover.png"
    assert field.to_index_value(Decimal("2")) == "2"


def test_numeric_falls_back_to_string():
    from uuid import UUID

    assert Numeric().to_index_value(UUID(int=1)).startswith("0000")


def test_char_maps_to_sortable_text():
    field = field_from_django(Category._meta.get_field("name"))
    assert isinstance(field, Text)
    assert field.sortable is True


def test_text_maps_to_unstored_text():
    field = field_from_django(Product._meta.get_field("description"))
    assert isinstance(field, Text)
    assert field.sortable is False


def test_decimal_maps_to_numeric():
    field = field_from_django(Product._meta.get_field("price"))
    assert isinstance(field, Numeric)
    assert field.to_index_value(Decimal("10.50")) == 10.5


def test_datetime_maps_to_numeric():
    field = field_from_django(Product._meta.get_field("created_at"))
    assert isinstance(field, Numeric)
    value = datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
    assert field.to_index_value(value) == value.timestamp()


def test_date_to_midnight_utc():
    field = field_from_django(Vendor._meta.get_field("establishment_date"))
    assert isinstance(field, Numeric)
    assert (
        field.to_index_value(datetime.date(2024, 1, 1))
        == datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
    )


def test_boolean_json_and_hash():
    field = Boolean()
    assert field.to_index_value(True, storage="json") is True
    assert field.to_index_value(False, storage="hash") == "false"


def test_tag_uuid_and_file_name():
    from uuid import UUID

    field = Tag()
    assert field.to_index_value("hello") == "hello"
    assert (
        field.to_index_value(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
        == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    class Fileish:
        name = "cover.txt"

    assert field.to_index_value(Fileish()) == "cover.txt"
    assert field.to_index_value(type("EmptyFile", (), {"name": ""})()) is None


def test_related_fields_are_rejected():
    with pytest.raises(ConfigurationError, match="Related field"):
        field_from_django(Product._meta.get_field("vendor"))


def test_unmapped_django_field_raises():
    blob = models.BinaryField()
    blob.name = "blob"
    with pytest.raises(ConfigurationError, match="No default"):
        field_from_django(blob)


def test_long_charfield_is_not_sortable():
    field = field_from_django(models.CharField(max_length=512))
    assert isinstance(field, Text)
    assert field.sortable is False


def test_text_tag_numeric_boolean_geo_vector_options():
    from redis_search_django.fields import Field, Geo, Vector

    text = Text(weight=2.0, phonetic="dm:en", index_missing=True, no_stem=True)
    text.bind("title", object)
    redis_text = text.to_redis_field(json_path="$.title", alias="title")
    assert redis_text is not None
    args = [str(item) for item in getattr(redis_text, "args", [])]
    assert "2.0" in args
    assert "NOSTEM" in args
    assert "dm:en" in args
    assert text.to_index_value(None) is None
    assert text.to_index_value(12) == "12"

    tag = Tag(sortable=True, index_missing=True, suffix_trie=True)
    tag.bind("code", object)
    redis_tag = tag.to_redis_field(json_path="$.code", alias="code")
    assert redis_tag is not None
    assert tag.to_index_value(None) is None

    numeric = Numeric(index_missing=True)
    numeric.bind("n", object)
    assert numeric.to_redis_field(json_path="$.n", alias="n") is not None
    assert numeric.to_index_value(None) is None

    boolean = Boolean(sortable=True, index_missing=True)
    boolean.bind("ok", object)
    assert boolean.to_redis_field(json_path="$.ok", alias="ok") is not None
    assert boolean.to_index_value(None) is None

    geo = Geo()
    geo.bind("loc", object)
    assert geo.redis_type() == "GEO"
    assert geo.to_redis_field(json_path="$.loc", alias="loc") is not None
    assert geo.to_index_value(None) is None
    assert geo.to_index_value("1,2") == "1,2"

    vector = Vector(dims=4, algorithm="FLAT", initial_cap=10)
    vector.bind("emb", object)
    assert vector.redis_type() == "VECTOR"
    assert vector.to_redis_field(json_path="$.emb", alias="emb") is not None
    hnsw = Vector(dims=4)
    hnsw.bind("emb", object)
    assert hnsw.to_redis_field(json_path="$.emb", alias="emb") is not None
    from redis_search_django.fields import Nested, Object

    emb = type("Emb", (), {"_meta": type("M", (), {"model": None})})
    obj = Object(emb)  # type: ignore[arg-type]
    assert obj.redis_type() == "OBJECT"
    nested = Nested(emb)  # type: ignore[arg-type]
    assert nested.redis_type() == "NESTED"
    named = Text()
    named.bind("title", object)
    assert named.hash_name() == "title"

    field = Field()
    field.bind("x", object)
    assert field.as_name("parent") == "parent_x"
    assert field.hash_name("parent") == "parent__x"
    named = Text(as_name="custom")
    named.bind("title", object)
    assert named.as_name() == "custom"
    assert field.to_index_value(None) is None
    with pytest.raises(NotImplementedError):
        field.redis_type()
    with pytest.raises(NotImplementedError):
        field.to_redis_field(json_path="$.x", alias="x")

    unbound = Field()
    assert unbound.hash_name() == ""
    assert unbound.as_name() == ""
    assert unbound.to_index_value(None) is None
    assert unbound.to_index_value("kept") == "kept"


def test_tag_suffix_trie_sets_withsuffixtrie():
    tag = Tag(suffix_trie=True)
    tag.bind("code", object)
    redis_tag = tag.to_redis_field(json_path="$.code", alias="code")
    assert "WITHSUFFIXTRIE" in getattr(redis_tag, "args", [])

    class NoArgs:
        def __init__(self, *args, **kwargs):
            pass

    from redis_search_django import fields as fields_mod

    original = fields_mod.RedisTagField
    fields_mod.RedisTagField = NoArgs
    try:
        fallback = tag.to_redis_field(json_path="$.code", alias="code")
        assert fallback is not None
    finally:
        fields_mod.RedisTagField = original


def test_resolve_attr_walks_none():
    from redis_search_django.fields import _resolve_attr

    class Box:
        inner = None

    assert _resolve_attr(Box(), "inner.missing") is None


def test_aware_datetime_to_timestamp():
    from django.utils import timezone

    from redis_search_django.fields import _datetime_to_ts

    aware = timezone.make_aware(datetime.datetime(2024, 6, 1, 12, 0))
    assert _datetime_to_ts(aware) == aware.timestamp()
    naive = datetime.datetime(2024, 6, 1, 12, 0)
    assert (
        _datetime_to_ts(naive)
        == naive.replace(tzinfo=datetime.timezone.utc).timestamp()
    )
