from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Protocol, overload

from django.core.exceptions import FieldError
from django.db import models
from redis.client import NEVER_DECODE
from redis.commands.search.query import Query

from ..client import (
    get_async_redis_connection,
    get_redis_connection,
    json_aget,
    json_get,
)
from ..conf import setting_int
from ..documents import Document
from ..embeddings import as_floats, call_embed_query, is_vector, resolve_embedder
from ..enums import QConnector, Storage
from ..exceptions import ConfigurationError, NotSupportedError
from ..fields import Nested, Object, Text, Vector
from ..redis import AsyncRedis, Redis, query_dialect, search_params, wait_redis
from ..schema import flatten_lookup
from ..targets import invalidate_targets
from ..types import (
    DocumentPayload,
    FacetMap,
    FacetRow,
    HashMapping,
    IndexValue,
    LookupValue,
    RedisSearchResult,
    as_float,
    as_hash_mapping,
    as_int,
    is_index_value,
)
from ..versioning import public_payload
from .compiler import QueryCompiler, QueryParams, ensure_query_params
from .instrument import NOOP_OBSERVE, current_listener, observe, query_text
from .knn import KnnClause, validate_score_name, wrap_knn_query
from .lookups import Q
from .results import SearchHit, SearchResult

if TYPE_CHECKING:
    from ..redis import AggregateRequest
    from .aggregate import Aggregate


class _SearchPage(Protocol):
    def __call__(
        self, *, offset: int, limit: int, content: bool = True
    ) -> SearchResult: ...


logger = logging.getLogger("redis_search_django")
_TEXT_LOOKUPS_ATTR = "_rsd_text_lookups"
_HASH_VECTORS_ATTR = "_rsd_hash_vectors"
_HAS_VECTOR_ATTR = "_rsd_has_vector"


