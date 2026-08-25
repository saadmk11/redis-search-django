from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest

from redis_search_django import fields
from redis_search_django.client import (
    reset_async_connection_cache,
    reset_connection_cache,
)
from redis_search_django.documents import Document
from redis_search_django.registry import document_registry

from .models import Category, Product, Tag, Vendor


@pytest.fixture(autouse=True)
def _fast_reindex(monkeypatch):
    monkeypatch.setattr("redis_search_django.index.SETTLE_SECONDS", 0.0)
    monkeypatch.setattr("redis_search_django.targets.CACHE_TTL", 0.0)


@pytest.fixture(autouse=True)
def _clean_registry():
    document_registry.clear()
    yield
    document_registry.clear()


@pytest.fixture(autouse=True)
def _close_sync_redis():
    yield
    reset_connection_cache()


@pytest.fixture(autouse=True)
async def _close_async_redis():
    """Close cached async clients after other fixtures (index drop, etc.)."""
    yield
    await reset_async_connection_cache()


def make_document(
    name: str,
    model: type,
    model_fields: list[str] | None = None,
    *,
    embedded: bool = False,
    auto_index: bool = True,
    extra_attrs: dict[str, Any] | None = None,
    **django_opts: Any,
) -> type[Document]:
    uid = uuid.uuid4().hex[:8]
    django = type(
        "Django",
        (),
        {
            "model": model,
            "fields": model_fields or [],
            "embedded": embedded,
            "auto_index": auto_index,
            **django_opts,
        },
    )
    extra = dict(extra_attrs or {})
    extra_index = extra.pop("Index", None)
    attrs: dict[str, Any] = {"Django": django, **extra}
    if not embedded:
        index_ns: dict[str, Any] = {
            "name": f"idx:test.{model._meta.model_name}.{name.lower()}.{uid}",
            "prefix": f"rsd:test.{model._meta.model_name}.{name.lower()}.{uid}:",
        }
        if extra_index is not None:
            for key, value in vars(extra_index).items():
                if key.startswith("_") or key in index_ns:
                    continue
                index_ns[key] = value
        attrs["Index"] = type("Index", (), index_ns)
    return type(name, (Document,), attrs)


@pytest.fixture
def document_class():
    return make_document


@pytest.fixture
def nested_document_class():
    category_doc = make_document("CategoryDocument", Category, ["name"], embedded=True)
    tag_doc = make_document("TagDocument", Tag, ["name"], embedded=True)
    vendor_doc = make_document(
        "VendorDocument", Vendor, ["name", "establishment_date"], embedded=True
    )
    product_doc = make_document(
        "ProductDocument",
        Product,
        ["name", "description", "price", "created_at"],
        extra_attrs={
            "vendor": fields.Object(vendor_doc),
            "category": fields.Object(category_doc, required=False),
            "tags": fields.Nested(tag_doc),
        },
        select_related_fields=["vendor", "category"],
        prefetch_related_fields=["tags"],
    )
    return product_doc, (category_doc, tag_doc, vendor_doc)


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
def product_with_tag(product_obj, tag_obj):
    product_obj.tags.add(tag_obj)
    return product_obj, tag_obj
