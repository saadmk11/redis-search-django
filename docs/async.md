---
icon: lucide/zap
---

# Async

Django 5.2+ has a first-class async ORM (`aget`, `acount`, `aiterator`). This
package mirrors that pattern: **the same** `DocumentQuerySet` / `Indexer` /
`IndexManager` classes grow `a`-prefixed methods, and Redis I/O goes through
`redis.asyncio` rather than wrapping the sync client in a thread.

Works on every version this package supports: **Django 5.2, 6.0, and 6.1**.

## What is async and what stays sync

Filter construction is lazy and **synchronous**, just like Django:

``` python
qs = ProductDocument.objects.filter(name__search="shoes").order_by("-price")[:20]
```

`filter` / `exclude` / `search` / `order_by` / slices / `raw()` never touch
Redis. Only **evaluation** hits Redis, and that is where the `a` methods live.

| Sync (blocking Redis) | Async (`redis.asyncio`) |
| --- | --- |
| `count()` / `len(qs)` | `await qs.acount()` |
| `exists()` / `bool(qs)` | `await qs.aexists()` |
| `get()` | `await qs.aget()` |
| `objects.get(pk=)` | `await objects.aget(pk=)` |
| `for hit in qs` | `async for hit in qs` |
| `to_queryset()` | `await qs.ato_queryset()` |
| `facets()` | `await qs.afacets()` |
| `aggregate()` | `await qs.aaggregate()` |
| `explain()` | `await qs.aexplain()` |
| `Indexer().upsert` / `populate` / `rebuild` | `aupsert` / `apopulate` / `arebuild` |
| `apply_index_action` | `aapply_index_action` |
| `Document.index_all()` | `await Document.aindex_all()` |

`__bool__` and `__len__` **cannot** be async (Python protocols). In async
code use `aexists()` / `acount()`. Do not write `if qs:` inside a coroutine
unless you intend to block.

Django model hooks (`should_index`, `prepare_*`, `get_queryset`,
`get_instances_from_related`) stay synchronous. The async indexer runs them
with `sync_to_async` so accidental ORM lazy-loads do not raise
`SynchronousOnlyOperation`. Redis writes still use `redis.asyncio`.

Signals stay synchronous. Django's `post_save` / `post_delete` / `m2m_changed`
are sync receivers. Queue a worker and call `aapply_index_action` there if
the worker itself is async.

## Query

``` python
from redis_search_django import Q
from shop.documents import ProductDocument

qs = (
    ProductDocument.objects.filter(
        Q(name__search="shoes") | Q(description__search="shoes"),
        price__lte=150,
        available=True,
    )
    .exclude(tags__name="discontinued")
    .order_by("-price")
)

total = await qs.acount()
exists = await qs.aexists()
hit = await ProductDocument.objects.aget(pk=42)

async for hit in qs[:20]:
    hit.pk, hit.score, hit.name

products = await qs[:20].ato_queryset()
async for product in products:
    product.vendor.name
```

`ato_queryset()` does the Redis search asynchronously, then returns a normal
Django `QuerySet` (construction is not I/O). Evaluate that queryset with
Django's async ORM (`async for`, `afirst`, `aget`) or pass it to existing
sync helpers via `sync_to_async`.

Facets and aggregates:

``` python
from redis_search_django import Aggregate

facets = await ProductDocument.objects.filter(name__search="shoes").afacets(
    "category__name",
    "tags__name",
)

rows = await ProductDocument.objects.filter(available=True).aaggregate(
    Aggregate().group_by("vendor__name").count("count").avg("price", "avg_price")
)
```

`raw()` is still sync (no I/O). `aexplain()` is the async `FT.EXPLAIN`.

## Pagination and `ato_queryset()`

Stock Django `Paginator` calls `count()`, which is sync. In an async view
use `acount()` (the mixin below does this) or paginate yourself:

``` python
qs = ProductDocument.objects.filter(name__search=q)
total = await qs.acount()
page = qs[(n - 1) * 20 : n * 20]
products = await page.ato_queryset()
```