class DocumentManager:
    """Entry point for ``Document.objects`` — same verbs as a Django manager."""

    def __init__(self, document_cls: type[Document]) -> None:
        self.document_cls = document_cls

    def get_queryset(self) -> DocumentQuerySet:
        return DocumentQuerySet(self.document_cls)

    def all(self) -> DocumentQuerySet:
        return self.get_queryset()

    def filter(self, *args: Q, **kwargs: LookupValue) -> DocumentQuerySet:
        return self.get_queryset().filter(*args, **kwargs)

    def exclude(self, *args: Q, **kwargs: LookupValue) -> DocumentQuerySet:
        return self.get_queryset().exclude(*args, **kwargs)

    def search(self, q: str) -> DocumentQuerySet:
        return self.get_queryset().search(q)

    def order_by(self, *fields: str) -> DocumentQuerySet:
        return self.get_queryset().order_by(*fields)

    def extra(self, query: str, params: QueryParams | None = None) -> DocumentQuerySet:
        return self.get_queryset().extra(query=query, params=params)

    def none(self) -> DocumentQuerySet:
        return self.get_queryset().none()

    def first(self) -> SearchHit | None:
        return self.get_queryset().first()

    async def afirst(self) -> SearchHit | None:
        return await self.get_queryset().afirst()

    def last(self) -> SearchHit | None:
        return self.get_queryset().last()

    async def alast(self) -> SearchHit | None:
        return await self.get_queryset().alast()

    def reverse(self) -> DocumentQuerySet:
        return self.get_queryset().reverse()

    def iterator(self) -> Iterator[SearchHit | DocumentPayload]:
        return self.get_queryset().iterator()

    def aiterator(self) -> AsyncIterator[SearchHit | DocumentPayload]:
        return self.get_queryset().aiterator()

    def highlight(self, *fields: str) -> DocumentQuerySet:
        return self.get_queryset().highlight(*fields)

    def values(self, *names: str) -> DocumentQuerySet:
        return self.get_queryset().values(*names)

    def return_fields(self, *names: str) -> DocumentQuerySet:
        return self.get_queryset().return_fields(*names)

    def facets(self, *lookups: str) -> FacetMap:
        return self.get_queryset().facets(*lookups)

    async def afacets(self, *lookups: str) -> FacetMap:
        return await self.get_queryset().afacets(*lookups)

    def aggregate(
        self,
        request: Aggregate | AggregateRequest,
        query_params: QueryParams | None = None,
    ) -> list[dict[str, IndexValue]]:
        return self.get_queryset().aggregate(request, query_params=query_params)

    async def aaggregate(
        self,
        request: Aggregate | AggregateRequest,
        query_params: QueryParams | None = None,
    ) -> list[dict[str, IndexValue]]:
        return await self.get_queryset().aaggregate(request, query_params=query_params)

    def knn(
        self,
        query: str | Sequence[float],
        /,
        *,
        field: str | None = None,
        k: int = 10,
        ef_runtime: int | None = None,
        score_name: str = "vector_score",
    ) -> DocumentQuerySet:
        return self.get_queryset().knn(
            query, field=field, k=k, ef_runtime=ef_runtime, score_name=score_name
        )

    def get(self, *args: Q, **kwargs: LookupValue) -> SearchHit:
        if args or set(kwargs) != {"pk"}:
            return self.filter(*args, **kwargs).get()
        return self.get_by_pk(kwargs["pk"])

    async def aget(self, *args: Q, **kwargs: LookupValue) -> SearchHit:
        if args or set(kwargs) != {"pk"}:
            return await self.filter(*args, **kwargs).aget()
        return await self.aget_by_pk(kwargs["pk"])

    def get_by_pk(self, pk: object) -> SearchHit:
        data = self._read_pk(pk)
        if not data:
            invalidate_targets(self.document_cls)
            data = self._read_pk(pk)
        return _hit_from_key(self.document_cls, pk, data)

    async def aget_by_pk(self, pk: object) -> SearchHit:
        data = await self._aread_pk(pk)
        if not data:
            invalidate_targets(self.document_cls)
            data = await self._aread_pk(pk)
        return _hit_from_key(self.document_cls, pk, data)

    def _read_pk(self, pk: object) -> DocumentPayload | HashMapping | None:
        client = get_redis_connection()
        key = self.document_cls.key_for(pk)
        command = (
            "JSON.GET" if self.document_cls._meta.storage is Storage.JSON else "HGETALL"
        )
        with _observe_key(self.document_cls, key, command) as obs:
            if self.document_cls._meta.storage is Storage.JSON:
                data = json_get(client, key)
            else:
                data = _load_hash(client, key, self.document_cls)
            if obs is not None:
                obs["total"] = 0 if not data else 1
        return data

    async def _aread_pk(self, pk: object) -> DocumentPayload | HashMapping | None:
        client = get_async_redis_connection()
        key = self.document_cls.key_for(pk)
        command = (
            "JSON.GET" if self.document_cls._meta.storage is Storage.JSON else "HGETALL"
        )
        with _observe_key(self.document_cls, key, command) as obs:
            if self.document_cls._meta.storage is Storage.JSON:
                data = await json_aget(client, key)
            else:
                data = await _aload_hash(client, key, self.document_cls)
            if obs is not None:
                obs["total"] = 0 if not data else 1
        return data


