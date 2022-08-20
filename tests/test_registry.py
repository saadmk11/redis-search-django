import datetime
from collections import defaultdict
from typing import List, Optional
from unittest import mock

from redis_search_django.documents import (
    EmbeddedJsonDocument,
    HashDocument,
    JsonDocument,
)
from redis_search_django.registry import DocumentRegistry
from tests.models import Category, Product, Tag, Vendor


def test_empty_registry():
    registry = DocumentRegistry()
    assert registry.django_model_map == {}
    assert registry.related_django_model_map == defaultdict(set)


def test_register(document_class):
    registry = DocumentRegistry()
    VendorDocumentClass = document_class(JsonDocument, Vendor, ["name"])
    CategoryDocumentClass = document_class(HashDocument, Category, ["name"])
    registry.register(VendorDocumentClass)
    registry.register(CategoryDocumentClass)

    assert registry.django_model_map == {
        Vendor: {VendorDocumentClass},
        Category: {CategoryDocumentClass},
    }
    assert registry.related_django_model_map == defaultdict(set)


def test_register_with_nested_documents(document_class):
    CategoryJsonDocument = document_class(JsonDocument, Category, ["name"])
    CategoryEmbeddedJsonDocument = document_class(
        EmbeddedJsonDocument, Category, ["name"]
    )
    TagEmbeddedJsonDocument = document_class(EmbeddedJsonDocument, Tag, ["name"])
    VendorEmbeddedJsonDocument = document_class(
        EmbeddedJsonDocument, Vendor, ["name", "establishment_date"]
    )

    class ProductJsonDocument(JsonDocument):
        # OnetoOneField
        vendor: VendorEmbeddedJsonDocument
        # ForeignKey field
        category: Optional[CategoryEmbeddedJsonDocument]
        # ManyToManyField
        tags: List[TagEmbeddedJsonDocument]

        class Django:
            model = Product
            fields = ["name", "description", "price", "created_at"]
            related_models = {
                Vendor: {
                    "related_name": "product",
                    "many": False,
                },
                Category: {
                    "related_name": "product_set",
                    "many": True,
                },
                Tag: {
                    "related_name": "product_set",
                    "many": True,
                },
            }

    class ProductLightJsonDocument(JsonDocument):
        category: Optional[CategoryEmbeddedJsonDocument]

        class Django:
            model = Product
            fields = ["name", "description", "price", "created_at"]
            related_models = {
                Category: {
                    "related_name": "product_set",
                    "many": True,
                },
            }

    registry = DocumentRegistry()
    registry.register(ProductJsonDocument)
    registry.register(ProductLightJsonDocument)
    registry.register(CategoryJsonDocument)

    assert registry.django_model_map == {
        Product: {ProductJsonDocument, ProductLightJsonDocument},
        Category: {CategoryJsonDocument},
    }
    assert registry.related_django_model_map == {
        Category: {ProductJsonDocument, ProductLightJsonDocument},
        Vendor: {ProductJsonDocument},
        Tag: {ProductJsonDocument},
    }


@mock.patch("redis_search_django.documents.JsonDocument.update_from_model_instance")
def test_update_document(update_from_model_instance, document_class):
    CategoryJsonDocument = document_class(JsonDocument, Category, ["name"])
    registry = DocumentRegistry()
    registry.register(CategoryJsonDocument)

    model_obj1 = Category(name="test")
    # Tag is not registered, so it should not be updated.
    model_obj2 = Tag(name="test")

    registry.update_document(model_obj1)
    registry.update_document(model_obj2)

    update_from_model_instance.assert_called_once_with(model_obj1, create=True)


@mock.patch("redis_search_django.documents.JsonDocument.update_from_model_instance")
def test_update_document_with_auto_index_disabled(
    update_from_model_instance, document_class
):
    CategoryJsonDocument = document_class(
        JsonDocument, Category, ["name"], enable_auto_index=False
    )
    registry = DocumentRegistry()
    registry.register(CategoryJsonDocument)
    model_obj1 = Category(name="test")

    registry.update_document(model_obj1)
    update_from_model_instance.assert_not_called()


@mock.patch("redis_search_django.documents.JsonDocument.update_from_model_instance")
def test_update_document_with_global_auto_index_disabled(
    update_from_model_instance, settings, document_class
):
    settings.REDIS_SEARCH_AUTO_INDEX = False

    CategoryJsonDocument = document_class(JsonDocument, Category, ["name"])
    registry = DocumentRegistry()
    registry.register(CategoryJsonDocument)
    model_obj1 = Category(name="test")

    registry.update_document(model_obj1)
    update_from_model_instance.assert_not_called()


