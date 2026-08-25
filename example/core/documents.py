from __future__ import annotations

from django.db import models

from redis_search_django import fields
from redis_search_django.documents import Document

from .embeddings import DIMS, embed
from .models import Category, Product, Tag, Vendor

# Redis GEO is "lon,lat". One city per catalog category so /lab/ can show
# the stored Geo field without adding columns to Product.
CATEGORY_COORDS: dict[str, str] = {
    "Electronics": "-74.0060,40.7128",  # New York
    "Clothing": "2.3522,48.8566",  # Paris
    "Books": "-0.1276,51.5074",  # London
    "Home": "139.6917,35.6895",  # Tokyo
    "Sports": "-104.9903,39.7392",  # Denver
}


class CategoryDocument(Document):
    custom_field = fields.Text()

    class Django:
        model = Category
        fields = ["name", "slug"]
        embedded = True

    @classmethod
    def prepare_custom_field(cls, obj: Category) -> str:
        return "CUSTOM FIELD VALUE"


class TagDocument(Document):
    class Django:
        model = Tag
        fields = ["name"]
        embedded = True


class TagHashDocument(Document):
    """Standalone HASH index of Tag. Nested is illegal on HASH; this is scalars only."""

    class Django:
        model = Tag
        fields = ["name"]

    class Index:
        storage = "hash"
        name = "idx:core.tag.hash"
        prefix = "rsd:core.tag.hash:"


class VendorDocument(Document):
    class Django:
        model = Vendor
        fields = ["logo", "identifier", "name", "email", "establishment_date"]
        embedded = True

    @classmethod
    def prepare_logo(cls, obj: Vendor) -> str:
        return obj.logo.url if obj.logo else ""


class ProductDocument(Document):
    vendor = fields.Object(VendorDocument)
    category = fields.Object(CategoryDocument, required=False)
    tags = fields.Nested(TagDocument)
    sku = fields.Tag()
    department = fields.Tag(index_missing=True)
    location = fields.Geo()
    embedding = fields.Vector(
        dims=DIMS, algorithm="FLAT", distance="COSINE", embedder=embed
    )

    class Django:
        model = Product
        fields = ["name", "description", "price", "created_at", "quantity", "available"]
        prefetch_related_fields = ["tags"]
        select_related_fields = ["vendor", "category"]

    class Index:
        search_fields = ["name", "description"]
        language = "english"

    @classmethod
    def get_queryset(cls) -> models.QuerySet[Product]:
        return super().get_queryset().filter(available=True)

    @classmethod
    def should_index(cls, instance: models.Model) -> bool:
        return bool(getattr(instance, "available", True))

    @classmethod
    def prepare_name(cls, obj: Product) -> str:
        return obj.name.upper()

    @classmethod
    def prepare_sku(cls, obj: Product) -> str:
        return f"SKU-{obj.pk:04d}"

    @classmethod
    def prepare_department(cls, obj: Product) -> str | None:
        if obj.category_id is None:
            return None
        return obj.category.slug

    @classmethod
    def prepare_location(cls, obj: Product) -> str | None:
        if obj.category_id is None:
            return None
        return CATEGORY_COORDS.get(obj.category.name)

    @classmethod
    def prepare_embedding(cls, obj: Product) -> str:
        return f"{obj.name} {obj.description}"