class DocumentQuerySet:
    """Lazy RediSearch queryset. ``filter`` / ``order_by`` / slices do not hit Redis."""

    def __init__(self, document_cls: type[Document]) -> None:
        self.document_cls = document_cls
        self._q = Q()
        self._sort: str | None = None
        self._sort_desc = False
        self._slice: tuple[int, int | None] | None = None
        self._highlight: tuple[str, ...] = ()
        self._return_fields: tuple[str, ...] = ()
        self._values = False
        self._extra: str | None = None
        self._extra_params: QueryParams = {}
        self._none = False
        self._knn: KnnClause | None = None
        self._result: SearchResult | None = None
        self._compiled: tuple[str, QueryParams | None] | None = None
        self._total: int | None = None

    def _clone(self) -> DocumentQuerySet:
        clone = DocumentQuerySet(self.document_cls)
        clone._q = self._q
        clone._sort = self._sort
        clone._sort_desc = self._sort_desc
        clone._slice = self._slice
        clone._highlight = self._highlight
        clone._return_fields = self._return_fields
        clone._values = self._values
        clone._extra = self._extra
        clone._extra_params = dict(self._extra_params)
        clone._none = self._none
        clone._knn = self._knn
        return clone

    def filter(self, *args: Q, **kwargs: LookupValue) -> DocumentQuerySet:
        if self._extra:
            raise ValueError(
                "Cannot call filter() after extra(). extra() replaces the "
                "compiled Q tree with a raw RediSearch string; put additional "
                "clauses in that string, or start a new queryset and call "
                "filter() first."
            )
        clone = self._clone()
        node = Q(*args, **kwargs)
        if clone._q.children:
            clone._q = clone._q & node
        else:
            clone._q = node
        return clone

    def exclude(self, *args: Q, **kwargs: LookupValue) -> DocumentQuerySet:
        return self.filter(~Q(*args, **kwargs))

    def search(self, q: str) -> DocumentQuerySet:
        text = q.strip()
        if not text:
            return self._clone()
        names = self.document_cls._meta.search_fields_option
        if names is None:
            names = tuple(_text_lookups(self.document_cls))
        node = Q()
        node.connector = QConnector.OR
        for name in names:
            node.children.append((f"{name}__search", text))
        return self.filter(node)

    def order_by(self, *fields: str) -> DocumentQuerySet:
        if len(fields) != 1:
            raise NotSupportedError("order_by() accepts exactly one field in 1.0.")
        name = fields[0]
        desc = name.startswith("-")
        path = name[1:] if desc else name
        try:
            alias, field = flatten_lookup(self.document_cls, path)
        except KeyError as exc:
            raise FieldError(f"Cannot resolve order_by field {path!r}.") from exc
        if not field.sortable:
            raise FieldError(f"{path} is not sortable.")
        clone = self._clone()
        clone._sort = alias
        clone._sort_desc = desc
        return clone

    def highlight(self, *fields: str) -> DocumentQuerySet:
        if self._values:
            raise ValueError("highlight() cannot be combined with values().")
        clone = self._clone()
        clone._highlight = fields
        return clone

    def return_fields(self, *names: str) -> DocumentQuerySet:
        clone = self._clone()
        clone._return_fields = names
        return clone

    def values(self, *names: str) -> DocumentQuerySet:
        if self._highlight:
            raise ValueError("values() cannot be combined with highlight().")
        clone = self._clone()
        clone._values = True
        clone._return_fields = names
        return clone

    def extra(self, query: str, params: QueryParams | None = None) -> DocumentQuerySet:
        if self._knn is not None:
            raise ValueError("Cannot call extra() after knn().")
        clone = self._clone()
        clone._extra = query
        clone._extra_params = dict(params) if params else {}
        return clone

    def none(self) -> DocumentQuerySet:
        clone = self._clone()
        clone._none = True
        return clone

    def reverse(self) -> DocumentQuerySet:
        if self._slice is not None:
            raise TypeError("Cannot reverse a query once a slice has been taken.")
        if self._sort is None:
            raise FieldError("Cannot reverse() without order_by().")
        clone = self._clone()
        clone._sort_desc = not self._sort_desc
        return clone

    def first(self) -> SearchHit | None:
        result = self._search(offset=self._start(), limit=1)
        return result.hits[0] if result.hits else None

    async def afirst(self) -> SearchHit | None:
        result = await self._asearch(offset=self._start(), limit=1)
        return result.hits[0] if result.hits else None

    def last(self) -> SearchHit | None:
        return self.reverse().first()

    async def alast(self) -> SearchHit | None:
        return await self.reverse().afirst()

    def iterator(self) -> Iterator[SearchHit | DocumentPayload]:
        return iter(self)

    def aiterator(self) -> AsyncIterator[SearchHit | DocumentPayload]:
        return self.__aiter__()

    def _start(self) -> int:
        return self._slice[0] if self._slice else 0

    def knn(
        self,
        query: str | Sequence[float],
        /,
        *,
        field: str | None = None,
        k: int = 10,
        ef_runtime: int | None = None,
        score_name: str = "vector_score",
    ) -> DocumentQuerySet:
        """Nearest-neighbor search, optionally restricted by ``filter()``.

        *query* is either a ``dims``-length float sequence or a value the
        field's embedder can encode (usually text). Existing ``filter()`` /
        ``search()`` clauses become the Redis pre-filter::

            (@available:{true})=>[KNN $k @embedding $vec AS vector_score]
        """
        if self._extra:
            raise ValueError("Cannot call knn() after extra().")
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise ValueError("knn() k must be a positive integer.")
        if ef_runtime is not None and (
            not isinstance(ef_runtime, int)
            or isinstance(ef_runtime, bool)
            or ef_runtime < 1
        ):
            raise ValueError("knn() ef_runtime must be a positive integer.")
        alias, vector = _resolve_knn_field(self.document_cls, field)
        blob = _knn_blob(self.document_cls, vector, query)
        clone = self._clone()
        clone._knn = KnnClause(
            alias=alias,
            field=vector,
            blob=blob,
            k=k,
            ef_runtime=ef_runtime,
            score_name=validate_score_name(score_name),
        )
        if clone._sort is None:
            clone._sort = clone._knn.score_name
            clone._sort_desc = False
        return clone

    def explain(self) -> str:
        query, params = self._explain_args()
        with self._observe("explain", query, params):
            return str(
                get_redis_connection()
                .ft(self.document_cls._meta.index_alias)
                .explain(query, query_params=search_params(params))
            )

    async def aexplain(self) -> str:
        query, params = self._explain_args()
        with self._observe("explain", query, params):
            return str(
                await wait_redis(
                    get_async_redis_connection()
                    .ft(self.document_cls._meta.index_alias)
                    .explain(query, query_params=search_params(params))
                )
            )

    def raw(self) -> tuple[str, QueryParams]:
        query, params = self._filter_query()
        return query, dict(params or {})

    def count(self) -> int:
        if self._knn is not None:
            return len(self._search(offset=0, limit=self._knn.k, content=False).hits)
        if self._result is not None:
            return self._result.total
        if self._total is not None:
            return self._total
        self._total = self._search(offset=0, limit=0).total
        return self._total

    async def acount(self) -> int:
        if self._knn is not None:
            result = await self._asearch(offset=0, limit=self._knn.k, content=False)
            return len(result.hits)
        if self._result is not None:
            return self._result.total
        if self._total is not None:
            return self._total
        self._total = (await self._asearch(offset=0, limit=0)).total
        return self._total

    def exists(self) -> bool:
        if self._result is not None:
            return self._result.total > 0
        if self._total is not None:
            return self._total > 0
        return self._search(offset=0, limit=1).total > 0

    async def aexists(self) -> bool:
        if self._result is not None:
            return self._result.total > 0
        if self._total is not None:
            return self._total > 0
        return (await self._asearch(offset=0, limit=1)).total > 0

    def __bool__(self) -> bool:
        return self.exists()

    def __len__(self) -> int:
        return self.count()

    @overload
    def __getitem__(self, item: int) -> SearchHit: ...

    @overload
    def __getitem__(self, item: slice) -> DocumentQuerySet: ...

    def __getitem__(self, item: int | slice) -> SearchHit | DocumentQuerySet:
        if isinstance(item, int):
            if item < 0:
                raise ValueError("Negative indexes are not supported.")
            result = self._search(offset=item, limit=1)
            if not result.hits:
                raise IndexError(item)
            return result.hits[0]
        if item.step not in {None, 1}:
            raise ValueError("Slicing with step is not supported.")
        start = item.start or 0
        stop = item.stop
        if start < 0 or (stop is not None and stop < 0):
            raise ValueError("Negative indexes are not supported.")
        clone = self._clone()
        if stop is None:
            clone._slice = (start, None)
        else:
            clone._slice = (start, stop - start)
        return clone

    def __iter__(self) -> Iterator[SearchHit | DocumentPayload]:
        result = self._evaluate()
        if self._values:
            return iter(hit.data for hit in result.hits)
        return iter(result.hits)

    async def __aiter__(self) -> AsyncIterator[SearchHit | DocumentPayload]:
        result = await self._aevaluate()
        hits = result.hits
        if self._values:
            for hit in hits:
                yield hit.data
        else:
            for hit in hits:
                yield hit

    def get(self) -> SearchHit:
        return _single_hit(self.document_cls, self._search(offset=0, limit=2))

    async def aget(self) -> SearchHit:
        return _single_hit(self.document_cls, await self._asearch(offset=0, limit=2))

    def to_queryset(self) -> models.QuerySet[models.Model]:
        return self._evaluate().to_queryset()

    async def ato_queryset(self) -> models.QuerySet[models.Model]:
        return (await self._aevaluate()).to_queryset()

    def facets(self, *lookups: str) -> FacetMap:
        return self._facets_from_rows(
            lookups, {lookup: self.aggregate(_count_agg(lookup)) for lookup in lookups}
        )

    async def afacets(self, *lookups: str) -> FacetMap:
        rows: dict[str, list[dict[str, IndexValue]]] = {}
        for lookup in lookups:
            rows[lookup] = await self.aaggregate(_count_agg(lookup))
        return self._facets_from_rows(lookups, rows)

    def aggregate(
        self,
        request: Aggregate | AggregateRequest,
        query_params: QueryParams | None = None,
    ) -> list[dict[str, IndexValue]]:
        from .aggregate import run_aggregate

        if self._none:
            return []
        return run_aggregate(self, request, query_params=query_params)

    async def aaggregate(
        self,
        request: Aggregate | AggregateRequest,
        query_params: QueryParams | None = None,
    ) -> list[dict[str, IndexValue]]:
        from .aggregate import arun_aggregate

        if self._none:
            return []
        return await arun_aggregate(self, request, query_params=query_params)

    def _facets_from_rows(
        self,
        lookups: tuple[str, ...],
        rows_by_lookup: dict[str, list[dict[str, IndexValue]]],
    ) -> FacetMap:
        out: FacetMap = {}
        for lookup in lookups:
            try:
                alias, _field = flatten_lookup(self.document_cls, lookup)
            except KeyError:
                alias = lookup
            rows: list[FacetRow] = [
                {
                    "value": row.get(lookup) or row.get(alias) or row.get("value"),
                    "count": as_int(row.get("count", 0)),
                }
                for row in rows_by_lookup[lookup]
            ]
            out[lookup] = rows
        return out

    def _window(self) -> tuple[int, int | None]:
        start = self._slice[0] if self._slice else 0
        if self._slice is not None and self._slice[1] is not None:
            return start, self._slice[1]
        if self._knn is not None:
            return start, self._knn.k
        return start, None

    def _evaluate(self) -> SearchResult:
        if self._result is not None:
            return self._result
        offset, limit = self._window()
        if limit is not None:
            self._result = self._search(offset=offset, limit=limit)
            return self._result
        self._result = self._exhaust(self._search)
        return self._result

    async def _aevaluate(self) -> SearchResult:
        if self._result is not None:
            return self._result
        offset, limit = self._window()
        if limit is not None:
            self._result = await self._asearch(offset=offset, limit=limit)
            return self._result
        self._result = await self._aexhaust()
        return self._result

    def _exhaust(self, search: _SearchPage) -> SearchResult:
        chunk = setting_int("CHUNK_SIZE")
        offset = self._slice[0] if self._slice else 0
        hits: list[SearchHit] = []
        total = 0
        while True:
            page = search(offset=offset, limit=chunk)
            total = page.total
            hits.extend(page.hits)
            if len(page.hits) < chunk:
                break
            offset += chunk
        if total > chunk:
            logger.warning("Unsliced search exhausted %s hits", total)
        return SearchResult(hits=hits, total=total, document_cls=self.document_cls)

    async def _aexhaust(self) -> SearchResult:
        chunk = setting_int("CHUNK_SIZE")
        offset = self._slice[0] if self._slice else 0
        hits: list[SearchHit] = []
        total = 0
        while True:
            page = await self._asearch(offset=offset, limit=chunk)
            total = page.total
            hits.extend(page.hits)
            if len(page.hits) < chunk:
                break
            offset += chunk
        if total > chunk:
            logger.warning("Unsliced search exhausted %s hits", total)
        return SearchResult(hits=hits, total=total, document_cls=self.document_cls)

    def _filter_query(self) -> tuple[str, QueryParams | None]:
        compiled = self._compiled
        if compiled is None:
            if self._extra is not None:
                query = self._extra
                params: QueryParams | None = self._extra_params or None
            else:
                built = QueryCompiler(self.document_cls).compile(self._q)
                query = built.query
                params = built.params or None
            self._compiled = (query, params)
        else:
            query, params = compiled
        if self._knn is not None:
            query, params = wrap_knn_query(query, self._knn, params)
        ensure_query_params(query, params)
        return query, params

    def _explain_args(self) -> tuple[Query, QueryParams | None]:
        query_str, params = self._filter_query()
        return query_dialect(Query(query_str), self.document_cls._meta.dialect), params

    def _search_args(
        self, *, offset: int, limit: int, content: bool = True
    ) -> tuple[Query, QueryParams | None]:
        query_str, params = self._filter_query()
        query = query_dialect(
            Query(query_str).paging(offset, limit),
            self.document_cls._meta.dialect,
        )
        if self._sort:
            query = query.sort_by(self._sort, asc=not self._sort_desc)
        if not content:
            query = query.no_content()
        elif self._return_fields:
            aliases = []
            for name in self._return_fields:
                try:
                    alias, _field = flatten_lookup(self.document_cls, name)
                except KeyError:
                    alias = name
                aliases.append(alias)
            if self._knn is not None and self._knn.score_name not in aliases:
                aliases.append(self._knn.score_name)
            query = query.return_fields(*aliases)
        if self._highlight:
            highlight = []
            for name in self._highlight:
                try:
                    alias, _field = flatten_lookup(self.document_cls, name)
                except KeyError:
                    alias = name
                highlight.append(alias)
            query = query.highlight(fields=highlight)
        return query, params

    def _result_from_raw(self, raw: RedisSearchResult) -> SearchResult:
        hits = []
        for doc in raw.docs:
            payload = dict(doc.__dict__)
            json_blob = payload.pop("json", None)
            if isinstance(json_blob, str):
                try:
                    json_blob = json.loads(json_blob)
                except json.JSONDecodeError:
                    json_blob = None
            if isinstance(json_blob, dict):
                payload.update(json_blob)
            key = str(payload.pop("id", ""))
            pk = str(payload.pop("pk", key.rsplit(":", 1)[-1] if key else ""))
            payload.pop("payload", None)
            if self.document_cls._meta.storage is Storage.HASH:
                payload = _coerce_hash_vectors(self.document_cls, payload)
            score = getattr(doc, "score", None)
            if self._knn is not None:
                raw_score = payload.get(self._knn.score_name)
                if raw_score is not None:
                    parsed = as_float(raw_score)
                    if parsed is not None:
                        score = parsed
            hits.append(
                SearchHit(
                    pk=pk,
                    score=score,
                    data=public_payload(payload),
                )
            )
        return SearchResult(
            hits=hits, total=int(raw.total), document_cls=self.document_cls
        )

    def _search(self, *, offset: int, limit: int, content: bool = True) -> SearchResult:
        if self._none:
            return SearchResult(hits=[], total=0, document_cls=self.document_cls)
        query, params = self._search_args(offset=offset, limit=limit, content=content)
        with self._observe("search", query, params, offset=offset, limit=limit) as obs:
            raw = (
                get_redis_connection()
                .ft(self.document_cls._meta.index_alias)
                .search(query, query_params=search_params(params))
            )
            result = self._result_from_raw(raw)
            if obs is not None:
                obs["total"] = result.total
            return result

    async def _asearch(
        self, *, offset: int, limit: int, content: bool = True
    ) -> SearchResult:
        if self._none:
            return SearchResult(hits=[], total=0, document_cls=self.document_cls)
        query, params = self._search_args(offset=offset, limit=limit, content=content)
        with self._observe("search", query, params, offset=offset, limit=limit) as obs:
            raw = await wait_redis(
                get_async_redis_connection()
                .ft(self.document_cls._meta.index_alias)
                .search(query, query_params=search_params(params))
            )
            result = self._result_from_raw(raw)
            if obs is not None:
                obs["total"] = result.total
            return result

    def _observe(
        self,
        kind: str,
        query: object,
        params: QueryParams | None,
        **kwargs: Any,
    ) -> Any:
        if current_listener() is None:
            return NOOP_OBSERVE
        return observe(
            kind=kind,
            document=self.document_cls.__name__,
            index=self.document_cls._meta.index_alias,
            query=query_text(query),
            params=dict(params or {}),
            knn=self._knn is not None,
            extra=self._extra is not None,
            dialect=self.document_cls._meta.dialect,
            sort=self._sort_label(),
            **kwargs,
        )

    def _sort_label(self) -> str | None:
        if not self._sort:
            return None
        return f"-{self._sort}" if self._sort_desc else self._sort


