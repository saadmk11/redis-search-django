from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from functools import cache

import pytest

from redis_search_django.client import get_redis_connection
from redis_search_django.documents import Document
from redis_search_django.index import IndexManager
from redis_search_django.redis import AsyncRedis, Redis, ResponseError
from redis_search_django.redis import ConnectionError as RedisConnectionError

_COLOR_VECTORS = {
    "red": [1.0, 0.0, 0.0, 0.0],
    "crimson": [0.95, 0.05, 0.0, 0.0],
    "blue": [0.0, 1.0, 0.0, 0.0],
    "navy": [0.0, 0.9, 0.1, 0.0],
}

NOT_AN_EMBEDDER = 42


def color_embed(value: str) -> list[float]:
    """Deterministic 4-d stand-in so similar color words sit near each other."""
    vec = [0.0, 0.0, 0.0, 0.0]
    for word in str(value).lower().split():
        part = _COLOR_VECTORS.get(word)
        if part:
            vec = [a + b for a, b in zip(vec, part, strict=True)]
    return vec


def make_sync_client() -> Redis:
    return Redis.from_url("redis://localhost:6379/0", decode_responses=True)


def make_async_client() -> AsyncRedis:
    return AsyncRedis.from_url("redis://localhost:6379/0", decode_responses=True)


@cache
def is_redis_running() -> bool:
    """Return True if Redis with Query Engine is reachable."""
    client = None
    try:
        client = get_redis_connection(use_cache=False)
        client.ping()
        try:
            client.execute_command("FT._LIST")
        except ResponseError:
            return False
        return True
    except (RedisConnectionError, OSError):
        return False
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def require_redis() -> None:
    if not is_redis_running():
        pytest.skip("Redis is not running")


@contextmanager
def live_index(document_cls: type[Document]) -> Generator[IndexManager, None, None]:
    require_redis()
    manager = IndexManager(document_cls)
    manager.create()
    try:
        yield manager
    finally:
        manager.drop(delete_docs=True)


@asynccontextmanager
async def alive_index(
    document_cls: type[Document],
) -> AsyncGenerator[IndexManager, None]:
    require_redis()
    manager = IndexManager(document_cls)
    await manager.acreate()
    try:
        yield manager
    finally:
        await manager.adrop(delete_docs=True)
