---
hide:
  - toc
---

<div class="rsd-hero" markdown>

# redis-search-django

Index Django models into **Redis Query Engine** and query them with the
lookups you already know — keyword search, facets, and nearest neighbors.

[Get started](getting-started.md){ .md-button .md-button--primary }
[Run the demo](demo.md){ .md-button }

</div>

<div class="grid cards" markdown>

-   :lucide-rocket:{ .lg .middle } **First index in minutes**

    ---

    Declare a `Document`, run `redisearch create` + `populate`, and search with
    `objects.filter()`.

    [Getting started :octicons-arrow-right-24:](getting-started.md)

-   :lucide-search:{ .lg .middle } **Django-style query**

    ---

    `Q`, lookups, `order_by`, stock `Paginator`, and `to_queryset()` that keeps
    RediSearch ranking.

    [Query API :octicons-arrow-right-24:](query.md)

-   :lucide-layout-dashboard:{ .lg .middle } **Click-through demo**

    ---

    A catalog app with search, KNN, aggregations, async, and CRUD.

    [Demo app :octicons-arrow-right-24:](demo.md)

-   :lucide-refresh-cw:{ .lg .middle } **Stays in sync**

    ---

    Signals index create / update / delete — including related FK, O2O, and M2M
    changes.

    [Signals :octicons-arrow-right-24:](signals.md)

</div>

## How it fits together

``` mermaid
flowchart LR
    A[Django models] -->|signals / populate| B[Document]
    B --> C[Redis JSON + FT.SEARCH]
    D[Views / services] -->|Document.objects| C
    D -->|facets / Aggregate / knn| C
```

Django stays the source of truth. Redis holds a search copy of the fields you
declare. Live `save()` / `delete()` keep that copy current; the
[`redisearch`](indexing.md) command covers first setup, schema changes, and
bulk imports.

## What you get

| Need | API |
| --- | --- |
| Keyword + filters | `Document.objects.filter(name__search="shoes", price__lte=150)` |
| Facets | `.facets("category__name", "tags__name")` |
| Stats | `.aggregate(Aggregate().group_by("vendor__name").avg("price", "avg"))` |
| Meaning | `.knn("comfortable running shoes", k=10)` |
| Django rows | `.to_queryset()` — same order as Redis |
| ASGI | `acount`, `aget`, `ato_queryset`, `async for` |

## Requirements

| Runtime | Versions |
| --- | --- |
| Python | 3.10 – 3.15 (free-threaded from 3.15t) |
| Django | 5.2, 6.0, 6.1 |
| redis-py | ≥ 8.0 (installed automatically) |
| Redis | 8+ with Query Engine and RedisJSON |

Do not `import redis` in application code. Types you need are re-exported from
`redis_search_django`.

## Next

<div class="grid cards" markdown>

-   [**Install and index a model**](getting-started.md)
-   [**Declare nested documents**](documents.md)
-   [**Upgrade from 0.1 / 0.2**](migrate.md)

</div>