def _observe_key(document_cls: type[Document], key: str, command: str) -> Any:
    if current_listener() is None:
        return NOOP_OBSERVE
    return observe(
        kind="get",
        document=document_cls.__name__,
        index=document_cls._meta.index_alias,
        query=f"{command} {key}",
        key=key,
        dialect=document_cls._meta.dialect,
    )


def _count_agg(lookup: str) -> Aggregate:
    from .aggregate import Aggregate

    return Aggregate().group_by(lookup).count("count")


def _hit_from_key(
    document_cls: type[Document],
    pk: object,
    data: DocumentPayload | HashMapping | None,
) -> SearchHit:
    if not data:
        raise document_cls.DoesNotExist(
            f"{document_cls.__name__} matching pk={pk!r} does not exist."
        )
    return SearchHit(pk=str(pk), data=public_payload(data) if data else data)


def _single_hit(document_cls: type[Document], result: SearchResult) -> SearchHit:
    if result.total == 0:
        raise document_cls.DoesNotExist
    if result.total > 1:
        raise document_cls.MultipleObjectsReturned
    return result.hits[0]


def _text_lookups(document_cls: type[Document]) -> list[str]:
    cached = document_cls.__dict__.get(_TEXT_LOOKUPS_ATTR)
    if isinstance(cached, list):
        return cached
    names = _collect_text_lookups(document_cls, "")
    setattr(document_cls, _TEXT_LOOKUPS_ATTR, names)
    return names


