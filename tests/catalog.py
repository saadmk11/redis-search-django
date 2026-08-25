from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from redis_search_django import fields
from redis_search_django.documents import Document

from .conftest import make_document
from .models import Author, Book, BookExtra, Genre, Publisher


def make_catalog_documents() -> dict[str, type[Document]]:
    author_doc = make_document(
        "AuthorDocument",
        Author,
        ["name", "email", "website", "active"],
        embedded=True,
    )
    publisher_doc = make_document(
        "PublisherDocument",
        Publisher,
        ["name", "slug", "founded"],
        embedded=True,
    )
    genre_doc = make_document("GenreDocument", Genre, ["name", "slug"], embedded=True)
    extra_doc = make_document(
        "BookExtraDocument", BookExtra, ["notes", "edition"], embedded=True
    )
    book_doc = make_document(
        "BookDocument",
        Book,
        [
            "title",
            "summary",
            "isbn",
            "sku",
            "price",
            "pages",
            "weight",
            "available",
            "featured",
            "published_on",
            "listed_at",
            "shop_opens",
            "cover",
        ],
        extra_attrs={
            "author": fields.Object(author_doc),
            "publisher": fields.Object(publisher_doc, required=False),
            "genres": fields.Nested(genre_doc),
            "extra": fields.Object(extra_doc, required=False, model_attr="extra"),
            "location": fields.Geo(),
            "embedding": fields.Vector(dims=4),
        },
        select_related_fields=["author", "publisher"],
        prefetch_related_fields=["genres"],
    )
    return {
        "book": book_doc,
        "author": author_doc,
        "publisher": publisher_doc,
        "genre": genre_doc,
        "extra": extra_doc,
    }


def create_sample_book(**overrides: object) -> Book:
    author = overrides.pop("author", None) or Author.objects.create(
        name="Ada Lovelace",
        email="ada@example.com",
        website="https://ada.example",
        active=True,
    )
    if "publisher" in overrides:
        publisher = overrides.pop("publisher")
    else:
        publisher = Publisher.objects.create(
            name="Analytical Press",
            slug="analytical",
            founded=datetime.date(1843, 1, 1),
        )
    genres = overrides.pop("genres", None)
    extra_notes = overrides.pop("extra_notes", "first edition")
    listed_at = overrides.pop(
        "listed_at",
        datetime.datetime(2024, 6, 1, 12, 0, tzinfo=datetime.timezone.utc),
    )
    defaults = {
        "title": "Notes on the Engine",
        "summary": "A trail running through early computing history",
        "isbn": "978-0001",
        "sku": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "price": Decimal("19.50"),
        "pages": 240,
        "weight": 0.45,
        "available": True,
        "featured": True,
        "published_on": datetime.date(2020, 5, 1),
        "listed_at": listed_at,
        "shop_opens": datetime.time(9, 30),
        "author": author,
        "publisher": publisher,
    }
    defaults.update(overrides)
    book = Book.objects.create(**defaults)
    if defaults.get("cover") is None:
        book.cover = "covers/cover.txt"
        book.save(update_fields=["cover"])
    if extra_notes is not None:
        BookExtra.objects.create(book=book, notes=extra_notes, edition=2)
    if genres is None:
        fiction = Genre.objects.create(name="Fiction", slug="fiction")
        history = Genre.objects.create(name="History", slug="history")
        book.genres.set([fiction, history])
    elif genres:
        book.genres.set(list(genres))
    return book
