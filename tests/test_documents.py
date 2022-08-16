import pytest
from django.core.exceptions import ImproperlyConfigured

from redis_search_django.documents import DjangoOptions
from tests.models import Category, Product, Tag, Vendor


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
