from typing import Any, Type

from django.conf import settings
from django.db import models
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete

from .registry import document_registry


def add_document_to_redis_index(
    sender: Type[models.Model], instance: models.Model, created: bool, **kwargs: Any
) -> None:
    """Signal handler for populating the redis index."""
    document_registry.update_document(instance, create=True)

    if not created:
        document_registry.update_related_documents(instance, exclude=None)


def remove_document_from_redis_index(
    sender: Type[models.Model], instance: models.Model, **kwargs: Any
) -> None:
    """Signal handler for removing data from redis index."""
    document_registry.remove_document(instance)


def remove_related_documents_from_redis_index(
    sender: Type[models.Model], instance: models.Model, **kwargs: Any
) -> None:
    """Signal handler for removing related model data from redis index."""
    document_registry.update_related_documents(instance, exclude=instance)


def update_redis_index_on_m2m_changed(
    sender: Type[models.Model], instance: models.Model, action: str, **kwargs: Any
) -> None:
    """Signal handler for updating redis index on m2m changed."""
    if action in ("post_add", "post_remove", "post_clear"):
        document_registry.update_document(instance, create=True)
        document_registry.update_related_documents(instance, exclude=None)
    elif action in ("pre_remove", "pre_clear"):
        document_registry.update_related_documents(instance, exclude=instance)


# Check if Auto Index is globally turned off using Django settings.
if getattr(settings, "REDIS_SEARCH_AUTO_INDEX", True):
    post_save.connect(add_document_to_redis_index)
    pre_delete.connect(remove_related_documents_from_redis_index)
    post_delete.connect(remove_document_from_redis_index)
    m2m_changed.connect(update_redis_index_on_m2m_changed)
