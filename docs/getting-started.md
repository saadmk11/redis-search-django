---
icon: lucide/rocket
---

# Getting started

Install the package, point it at Redis, declare a `Document`, and run your first
search.

## Install

=== "uv"

    ``` console
    $ uv add redis-search-django
    ```

=== "pip"

    ``` console
    $ pip install redis-search-django
    ```

Add the app:

``` python title="settings.py"
INSTALLED_APPS = [
    ...,
    "redis_search_django",
]
```

In development you can also add the [debug overlay](debug.md). Do not add it in
production.

## Redis

You need **Redis 8+** with Query Engine (RediSearch) and RedisJSON. The
[redis-stack](https://hub.docker.com/r/redis/redis-stack) image is the easiest
local setup. This repository includes a Compose file:

``` console
$ docker compose up -d
```

That publishes Redis on `6379` and Redis Insight on `8001`.

!!! tip "Existing Redis"

    Point the package at it with `REDIS_SEARCH["URL"]`. Full settings:
    [Settings](settings.md).

## Declare a document

Document classes **must** live in a `documents.py` module so
`AppConfig.ready()` can autodiscover them.

``` python title="shop/documents.py"
from redis_search_django.documents import Document

from .models import Category


class CategoryDocument(Document):
    class Django:
        model = Category
        fields = ["name", "slug"]
```

Create the index and load existing rows:

``` console
$ python manage.py redisearch create
$ python manage.py redisearch populate
```

After that, `Category` rows are written to Redis on `save()` and removed on
`delete()`.

## Search

``` python
from redis_search_django import Q
from shop.documents import CategoryDocument

list(CategoryDocument.objects.filter(name__search="shoes")[:20])
```

Use **`redis_search_django.Q` only**. Passing `django.db.models.Q` is a
`TypeError`.

## Where to go next

<div class="grid cards" markdown>

-   [**Documents**](documents.md) — nested relations, field types, hooks
-   [**Query**](query.md) — lookups, pagination, views
-   [**Indexing**](indexing.md) — `redisearch` commands and production workflows
-   [**Demo app**](demo.md) — run the catalog and click through every feature

</div>
