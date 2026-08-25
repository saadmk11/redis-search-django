from __future__ import annotations

import datetime
import struct
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

from django.db import models
from django.utils import timezone
from redis.commands.search.field import Field as RedisField
from redis.commands.search.field import GeoField as RedisGeoField
from redis.commands.search.field import NumericField as RedisNumericField
from redis.commands.search.field import TagField as RedisTagField
from redis.commands.search.field import TextField as RedisTextField
from redis.commands.search.field import VectorField as RedisVectorField

from .embeddings import (
    Embedder,
    EmbedFn,
    _as_numeric_list,
    as_floats,
    call_embed,
    resolve_embedder,
    resolve_source,
)
from .enums import Storage
from .exceptions import ConfigurationError
from .types import (
    FieldInput,
    IndexValue,
    Named,
    is_prepare_hook,
)

if TYPE_CHECKING:
    from .documents import Document

_UNSET = object()


class Field:
    """Base RediSearch field declared on a Document."""

    def __init__(
        self,
        *,
        model_attr: str | None = None,
        sortable: bool = False,
        no_index: bool = False,
        index_missing: bool = False,
        as_name: str | None = None,
        weight: float | None = None,
        no_stem: bool = False,
        phonetic: str | None = None,
    ) -> None:
        self.name: str | None = None
        self.model_attr = model_attr
        self.sortable = sortable
        self.no_index = no_index
        self.index_missing = index_missing
        self._as_name = as_name
        self.weight = weight
        self.no_stem = no_stem
        self.phonetic = phonetic
        self.document_cls: type[Document] | None = None
        self._prepare_hook: Callable[[models.Model], object] | object | None = _UNSET

    def bind(self, name: str, document_cls: type[Document]) -> None:
        self.name = name
        self.document_cls = document_cls
        if self.model_attr is None:
            self.model_attr = name
        self._prepare_hook = _UNSET

    def copy(self) -> Field:
        """Shallow copy so subclasses do not mutate a shared base Field."""
        clone = self.__class__.__new__(self.__class__)
        clone.__dict__.update(self.__dict__)
        clone.document_cls = None
        clone._prepare_hook = _UNSET
        return clone

    def _resolved_prepare(
        self, document_cls: type[Document]
    ) -> Callable[[models.Model], object] | None:
        hook = self._prepare_hook
        if hook is _UNSET:
            found = getattr(document_cls, f"prepare_{self.name}", None)
            hook = found if is_prepare_hook(found) else None
            self._prepare_hook = hook
        return hook  # type: ignore[return-value]

    def redis_type(self) -> str:
        raise NotImplementedError

    def json_path(self, parent_path: str = "$") -> str:
        return f"{parent_path}.{self.name}"

    def as_name(self, parent_alias: str = "") -> str:
        if self._as_name:
            return self._as_name
        if parent_alias:
            return f"{parent_alias}_{self.name}"
        return self.name or ""

    def hash_name(self, parent_alias: str = "") -> str:
        if parent_alias:
            return f"{parent_alias}__{self.name}"
        return self.name or ""

    def to_redis_field(self, *, json_path: str, alias: str) -> RedisField:
        raise NotImplementedError

    def to_index_value(self, raw: object, *, storage: str = "json") -> IndexValue:
        if raw is None or isinstance(raw, (str, int, float, bool, bytes)):
            return raw
        if isinstance(raw, (bytearray, memoryview)):
            return bytes(raw)
        if isinstance(raw, Named) and not isinstance(raw, (str, bytes)):
            return raw.name
        return str(raw)

    def prepare(self, instance: models.Model, document_cls: type[Document]) -> object:
        hook = self._resolved_prepare(document_cls)
        if hook is not None:
            return hook(instance)
        assert self.name is not None
        return _resolve_attr(instance, self.model_attr or self.name)


class Text(Field):
    def redis_type(self) -> str:
        return "TEXT"

    def to_redis_field(self, *, json_path: str, alias: str) -> RedisField:
        if self.phonetic is None:
            return RedisTextField(
                json_path,
                as_name=alias,
                sortable=self.sortable,
                no_stem=self.no_stem,
                weight=1.0 if self.weight is None else self.weight,
                index_missing=self.index_missing,
            )
        return RedisTextField(
            json_path,
            as_name=alias,
            sortable=self.sortable,
            no_stem=self.no_stem,
            weight=1.0 if self.weight is None else self.weight,
            phonetic_matcher=self.phonetic,
            index_missing=self.index_missing,
        )

    def to_index_value(self, raw: object, *, storage: str = "json") -> IndexValue:
        if raw is None:
            return None
        return str(raw)


