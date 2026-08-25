from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

from django.db import models
from django.db.models import Case, IntegerField, When

from ..conf import setting_int
from ..types import DocumentPayload, IndexValue

if TYPE_CHECKING:
    from ..documents import Document

logger = logging.getLogger("redis_search_django")


def _wrap_value(value: IndexValue | DocumentPayload) -> WrappedHitValue:
    if isinstance(value, dict):
        return SearchHit(pk=str(value.get("pk", "")), data=value)
    if isinstance(value, list):
        return [_wrap_value(item) for item in value]
    return value


@dataclass
class SearchHit:
    pk: str
    score: float | None = None
    data: DocumentPayload = field(default_factory=dict)

    def __getattr__(self, name: str) -> WrappedHitValue | None:
        try:
            return _wrap_value(self.data[name])
        except KeyError:
            return None


WrappedHitValue: TypeAlias = IndexValue | SearchHit | list["WrappedHitValue"]


@dataclass
class SearchResult:
    hits: list[SearchHit]
    total: int
    document_cls: type[Document]

    def __iter__(self) -> Iterator[SearchHit]:
        return iter(self.hits)

    def __len__(self) -> int:
        return len(self.hits)

    def __bool__(self) -> bool:
        return bool(self.hits)

    def to_queryset(self) -> models.QuerySet[models.Model]:
        model = self.document_cls._meta.model
        assert model is not None
        manager = model._default_manager
        if not self.hits:
            return manager.none()
        pks = [hit.pk for hit in self.hits]
        warn_at = setting_int("TO_QUERYSET_WARN")
        max_at = setting_int("TO_QUERYSET_MAX")
        if warn_at and len(pks) > warn_at:
            logger.warning("to_queryset() loading %s primary keys", len(pks))
        if max_at and len(pks) > max_at:
            raise ValueError(
                f"to_queryset() refused {len(pks)} hits "
                f"(REDIS_SEARCH['TO_QUERYSET_MAX']={max_at}). Slice first."
            )
        ordered: models.QuerySet[models.Model] = manager.filter(pk__in=pks).order_by(
            Case(
                *[When(pk=pk, then=position) for position, pk in enumerate(pks)],
                output_field=IntegerField(),
            )
        )
        return ordered
