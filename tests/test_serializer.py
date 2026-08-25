from __future__ import annotations

import datetime

import pytest

from redis_search_django.serializer import Serializer

from .models import Category, Product, Vendor


@pytest.mark.django_db
def test_serializes_flat_fields(document_class, category_obj):
    doc = document_class("CategoryDocument", Category, ["name"])
    payload = Serializer().to_document(doc, category_obj)
    assert payload == {"pk": str(category_obj.pk), "name": "Test"}


@pytest.mark.django_db
def test_serializes_nested_relations(
    nested_document_class, product_with_tag, category_obj
):
    product, tag = product_with_tag
    product.category = category_obj
    product.save()
    product_doc, _ = nested_document_class
    payload = Serializer().to_document(product_doc, product)
    assert payload["pk"] == str(product.pk)
    assert payload["name"] == "Test"
    assert payload["vendor"]["name"] == "Test"
    assert payload["vendor"]["pk"] == str(product.vendor_id)
    assert payload["category"]["name"] == "Test"
    assert payload["tags"][0]["name"] == "Test"
    assert payload["tags"][0]["pk"] == str(tag.pk)
    assert isinstance(payload["created_at"], float)
    assert payload["price"] == 10.0


@pytest.mark.django_db
def test_exclude_omits_related(nested_document_class, product_with_tag):
    product, tag = product_with_tag
    product_doc, _ = nested_document_class
    payload = Serializer().to_document(product_doc, product, exclude=tag)
    assert payload["tags"] == []


@pytest.mark.django_db
def test_exclude_nulls_object(nested_document_class, product_obj):
    product_doc, _ = nested_document_class
    payload = Serializer().to_document(
        product_doc, product_obj, exclude=product_obj.vendor
    )
    assert "vendor" not in payload


@pytest.mark.django_db
def test_prepare_replaces_payload(category_obj):
    from redis_search_django.documents import Document

    class Custom(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.prepare"
            prefix = "rsd:test.category.prepare:"

        @classmethod
        def prepare(cls, instance):
            return {"pk": "custom", "name": "override"}

    payload = Serializer().to_document(Custom, category_obj)
    assert payload == {"pk": "custom", "name": "override"}


@pytest.mark.django_db
def test_object_and_nested_skip_non_models(category_obj):
    from redis_search_django.documents import Document
    from redis_search_django.fields import Nested, Object

    class ExtraInner(Document):
        class Django:
            model = Vendor
            fields = ["name"]
            embedded = True

    class ItemInner(Document):
        class Django:
            model = Vendor
            fields = ["name"]
            embedded = True

    class BadObject(Document):
        extra = Object(ExtraInner)

        class Django:
            model = Category
            fields = ["name"]
            related_models = {}

        class Index:
            name = "idx:test.category.badobj"
            prefix = "rsd:test.category.badobj:"

        @classmethod
        def prepare_extra(cls, instance):
            return {"not": "a model"}

    class BadNested(Document):
        items = Nested(ItemInner)

        class Django:
            model = Category
            fields = ["name"]
            related_models = {}

        class Index:
            name = "idx:test.category.badnest"
            prefix = "rsd:test.category.badnest:"

        @classmethod
        def prepare_items(cls, instance):
            return 123

    class BadNestedItems(Document):
        items = Nested(ItemInner)

        class Django:
            model = Category
            fields = ["name"]
            related_models = {}

        class Index:
            name = "idx:test.category.badnestitems"
            prefix = "rsd:test.category.badnestitems:"

        @classmethod
        def prepare_items(cls, instance):
            return ["not-a-model"]

    with pytest.raises(TypeError, match="Object field"):
        Serializer().to_document(BadObject, category_obj)
    with pytest.raises(TypeError, match="iterable of models"):
        Serializer().to_document(BadNested, category_obj)
    with pytest.raises(TypeError, match="model instances"):
        Serializer().to_document(BadNestedItems, category_obj)


@pytest.mark.django_db
def test_hash_flatten(nested_document_class, product_obj):
    product_doc, _ = nested_document_class
    serializer = Serializer()
    payload = serializer.to_document(product_doc, product_obj)
    with pytest.raises(Exception, match="Hash storage"):
        serializer.flatten_hash(product_doc, payload)


@pytest.mark.django_db
def test_date_field_on_vendor(document_class):
    vendor = Vendor.objects.create(
        name="Acme", establishment_date=datetime.date(2020, 5, 1)
    )
    doc = document_class("VendorDocument", Vendor, ["name", "establishment_date"])
    payload = Serializer().to_document(doc, vendor)
    assert (
        payload["establishment_date"]
        == datetime.datetime(2020, 5, 1, tzinfo=datetime.timezone.utc).timestamp()
    )


@pytest.mark.django_db
def test_product_without_category(nested_document_class, product_obj):
    product_doc, _ = nested_document_class
    payload = Serializer().to_document(product_doc, product_obj)
    assert "category" not in payload
    assert payload["tags"] == []


def test_hash_flatten_object_and_composite_pk(document_class):
    from redis_search_django import fields
    from redis_search_django.documents import Document
    from redis_search_django.exceptions import ConfigurationError

    vendor_doc = document_class("VendHash", Vendor, ["name"], embedded=True)

    class ProdHash(Document):
        vendor = fields.Object(vendor_doc)

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.product.hashflat"
            prefix = "rsd:test.product.hashflat:"

    serializer = Serializer()
    payload = {
        "pk": "2",
        "name": "W",
        "vendor": {"pk": "1", "name": "Acme"},
    }
    payload["_v"] = "abc"
    flat = serializer.flatten_hash(ProdHash, payload)
    assert flat["pk"] == "2"
    assert flat["_v"] == "abc"
    assert flat["name"] == "W"
    assert flat["vendor__name"] == "Acme"
    assert flat["vendor__pk"] == "1"

    class Composite:
        class _meta:
            label = "tests.Composite"
            pk_fields = ("a", "b")

    with pytest.raises(ConfigurationError, match="composite"):
        serializer._assert_single_pk(Composite())


@pytest.mark.django_db
def test_nested_prepare_none_becomes_list(nested_document_class, product_obj):
    product_doc, _ = nested_document_class

    def empty_tags(instance):
        return None

    product_doc.prepare_tags = classmethod(lambda cls, instance: None)
    payload = Serializer().to_document(product_doc, product_obj)
    assert payload["tags"] == []