class Tag(Field):
    def __init__(
        self,
        *,
        separator: str = ",",
        case_sensitive: bool = False,
        suffix_trie: bool = False,
        model_attr: str | None = None,
        sortable: bool = False,
        no_index: bool = False,
        index_missing: bool = False,
        as_name: str | None = None,
    ) -> None:
        super().__init__(
            model_attr=model_attr,
            sortable=sortable,
            no_index=no_index,
            index_missing=index_missing,
            as_name=as_name,
        )
        self.separator = separator
        self.case_sensitive = case_sensitive
        self.suffix_trie = suffix_trie

    def redis_type(self) -> str:
        return "TAG"

    def to_redis_field(self, *, json_path: str, alias: str) -> RedisField:
        field = RedisTagField(
            json_path,
            as_name=alias,
            separator=self.separator,
            case_sensitive=self.case_sensitive,
            sortable=self.sortable,
            index_missing=self.index_missing,
        )
        if self.suffix_trie:
            # redis-py TagField accepts withsuffixtrie on recent versions
            try:
                field.args.append("WITHSUFFIXTRIE")
            except AttributeError:
                pass
        return field

    def to_index_value(self, raw: object, *, storage: str = "json") -> IndexValue:
        if raw is None:
            return None
        if isinstance(raw, UUID):
            return str(raw)
        if isinstance(raw, Named) and not isinstance(raw, str):
            return raw.name or None
        return str(raw)


class Numeric(Field):
    def __init__(
        self,
        *,
        model_attr: str | None = None,
        sortable: bool = True,
        no_index: bool = False,
        index_missing: bool = False,
        as_name: str | None = None,
    ) -> None:
        super().__init__(
            model_attr=model_attr,
            sortable=sortable,
            no_index=no_index,
            index_missing=index_missing,
            as_name=as_name,
        )

    def redis_type(self) -> str:
        return "NUMERIC"

    def to_redis_field(self, *, json_path: str, alias: str) -> RedisField:
        kwargs: dict[str, str | bool] = {"as_name": alias, "sortable": self.sortable}
        if self.index_missing:
            kwargs["index_missing"] = True
        return RedisNumericField(json_path, **kwargs)

    def to_index_value(self, raw: object, *, storage: str = "json") -> IndexValue:
        if raw is None:
            return None
        if isinstance(raw, datetime.datetime):
            return _datetime_to_ts(raw)
        if isinstance(raw, datetime.date):
            return _date_to_ts(raw)
        if isinstance(raw, Decimal):
            return float(raw)
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, (int, float, str)):
            return raw
        return str(raw)


class Boolean(Field):
    def redis_type(self) -> str:
        return "TAG"

    def to_redis_field(self, *, json_path: str, alias: str) -> RedisField:
        return RedisTagField(
            json_path,
            as_name=alias,
            sortable=self.sortable,
            index_missing=self.index_missing,
        )

    def to_index_value(self, raw: object, *, storage: str = "json") -> IndexValue:
        if raw is None:
            return None
        flag = bool(raw)
        if storage == Storage.HASH:
            return "true" if flag else "false"
        return flag


class Geo(Field):
    def redis_type(self) -> str:
        return "GEO"

    def to_redis_field(self, *, json_path: str, alias: str) -> RedisField:
        return RedisGeoField(json_path, as_name=alias)

    def to_index_value(self, raw: object, *, storage: str = "json") -> IndexValue:
        if raw is None:
            return None
        return str(raw)


