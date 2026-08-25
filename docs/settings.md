---
icon: lucide/settings
---

# Settings

One dict. There is no `REDIS_OM_URL` and no `REDIS_SEARCH_AUTO_INDEX`.

``` python title="settings.py"
REDIS_SEARCH = {
    "URL": "redis://localhost:6379/0",
    "PREFIX": "rsd",
    "AUTO_INDEX": True,
    "DIALECT": 2,
    "DEFAULT_STORAGE": "json",
    "CHUNK_SIZE": 2000,
    "SOCKET_TIMEOUT": 5,
    "SIGNAL_PROCESSOR": "redis_search_django.signals.RealtimeSignalProcessor",
    "SIGNAL_ERRORS": "raise",  # or "log"
    "TO_QUERYSET_WARN": 1000,
    "TO_QUERYSET_MAX": 5000,
    "CONNECTION": None,
    "ASYNC_CONNECTION": None,
}
```

| Key | Default | Purpose |
| --- | --- | --- |
| `URL` | `redis://localhost:6379/0` | redis-py URL |
| `PREFIX` | `rsd` | Key / meta prefix |
| `AUTO_INDEX` | `True` | Run `SignalProcessor.setup()` |
| `DIALECT` | `2` | Query dialect |
| `DEFAULT_STORAGE` | `json` | When `Index.storage` is omitted |
| `CHUNK_SIZE` | `2000` | Bulk pipeline and unsliced iteration |
| `SOCKET_TIMEOUT` | `5` | Connect / socket timeout |
| `SIGNAL_PROCESSOR` | `…RealtimeSignalProcessor` | Dotted path — see [signals](signals.md) |
| `SIGNAL_ERRORS` | `raise` | `raise` or `log` |
| `TO_QUERYSET_WARN` | `1000` | Warn when `to_queryset()` loads this many pks |
| `TO_QUERYSET_MAX` | `5000` | Raise above this (`0` disables) |
| `CONNECTION` | `None` | Factory returning `redis_search_django.Redis(decode_responses=True)` |
| `ASYNC_CONNECTION` | `None` | Factory returning `redis_search_django.AsyncRedis(decode_responses=True)` |

`REDIS_OM_URL` and `REDIS_SEARCH_AUTO_INDEX` are ignored. If `REDIS_OM_URL` is
set, the app logs a one-time warning.

Optional development overlay: add `redis_search_django.debug` and
`SearchDebugMixin` / `@search_debug` — [debug overlay](debug.md). That app
uses a separate `REDIS_SEARCH_DEBUG` dict.

## Redis client types

Do not import the `redis` package in application code. Types you need are
re-exported:

``` python
from redis_search_django import (
    Aggregate,
    AggregateRequest,
    AsyncRedis,
    Redis,
    get_async_redis_connection,
    get_redis_connection,
    reducers,
)
from redis_search_django.redis import Query, ResponseError, ConnectionError
```

Custom connection (replica, TLS, Sentinel, …):

``` python
from redis_search_django import AsyncRedis, Redis


def get_search_client() -> Redis:
    return Redis.from_url("redis://search-replica:6379/0", decode_responses=True)


def get_async_search_client() -> AsyncRedis:
    return AsyncRedis.from_url(
        "redis://search-replica:6379/0", decode_responses=True
    )


REDIS_SEARCH = {
    "CONNECTION": "myproject.search.get_search_client",
    "ASYNC_CONNECTION": "myproject.search.get_async_search_client",
}
```

`REDIS_SEARCH["CONNECTION"]` is the **sync** factory only. Do not return an
`AsyncRedis` from it.

The sync client is a process-local singleton. The asyncio pool is not
fork-safe, so one async client is cached per `(pid, event loop)`.
`reset_connection_cache()` closes the sync client. Tests that use the async
client should also `await reset_async_connection_cache()`.

## What is not indexed automatically

These ORM paths do **not** fire `post_save` / `post_delete`:

- `QuerySet.update()`
- `bulk_create()` / `bulk_update()`
- `QuerySet.delete()`

Heal with `redisearch populate`. Step-by-step:
[Indexing — after a bulk import](indexing.md#workflows).

Documents imported **after** `AppConfig.ready()` are registered but **not**
connected to signals. Call `processor.setup()` or `connect_document(cls)`
(tests do this).
