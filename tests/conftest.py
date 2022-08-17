import datetime
import uuid
from typing import List, Optional

import pytest

from redis_search_django.documents import EmbeddedJsonDocument, JsonDocument
from tests.models import Category, Product, Tag, Vendor


@pytest.fixture
def document_class():
    def build_document_class(
        document_type,
        model_class,
        model_fields,
        enable_auto_index=True,
    ):
        class DocumentClass(document_type):
            class Meta:
                model_key_prefix = (
                    f"{model_class.__name__.lower()}-{uuid.uuid4().hex[:5]}"
                )
                global_key_prefix = "test_redis_search"

            class Django:
                model = model_class
                fields = model_fields
                auto_index = enable_auto_index

        return DocumentClass

    return build_document_class


@pytest.fixture
def nested_document_class(document_class):
    CategoryEmbeddedJsonDocument = document_class(
        EmbeddedJsonDocument, Category, ["name"]
    )
    TagEmbeddedJsonDocument = document_class(EmbeddedJsonDocument, Tag, ["name"])
    VendorEmbeddedJsonDocument = document_class(
        EmbeddedJsonDocument, Vendor, ["name", "establishment_date"]
    )

    class ProductJsonDocument(JsonDocument):
        # OnetoOneField with null=True
        vendor: VendorEmbeddedJsonDocument
        # ForeignKey field
        category: Optional[CategoryEmbeddedJsonDocument]
        # ManyToManyField
        tags: List[TagEmbeddedJsonDocument]

        class Meta:
            model_key_prefix = f"{Product.__name__.lower()}-{uuid.uuid4().hex[:5]}"
            global_key_prefix = "test_redis_search"

        class Django:
            model = Product
            fields = ["name", "description", "price", "created_at"]
            select_related_fields = ["vendor", "category"]
            prefetch_related_fields = ["tags"]
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

    return ProductJsonDocument, (
        CategoryEmbeddedJsonDocument,
        TagEmbeddedJsonDocument,
        VendorEmbeddedJsonDocument,
    )


@pytest.fixture
def product_obj():
    return Product.objects.create(
        name="Test",
        price=10.0,
        vendor=Vendor.objects.create(
            name="Test", establishment_date=datetime.date.today()
        ),
    )


@pytest.fixture
def tag_obj():
    return Tag.objects.create(name="Test")


@pytest.fixture
def category_obj():
    return Category.objects.create(name="Test")


@pytest.fixture
def get_product_with_tag(product_obj, tag_obj):
    product_obj.tags.add(tag_obj)
    return product_obj, tag_obj