@mock.patch(
    "redis_search_django.documents.JsonDocument.update_from_related_model_instance"
)
def test_update_related_documents(update_from_related_model_instance, document_class):
    CategoryEmbeddedJsonDocument = document_class(
        EmbeddedJsonDocument, Category, ["name"]
    )

    class ProductJsonDocument(JsonDocument):
        category: Optional[CategoryEmbeddedJsonDocument]

        class Django:
            model = Product
            fields = ["name", "description", "price", "created_at"]
            related_models = {
                Category: {
                    "related_name": "product_set",
                    "many": True,
                },
            }

    registry = DocumentRegistry()
    registry.register(ProductJsonDocument)

    model_obj1 = Category(name="test")
    # Tag and Vendor are not registered, so it should not be updated.
    model_obj2 = Tag(name="test")
    model_obj3 = Vendor(name="test", establishment_date=datetime.date.today())

    registry.update_related_documents(model_obj1)
    registry.update_related_documents(model_obj2)
    registry.update_related_documents(model_obj3)

    update_from_related_model_instance.assert_called_once_with(model_obj1, exclude=None)


@mock.patch(
    "redis_search_django.documents.JsonDocument.update_from_related_model_instance"
)
def test_update_related_documents_with_auto_index_disabled(
    update_from_related_model_instance, document_class
):
    CategoryEmbeddedJsonDocument = document_class(
        EmbeddedJsonDocument, Category, ["name"], enable_auto_index=False
    )

    class ProductJsonDocument(JsonDocument):
        category: Optional[CategoryEmbeddedJsonDocument]

        class Django:
            model = Product
            fields = ["name", "description", "price", "created_at"]
            related_models = {
                Category: {
                    "related_name": "product_set",
                    "many": True,
                },
            }
            auto_index = False

    registry = DocumentRegistry()
    registry.register(ProductJsonDocument)

    model_obj1 = Category(name="test")

    registry.update_related_documents(model_obj1)

    update_from_related_model_instance.assert_not_called()


@mock.patch(
    "redis_search_django.documents.JsonDocument.update_from_related_model_instance"
)
def test_update_related_documents_with_global_auto_index_disabled(
    update_from_related_model_instance, settings, document_class
):
    settings.REDIS_SEARCH_AUTO_INDEX = False
    CategoryEmbeddedJsonDocument = document_class(
        EmbeddedJsonDocument, Category, ["name"]
    )

    class ProductJsonDocument(JsonDocument):
        category: Optional[CategoryEmbeddedJsonDocument]

        class Django:
            model = Product
            fields = ["name", "description", "price", "created_at"]
            related_models = {
                Category: {
                    "related_name": "product_set",
                    "many": True,
                },
            }
            auto_index = True

    registry = DocumentRegistry()
    registry.register(ProductJsonDocument)

    model_obj1 = Category(name="test")

    registry.update_related_documents(model_obj1)

    update_from_related_model_instance.assert_not_called()


@mock.patch("redis_search_django.documents.JsonDocument.delete")
def test_remove_document(delete, document_class):
    CategoryJsonDocument = document_class(JsonDocument, Category, ["name"])
    registry = DocumentRegistry()
    registry.register(CategoryJsonDocument)

    model_obj1 = Category(name="test")
    # Tag is not registered, so it should not be deleted.
    model_obj2 = Tag(name="test")

    registry.remove_document(model_obj1)
    registry.remove_document(model_obj2)

    delete.assert_called_once_with(model_obj1.pk)


@mock.patch("redis_search_django.documents.JsonDocument.delete")
def test_remove_document_with_auto_index_disabled(delete, document_class):
    CategoryJsonDocument = document_class(
        JsonDocument, Category, ["name"], enable_auto_index=False
    )
    registry = DocumentRegistry()
    registry.register(CategoryJsonDocument)

    model_obj1 = Category(name="test")

    registry.remove_document(model_obj1)

    delete.assert_not_called()


@mock.patch("redis_search_django.documents.JsonDocument.delete")
def test_remove_document_with_global_auto_index_disabled(
    delete, settings, document_class
):
    settings.REDIS_SEARCH_AUTO_INDEX = False

    CategoryJsonDocument = document_class(JsonDocument, Category, ["name"])
    registry = DocumentRegistry()
    registry.register(CategoryJsonDocument)

    model_obj1 = Category(name="test")

    registry.remove_document(model_obj1)

    delete.assert_not_called()


@mock.patch("redis_search_django.documents.Document.index_all")
def test_index_documents(index_all, document_class):
    registry = DocumentRegistry()
    VendorDocumentClass = document_class(JsonDocument, Vendor, ["name"])
    CategoryDocumentClass = document_class(HashDocument, Category, ["name"])
    registry.register(VendorDocumentClass)
    registry.register(CategoryDocumentClass)

    registry.index_documents()

    assert index_all.call_count == 2


@mock.patch("redis_search_django.documents.Document.index_all")
def test_index_documents_with_specific_model(index_all, document_class):
    registry = DocumentRegistry()
    VendorDocumentClass = document_class(JsonDocument, Vendor, ["name"])
    CategoryDocumentClass = document_class(HashDocument, Category, ["name"])
    registry.register(VendorDocumentClass)
    registry.register(CategoryDocumentClass)

    registry.index_documents(["tests.Category"])

    index_all.assert_called_once()
