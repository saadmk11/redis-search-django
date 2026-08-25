"""Concurrency tests for the Redis client cache.

- Sync: first-use publishes one process-local client; redis-py's pool is
  the concurrency boundary after that.
- Async: tasks on one loop share a client; another thread's loop gets its
  own (asyncio locks are loop-bound).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from redis_search_django.client import (
    get_async_redis_connection,
    get_redis_connection,
    reset_async_connection_cache,
    reset_connection_cache,
)
from redis_search_django.redis import AsyncRedis, Redis

from .helpers import is_redis_running

_LIVE = pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")


def _unique_key(kind: str) -> str:
    return f"rsd:test:conc:{kind}:{uuid.uuid4().hex}"


def test_sync_stampede_returns_usable_clients():
    """First-use from many threads must publish one cached client."""
    reset_connection_cache()
    n = 8
    barrier = threading.Barrier(n)
    seen: dict[int, Redis] = {}

    def worker() -> None:
        barrier.wait()
        seen[threading.get_ident()] = get_redis_connection()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == n
    clients = list(seen.values())
    assert all(isinstance(client, Redis) for client in clients)
    assert len({id(client) for client in clients}) == 1
    assert get_redis_connection() is clients[0]
    reset_connection_cache()


def test_sync_double_check_reuses_winner(monkeypatch):
    """A waiter that lost the first-use race must take the published client."""
    from redis_search_django import client as client_mod

    reset_connection_cache()
    entered = threading.Event()
    release = threading.Event()
    built: list[Redis] = []

    def slow_build() -> Redis:
        client = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        built.append(client)
        entered.set()
        release.wait(timeout=2)
        return client

    monkeypatch.setattr(client_mod, "_build_connection", slow_build)
    first: list[Redis] = []
    second: list[Redis] = []

    def starter() -> None:
        first.append(get_redis_connection())

    def waiter() -> None:
        entered.wait(timeout=2)
        second.append(get_redis_connection())

    t1 = threading.Thread(target=starter)
    t2 = threading.Thread(target=waiter)
    t1.start()
    assert entered.wait(timeout=2)
    t2.start()
    t2.join(timeout=0.1)
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert first and second
    assert first[0] is second[0]
    assert len(built) == 1
    reset_connection_cache()


@_LIVE
def test_shared_sync_client_thread_pool_incr():
    """One cached client, many threads: INCR is atomic via redis-py's pool."""
    reset_connection_cache()
    key = _unique_key("incr")
    client = get_redis_connection()
    client.delete(key)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: get_redis_connection().incr(key), range(50)))
        assert int(client.get(key)) == 50
    finally:
        client.delete(key)
        reset_connection_cache()


@_LIVE
def test_shared_sync_client_threads_do_not_crosstalk():
    reset_connection_cache()
    prefix = _unique_key("iso")
    n = 8

    def write(i: int) -> str:
        key = f"{prefix}:{i}"
        get_redis_connection().set(key, str(i))
        return get_redis_connection().get(key)

    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            values = list(pool.map(write, range(n)))
        assert values == [str(i) for i in range(n)]
    finally:
        client = get_redis_connection()
        client.delete(*[f"{prefix}:{i}" for i in range(n)])
        reset_connection_cache()


async def test_async_tasks_on_one_loop_share_client():
    await reset_async_connection_cache()

    async def grab() -> AsyncRedis:
        return get_async_redis_connection()

    clients = await asyncio.gather(*[grab() for _ in range(16)])
    assert len({id(client) for client in clients}) == 1
    await reset_async_connection_cache()


@_LIVE
async def test_shared_async_client_gather_incr():
    await reset_async_connection_cache()
    key = _unique_key("aincr")
    client = get_async_redis_connection()
    await client.delete(key)
    try:

        async def incr() -> None:
            await get_async_redis_connection().incr(key)

        await asyncio.gather(*[incr() for _ in range(50)])
        assert int(await client.get(key)) == 50
    finally:
        await client.delete(key)
        await reset_async_connection_cache()


def test_async_stampede_without_running_loop_is_single_client():
    """Threads with no running loop share key (pid, None); publish once."""
    asyncio.run(reset_async_connection_cache())
    n = 8
    barrier = threading.Barrier(n)
    seen: dict[int, AsyncRedis] = {}

    def worker() -> None:
        barrier.wait()
        seen[threading.get_ident()] = get_async_redis_connection()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == n
    clients = list(seen.values())
    assert all(isinstance(client, AsyncRedis) for client in clients)
    assert len({id(client) for client in clients}) == 1
    asyncio.run(reset_async_connection_cache())


def test_async_double_check_reuses_winner(monkeypatch):
    """A waiter on the same (pid, None) key must not build a second client."""
    from redis_search_django import client as client_mod

    asyncio.run(reset_async_connection_cache())
    entered = threading.Event()
    release = threading.Event()
    built: list[AsyncRedis] = []

    def slow_build() -> AsyncRedis:
        client = AsyncRedis.from_url("redis://localhost:6379/0", decode_responses=True)
        built.append(client)
        entered.set()
        release.wait(timeout=2)
        return client

    monkeypatch.setattr(client_mod, "_build_async_connection", slow_build)
    first: list[AsyncRedis] = []
    second: list[AsyncRedis] = []

    def starter() -> None:
        first.append(get_async_redis_connection())

    def waiter() -> None:
        entered.wait(timeout=2)
        second.append(get_async_redis_connection())

    t1 = threading.Thread(target=starter)
    t2 = threading.Thread(target=waiter)
    t1.start()
    assert entered.wait(timeout=2)
    t2.start()
    t2.join(timeout=0.1)
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert first and second
    assert first[0] is second[0]
    assert len(built) == 1
    asyncio.run(reset_async_connection_cache())


def test_async_client_is_stable_per_thread_event_loop():
    """A second thread with its own loop must not steal the first thread's client."""
    asyncio.run(reset_async_connection_cache())
    barrier = threading.Barrier(2)
    seen: dict[str, tuple[AsyncRedis, AsyncRedis]] = {}
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        async def main() -> None:
            first = get_async_redis_connection()
            barrier.wait()
            second = get_async_redis_connection()
            seen[name] = (first, second)

        try:
            asyncio.run(main())
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("a",)),
        threading.Thread(target=worker, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    first_a, second_a = seen["a"]
    first_b, second_b = seen["b"]
    assert first_a is second_a
    assert first_b is second_b
    assert first_a is not first_b
    asyncio.run(reset_async_connection_cache())
