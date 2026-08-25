from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from redis_search_django.fields import Boolean, Numeric, Tag, Text
from redis_search_django.mapping import field_from_django
from redis_search_django.serializer import Serializer

from .catalog import create_sample_book, make_catalog_documents
from .models import Author, Book, Publisher


def test_all_django_field_mappings():
    meta = Book._meta
    assert isinstance(field_from_django(meta.get_field("title")), Text)
    assert isinstance(field_from_django(meta.get_field("summary")), Text)
    assert isinstance(field_from_django(Author._meta.get_field("email")), Text)
    assert isinstance(field_from_django(meta.get_field("isbn")), Tag)
    assert isinstance(field_from_django(meta.get_field("sku")), Tag)
    assert isinstance(field_from_django(Author._meta.get_field("website")), Tag)
    assert isinstance(field_from_django(meta.get_field("cover")), Tag)
    assert isinstance(field_from_django(meta.get_field("shop_opens")), Tag)
    assert isinstance(field_from_django(meta.get_field("price")), Numeric)
    assert isinstance(field_from_django(meta.get_field("pages")), Numeric)
    assert isinstance(field_from_django(meta.get_field("weight")), Numeric)
    assert isinstance(field_from_django(meta.get_field("published_on")), Numeric)
    assert isinstance(field_from_django(meta.get_field("listed_at")), Numeric)
    assert isinstance(field_from_django(meta.get_field("available")), Boolean)
    assert field_from_django(meta.get_field("featured")).index_missing is True
    assert isinstance(field_from_django(Publisher._meta.get_field("slug")), Tag)


@pytest.mark.django_db
def test_serializer_emits_all_scalar_types():
    docs = make_catalog_documents()
    book = create_sample_book()
    payload = Serializer().to_document(docs["book"], book)
    assert payload["title"] == "Notes on the Engine"
    assert payload["summary"].startswith("A trail")
    assert payload["isbn"] == "978-0001"
    assert payload["sku"] == "11111111-1111-1111-1111-111111111111"
    assert payload["price"] == 19.5
    assert payload["pages"] == 240
    assert payload["weight"] == 0.45
    assert payload["available"] is True
    assert payload["featured"] is True
    assert (
        payload["published_on"]
        == datetime.datetime(2020, 5, 1, tzinfo=datetime.timezone.utc).timestamp()
    )
    assert isinstance(payload["listed_at"], float)
    assert payload["shop_opens"] == "09:30:00"
    assert payload["cover"].startswith("covers/cover")
    assert payload["author"]["email"] == "ada@example.com"
    assert payload["author"]["website"] == "https://ada.example"
    assert payload["author"]["active"] is True
    assert payload["publisher"]["slug"] == "analytical"
    assert (
        payload["publisher"]["founded"]
        == datetime.datetime(1843, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
    )
    assert {item["slug"] for item in payload["genres"]} == {"fiction", "history"}
    assert payload["extra"]["edition"] == 2
    assert "location" not in payload
    assert "embedding" not in payload


def test_uuid_and_decimal_coercion():
    assert Tag().to_index_value(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")) == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
    assert Numeric().to_index_value(Decimal("3.14")) == 3.14
    assert Numeric().to_index_value(True) == 1
    assert Tag().to_index_value(datetime.time(14, 5, 6)) == "14:05:06"
