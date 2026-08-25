from __future__ import annotations

from collections.abc import Iterable

from django.db import models

from .documents import Document
from .exceptions import ConfigurationError
from .fields import Field, Nested, Object
from .types import DocumentPayload, HashMapping, IndexValue
from .versioning import STAMP


class Serializer:
    """Turn a Django model instance into a Redis JSON/Hash payload."""

    def to_document(
        self,
        document_cls: type[Document],
        instance: models.Model,
        *,
        exclude: models.Model | None = None,
        storage: str | None = None,
    ) -> DocumentPayload:
        storage = storage or document_cls._meta.storage
        self._assert_single_pk(instance)
        payload: DocumentPayload = {"pk": str(instance.pk)}
        for field in document_cls._meta.fields.values():
            value = self._field_value(
                document_cls, instance, field, exclude=exclude, storage=storage
            )
            if value is None:
                continue
            payload[field.name or ""] = value
        replacement = document_cls.prepare(instance)
        if replacement is not None:
            return replacement
        return payload

    def _field_value(
        self,
        document_cls: type[Document],
        instance: models.Model,
        field: Field,
        *,
        exclude: models.Model | None,
        storage: str,
    ) -> IndexValue:
        if isinstance(field, Object):
            raw = field.prepare(instance, document_cls)
            if raw is None or raw == exclude:
                return None
            if not isinstance(raw, models.Model):
                raise TypeError(
                    f"{document_cls.__name__}.{field.name} Object field "
                    f"expected a model instance, got {type(raw).__name__}."
                )
            return self.to_document(
                field.target,
                raw,
                exclude=exclude,
                storage=storage,
            )
        if isinstance(field, Nested):
            raw = field.prepare(instance, document_cls)
            if raw is None:
                return []
            items = raw.all() if hasattr(raw, "all") else raw
            if not isinstance(items, Iterable):
                raise TypeError(
                    f"{document_cls.__name__}.{field.name} Nested field "
                    f"expected an iterable of models, got {type(items).__name__}."
                )
            nested: list[IndexValue] = []
            for obj in items:
                if obj == exclude:
                    continue
                if not isinstance(obj, models.Model):
                    raise TypeError(
                        f"{document_cls.__name__}.{field.name} Nested field "
                        f"expected model instances, got {type(obj).__name__}."
                    )
                nested.append(
                    self.to_document(
                        field.target,
                        obj,
                        exclude=exclude,
                        storage=storage,
                    )
                )
            return nested
        raw = field.prepare(instance, document_cls)
        return field.to_index_value(raw, storage=storage)

    def flatten_hash(
        self, document_cls: type[Document], payload: DocumentPayload
    ) -> HashMapping:
        flat: HashMapping = {"pk": str(payload["pk"])}
        if STAMP in payload:
            flat[STAMP] = payload[STAMP]
        for field in document_cls._meta.fields.values():
            name = field.name or ""
            value = payload.get(name)
            if isinstance(field, Nested):
                raise ConfigurationError("Hash storage cannot serialize Nested fields.")
            if isinstance(field, Object):
                if not isinstance(value, dict):
                    continue
                for child in field.target._meta.fields.values():
                    child_name = child.name or ""
                    key = child.hash_name(field.hash_name())
                    flat[key] = value.get(child_name)
                flat[field.hash_name() + "__pk"] = value.get("pk")
                continue
            flat[field.hash_name()] = value
        return flat

    def _assert_single_pk(self, instance: models.Model) -> None:
        opts = instance._meta
        pk_fields = getattr(opts, "pk_fields", None)
        if pk_fields is not None and len(pk_fields) != 1:
            raise ConfigurationError(
                f"{opts.label} has a composite primary key; redis-search-django 1.0 "
                "requires a single atomic pk."
            )
