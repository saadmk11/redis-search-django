from __future__ import annotations

import pytest

from redis_search_django.serializer import Serializer

from .catalog import create_sample_book, make_catalog_documents
from .models import Author, BookExtra, Genre, Publisher


@pytest.mark.django_db
def test_infers_fk_o2o_and_m2m():
    docs = make_catalog_documents()
    related = docs["book"]._meta.related_map
    assert related[Author]["related_name"] == "books"
    assert related[Author]["many"] is True
    assert related[Publisher]["related_name"] == "books"
    assert related[Publisher]["many"] is True
    assert related[Genre]["related_name"] == "books"
    assert related[Genre]["many"] is True
    assert related[BookExtra]["related_name"] == "book"
    assert related[BookExtra]["many"] is False


@pytest.mark.django_db
def test_optional_fk_serializes_null():
    docs = make_catalog_documents()
    book = create_sample_book(publisher=None)
    payload = Serializer().to_document(docs["book"], book)
    assert "publisher" not in payload
    assert payload["author"]["name"] == "Ada Lovelace"


@pytest.mark.django_db
def test_get_instances_from_related_all_directions():
    docs = make_catalog_documents()
    book = create_sample_book()
    book_doc = docs["book"]
    parents = list(book_doc.get_instances_from_related(book.author))
    assert book in parents
    parents = list(book_doc.get_instances_from_related(book.publisher))
    assert book in parents
    genre = book.genres.first()
    assert book in list(book_doc.get_instances_from_related(genre))
    assert book_doc.get_instances_from_related(book.extra) == book