def _collect_text_lookups(document_cls: type[Document], prefix: str) -> list[str]:
    names: list[str] = []
    for name, field in document_cls._meta.fields.items():
        path = f"{prefix}__{name}" if prefix else name
        if isinstance(field, Text):
            names.append(path)
        elif isinstance(field, (Object, Nested)):
            names.extend(_collect_text_lookups(field.target, path))
    return names


def _resolve_knn_field(
    document_cls: type[Document], field_name: str | None
) -> tuple[str, Vector]:
    if field_name is not None:
        try:
            alias, field = flatten_lookup(document_cls, field_name)
        except KeyError as exc:
            raise FieldError(f"Cannot resolve knn() field {field_name!r}.") from exc
        if not isinstance(field, Vector):
            raise FieldError(f"{field_name} is not a Vector field.")
        return alias, field
    vectors = [
        (name, field)
        for name, field in document_cls._meta.fields.items()
        if isinstance(field, Vector)
    ]
    if not vectors:
        raise FieldError(f"{document_cls.__name__} has no Vector field.")
    if len(vectors) > 1:
        names = ", ".join(name for name, _ in vectors)
        raise FieldError(
            f"{document_cls.__name__} has multiple Vector fields ({names}); "
            "pass field= to knn()."
        )
    _name, field = vectors[0]
    return field.as_name(), field


