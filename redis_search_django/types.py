"""Shared type aliases for Redis payloads, settings, and field values."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import (
    TYPE_CHECKING,
    Protocol,
    TypeAlias,
    TypedDict,
    TypeGuard,
    runtime_checkable,
)
from uuid import UUID

from django.db import models
from typing_extensions import NotRequired

if TYPE_CHECKING:
    from .registry import DocumentRegistry

from .redis import AsyncRedis, Redis

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# Values we write into JSON or Hash documents.
IndexScalar: TypeAlias = str | int | float | bool | None
IndexValue: TypeAlias = (
    IndexScalar | bytes | list[float] | list["IndexValue"] | dict[str, "IndexValue"]
)
DocumentPayload: TypeAlias = dict[str, IndexValue]
HashMapping: TypeAlias = dict[str, IndexValue]

# Django / prepare_* values before they become IndexValue.
FieldInput: TypeAlias = (
    str
    | int
    | float
    | bool
    | bytes
    | Decimal
    | UUID
    | datetime
    | date
    | time
    | Sequence[float]
    | None
)

# ``filter(name=..., name__in=[...])`` values.
LookupValue: TypeAlias = FieldInput | Sequence[FieldInput]

PrimaryKey: TypeAlias = str | int | UUID
ModelType: TypeAlias = type[models.Model]


class IndexActionPayload(TypedDict):
    """JSON-serializable signal / worker payload."""

    document: str
    pk: PrimaryKey
    related: NotRequired[str]
    deleting: NotRequired[bool]


class FacetRow(TypedDict):
    value: IndexValue
    count: int


FacetMap: TypeAlias = dict[str, list[FacetRow]]
AggregateValue: TypeAlias = str | int | float | list[str] | None
AggregateRow: TypeAlias = dict[str, AggregateValue]


class RedisSearchDoc(Protocol):
    """redis-py ``Document`` row from ``FT.SEARCH``."""

    __dict__: dict[str, IndexValue]


class RedisSearchResult(Protocol):
    docs: Sequence[RedisSearchDoc]
    total: int


class RedisAggregateResult(Protocol):
    rows: Sequence[Mapping[str, IndexValue] | Sequence[IndexValue]]


@runtime_checkable
class Named(Protocol):
    """FileField / FieldFile expose ``name`` for TAG storage."""

    name: str


@runtime_checkable
class ConnectionFactory(Protocol):
    def __call__(self) -> Redis: ...


@runtime_checkable
class AsyncConnectionFactory(Protocol):
    def __call__(self) -> AsyncRedis: ...


class SignalProcessor(Protocol):
    def setup(self, registry: DocumentRegistry | None = None) -> None: ...

    def teardown(self) -> None: ...


SettingValue: TypeAlias = (
    str | int | float | bool | ConnectionFactory | AsyncConnectionFactory | None
)

ToIndexRaw: TypeAlias = FieldInput | Named | bytes | bytearray | memoryview

IndexMetaValue: TypeAlias = str | bool | int | list[str] | dict[str, str] | None
IndexMeta: TypeAlias = dict[str, IndexMetaValue]
RedisInfo: TypeAlias = dict[str, str | int | float | list[str]]

_FIELD_INPUT_TYPES = (
    str,
    int,
    float,
    bool,
    bytes,
    Decimal,
    UUID,
    datetime,
    date,
    time,
)


def is_primary_key(value: object) -> TypeGuard[PrimaryKey]:
    return isinstance(value, (str, int, UUID)) and not isinstance(value, bool)


def is_field_input(value: object) -> TypeGuard[FieldInput]:
    if value is None:
        return True
    if isinstance(value, (str, bytes, bytearray, memoryview)):
        return isinstance(value, (str, bytes))
    if isinstance(value, Sequence):
        return True
    return isinstance(value, _FIELD_INPUT_TYPES)


def is_index_value(value: object) -> TypeGuard[IndexValue]:
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return True
    if isinstance(value, dict):
        return True
    if isinstance(value, list):
        return True
    return False


def is_setting_value(value: object) -> TypeGuard[SettingValue]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    return callable(value)


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def as_document_payload(value: object) -> DocumentPayload | None:
    if not isinstance(value, dict):
        return None
    payload: DocumentPayload = {}
    for key, item in value.items():
        if isinstance(key, str) and is_index_value(item):
            payload[key] = item
    return payload


def as_hash_mapping(value: object) -> HashMapping:
    payload = as_document_payload(value)
    return payload if payload is not None else {}


def is_prepare_hook(value: object) -> TypeGuard[Callable[[models.Model], object]]:
    return callable(value)
