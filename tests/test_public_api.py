from __future__ import annotations

import redis_search_django as rsd
from redis_search_django import (
    Aggregate,
    AggregateRequest,
    AsyncRedis,
    Document,
    Embedder,
    IndexAction,
    Lookup,
    Q,
    Redis,
    Storage,
    aapply_index_action,
    apply_index_action,
    document_registry,
    fields,
    get_async_redis_connection,
    get_redis_connection,
    reducers,
)
from redis_search_django.redis import Query, ResponseError


def test_package_reexports_match_all():
    assert set(rsd.__all__) == {
        "Aggregate",
        "AggregateRequest",
        "AsyncRedis",
        "Document",
        "Embedder",
        "IndexAction",
        "Lookup",
        "Q",
        "Redis",
        "Storage",
        "__version__",
        "aapply_index_action",
        "apply_index_action",
        "document_registry",
        "fields",
        "get_async_redis_connection",
        "get_redis_connection",
        "reducers",
    }
    assert rsd.Redis is Redis
    assert rsd.AsyncRedis is AsyncRedis
    assert rsd.Document is Document
    assert rsd.Q is Q
    assert rsd.Aggregate is Aggregate
    assert rsd.AggregateRequest is AggregateRequest
    assert rsd.document_registry is document_registry
    assert rsd.apply_index_action is apply_index_action
    assert rsd.aapply_index_action is aapply_index_action
    assert rsd.get_redis_connection is get_redis_connection
    assert rsd.get_async_redis_connection is get_async_redis_connection
    assert rsd.fields is fields
    assert rsd.Embedder is Embedder
    assert IndexAction.UPSERT == "upsert"
    assert Lookup.ISNULL == "isnull"
    assert Storage.JSON == "json"
    assert callable(reducers.count)
    assert issubclass(ResponseError, Exception)
    assert Query is not rsd.Q
