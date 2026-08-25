from __future__ import annotations

import pytest

from redis_search_django.documents import Document
from redis_search_django.exceptions import ConfigurationError
from redis_search_django.registry import DocumentRegistry, document_registry

from .models import Category, Vendor


def test_empty_registry():
    registry = DocumentRegistry()
    assert registry.primary_documents() == []
    assert registry.get_for_model(Vendor) == set()


def test_register_tracks_models(document_class):
    vendor_doc = document_class("VendorDocument", Vendor, ["name"])
    category_doc = document_class("CategoryDocument", Category, ["name"])
    assert document_registry.get_for_model(Vendor) == {vendor_doc}
    assert document_registry.get_for_model(Category) == {category_doc}
    assert document_registry.get_by_label("tests.VendorDocument") is vendor_doc
    assert document_registry.get_by_label("tests.CategoryDocument") is category_doc


def test_two_documents_on_same_model(document_class):
    first = document_class("ProductFull", Category, ["name"])
    second = document_class("ProductLight", Category, ["name"])
    assert document_registry.get_for_model(Category) == {first, second}


def test_duplicate_alias_is_rejected():
    class First(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:dup"
            prefix = "rsd:dup:a:"

    with pytest.raises(ConfigurationError, match="Index alias"):

        class Second(Document):
            class Django:
                model = Vendor
                fields = ["name"]

            class Index:
                name = "idx:dup"
                prefix = "rsd:dup:b:"


def test_duplicate_prefix_is_rejected():
    class First(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:pre1"
            prefix = "rsd:shared:"

    with pytest.raises(ConfigurationError, match="Key prefix"):

        class Second(Document):
            class Django:
                model = Vendor
                fields = ["name"]

            class Index:
                name = "idx:pre2"
                prefix = "rsd:shared:"


def test_register_skips_embedded_and_duplicate_label(document_class):
    embedded = document_class("EmbSkip", Category, ["name"], embedded=True)
    before = list(document_registry.documents)
    document_registry.register(embedded)
    assert embedded not in document_registry.documents
    assert document_registry.documents == before
    first = document_class("DupLabel", Category, ["name"])
    with pytest.raises(ConfigurationError, match="label"):
        type(
            "DupLabel",
            (Document,),
            {
                "Django": type("Django", (), {"model": Category, "fields": ["name"]}),
                "Index": type(
                    "Index",
                    (),
                    {
                        "name": "idx:test.duplabel2",
                        "prefix": "rsd:test.duplabel2:",
                    },
                ),
            },
        )
    assert document_registry.get_by_label("tests.DupLabel") is first