def _knn_blob(
    document_cls: type[Document], field: Vector, query: str | Sequence[float]
) -> bytes:
    name = field.name or "vector"
    if is_vector(query):
        return field.to_blob(as_floats(query, field_name=name, dims=field.dims))
    embedder = resolve_embedder(document_cls, field)
    if embedder is None:
        raise ConfigurationError(
            f"knn() got a {type(query).__name__} query; pass a {field.dims}-float "
            f"vector or configure an embedder on {name!r}."
        )
    return field.to_blob(
        as_floats(call_embed_query(embedder, query), field_name=name, dims=field.dims)
    )


def _iter_hash_vectors(document_cls: type[Document]) -> list[tuple[str, Vector]]:
    cached = document_cls.__dict__.get(_HASH_VECTORS_ATTR)
    if isinstance(cached, list):
        return cached
    found = _collect_hash_vectors(document_cls, "")
    setattr(document_cls, _HASH_VECTORS_ATTR, found)
    return found


def _collect_hash_vectors(
    document_cls: type[Document], parent: str
) -> list[tuple[str, Vector]]:
    found: list[tuple[str, Vector]] = []
    for field in document_cls._meta.fields.values():
        if isinstance(field, Vector):
            found.append((field.hash_name(parent), field))
        elif isinstance(field, Object):
            found.extend(_collect_hash_vectors(field.target, field.hash_name(parent)))
    return found


