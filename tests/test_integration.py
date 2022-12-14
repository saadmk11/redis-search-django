import datetime

import pytest
from redis_om import NotFoundError

from redis_search_django.documents import JsonDocument

from .helpers import is_redis_running
from .models import Category, Product, Tag, Vendor


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_object_create(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])

    category = Category.objects.create(name="test")
    document_obj = DocumentClass.get(pk=category.pk)

    assert int(document_obj.pk) == category.pk
    assert document_obj.name == category.name


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_object_update(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])

    category = Category.objects.create(name="test")
    document_obj = DocumentClass.get(pk=category.pk)

    assert document_obj.name == "test"

    category.name = "test2"
    category.save()

    document_obj = DocumentClass.get(pk=category.pk)

    assert document_obj.name == "test2"


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_object_delete(document_class):
    DocumentClass = document_class(JsonDocument, Category, ["name"])

    category = Category.objects.create(name="test")
    document_obj = DocumentClass.get(pk=category.pk)

    assert int(document_obj.pk) == category.pk

    category.delete()

    with pytest.raises(NotFoundError):
        DocumentClass.get(pk=category.pk)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_related_object_add(nested_document_class):
    ProductDocumentCalss = nested_document_class[0]
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(
        name="Test",
        price=10.0,
        vendor=vendor,
    )

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert int(document_obj.pk) == product.pk
    assert document_obj.name == product.name
    assert document_obj.vendor.name == vendor.name
    assert document_obj.category is None
    assert document_obj.tags == []

    category = Category.objects.create(name="test")
    product.category = category
    product.save()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.category.name == category.name
    assert int(document_obj.category.pk) == category.pk
    assert document_obj.tags == []

    tag = Tag.objects.create(name="test")
    tag2 = Tag.objects.create(name="test2")
    product.tags.set([tag, tag2])

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert len(document_obj.tags) == 2
    assert int(document_obj.tags[0].pk) == tag.pk
    assert int(document_obj.tags[1].pk) == tag2.pk


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_related_object_update(nested_document_class):
    ProductDocumentCalss = nested_document_class[0]
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(
        name="Test",
        price=10.0,
        vendor=vendor,
    )

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.vendor.name == "test"

    vendor.name = "test2"
    vendor.save()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.vendor.name == "test2"

    category = Category.objects.create(name="test")
    product.category = category
    product.save()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.category.name == "test"

    category.name = "test2"
    category.save()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.category.name == "test2"

    tag = Tag.objects.create(name="test")
    tag2 = Tag.objects.create(name="test2")
    product.tags.set([tag, tag2])

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.tags[0].name == "test"
    assert document_obj.tags[1].name == "test2"

    tag.name = "test3"
    tag.save()
    tag2.name = "test4"
    tag2.save()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.tags[0].name == "test3"
    assert document_obj.tags[1].name == "test4"


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db
def test_related_object_delete(nested_document_class):
    ProductDocumentCalss = nested_document_class[0]
    vendor = Vendor.objects.create(
        name="test", establishment_date=datetime.date.today()
    )
    product = Product.objects.create(
        name="Test",
        price=10.0,
        vendor=vendor,
    )

    category = Category.objects.create(name="test")
    product.category = category
    product.save()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert int(document_obj.category.pk) == category.pk

    category.delete()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert document_obj.category is None

    tag = Tag.objects.create(name="test")
    tag2 = Tag.objects.create(name="test2")
    product.tags.set([tag, tag2])

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert int(document_obj.tags[0].pk) == tag.pk
    assert int(document_obj.tags[1].pk) == tag2.pk

    tag.delete()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert len(document_obj.tags) == 1
    assert int(document_obj.tags[0].pk) == tag2.pk

    tag2.delete()

    document_obj = ProductDocumentCalss.get(pk=product.pk)

    assert len(document_obj.tags) == 0

    vendor.delete()

    with pytest.raises(NotFoundError):
        ProductDocumentCalss.get(pk=product.pk)
