from typing import List, Optional

from django.db import models
from redis_om import Field

from redis_search_django.documents import EmbeddedJsonDocument, JsonDocument

from .models import Category, Product, Tag, Vendor


class CategoryDocument(EmbeddedJsonDocument):
    # name: str = Field(index=True, full_text_search=True)
    # slug: str = Field(index=True)
    custom_field: str = Field(index=True, full_text_search=True)

    class Django:
        model = Category
        fields = ["name", "slug"]

    @classmethod
    def prepare_custom_field(cls, obj):
        return "CUSTOM FIELD VALUE"


class TagDocument(EmbeddedJsonDocument):
    # name: str = Field(index=True)

    class Django:
        model = Tag
        fields = ["name"]


class VendorDocument(EmbeddedJsonDocument):
    # logo: str = Field(index=True)
    # identifier: str = Field(index=True)
    # name: str = Field(index=True, full_text_search=True, sortable=True)
    # email: str = Field(index=True, full_text_search=True, sortable=True)
    # establishment_date: datetime.date = Field(
    #     index=True, full_text_search=True, sortable=True
    # )

    class Django:
        model = Vendor
        fields = ["logo", "identifier", "name", "email", "establishment_date"]

    @classmethod
    def prepare_logo(cls, obj):
        return obj.logo.url if obj.logo else ""


class ProductDocument(JsonDocument):
    # Fields can be defined manually or
    # `Django.fields` can be used to define the Django Model fields automatically.

    # name: str = Field(index=True, full_text_search=True, sortable=True)
    # description: str = Field(index=True, full_text_search=True)
    # price: Decimal = Field(index=True, sortable=True)
    # created_at: Optional[datetime.date] = Field(index=True)
    # quantity: Optional[int] = Field(index=True, sortable=True)
    # available: int = Field(index=True, sortable=True)

    # OnetoOneField
    vendor: VendorDocument
    # ForeignKey field
    category: Optional[CategoryDocument]
    # ManyToManyField
    tags: List[TagDocument]

    # class Meta:
    #     model_key_prefix = "product"
    #     global_key_prefix = "redis_search"

    class Django:
        model = Product
        # Django Model fields can be added to the Document automatically.
        fields = ["name", "description", "price", "created_at", "quantity", "available"]
        prefetch_related_fields = ["tags"]
        select_related_fields = ["vendor", "category"]
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

    @classmethod
    def get_queryset(cls) -> models.QuerySet:
        return super().get_queryset().filter(available=True)

    @classmethod
    def prepare_name(cls, obj):
        return obj.name.upper()
