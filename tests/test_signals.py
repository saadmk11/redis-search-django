from unittest import mock

import pytest

from tests.models import Category


@pytest.mark.django_db
@mock.patch("redis_search_django.signals.document_registry")
def test_add_to_redis_index(document_registry):
    category = Category.objects.create(name="Test")
    document_registry.update_document.assert_called_once_with(category, create=True)

    category.name = "Test2"
    category.save()

    document_registry.update_related_documents.assert_called_once_with(
        category, exclude=None
    )


@pytest.mark.django_db
@mock.patch("redis_search_django.signals.document_registry")
def test_remove_from_redis_index(document_registry):
    category = Category.objects.create(name="Test")
    category.delete()
    document_registry.remove_document.assert_called_once_with(category)


@pytest.mark.django_db
@mock.patch("redis_search_django.signals.document_registry")
def test_remove_related_model_data_from_redis_index(document_registry):
    category = Category.objects.create(name="Test")
    category.delete()
    document_registry.update_related_documents.assert_called_once_with(
        category, exclude=category
    )


@pytest.mark.django_db
@mock.patch("redis_search_django.signals.document_registry")
def test_update_redis_index_on_m2m_changed_add(document_registry, product_obj, tag_obj):
    product_obj.tags.add(tag_obj)
    document_registry.update_document.assert_called_once_with(product_obj, create=True)
    document_registry.update_related_documents.assert_called_once_with(
        product_obj, exclude=None
    )


@pytest.mark.django_db
@mock.patch("redis_search_django.signals.document_registry")
def test_update_redis_index_on_m2m_changed_remove(
    document_registry, get_product_with_tag
):
    product, tag = get_product_with_tag
    product.tags.clear()
    document_registry.update_related_documents.assert_called_with(product, exclude=None)
