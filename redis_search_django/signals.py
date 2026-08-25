from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ParamSpec

from django.core.exceptions import FieldDoesNotExist
from django.db import models, transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import Signal

from .actions import apply_index_action, document_label, iter_save_payloads
from .conf import setting_bool, setting_str
from .documents import Document
from .enums import M2M_REINDEX_ACTIONS, IndexAction, SignalErrorMode
from .fields import Nested
from .indexer import Indexer
from .registry import DocumentRegistry, document_registry
from .types import IndexActionPayload, PrimaryKey

_P = ParamSpec("_P")

logger = logging.getLogger("redis_search_django")


class BaseSignalProcessor:
    """Connect targeted signals and dispatch JSON-serializable index actions.

    Override :meth:`dispatch` to enqueue work on Celery, django-q, RQ, or any
    other broker. The default implementation runs :func:`apply_index_action`
    in-process. The package does not depend on a task queue.
    """

    def __init__(self, registry: DocumentRegistry | None = None) -> None:
        self.registry = registry or document_registry
        self._connections: list[
            tuple[Signal, Callable[..., None], type[models.Model], str]
        ] = []

    def setup(self, registry: DocumentRegistry | None = None) -> None:
        if registry is not None:
            self.registry = registry
        if not setting_bool("AUTO_INDEX"):
            return
        for document_cls in self.registry.primary_documents():
            self.connect_document(document_cls)

    def teardown(self) -> None:
        for signal, handler, sender, uid in self._connections:
            signal.disconnect(handler, sender=sender, dispatch_uid=uid)
        self._connections.clear()

    def dispatch(self, action: str, payload: IndexActionPayload) -> None:
        """Run or enqueue one index action.

        ``payload`` contains only document labels and primary keys. Override
        this method to send ``(action, payload)`` to a worker that calls
        :func:`redis_search_django.apply_index_action`.
        """
        apply_index_action(action, payload)

    def connect_document(self, document_cls: type[Document]) -> None:
        if document_cls._meta.auto_index is False:
            return
        model = document_cls._meta.model
        if model is None:
            return
        self._connect(post_save, self.handle_save, model, document_cls, "post_save")
        self._connect(
            pre_delete, self.handle_pre_delete, model, document_cls, "pre_delete"
        )
        self._connect(
            post_delete, self.handle_delete, model, document_cls, "post_delete"
        )
        for related_model in document_cls._meta.related_map:
            self._connect(
                post_save, self.handle_save, related_model, document_cls, "post_save"
            )
            self._connect(
                pre_delete,
                self.handle_pre_delete,
                related_model,
                document_cls,
                "pre_delete",
            )
            self._connect(
                post_delete,
                self.handle_delete,
                related_model,
                document_cls,
                "post_delete",
            )
        for through in _m2m_through_models(document_cls):
            self._connect(m2m_changed, self.handle_m2m, through, document_cls, "m2m")

    def _connect(
        self,
        signal: Signal,
        handler: Callable[..., None],
        sender: type[models.Model],
        document_cls: type[Document],
        signal_name: str,
    ) -> None:
        uid = (
            f"rsd.{document_cls.__module__}.{document_cls.__name__}."
            f"{sender._meta.label}.{signal_name}"
        )
        if any(item[3] == uid for item in self._connections):
            return
        signal.connect(handler, sender=sender, dispatch_uid=uid)
        self._connections.append((signal, handler, sender, uid))

    def handle_save(
        self,
        sender: type[models.Model],
        instance: models.Model,
        **kwargs: object,
    ) -> None:
        for action, payload in iter_save_payloads(instance):
            self._schedule(self.dispatch, action, payload)

    def handle_delete(
        self,
        sender: type[models.Model],
        instance: models.Model,
        **kwargs: object,
    ) -> None:
        pk: PrimaryKey = instance.pk
        for document_cls in self.registry.get_for_model(instance.__class__):
            if document_cls._meta.auto_index:
                self._schedule(
                    self.dispatch,
                    IndexAction.DELETE,
                    {"document": document_label(document_cls), "pk": pk},
                )

    def handle_pre_delete(
        self,
        sender: type[models.Model],
        instance: models.Model,
        **kwargs: object,
    ) -> None:
        # Related row may be gone after commit. Capture parent pks now and
        # upsert those parents (CASCADE parents are skipped).
        indexer = Indexer()
        for document_cls in self.registry.get_for_related(instance.__class__):
            if not document_cls._meta.auto_index:
                continue
            if indexer._cascade_deletes_parent(document_cls, instance):
                continue
            parents = document_cls.get_instances_from_related(instance)
            if parents is None:
                continue
            if isinstance(parents, models.Model):
                pks: list[PrimaryKey] = [parents.pk]
            else:
                pks = [parent.pk for parent in parents]
            label = document_label(document_cls)
            for pk in pks:
                self._schedule(
                    self.dispatch,
                    IndexAction.UPSERT,
                    {"document": label, "pk": pk},
                )

    def handle_m2m(
        self,
        sender: type[models.Model],
        instance: models.Model,
        action: str,
        **kwargs: object,
    ) -> None:
        if action in M2M_REINDEX_ACTIONS:
            for index_action, payload in iter_save_payloads(instance):
                self._schedule(self.dispatch, index_action, payload)

    def _schedule(
        self, fn: Callable[_P, None], *args: _P.args, **kwargs: _P.kwargs
    ) -> None:
        def run() -> None:
            try:
                fn(*args, **kwargs)
            except Exception:
                if setting_str("SIGNAL_ERRORS") == SignalErrorMode.LOG:
                    logger.exception("redis-search-django signal handler failed")
                    return
                raise

        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(run)
        else:
            run()


class RealtimeSignalProcessor(BaseSignalProcessor):
    """In-process processor. ``dispatch`` calls :func:`apply_index_action`."""


def _m2m_through_models(document_cls: type[Document]) -> list[type[models.Model]]:
    model = document_cls._meta.model
    if model is None or not document_cls._meta.related_map:
        return []
    throughs: list[type[models.Model]] = []
    for field in document_cls._meta.fields.values():
        if not isinstance(field, Nested):
            continue
        related_model = field.target._meta.model
        if related_model is None or related_model not in document_cls._meta.related_map:
            continue
        source = (field.model_attr or field.name or "").split(".", 1)[0]
        try:
            django_field = model._meta.get_field(source)
        except FieldDoesNotExist:
            continue
        if isinstance(django_field, models.ManyToManyField):
            through = django_field.remote_field.through
            assert through is not None
            throughs.append(through)
    return throughs
