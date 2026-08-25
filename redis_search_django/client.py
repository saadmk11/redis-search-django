from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from django.utils.module_loading import import_string

from .conf import redis_search_setting
from .redis import AsyncRedis, Redis
from .types import (
    AsyncConnectionFactory,
    ConnectionFactory,
    DocumentPayload,
    HashMapping,
)

# Sync: one process-local client. redis-py's pool is the concurrency
# boundary after init. A lock makes first-use safe without the GIL.
# Async: redis.asyncio has no fork check and its Lock is loop-bound, so
# cache one client per (pid, loop). Same loop's tasks share the pool;
# another thread's loop must not overwrite it.
_client: Redis | None = None
_client_lock = threading.Lock()
_async_clients: dict[tuple[int, int | None], AsyncRedis] = {}
_async_lock = threading.Lock()


def get_redis_connection(*, use_cache: bool = True) -> Redis:
    """Return a redis-py client with ``decode_responses=True``."""
    if not use_cache:
        return _build_connection()

    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = _build_connection()
        return _client


def get_async_redis_connection(*, use_cache: bool = True) -> AsyncRedis:
    """Return a redis-py asyncio client with ``decode_responses=True``."""
    if not use_cache:
        return _build_async_connection()

    loop = _running_loop()
    key = (os.getpid(), _running_loop_id())
    client = _async_clients.get(key)
    if client is not None:
        return client
    with _async_lock:
        client = _async_clients.get(key)
        if client is None:
            client = _build_async_connection()
            _async_clients[key] = client
            if loop is not None:
                _watch_loop(loop, key)
        return client


def reset_connection_cache() -> None:
    """Drop and close the cached sync client (tests)."""
    global _client
    with _client_lock:
        client = _client
        _client = None
    if client is not None:
        client.close()


async def reset_async_connection_cache() -> None:
    """Drop and close cached async clients (tests)."""
    with _async_lock:
        clients = list(_async_clients.values())
        _async_clients.clear()
        try:
            loop: Any = asyncio.get_running_loop()
            keys = getattr(loop, "_rsd_async_keys", None)
            if isinstance(keys, set):
                keys.clear()
        except RuntimeError:
            pass
    await _aclose_clients(clients)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _running_loop_id() -> int | None:
    loop = _running_loop()
    return None if loop is None else id(loop)


async def _aclose_client(client: AsyncRedis) -> None:
    aclose = getattr(client, "aclose", None)
    if not callable(aclose):
        return
    try:
        await aclose(close_connection_pool=True)
    except TypeError:
        await aclose()


async def _aclose_clients(clients: list[AsyncRedis]) -> None:
    for client in clients:
        try:
            await _aclose_client(client)
        except Exception:
            pass


def _watch_loop(loop: asyncio.AbstractEventLoop, key: tuple[int, int | None]) -> None:
    """Close cached clients for *loop* when the loop itself is closed."""
    tagged: Any = loop
    watched: set[tuple[int, int | None]] | None = getattr(
        tagged, "_rsd_async_keys", None
    )
    if watched is None:
        watched = set()
        tagged._rsd_async_keys = watched
        original_close = loop.close

        def close() -> None:
            with _async_lock:
                leftover = [
                    _async_clients.pop(item, None)
                    for item in list(getattr(tagged, "_rsd_async_keys", ()))
                ]
                tagged._rsd_async_keys = set()
            pending = [client for client in leftover if client is not None]
            if pending and not loop.is_closed():
                try:
                    loop.run_until_complete(_aclose_clients(pending))
                except RuntimeError:
                    pass
            original_close()

        tagged.close = close
    watched.add(key)


def _socket_timeout() -> float | None:
    timeout = redis_search_setting("SOCKET_TIMEOUT")
    if isinstance(timeout, (int, float)):
        return float(timeout)
    return None


def _redis_url() -> str:
    url = redis_search_setting("URL")
    if not isinstance(url, str):
        raise TypeError("REDIS_SEARCH['URL'] must be a string.")
    return url


def _build_connection() -> Redis:
    factory_path = redis_search_setting("CONNECTION")
    if factory_path:
        factory: ConnectionFactory
        if isinstance(factory_path, str):
            loaded: ConnectionFactory = import_string(factory_path)
            factory = loaded
        elif isinstance(factory_path, ConnectionFactory):
            factory = factory_path
        else:
            raise TypeError("REDIS_SEARCH['CONNECTION'] must be a callable or path.")
        client = factory()
        if not isinstance(client, Redis):
            raise TypeError(
                "REDIS_SEARCH['CONNECTION'] must return redis_search_django.Redis"
            )
        return client

    timeout = _socket_timeout()
    return Redis.from_url(
        _redis_url(),
        decode_responses=True,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
    )


def _build_async_connection() -> AsyncRedis:
    factory_path = redis_search_setting("ASYNC_CONNECTION")
    if factory_path:
        factory: AsyncConnectionFactory
        if isinstance(factory_path, str):
            loaded: AsyncConnectionFactory = import_string(factory_path)
            factory = loaded
        elif isinstance(factory_path, AsyncConnectionFactory):
            factory = factory_path
        else:
            raise TypeError(
                "REDIS_SEARCH['ASYNC_CONNECTION'] must be a callable or path."
            )
        client = factory()
        if not isinstance(client, AsyncRedis):
            raise TypeError(
                "REDIS_SEARCH['ASYNC_CONNECTION'] must return "
                "redis_search_django.AsyncRedis"
            )
        return client

    timeout = _socket_timeout()
    return AsyncRedis.from_url(
        _redis_url(),
        decode_responses=True,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
    )


def json_set(client: Any, key: str, payload: DocumentPayload, path: str = ".") -> None:
    """JSON.SET. ``client`` is redis-py or a pipeline (stubs mix sync/async)."""
    json_commands: Any = client.json()
    json_commands.set(key, path, payload)


async def json_aset(
    client: Any, key: str, payload: DocumentPayload, path: str = "."
) -> None:
    json_commands: Any = client.json()
    await json_commands.set(key, path, payload)


def json_get(client: Any, key: str) -> DocumentPayload | None:
    json_commands: Any = client.json()
    result: DocumentPayload | None = json_commands.get(key)
    if not result:
        return None
    return result


async def json_aget(client: Any, key: str) -> DocumentPayload | None:
    json_commands: Any = client.json()
    result: DocumentPayload | None = await json_commands.get(key)
    if not result:
        return None
    return result


async def hash_aset(client: Any, key: str, mapping: HashMapping) -> None:
    await client.hset(key, mapping=mapping)