`TO_QUERYSET_WARN` / `TO_QUERYSET_MAX` apply to `ato_queryset()` the same way.

## Views

There is no separate async mixin. Django has one `ListView`; this package
has one `SearchListViewMixin`. `get()` stays synchronous. Wire the async
path the same way Django does — write `async def get` and call `aget()`:

``` python
from django.views.generic import ListView

from redis_search_django import Q
from redis_search_django.views import SearchListViewMixin

from .documents import ProductDocument
from .models import Product


class SearchView(SearchListViewMixin, ListView):
    paginate_by = 20
    model = Product
    template_name = "shop/search.html"
    document_class = ProductDocument

    async def get(self, request, *args, **kwargs):
        return await self.aget(request, *args, **kwargs)

    def get_search_queryset(self):
        qs = ProductDocument.objects.all()
        query = self.request.GET.get("query")
        if query:
            qs = qs.filter(Q(name__search=query) | Q(description__search=query))
        return qs

    def facets(self):
        return self.get_search_queryset().facets("category__name", "tags__name")

    async def afacets(self):
        return await self.get_search_queryset().afacets(
            "category__name", "tags__name"
        )
```

`get_search_queryset()` stays sync. `aget()` uses `acount` / `aexists` /
`ato_queryset` / `afacets` and paginates with the same `?page=` / `last` /
`Http404` contract as `MultipleObjectMixin`. Override `afacets()` if you
overrode `facets()` — the default `afacets` does not call `facets()`.

Serve with ASGI (`example.asgi:application`, uvicorn/daphne). Django’s
`runserver` can run async views, but it still uses a thread per request.

## Indexing

``` python
from redis_search_django.indexer import Indexer
from redis_search_django.index import IndexManager

indexer = Indexer()
await indexer.aupsert(ProductDocument, product)
await indexer.adelete(ProductDocument, product.pk)
await indexer.apopulate(ProductDocument)
await indexer.arebuild(ProductDocument)
await indexer.areindex(ProductDocument)
report = await indexer.averify(ProductDocument, repair=True)

manager = IndexManager(ProductDocument)
await manager.acreate()
await manager.aexists()
info = await manager.ainfo()
await manager.adrop(delete_docs=True)

await ProductDocument.aindex_all()
```

`redisearch` management commands stay synchronous. Use them from the CLI
(see [Indexing](indexing.md)); use `apopulate` / `arebuild` / `areindex` /
`averify` from async application code.

## Workers (`aapply_index_action`)

Same JSON payload as `apply_index_action`. Loads the Django row with
`aget()` and writes Redis with `redis.asyncio`.

``` python
from redis_search_django import aapply_index_action

await aapply_index_action("upsert", {"document": "shop.ProductDocument", "pk": 42})
```

A signal processor still **dispatches synchronously**. If the worker is
async (an ASGI background task, an asyncio consumer), have `dispatch`
enqueue the payload and call `aapply_index_action` in the worker.

## Connection

``` python
from redis_search_django import AsyncRedis, get_async_redis_connection

client = get_async_redis_connection()  # one client per process + event loop
```

`REDIS_SEARCH["CONNECTION"]` is the **sync** factory only. Do not return an
`AsyncRedis` from it. For a custom async client:

``` python
from redis_search_django import AsyncRedis


def get_async_search_client() -> AsyncRedis:
    return AsyncRedis.from_url(
        "redis://search-replica:6379/0",
        decode_responses=True,
    )


# REDIS_SEARCH = {"ASYNC_CONNECTION": "myproject.search.get_async_search_client"}
```

The sync client is a process-local singleton. redis-py's sync pool is
fork-safe. The asyncio pool is not, and its lock is loop-bound, so one async
client is cached per `(pid, event loop)`.

`reset_connection_cache()` closes the sync client. Tests that use the async
client should also `await reset_async_connection_cache()`.
