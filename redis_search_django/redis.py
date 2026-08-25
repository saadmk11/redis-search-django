"""Public redis-py names for package users.

Import these instead of ``redis`` / ``redis.commands``. Internals may still
talk to redis-py directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import TYPE_CHECKING, Any, cast

from redis.asyncio import Redis as AsyncRedisClient
from redis.commands.search import reducers
from redis.commands.search.aggregation import AggregateRequest
from redis.commands.search.query import Query
from redis.exceptions import ConnectionError as ConnectionError
from redis.exceptions import ResponseError, TimeoutError

from redis import Redis as RedisClient

if TYPE_CHECKING:
    # decode_responses=True — keys and values are str.
    Redis = RedisClient[str]
    AsyncRedis = AsyncRedisClient[str]
else:
    Redis = RedisClient
    AsyncRedis = AsyncRedisClient

SearchParams = Mapping[str, str | float]

__all__ = [
    "AggregateRequest",
    "AsyncRedis",
    "AsyncRedisClient",
    "ConnectionError",
    "Query",
    "Redis",
    "RedisClient",
    "ResponseError",
    "SearchParams",
    "TimeoutError",
    "aggregate_dialect",
    "aggregate_query",
    "hash_fields",
    "query_dialect",
    "reducers",
    "search_params",
    "wait_redis",
]


def query_dialect(query: Query, dialect: int) -> Query:
    """``Query.dialect`` exists at runtime; types-redis omits it."""
    applied: Query = query.dialect(dialect)  # type: ignore[attr-defined]
    return applied


def aggregate_dialect(request: AggregateRequest, dialect: int) -> AggregateRequest:
    applied: AggregateRequest = request.dialect(dialect)  # type: ignore[attr-defined]
    return applied


def aggregate_query(request: AggregateRequest) -> str:
    return str(getattr(request, "_query", ""))


def search_params(params: Mapping[str, object] | None) -> SearchParams | None:
    """Narrow PARAMS for redis-py stubs (int/bytes are valid at runtime)."""
    if params is None:
        return None
    return cast(SearchParams, params)


def hash_fields(
    mapping: Mapping[str, Any],
) -> Mapping[str | bytes, bytes | float | int | str]:
    return cast(Mapping[str | bytes, bytes | float | int | str], mapping)


def wait_redis(value: object) -> Awaitable[Any]:
    """Async redis-py search helpers are typed as sync in types-redis."""
    return cast(Awaitable[Any], value)
