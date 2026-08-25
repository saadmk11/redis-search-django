from __future__ import annotations

from typing import TYPE_CHECKING, Any

from redis.commands.search.aggregation import Asc, Desc

from ..client import get_async_redis_connection, get_redis_connection
from ..documents import Document
from ..enums import ReducerKind
from ..redis import (
    AggregateRequest,
    aggregate_dialect,
    aggregate_query,
    reducers,
    search_params,
    wait_redis,
)
from ..schema import flatten_lookup
from ..types import IndexValue, RedisAggregateResult
from .compiler import QueryParams, ensure_query_params
from .instrument import observe, query_text

if TYPE_CHECKING:
    from .queryset import DocumentQuerySet


class Aggregate:
    """Fluent ``FT.AGGREGATE`` builder. Lookups are Django names (``vendor__name``)."""

    def __init__(self) -> None:
        self._group: str | None = None
        self._reducers: list[tuple[ReducerKind, str, str | None]] = []
        self._sort: str | None = None
        self._limit: int | None = None
        self._load: tuple[str, ...] = ()

    def group_by(self, lookup: str) -> Aggregate:
        self._group = lookup
        return self

    def count(self, alias: str = "count") -> Aggregate:
        self._reducers.append((ReducerKind.COUNT, alias, None))
        return self

    def avg(self, lookup: str, alias: str) -> Aggregate:
        self._reducers.append((ReducerKind.AVG, alias, lookup))
        return self

    def sum(self, lookup: str, alias: str) -> Aggregate:
        self._reducers.append((ReducerKind.SUM, alias, lookup))
        return self

    def min(self, lookup: str, alias: str) -> Aggregate:
        self._reducers.append((ReducerKind.MIN, alias, lookup))
        return self

    def max(self, lookup: str, alias: str) -> Aggregate:
        self._reducers.append((ReducerKind.MAX, alias, lookup))
        return self

    def tolist(self, lookup: str, alias: str) -> Aggregate:
        self._reducers.append((ReducerKind.TOLIST, alias, lookup))
        return self

    def sort_by(self, field: str) -> Aggregate:
        self._sort = field
        return self

    def limit(self, n: int) -> Aggregate:
        self._limit = n
        return self

    def load(self, *names: str) -> Aggregate:
        self._load = names
        return self


def _build_aggregate(
    queryset: DocumentQuerySet,
    spec: Aggregate | AggregateRequest,
    query_params: QueryParams | None = None,
) -> tuple[type[Document], AggregateRequest, QueryParams | None]:
    document_cls = queryset.document_cls
    if queryset._compiled is None:
        queryset._filter_query()
    compiled = queryset._compiled
    assert compiled is not None
    query, compiled_params = compiled
    if query_params is not None:
        params = query_params or None
    else:
        params = compiled_params

    if isinstance(spec, AggregateRequest):
        request = spec
        query = aggregate_query(spec)
    else:
        request = aggregate_dialect(AggregateRequest(query), document_cls._meta.dialect)
        if spec._group:
            alias, _field = flatten_lookup(document_cls, spec._group)
            reds = []
            for kind, out_alias, source in spec._reducers:
                if kind is ReducerKind.COUNT:
                    reds.append(reducers.count().alias(out_alias))
                else:
                    src_alias, _sf = flatten_lookup(document_cls, source or spec._group)
                    fn = getattr(reducers, kind.value)
                    reds.append(fn(f"@{src_alias}").alias(out_alias))
            request = request.group_by([f"@{alias}"], *reds)
        if spec._sort:
            desc = spec._sort.startswith("-")
            path = spec._sort[1:] if desc else spec._sort
            try:
                sort_alias, _ = flatten_lookup(document_cls, path)
            except KeyError:
                sort_alias = path
            direction = Desc if desc else Asc
            request = request.sort_by(direction(f"@{sort_alias}"))
        if spec._limit:
            request = request.limit(0, spec._limit)
        if spec._load:
            aliases = []
            for name in spec._load:
                load_alias, _ = flatten_lookup(document_cls, name)
                aliases.append(f"@{load_alias}")
            request = request.load(*aliases)

    ensure_query_params(query, params)
    return document_cls, request, params


def _rows_from_aggregate(raw: RedisAggregateResult) -> list[dict[str, IndexValue]]:
    rows: list[dict[str, IndexValue]] = []
    for row in raw.rows:
        item: dict[str, IndexValue] = {}
        if isinstance(row, dict):
            item = {str(k).lstrip("@"): v for k, v in row.items()}
        else:
            values = list(row)
            for i in range(0, len(values), 2):
                key = str(values[i]).lstrip("@")
                item[key] = values[i + 1]
        rows.append(item)
    return rows


def run_aggregate(
    queryset: DocumentQuerySet,
    spec: Aggregate | AggregateRequest,
    query_params: QueryParams | None = None,
) -> list[dict[str, IndexValue]]:
    document_cls, request, params = _build_aggregate(
        queryset, spec, query_params=query_params
    )
    with observe(
        kind="aggregate",
        document=document_cls.__name__,
        index=document_cls._meta.index_alias,
        query=query_text(getattr(request, "_query", request)),
        params=dict(params or {}),
        extra=queryset._extra is not None,
        dialect=document_cls._meta.dialect,
    ) as obs:
        raw = (
            get_redis_connection()
            .ft(document_cls._meta.index_alias)
            .aggregate(request, query_params=search_params(params))
        )
        result: RedisAggregateResult = raw
        rows = _rows_from_aggregate(result)
        if obs is not None:
            obs["total"] = len(rows)
        return rows


async def arun_aggregate(
    queryset: DocumentQuerySet,
    spec: Aggregate | AggregateRequest,
    query_params: QueryParams | None = None,
) -> list[dict[str, IndexValue]]:
    document_cls, request, params = _build_aggregate(
        queryset, spec, query_params=query_params
    )
    with observe(
        kind="aggregate",
        document=document_cls.__name__,
        index=document_cls._meta.index_alias,
        query=query_text(aggregate_query(request)),
        params=dict(params or {}),
        extra=queryset._extra is not None,
        dialect=document_cls._meta.dialect,
    ) as obs:
        search: Any = get_async_redis_connection().ft(document_cls._meta.index_alias)
        raw = await wait_redis(
            search.aggregate(request, query_params=search_params(params))
        )
        result: RedisAggregateResult = raw
        rows = _rows_from_aggregate(result)
        if obs is not None:
            obs["total"] = len(rows)
        return rows
