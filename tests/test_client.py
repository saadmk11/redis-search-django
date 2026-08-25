from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from redis_search_django.client import (
    get_async_redis_connection,
    get_redis_connection,
    reset_async_connection_cache,
    reset_connection_cache,
)
from redis_search_django.redis import AsyncRedis, Redis


def test_cached_connection_reused_in_same_process():
    reset_connection_cache()
    first = get_redis_connection()
    second = get_redis_connection()
    assert first is second
    reset_connection_cache()


def test_uncached_connection_is_distinct():
    reset_connection_cache()
    cached = get_redis_connection()
    fresh = get_redis_connection(use_cache=False)
    assert fresh is not cached
    close = getattr(fresh, "close", None)
    if callable(close):
        close()
    reset_connection_cache()


def test_sync_client_is_reused_after_pid_change():
    """The sync cache is process-local; redis-py's pool resets sockets after fork."""
    reset_connection_cache()
    parent = get_redis_connection()
    with mock.patch("redis_search_django.client.os.getpid", return_value=-1):
        assert get_redis_connection() is parent
    reset_connection_cache()


def test_reset_closes_cached_client():
    reset_connection_cache()
    client = get_redis_connection()
    with mock.patch.object(client, "close") as close:
        reset_connection_cache()
        close.assert_called_once()
    assert get_redis_connection() is not client
    reset_connection_cache()


def test_connection_factory_must_return_sync_redis(settings):
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "CONNECTION": lambda: object(),
    }
    with pytest.raises(TypeError, match="CONNECTION"):
        get_redis_connection(use_cache=False)


def test_unknown_setting_object_is_returned_raw(settings):
    from redis_search_django.conf import redis_search_setting, setting_str

    sentinel = object()
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "PREFIX": sentinel}
    assert redis_search_setting("PREFIX") is sentinel
    with pytest.raises(TypeError, match="PREFIX"):
        setting_str("PREFIX")


def test_connection_settings_must_have_expected_types(settings):
    from redis_search_django.conf import setting_bool, setting_int, setting_str

    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "PREFIX": 1}
    with pytest.raises(TypeError, match="PREFIX"):
        setting_str("PREFIX")
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": "big"}
    with pytest.raises(TypeError, match="CHUNK_SIZE"):
        setting_int("CHUNK_SIZE")
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "AUTO_INDEX": "yes"}
    with pytest.raises(TypeError, match="AUTO_INDEX"):
        setting_bool("AUTO_INDEX")
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "URL": 123, "CONNECTION": None}
    with pytest.raises(TypeError, match="URL"):
        get_redis_connection(use_cache=False)
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "URL": "redis://localhost:6379/0",
        "CONNECTION": 42,
    }
    with pytest.raises(TypeError, match="CONNECTION"):
        get_redis_connection(use_cache=False)
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "ASYNC_CONNECTION": 42,
    }
    with pytest.raises(TypeError, match="ASYNC_CONNECTION"):
        get_async_redis_connection(use_cache=False)
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "CONNECTION": None,
        "SOCKET_TIMEOUT": "slow",
    }
    leftover = get_redis_connection(use_cache=False)
    assert leftover is not None
    leftover.close()


async def test_async_connection_without_running_loop():
    """Cache still works when the lookup reports no running loop."""
    await reset_async_connection_cache()
    with (
        mock.patch("redis_search_django.client._running_loop", return_value=None),
        mock.patch("redis_search_django.client._running_loop_id", return_value=None),
    ):
        client = get_async_redis_connection()
    assert isinstance(client, AsyncRedis)
    await reset_async_connection_cache()


async def test_async_cached_connection_reused_on_same_loop():
    await reset_async_connection_cache()
    first = get_async_redis_connection()
    second = get_async_redis_connection()
    assert first is second
    assert isinstance(first, AsyncRedis)
    await reset_async_connection_cache()


async def test_async_uncached_connection_is_distinct():
    await reset_async_connection_cache()
    cached = get_async_redis_connection()
    fresh = get_async_redis_connection(use_cache=False)
    assert fresh is not cached
    await fresh.aclose()
    await reset_async_connection_cache()


async def test_async_fork_pid_change_builds_new_client():
    await reset_async_connection_cache()
    parent = get_async_redis_connection()
    with mock.patch("redis_search_django.client.os.getpid", return_value=-1):
        child = get_async_redis_connection()
        assert child is not parent
    await reset_async_connection_cache()


