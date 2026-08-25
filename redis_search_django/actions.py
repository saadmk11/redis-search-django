from __future__ import annotations

from collections.abc import Iterator

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from .documents import Document
from .enums import IndexAction
from .exceptions import UnknownIndexAction
from .indexer import Indexer
from .registry import document_registry
from .types import IndexActionPayload, PrimaryKey


def document_label(document_cls: type[Document]) -> str:
    """Stable label for a Document class, e.g. ``shop.ProductDocument``."""
    return document_registry.label_for(document_cls)


def iter_save_payloads(
    instance: models.Model,
) -> Iterator[tuple[str, IndexActionPayload]]:
    """Yield ``(action, payload)`` pairs for a saved or M2M-changed instance."""
    model = instance.__class__
    pk: PrimaryKey = instance.pk
    related_label = instance._meta.label
    for document_cls in document_registry.get_for_model(model):
        if document_cls._meta.auto_index:
            yield (
                IndexAction.UPSERT,
                {"document": document_label(document_cls), "pk": pk},
            )
    for document_cls in document_registry.get_for_related(model):
        if document_cls._meta.auto_index:
            yield (
                IndexAction.REINDEX_RELATED,
                {
                    "document": document_label(document_cls),
                    "related": related_label,
                    "pk": pk,
                },
            )


def _coerce_action(action: str | IndexAction) -> IndexAction:
    try:
        return IndexAction(action)
    except ValueError:
        raise UnknownIndexAction(
            f"Unknown index action {action!r}. "
            f"Expected one of {[item.value for item in IndexAction]}."
        ) from None


def apply_index_action(action: str | IndexAction, payload: IndexActionPayload) -> None:
    """Run one index write. Safe to call from Celery, django-q, RQ, or inline.

    ``payload`` must be JSON-serializable (document label + primary keys).
    """
    action = _coerce_action(action)
    document_cls = document_registry.get_by_label(payload["document"])
    indexer = Indexer()
    if action is IndexAction.DELETE:
        indexer.delete(document_cls, payload["pk"])
        return
    if action is IndexAction.UPSERT:
        try:
            instance = document_cls.instance_queryset().get(pk=payload["pk"])
        except ObjectDoesNotExist:
            indexer.delete(document_cls, payload["pk"])
            return
        indexer.upsert(document_cls, instance)
        return
    related_model = apps.get_model(payload["related"])
    try:
        related = related_model._default_manager.get(pk=payload["pk"])
    except ObjectDoesNotExist:
        return
    indexer.reindex_related(
        document_cls,
        related,
        deleting=bool(payload.get("deleting", False)),
    )


async def aapply_index_action(
    action: str | IndexAction, payload: IndexActionPayload
) -> None:
    """Async counterpart of :func:`apply_index_action`.

    Loads Django rows with the async ORM and writes Redis through
    ``redis.asyncio``. Safe to call from an async worker.
    """
    action = _coerce_action(action)
    document_cls = document_registry.get_by_label(payload["document"])
    indexer = Indexer()
    if action is IndexAction.DELETE:
        await indexer.adelete(document_cls, payload["pk"])
        return
    if action is IndexAction.UPSERT:
        try:
            instance = await document_cls.instance_queryset().aget(pk=payload["pk"])
        except ObjectDoesNotExist:
            await indexer.adelete(document_cls, payload["pk"])
            return
        await indexer.aupsert(document_cls, instance)
        return
    related_model = apps.get_model(payload["related"])
    try:
        related = await related_model._default_manager.aget(pk=payload["pk"])
    except ObjectDoesNotExist:
        return
    await indexer.areindex_related(
        document_cls,
        related,
        deleting=bool(payload.get("deleting", False)),
    )
