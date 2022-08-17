import datetime

import pytest
from django.core.exceptions import ImproperlyConfigured
from redis_om import NotFoundError

from redis_search_django.documents import (
    DjangoOptions,
    EmbeddedJsonDocument,
    HashDocument,
    JsonDocument,
)
from tests.models import Category, Product, Tag, Vendor
from tests.utils import is_redis_running


def test_django_options():
    class Django:
        model = Product
        fields = ["name", "description", "price", "created_at"]
        select_related_fields = ["vendor", "category"]
        prefetch_related_fields = ["tags"]
        auto_index = True
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

    options = DjangoOptions(Django)

    assert options.model == Product
    assert options.auto_index is True
    assert options.fields == ["name", "description", "price", "created_at"]
    assert options.select_related_fields == ["vendor", "category"]
    assert options.prefetch_related_fields == ["tags"]
    assert options.related_models == {
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


def test_django_options_with_empty_optional_value():
    class Django:
        model = Product
        fields = None
        auto_index = False
        related_models = None
        select_related_fields = None
        prefetch_related_fields = None

    options = DjangoOptions(Django)

    assert options.model == Product
    assert options.auto_index is False
    assert options.fields == []
    assert options.select_related_fields == []
    assert options.prefetch_related_fields == []
    assert options.related_models == {}


def test_django_options_with_empty_required_value():
    class Django:
        model = None

    with pytest.raises(ImproperlyConfigured):
        DjangoOptions(Django)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_document_id(document_class, category_obj):
    CategoryJsonDocument = document_class(JsonDocument, Category, ["name"])

    document = CategoryJsonDocument.from_model_instance(category_obj, save=False)

    assert document.id == str(category_obj.id)
    assert document.id == document.pk


def test_json_document_with_django_fields_including_related_field(document_class):
    with pytest.raises(ImproperlyConfigured):
        document_class(JsonDocument, Product, ["name", "vendor"])


def test_json_document_with_django_fields(document_class):
    CategoryJsonDocument = document_class(JsonDocument, Category, ["name"])

    assert list(CategoryJsonDocument.__fields__.keys()) == ["pk", "name"]


def test_json_document_with_django_fields_including_id(document_class):
    CategoryJsonDocument = document_class(JsonDocument, Category, ["name", "id"])

    assert list(CategoryJsonDocument.__fields__.keys()) == ["pk", "name"]


def test_json_document_without_django_fields(document_class):
    CategoryJsonDocument = document_class(JsonDocument, Category, [])

    assert list(CategoryJsonDocument.__fields__.keys()) == ["pk"]


def test_embedded_json_document_with_django_fields(document_class):
    CategoryJsonDocument = document_class(EmbeddedJsonDocument, Category, ["name"])

    assert list(CategoryJsonDocument.__fields__.keys()) == ["pk", "name"]


def test_embedded_json_document_without_django_fields(document_class):
    CategoryJsonDocument = document_class(EmbeddedJsonDocument, Category, [])

    assert list(CategoryJsonDocument.__fields__.keys()) == ["pk"]


def test_hash_document_with_django_fields(document_class):
    CategoryJsonDocument = document_class(HashDocument, Category, ["name"])

    assert list(CategoryJsonDocument.__fields__.keys()) == ["pk", "name"]


def test_hash_document_without_django_fields(document_class):
    CategoryJsonDocument = document_class(HashDocument, Category, [])

    assert list(CategoryJsonDocument.__fields__.keys()) == ["pk"]


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_get_queryset(document_class, product_obj):
    ProductJsonDocument = document_class(JsonDocument, Product, ["name"])
    assert ProductJsonDocument.get_queryset().count() == 1


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_index_all(nested_document_class):
    ProductDocumentCalss = nested_document_class[0]
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(
        name="Test",
        price=10.0,
        vendor=vendor,
        category=Category.objects.create(name="test"),
    )

    tag = Tag.objects.create(name="test")
    tag2 = Tag.objects.create(name="test2")
    product.tags.set([tag, tag2])

    ProductDocumentCalss.delete(pk=product.pk)

    with pytest.raises(NotFoundError):
        ProductDocumentCalss.get(pk=product.pk)

    ProductDocumentCalss.index_all()

    assert ProductDocumentCalss.get(pk=product.pk).pk == str(product.pk)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_index_queryset(nested_document_class):
    ProductDocumentCalss = nested_document_class[0]
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(
        name="Test",
        price=10.0,
        vendor=vendor,
        category=Category.objects.create(name="test"),
    )

    tag = Tag.objects.create(name="test")
    tag2 = Tag.objects.create(name="test2")
    product.tags.set([tag, tag2])

    ProductDocumentCalss.delete(pk=product.pk)

    with pytest.raises(NotFoundError):
        ProductDocumentCalss.get(pk=product.pk)

    ProductDocumentCalss.index_queryset(Product.objects.all())

    assert ProductDocumentCalss.get(pk=product.pk).pk == str(product.pk)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_update_from_model_instance(document_class, category_obj):
    CategoryDocumentClass = document_class(JsonDocument, Category, ["name"])

    with pytest.raises(NotFoundError):
        CategoryDocumentClass.get(pk=category_obj.pk)

    CategoryDocumentClass.update_from_model_instance(category_obj, create=True)

    assert CategoryDocumentClass.get(pk=category_obj.pk) is not None


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_update_from_model_instance_no_create(document_class, category_obj):
    CategoryDocumentClass = document_class(JsonDocument, Category, ["name"])

    with pytest.raises(NotFoundError):
        CategoryDocumentClass.get(pk=category_obj.pk)

    with pytest.raises(NotFoundError):
        CategoryDocumentClass.update_from_model_instance(category_obj, create=False)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
def test_from_data(document_class):
    CategoryDocumentClass = document_class(JsonDocument, Category, ["name"])

    data = {"name": "test"}
    document = CategoryDocumentClass.from_data(data)

    assert document.name == "test"


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_from_model_instance(document_class, category_obj):
    CategoryDocumentClass = document_class(JsonDocument, Category, ["name"])

    with pytest.raises(NotFoundError):
        CategoryDocumentClass.get(pk=category_obj.pk)

    CategoryDocumentClass.from_model_instance(category_obj)

    assert CategoryDocumentClass.get(pk=category_obj.pk) is not None
