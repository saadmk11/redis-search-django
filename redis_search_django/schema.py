from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from redis.commands.search.field import Field as RedisField

from .documents import Document
from .enums import Storage
from .fields import Field, Nested, Object, Tag, Vector

_SCHEMA_CACHE_ATTR = "_rsd_schema_cache"
_LOOKUP_CACHE_ATTR = "_rsd_lookup_cache"


@dataclass(frozen=True)
class SchemaField:
    name: str
    type: str
    path: str
    alias: str
    sortable: bool
    index_missing: bool
    extra: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class IndexSchema:
    alias: str
    prefix: str
    storage: Storage
    language: str | None
    stopwords: tuple[str, ...] | None
    score: float
    fields: tuple[SchemaField, ...]

    def fingerprint(self) -> str:
        cached = getattr(self, "_fingerprint", None)
        if isinstance(cached, str):
            return cached
        payload = {
            "v": 1,
            "storage": self.storage.value,
            "prefix": self.prefix,
            "language": self.language,
            "stopwords": None if self.stopwords is None else list(self.stopwords),
            "score": self.score,
            "fields": [
                {
                    "name": item.name,
                    "type": item.type,
                    "path": item.path,
                    "alias": item.alias,
                    "sortable": item.sortable,
                    "index_missing": item.index_missing,
                    **item.extra,
                }
                for item in sorted(self.fields, key=lambda item: item.name)
            ],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        digest = "rsd-schema-v1:" + hashlib.sha256(blob).hexdigest()[:16]
        object.__setattr__(self, "_fingerprint", digest)
        return digest

    @property
    def physical_name(self) -> str:
        return f"{self.alias}:{self.fingerprint().rsplit(':', 1)[-1][:8]}"


def build_schema(document_cls: type[Document]) -> IndexSchema:
    """Return the schema for *document_cls*, reusing a cached copy."""
    token = _schema_token(document_cls)
    cached = getattr(document_cls, _SCHEMA_CACHE_ATTR, None)
    if isinstance(cached, tuple) and cached[0] == token:
        return cast(IndexSchema, cached[1])
    schema = _build_schema(document_cls)
    setattr(document_cls, _SCHEMA_CACHE_ATTR, (token, schema))
    return schema


def _schema_token(document_cls: type[Document]) -> tuple[object, ...]:
    meta = document_cls._meta
    return (
        meta.index_alias,
        meta.key_prefix,
        meta.storage,
        meta.language,
        meta.stopwords,
        meta.score,
        tuple(meta.fields.keys()),
        tuple(id(item) for item in meta.fields.values()),
    )


def _build_schema(document_cls: type[Document]) -> IndexSchema:
    meta = document_cls._meta
    schema_fields: list[SchemaField] = [
        SchemaField(
            name="pk",
            type="TAG",
            path="$.pk" if meta.storage is Storage.JSON else "pk",
            alias="pk",
            sortable=True,
            index_missing=False,
            extra={},
        )
    ]
    for item in meta.fields.values():
        schema_fields.extend(_schema_fields_for(item, storage=meta.storage))
    return IndexSchema(
        alias=meta.index_alias,
        prefix=meta.key_prefix,
        storage=meta.storage,
        language=meta.language,
        stopwords=meta.stopwords,
        score=meta.score,
        fields=tuple(schema_fields),
    )


def _schema_fields_for(
    field: Field,
    *,
    storage: Storage | str,
    parent_path: str = "$",
    parent_alias: str = "",
) -> list[SchemaField]:
    if isinstance(field, Object):
        path = field.json_path(parent_path)
        alias = field.as_name(parent_alias)
        fields = [
            SchemaField(
                name=f"{alias}_pk",
                type="TAG",
                path=f"{path}.pk",
                alias=f"{alias}_pk",
                sortable=False,
                index_missing=not field.required,
                extra={"ismissing": True} if not field.required else {},
            )
        ]
        for child in field.target._meta.fields.values():
            fields.extend(
                _schema_fields_for(
                    child, storage=storage, parent_path=path, parent_alias=alias
                )
            )
        return fields
    if isinstance(field, Nested):
        path = f"{parent_path}.{field.name}[*]"
        alias = field.as_name(parent_alias)
        fields = [
            SchemaField(
                name=f"{alias}_pk",
                type="TAG",
                path=f"{path}.pk",
                alias=f"{alias}_pk",
                sortable=False,
                index_missing=False,
                extra={},
            )
        ]
        for child in field.target._meta.fields.values():
            fields.extend(
                _schema_fields_for(
                    child, storage=storage, parent_path=path, parent_alias=alias
                )
            )
        return fields

    path = (
        field.json_path(parent_path)
        if storage is Storage.JSON
        else field.hash_name(parent_alias)
    )
    extra: dict[str, str | int | float | bool] = {}
    if field.weight is not None:
        extra["weight"] = field.weight
    if field.no_stem:
        extra["no_stem"] = True
    if isinstance(field, Tag):
        extra["separator"] = field.separator
        extra["case_sensitive"] = field.case_sensitive
        extra["suffix_trie"] = field.suffix_trie
    if isinstance(field, Vector):
        extra.update(
            {
                "dims": field.dims,
                "algorithm": field.algorithm,
                "distance": field.distance,
                "type": field.vector_type,
            }
        )
    return [
        SchemaField(
            name=field.name or "",
            type=field.redis_type(),
            path=path,
            alias=field.as_name(parent_alias),
            sortable=field.sortable,
            index_missing=field.index_missing,
            extra=extra,
        )
    ]


def redis_fields(document_cls: type[Document]) -> list[RedisField]:
    """Build redis-py SCHEMA field objects for FT.CREATE."""
    meta = document_cls._meta
    built: list[RedisField] = []
    pk = Tag(sortable=True)
    pk.bind("pk", document_cls)
    path = "$.pk" if meta.storage is Storage.JSON else "pk"
    built.append(pk.to_redis_field(json_path=path, alias="pk"))
    for item in meta.fields.values():
        built.extend(_redis_fields_for(item, storage=meta.storage))
    return built


def _redis_fields_for(
    field: Field,
    *,
    storage: Storage | str,
    parent_path: str = "$",
    parent_alias: str = "",
) -> list[RedisField]:
    if isinstance(field, (Object, Nested)):
        path = (
            f"{parent_path}.{field.name}[*]"
            if isinstance(field, Nested)
            else field.json_path(parent_path)
        )
        alias = field.as_name(parent_alias)
        pk = Tag(index_missing=isinstance(field, Object) and not field.required)
        pk.bind("pk", field.target)
        fields = [pk.to_redis_field(json_path=f"{path}.pk", alias=f"{alias}_pk")]
        for child in field.target._meta.fields.values():
            fields.extend(
                _redis_fields_for(
                    child, storage=storage, parent_path=path, parent_alias=alias
                )
            )
        return fields
    path = (
        field.json_path(parent_path)
        if storage is Storage.JSON
        else field.hash_name(parent_alias)
    )
    return [field.to_redis_field(json_path=path, alias=field.as_name(parent_alias))]


def flatten_lookup(document_cls: type[Document], lookup: str) -> tuple[str, Field]:
    """Resolve a Django-style lookup path to (alias, leaf field)."""
    cache = document_cls.__dict__.get(_LOOKUP_CACHE_ATTR)
    if cache is None:
        cache = {}
        setattr(document_cls, _LOOKUP_CACHE_ATTR, cache)
    cached = cache.get(lookup)
    if cached is not None:
        return cast(tuple[str, Field], cached)
    resolved = _flatten_lookup(document_cls, lookup)
    cache[lookup] = resolved
    return resolved


def _flatten_lookup(document_cls: type[Document], lookup: str) -> tuple[str, Field]:
    parts = [part for part in lookup.split("__") if part]
    parent_alias = ""
    current_doc = document_cls
    i = 0
    while i < len(parts):
        name = parts[i]
        if name not in current_doc._meta.fields:
            # maybe the remainder is a lookup type — handled by caller
            raise KeyError(lookup)
        field = current_doc._meta.fields[name]
        if isinstance(field, (Object, Nested)) and i < len(parts) - 1:
            parent_alias = field.as_name(parent_alias)
            current_doc = field.target
            i += 1
            continue
        return field.as_name(parent_alias), field
    raise KeyError(lookup)