async def test_async_loop_change_builds_new_client():
    await reset_async_connection_cache()
    first = get_async_redis_connection()
    with mock.patch("redis_search_django.client._running_loop_id", return_value=-1):
        second = get_async_redis_connection()
        assert second is not first
    await reset_async_connection_cache()


async def test_reset_async_closes_cached_client():
    await reset_async_connection_cache()
    client = get_async_redis_connection()
    with mock.patch.object(client, "aclose", wraps=client.aclose) as aclose:
        await reset_async_connection_cache()
        aclose.assert_called_once()
    assert get_async_redis_connection() is not client
    await reset_async_connection_cache()


def test_async_connection_factory_must_return_async_redis(settings):
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "ASYNC_CONNECTION": lambda: Redis.from_url(
            "redis://localhost:6379/0", decode_responses=True
        ),
    }
    with pytest.raises(TypeError, match="ASYNC_CONNECTION"):
        get_async_redis_connection(use_cache=False)


async def test_async_connection_factory_callable(settings):
    created: list[AsyncRedis] = []

    def factory() -> AsyncRedis:
        client = AsyncRedis.from_url("redis://localhost:6379/0", decode_responses=True)
        created.append(client)
        return client

    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "ASYNC_CONNECTION": factory,
    }
    client = get_async_redis_connection(use_cache=False)
    assert client is created[0]
    assert isinstance(client, AsyncRedis)
    await client.aclose()


def test_connection_factory_dotted_path(settings):
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "CONNECTION": "tests.helpers.make_sync_client",
        "ASYNC_CONNECTION": "tests.helpers.make_async_client",
    }
    sync_client = get_redis_connection(use_cache=False)
    assert isinstance(sync_client, Redis)
    sync_client.close()


async def test_async_connection_factory_dotted_path(settings):
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "ASYNC_CONNECTION": "tests.helpers.make_async_client",
    }
    async_client = get_async_redis_connection(use_cache=False)
    assert isinstance(async_client, AsyncRedis)
    await async_client.aclose()


async def test_reset_async_swallows_second_aclose():
    await reset_async_connection_cache()
    client = get_async_redis_connection()
    await client.aclose()
    await reset_async_connection_cache()
    assert get_async_redis_connection() is not client
    await reset_async_connection_cache()


async def test_reset_async_swallows_aclose_errors():
    from redis_search_django import client as client_mod

    class Broken:
        async def aclose(self):
            raise RuntimeError("closed")

    client_mod._async_clients[(0, 0)] = Broken()
    await reset_async_connection_cache()
    assert client_mod._async_clients == {}


def test_running_loop_helpers_without_event_loop():
    import threading

    from redis_search_django.client import _running_loop, _running_loop_id

    seen: dict[str, object] = {}

    def worker() -> None:
        seen["loop"] = _running_loop()
        seen["id"] = _running_loop_id()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen["loop"] is None
    assert seen["id"] is None


async def test_reset_async_when_get_running_loop_fails():
    get_async_redis_connection()
    with mock.patch(
        "redis_search_django.client.asyncio.get_running_loop",
        side_effect=RuntimeError,
    ):
        await reset_async_connection_cache()
    from redis_search_django import client as client_mod

    assert client_mod._async_clients == {}


async def test_reset_async_skips_client_without_aclose():
    from redis_search_django import client as client_mod

    class Mute:
        aclose = None

    client_mod._async_clients[(1, 1)] = Mute()
    await reset_async_connection_cache()
    assert client_mod._async_clients == {}


async def test_loop_close_swallows_runtimeerror_while_closing_clients():
    from redis_search_django import client as client_mod

    loop = asyncio.new_event_loop()
    client = get_async_redis_connection(use_cache=False)
    key = (0, id(loop))
    client_mod._async_clients[key] = client
    client_mod._watch_loop(loop, key)

    def boom(coro: object) -> None:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError

    with mock.patch.object(loop, "run_until_complete", side_effect=boom):
        loop.close()
    assert key not in client_mod._async_clients
    await client.aclose()


def test_cached_async_client_closes_when_loop_closes():
    from redis_search_django import client as client_mod

    from .helpers import require_redis

    require_redis()
    loop = asyncio.new_event_loop()

    async def ping() -> int:
        client = get_async_redis_connection()
        await client.ping()
        return id(client)

    try:
        client_id = loop.run_until_complete(ping())
        assert any(id(item) == client_id for item in client_mod._async_clients.values())
    finally:
        loop.close()
    assert all(id(item) != client_id for item in client_mod._async_clients.values())
