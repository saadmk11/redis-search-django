from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Q as DjangoQ

from ..enums import Lookup, QConnector
from ..exceptions import NotSupportedError
from ..types import LookupValue

REJECTED_LOOKUPS = {
    "contains",
    "icontains",
    "endswith",
    "iendswith",
    "iexact",
    "year",
    "month",
    "day",
    "week",
    "iso_year",
    "week_day",
    "quarter",
    "hour",
    "minute",
    "second",
    "regex",
    "iregex",
}


@dataclass
class Q:
    """Boolean tree of lookups. Use redis_search_django.Q, not django.db.models.Q."""

    connector: QConnector = QConnector.AND
    negated: bool = False
    children: list[Q | tuple[str, LookupValue]] = field(default_factory=list)

    def __init__(self, *args: Q | DjangoQ, **lookups: LookupValue) -> None:
        if args and isinstance(args[0], DjangoQ):
            raise TypeError("use redis_search_django.Q, not django.db.models.Q")
        self.connector = QConnector.AND
        self.negated = False
        self.children = []
        for arg in args:
            if isinstance(arg, DjangoQ):
                raise TypeError("use redis_search_django.Q, not django.db.models.Q")
            self.children.append(arg)
        for key, value in lookups.items():
            self.children.append((key, value))

    def _combine(self, other: Q | DjangoQ, connector: QConnector) -> Q:
        if isinstance(other, DjangoQ):
            raise TypeError("use redis_search_django.Q, not django.db.models.Q")
        combined = Q()
        combined.connector = connector
        combined.children = [self, other]
        return combined

    def __and__(self, other: Q) -> Q:
        return self._combine(other, QConnector.AND)

    def __or__(self, other: Q) -> Q:
        return self._combine(other, QConnector.OR)

    def __invert__(self) -> Q:
        clone = Q()
        clone.connector = self.connector
        clone.negated = not self.negated
        clone.children = list(self.children)
        return clone

    def add(
        self, child: Q | tuple[str, LookupValue], connector: QConnector = QConnector.AND
    ) -> None:
        if self.children and self.connector != connector:
            wrapped = Q()
            wrapped.connector = self.connector
            wrapped.children = list(self.children)
            self.children = [wrapped, child]
            self.connector = connector
        else:
            self.connector = connector
            self.children.append(child)


def split_lookup(key: str) -> tuple[str, Lookup]:
    """Return (field_path, lookup) for a filter kwarg."""
    parts = key.split("__")
    if len(parts) >= 2:
        try:
            return "__".join(parts[:-1]), Lookup(parts[-1])
        except ValueError:
            if parts[-1] in REJECTED_LOOKUPS:
                raise NotSupportedError(
                    f"Lookup {parts[-1]!r} is not supported."
                ) from None
    return key, Lookup.EXACT