def _has_vector(document_cls: type[Document]) -> bool:
    cached = document_cls.__dict__.get(_HAS_VECTOR_ATTR)
    if isinstance(cached, bool):
        return cached
    found = bool(_iter_hash_vectors(document_cls))
    setattr(document_cls, _HAS_VECTOR_ATTR, found)
    return found


def _maybe_decode(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _maybe_decode_value(value: IndexValue) -> IndexValue:
    if not isinstance(value, bytes):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value


def _pairs_to_hash(raw: object) -> HashMapping:
    pairs: list[tuple[object, object]]
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = list(raw)
        pairs = list(zip(values[0::2], values[1::2], strict=False))
    else:
        return {}
    out: HashMapping = {}
    for key, val in pairs:
        decoded = _maybe_decode(key) if isinstance(key, (bytes, str)) else str(key)
        if is_index_value(val):
            out[decoded] = val
    return out


def _decode_hash_strings(data: HashMapping) -> HashMapping:
    for key, value in list(data.items()):
        if isinstance(value, bytes):
            data[key] = _maybe_decode_value(value)
    return data


def _coerce_hash_vectors(
    document_cls: type[Document], data: HashMapping
) -> HashMapping:
    for name, field in _iter_hash_vectors(document_cls):
        if name not in data:
            continue
        value = data[name]
        blob: bytes | None = None
        if isinstance(value, (bytes, bytearray, memoryview)):
            blob = bytes(value)
        elif isinstance(value, str):
            blob = value.encode("utf-8")
        if blob is not None:
            try:
                data[name] = field.from_blob(blob)
                continue
            except ConfigurationError:
                pass
        if not is_vector(value):
            data.pop(name, None)
    return data


def _load_hash(client: Redis, key: str, document_cls: type[Document]) -> HashMapping:
    if _has_vector(document_cls):
        raw = client.execute_command("HGETALL", key, **{NEVER_DECODE: True})
        data = _decode_hash_strings(
            _coerce_hash_vectors(document_cls, _pairs_to_hash(raw))
        )
    else:
        data = as_hash_mapping(client.hgetall(key))
    return data


async def _aload_hash(
    client: AsyncRedis, key: str, document_cls: type[Document]
) -> HashMapping:
    if _has_vector(document_cls):
        raw = await client.execute_command("HGETALL", key, **{NEVER_DECODE: True})
        data = _decode_hash_strings(
            _coerce_hash_vectors(document_cls, _pairs_to_hash(raw))
        )
    else:
        hgetall: Any = client.hgetall
        data = as_hash_mapping(await hgetall(key))
    return data
