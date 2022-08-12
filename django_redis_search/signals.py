from typing import Any, Type

from django.conf import settings
from django.db import models
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete

from .registry import document_registry


def add_to_redis_index(
    sender: Type[models.Model], instance: models.Model, created: bool, **kwargs: Any
) -> None:
    """Signal handler for populating the redis index."""
    document_registry.update_document(sender, instance, create=True)

    if not created:
        document_registry.update_related_documents(sender, instance, exclude=None)


def remove_from_redis_index(
    sender: Type[models.Model], instance: models.Model, **kwargs: Any
) -> None:
    """Signal handler for removing data from redis index."""
    document_class = document_registry.django_model_map.get(sender)

    # Check if Auto Index is turned off for this specific Document.
    if document_class and document_class._django.auto_index:
        # Try to Delete the Document from Redis Index.
        document_class.delete(instance.pk)


def remove_related_model_data_from_redis_index(
    sender: Type[models.Model], instance: models.Model, **kwargs: Any
) -> None:
    """Signal handler for removing related model data from redis index."""
    document_registry.update_related_documents(sender, instance, exclude=instance)


def update_redis_index_on_m2m_changed(
    sender: Type[models.Model], instance: models.Model, action: str, **kwargs: Any
) -> None:
    """Signal handler for updating redis index on m2m changed."""
    model_class = instance.__class__

    if action in ("post_add", "post_remove", "post_clear"):
        document_registry.update_document(model_class, instance, create=True)
        document_registry.update_related_documents(model_class, instance, exclude=None)
    elif action in ("pre_remove", "pre_clear"):
        document_registry.update_related_documents(
            model_class, instance, exclude=instance
        )


# Check if Auto Index is globally turned off using Django settings.
if getattr(settings, "REDIS_SEARCH_AUTO_INDEX", True):
    post_save.connect(add_to_redis_index)
    pre_delete.connect(remove_related_model_data_from_redis_index)
    post_delete.connect(remove_from_redis_index)
    m2m_changed.connect(update_redis_index_on_m2m_changed)
