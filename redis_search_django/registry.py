from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from .documents import Document


class DocumentRegistry:
    """Maps Django models to Document classes and related-model reverse lookups."""

    def __init__(self) -> None:
        self.documents: list[type[Document]] = []
        self.django_model_map: dict[type[models.Model], set[type[Document]]] = (
            defaultdict(set)
        )
        self.related_django_model_map: dict[type[models.Model], set[type[Document]]] = (
            defaultdict(set)
        )
        self._aliases: dict[str, type[Document]] = {}
        self._prefixes: dict[str, type[Document]] = {}
        self._labels: dict[str, type[Document]] = {}

    def label_for(self, document_cls: type[Document]) -> str:
        """Return ``{app_label}.{DocumentClassName}``."""
        return f"{document_cls._meta.app_label}.{document_cls.__name__}"

    def get_by_label(self, label: str) -> type[Document]:
        try:
            return self._labels[label]
        except KeyError:
            raise LookupError(f"No Document registered with label {label!r}.") from None

    def register(self, document_cls: type[Document]) -> None:
        meta = document_cls._meta
        if meta.abstract or meta.embedded:
            return

        alias = meta.index_alias
        prefix = meta.key_prefix
        if alias in self._aliases:
            raise document_cls._config_error(
                f"Index alias {alias!r} is already used by "
                f"{self._aliases[alias].__name__}."
            )
        if prefix in self._prefixes:
            raise document_cls._config_error(
                f"Key prefix {prefix!r} is already used by "
                f"{self._prefixes[prefix].__name__}."
            )

        label = self.label_for(document_cls)
        if label in self._labels:
            raise document_cls._config_error(
                f"Document label {label!r} is already used by "
                f"{self._labels[label].__module__}.{self._labels[label].__name__}."
            )

        self._aliases[alias] = document_cls
        self._prefixes[prefix] = document_cls
        self._labels[label] = document_cls
        self.documents.append(document_cls)
        assert meta.model is not None
        self.django_model_map[meta.model].add(document_cls)
        for related_model in meta.related_map:
            self.related_django_model_map[related_model].add(document_cls)

    def get_for_model(self, model: type[models.Model]) -> set[type[Document]]:
        concrete = getattr(getattr(model, "_meta", None), "concrete_model", model)
        return set(self.django_model_map.get(model, set())) | set(
            self.django_model_map.get(concrete, set())
        )

    def get_for_related(self, model: type[models.Model]) -> set[type[Document]]:
        concrete = getattr(getattr(model, "_meta", None), "concrete_model", model)
        return set(self.related_django_model_map.get(model, set())) | set(
            self.related_django_model_map.get(concrete, set())
        )

    def primary_documents(self) -> list[type[Document]]:
        return list(self.documents)

    def clear(self) -> None:
        self.documents.clear()
        self.django_model_map.clear()
        self.related_django_model_map.clear()
        self._aliases.clear()
        self._prefixes.clear()
        self._labels.clear()


document_registry = DocumentRegistry()
