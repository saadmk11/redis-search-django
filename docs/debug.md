---
icon: lucide/bug
---

# Debug overlay

Optional per-view overlay that records Redis Query Engine calls made through
`Document.objects` and shows query text, params, timing, hit counts, and
on-demand `FT.EXPLAIN`.

It is a **second Django app** in the same `redis-search-django` distribution.
Nothing is recorded unless you opt a view in. Production installs that only
add `redis_search_django` pay one `ContextVar` read per query.

!!! warning "Development only"

    Do **not** add the debug app in production.

## Install

``` python title="settings.py"
if DEBUG:
    INSTALLED_APPS += ["redis_search_django.debug"]
```

That is the only project-level change. There is **no middleware** and **no
URL include**. Then opt in on the views that run search:

=== "Class-based view"

    ``` python
    from redis_search_django.debug import SearchDebugMixin
    from redis_search_django.views import SearchListViewMixin


    class ProductSearch(SearchDebugMixin, SearchListViewMixin, ListView):
        document_class = ProductDocument
    ```

    Put `SearchDebugMixin` first so it wraps `dispatch`.

=== "Function view"

    ``` python
    from redis_search_django.debug import search_debug


    @search_debug
    def search(request):
        hits = ProductDocument.objects.search(request.GET.get("q", ""))[:20]
        return render(request, "search.html", {"hits": hits})
    ```

Both work with `async def` views. When `DEBUG` is False the mixin and
decorator are a pass-through: no listener, no HTML rewrite.

Open an opted-in HTML page. A pill in the bottom-left corner shows the
query count and total Redis time. Click it (or press ++alt+r++) to open the
drawer. ++escape++ closes it.

`FT.EXPLAIN` is fetched on demand against **the same view URL**
(`?_rsd_explain=…`). The view body does not run again.

## What it records

| Kind | Source |
| --- | --- |
| `search` | `FT.SEARCH` from evaluation (`count`, iteration, slices, `exists`, …) |
| `aggregate` | `FT.AGGREGATE` from `aggregate()` / `facets()` |
| `explain` | `explain()` / `aexplain()` if you call them yourself |
| `get` | `JSON.GET` / `HGETALL` from `objects.get(pk=)` |
| `write` | `JSON.SET` / `HSET` from `Indexer.upsert` / related reindex / a populate pipeline |
| `delete` | `DEL` from `Indexer.delete` (or `should_index` dropping a key) |

The pill and `X-RSD-Queries` count **this request only**. Writes carried
over from a POST-redirect stay in the Queries list as “previous request”
and are not included in that number.

Each row shows the reconstructed command, duration, hit count, `PARAMS`
(vectors and blobs are replaced with placeholders), the first project
frame that issued the call (Django / redis-py / this package’s query
layer are filtered out), duplicate / slow badges, and an **FT.EXPLAIN**
button. Set `STACKTRACES` to `False` to skip the stack walk.

JSON / AJAX responses from an opted-in view are not rewritten. They still get:

```
X-RSD-Queries: 3
X-RSD-Query-Time: 12.400
```

Views without the mixin or decorator are untouched.

## Settings

Optional dict, separate from `REDIS_SEARCH` so production config stays clean.

``` python
REDIS_SEARCH_DEBUG = {
    "SHOW_TOOLBAR": "redis_search_django.debug.conf.show_toolbar",
    "PANELS": [
        "redis_search_django.debug.panels.queries.QueriesPanel",
        "redis_search_django.debug.panels.indexes.IndexesPanel",
        "redis_search_django.debug.panels.config.ConfigPanel",
    ],
    "SLOW_MS": 10.0,
    "INSERT_BEFORE": "</body>",
    "STORE_SIZE": 25,
    "STACKTRACES": True,
}
```

| Key | Default | Purpose |
| --- | --- | --- |
| `SHOW_TOOLBAR` | `show_toolbar` | Callable or import path. Default: on when `DEBUG` is True. If `INTERNAL_IPS` is non-empty, the client address must also match. |
| `PANELS` | queries, indexes, config | Import paths of `Panel` subclasses. Add your own panel here. |
| `SLOW_MS` | `10.0` | Duration at or above this is marked slow. |
| `INSERT_BEFORE` | `</body>` | Case-insensitive marker the overlay is inserted before. |
| `STORE_SIZE` | `25` | In-memory LRU of request traces used by Explain. |
| `STACKTRACES` | `True` | Record a filtered call stack (first project frame) on each query. |

`SHOW_TOOLBAR` can be a function:

``` python
def show_rsd(request):
    return request.user.is_superuser

REDIS_SEARCH_DEBUG = {"SHOW_TOOLBAR": show_rsd}
```

## Adding a panel

``` python title="myapp/rsd_panels.py"
from redis_search_django.debug.panels.base import Panel


class CachePanel(Panel):
    title = "Cache"
    panel_id = "cache"
    template = "myapp/rsd_cache.html"

    def nav_subtitle(self) -> str:
        return str(self.stats.get("count", 0))

    def generate_stats(self, request, response) -> None:
        self.stats = {"count": 0}
```

``` python
REDIS_SEARCH_DEBUG = {
    "PANELS": [
        "redis_search_django.debug.panels.queries.QueriesPanel",
        "redis_search_django.debug.panels.indexes.IndexesPanel",
        "redis_search_django.debug.panels.config.ConfigPanel",
        "myapp.rsd_panels.CachePanel",
    ]
}
```

`generate_stats` runs after the view. The overlay catches panel errors and
shows them in that tab.

## Recording queries in tests

``` python
from redis_search_django.query.instrument import capture_queries

with capture_queries() as collector:
    list(ProductDocument.objects.search("shoes")[:10])

assert collector.events[0].kind == "search"
assert collector.events[0].duration_ms >= 0
```

Without a listener (the default) recording is a no-op.

## What it does not do

- It does not wrap the whole site. Catalog / admin / other views stay clean
  unless you add the mixin or decorator.
- `FT.CREATE` / `FT.ALTER` / `FT.DROPINDEX` from `redisearch` management
  commands are not listed (those runs have no HTTP request).
- A POST that redirects (create / update / delete) stores the write list and
  shows it on the **next opted-in GET** (signed cookie + in-memory store),
  like Django Debug Toolbar's history of the redirected request. The success
  URL view must also use the mixin. Writes scheduled with
  `transaction.on_commit` (including `ATOMIC_REQUESTS`) run after the
  listener is cleared and will not appear on that POST.