class Vector(Field):
    """Dense VECTOR field. Pair with an embedder to encode values on save.

    ``source`` is the Django attribute to encode (dotted paths allowed).
    ``embedder`` is a callable, an :class:`~redis_search_django.embeddings.Embedder`,
    or a dotted import path. If omitted, ``Document.embed_{name}`` then
    ``Document.embedder`` are used. ``prepare_{name}`` still wins and may
    return either the source text or the finished vector.
    """

    def __init__(
        self,
        *,
        dims: int,
        algorithm: Literal["FLAT", "HNSW"] = "HNSW",
        distance: Literal["COSINE", "L2", "IP"] = "COSINE",
        type: Literal["FLOAT32", "FLOAT64"] = "FLOAT32",
        m: int = 16,
        ef_construction: int = 200,
        ef_runtime: int = 10,
        initial_cap: int | None = None,
        source: str | None = None,
        embedder: Embedder | EmbedFn | str | None = None,
        model_attr: str | None = None,
        sortable: bool = False,
        no_index: bool = False,
        index_missing: bool = False,
        as_name: str | None = None,
    ) -> None:
        super().__init__(
            model_attr=model_attr,
            sortable=sortable,
            no_index=no_index,
            index_missing=index_missing,
            as_name=as_name,
        )
        self.dims = dims
        self.algorithm = algorithm
        self.distance = distance
        self.vector_type = type
        self.m = m
        self.ef_construction = ef_construction
        self.ef_runtime = ef_runtime
        self.initial_cap = initial_cap
        self.source = source
        self.embedder = embedder
        code = "f" if self.vector_type == "FLOAT32" else "d"
        self._struct_fmt = f"<{self.dims}{code}"

    def redis_type(self) -> str:
        return "VECTOR"

    def to_redis_field(self, *, json_path: str, alias: str) -> RedisField:
        attrs: dict[str, str | int] = {
            "TYPE": self.vector_type,
            "DIM": self.dims,
            "DISTANCE_METRIC": self.distance,
        }
        if self.algorithm == "HNSW":
            attrs["M"] = self.m
            attrs["EF_CONSTRUCTION"] = self.ef_construction
            attrs["EF_RUNTIME"] = self.ef_runtime
        elif self.initial_cap is not None:
            attrs["INITIAL_CAP"] = self.initial_cap
        return RedisVectorField(json_path, self.algorithm, attrs, as_name=alias)

    def prepare(
        self, instance: models.Model, document_cls: type[Document]
    ) -> list[float] | None:
        assert self.name is not None
        hook = self._resolved_prepare(document_cls)
        if hook is not None:
            raw = hook(instance)
        else:
            path = self.source or self.model_attr or self.name
            raw = (
                resolve_source(instance, path)
                if self.source
                else _resolve_attr(instance, path)
            )
        if raw is None:
            return None
        try:
            values = _as_numeric_list(raw)
        except (TypeError, ValueError):
            values = None
        if values is not None:
            if len(values) != self.dims:
                raise ConfigurationError(
                    f"Vector field {self.name!r} expected {self.dims} dimensions, "
                    f"got {len(values)}."
                )
            return values
        embedder = resolve_embedder(document_cls, self)
        if embedder is None:
            raise ConfigurationError(
                f"Vector field {self.name!r} got {type(raw).__name__}; "
                "set embedder=, implement embed_"
                f"{self.name}(), or return a {self.dims}-float sequence "
                f"from prepare_{self.name}()."
            )
        return as_floats(
            call_embed(embedder, cast(FieldInput, raw)),
            field_name=self.name,
            dims=self.dims,
        )

    def to_index_value(self, raw: object, *, storage: str = "json") -> IndexValue:
        if raw is None:
            return None
        name = self.name or "vector"
        if isinstance(raw, (bytes, bytearray, memoryview)):
            values = self.from_blob(bytes(raw))
        else:
            values = as_floats(raw, field_name=name, dims=self.dims)
        if storage == Storage.HASH:
            return self.to_blob(values)
        return values

    def to_blob(self, values: Sequence[float]) -> bytes:
        """Pack floats as little-endian FLOAT32/FLOAT64 for PARAMS / Hash."""
        floats = as_floats(values, field_name=self.name or "vector", dims=self.dims)
        return struct.pack(self._struct_format(), *floats)

    def from_blob(self, blob: bytes) -> list[float]:
        expected = struct.calcsize(self._struct_format())
        if len(blob) != expected:
            raise ConfigurationError(
                f"Vector field {self.name!r} blob is {len(blob)} bytes, "
                f"expected {expected} for {self.dims}x{self.vector_type}."
            )
        return [float(item) for item in struct.unpack(self._struct_format(), blob)]

    def _struct_format(self) -> str:
        return self._struct_fmt


class Object(Field):
    """Embedded object. Constructor: ``Object(OtherDocument, *, required=True)``."""

    def __init__(
        self,
        document: type[Document],
        *,
        required: bool = True,
        model_attr: str | None = None,
        sortable: bool = False,
        no_index: bool = False,
        index_missing: bool = False,
        as_name: str | None = None,
    ) -> None:
        super().__init__(
            model_attr=model_attr,
            sortable=sortable,
            no_index=no_index,
            index_missing=index_missing,
            as_name=as_name,
        )
        self.target = document
        self.required = required

    def redis_type(self) -> str:
        return "OBJECT"


class Nested(Field):
    """List of embedded objects. Constructor: ``Nested(OtherDocument)``."""

    def __init__(
        self,
        document: type[Document],
        *,
        model_attr: str | None = None,
        sortable: bool = False,
        no_index: bool = False,
        index_missing: bool = False,
        as_name: str | None = None,
    ) -> None:
        super().__init__(
            model_attr=model_attr,
            sortable=sortable,
            no_index=no_index,
            index_missing=index_missing,
            as_name=as_name,
        )
        self.target = document

    def redis_type(self) -> str:
        return "NESTED"


def _resolve_attr(obj: models.Model, path: str) -> object:
    current: object = obj
    for part in path.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


def _datetime_to_ts(value: datetime.datetime) -> float:
    if timezone.is_aware(value):
        value = timezone.localtime(value).astimezone(datetime.timezone.utc)
    else:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.timestamp()


def _date_to_ts(value: datetime.date) -> float:
    dt = datetime.datetime.combine(
        value, datetime.time.min, tzinfo=datetime.timezone.utc
    )
    return dt.timestamp()
