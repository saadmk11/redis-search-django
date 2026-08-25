# redis-search-django

[![Pypi Version](https://img.shields.io/pypi/v/redis-search-django.svg?style=flat-square)](https://pypi.org/project/redis-search-django/)
[![Supported Python Versions](https://img.shields.io/pypi/pyversions/redis-search-django?style=flat-square)](https://pypi.org/project/redis-search-django/)
[![Supported Django Versions](https://img.shields.io/pypi/frameworkversions/django/redis-search-django?color=darkgreen&style=flat-square)](https://pypi.org/project/redis-search-django/)
[![License](https://img.shields.io/github/license/saadmk11/redis-search-django?style=flat-square)](https://github.com/saadmk11/redis-search-django/blob/main/LICENSE)

![Django Tests](https://img.shields.io/github/actions/workflow/status/saadmk11/redis-search-django/test.yml?label=Test&style=flat-square&branch=main)
![Codecov](https://img.shields.io/codecov/c/github/saadmk11/redis-search-django?style=flat-square&token=ugjHXbEKib)
![pre-commit.ci](https://img.shields.io/badge/pre--commit.ci-enabled-brightgreen?logo=pre-commit&logoColor=white&style=flat-square)
![Changelog-CI](https://img.shields.io/github/actions/workflow/status/saadmk11/redis-search-django/changelog-ci.yaml?label=Changelog-CI&style=flat-square&branch=main)
![Code Style](https://img.shields.io/badge/Code%20Style-Ruff-d7ff64?style=flat-square)

Index Django models into **[Redis Query Engine](https://redis.io/docs/latest/develop/interact/search-and-query/)** (RediSearch) and query them with the same lookups you already use in the ORM.

Django stays the source of truth. You declare a `Document`, signals (or `redisearch populate`) keep a search copy in Redis, and you query with `Document.objects.filter(...)`.

**Documentation:** https://saadmk11.github.io/redis-search-django/ · [Getting started](https://saadmk11.github.io/redis-search-django/getting-started/) · [Demo app](https://saadmk11.github.io/redis-search-django/demo/) · [Contribute](https://saadmk11.github.io/redis-search-django/contributing/)

## Features

- **Declarative documents** — one `Document` class per model, auto-discovered from `documents.py`
- **Live sync** — create / update / delete (including related FK, O2O, and M2M) update Redis automatically
- **Nested data** — `fields.Object` and `fields.Nested` for related rows (JSON or HASH storage)
- **Django-style query** — `filter`, `exclude`, `search`, `Q`, `order_by`, stock `Paginator`
- **ORM hand-off** — `to_queryset()` returns Django rows in RediSearch rank order
- **Facets and aggregates** — `.facets()` and a first-class `Aggregate` builder over `FT.AGGREGATE`
- **Vector search** — embed on save with a pluggable function, then `knn()` next to `filter()`
- **Async** — `acount`, `aget`, `ato_queryset`, async index writes, and async views
- **Index lifecycle** — `redisearch` for create, update, populate, rebuild, blue-green reindex, and verify
- **Debug overlay** — optional per-view Redis query / write inspector (`SearchDebugMixin`)
- **Queues without a dependency** — swap `SIGNAL_PROCESSOR` to run the same writes in Celery, django-q, or RQ

## Requirements

| Runtime | Versions |
| --- | --- |
| Python | 3.10 – 3.15 (free-threaded from 3.15t) |
| Django | 5.2, 6.0, 6.1 |
| redis-py | ≥ 5.0 (installed with the package) |
| Redis | 8+ with Query Engine and RedisJSON |

## Quick start

```bash
uv add redis-search-django
# or
pip install redis-search-django
```

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "redis_search_django",
]

# Optional. Default is redis://localhost:6379/0
REDIS_SEARCH = {
    "URL": "redis://localhost:6379/0",
}
```

This repo’s Compose file starts Redis Stack (`6379`) and Redis Insight (`8001`):

```bash
docker compose up -d
```

Declare a document in `documents.py` so the app can discover it:

```python
# shop/documents.py
from redis_search_django.documents import Document

from .models import Product


class ProductDocument(Document):
    class Django:
        model = Product
        fields = ["name", "description", "price", "available"]
```

Create the index and load existing rows:

```bash
python manage.py redisearch create
python manage.py redisearch populate
```

Search:

```python
from redis_search_django import Q
from shop.documents import ProductDocument

hits = ProductDocument.objects.filter(
    Q(name__search="shoes") | Q(description__search="shoes"),
    price__lte=150,
    available=True,
)[:20]

for hit in hits:
    hit.pk, hit.score, hit.name
```

Use **`redis_search_django.Q`**, not `django.db.models.Q`. Full walkthrough: [Getting started](https://saadmk11.github.io/redis-search-django/getting-started/).

## Query API

`DocumentQuerySet` is lazy and clone-on-write, like Django.

| Need | Call |
| --- | --- |
| Keyword + filters | `objects.filter(name__search="shoes", price__lte=150)` |
| Exact / membership | `name__exact`, `tags__name__in=["sale"]`, `available=True` |
| Ranges | `price__gte`, `created_at__range=(start, end)` |
| Missing values | `category__isnull=True` (optional `Object` uses `INDEXMISSING`) |
| Exclude | `objects.exclude(tags__name="discontinued")` |
| Sort / slice | `.order_by("-price")[:20]` |
| Facets | `.facets("category__name", "tags__name")` |
| Aggregations | `.aggregate(Aggregate().group_by("vendor__name").avg("price", "avg"))` |
| Nearest neighbors | `.knn("comfortable running shoes", k=10)` |
| Django rows | `.to_queryset()` |
| Async | `acount()`, `aget()`, `ato_queryset()`, `async for hit in qs` |

Lookups, pagination, and views: [Query](https://saadmk11.github.io/redis-search-django/query/). Vector fields and `knn()`: [Vector search](https://saadmk11.github.io/redis-search-django/vector/).

## Indexing

| Command | Purpose |
| --- | --- |
| `redisearch create` | Create missing indexes |
| `redisearch update` | Apply compatible schema changes |
| `redisearch populate` | Write current Django rows into Redis |
| `redisearch rebuild` | Drop, create, and populate |
| `redisearch reindex` | In-place rebuild (`--blue-green` for a zero-downtime swap) |
| `redisearch verify` | Diff Django PKs vs Redis (`--repair` to fix drift) |
| `redisearch drop` / `info` / `check` | Remove an index, print `FT.INFO`, check drift |

Details: [Indexing](https://saadmk11.github.io/redis-search-django/indexing/). Upgrading from 0.1: [Migrate](https://saadmk11.github.io/redis-search-django/migrate/).

## Example app

[`example/`](example/) is a catalog you can click through — search, KNN, aggregations, async, and CRUD. How to run it and what each page is: [Demo app](https://saadmk11.github.io/redis-search-django/demo/).

## Development

```bash
uv sync --group dev
docker compose up -d
uv run pytest
```

| Check | Command |
| --- | --- |
| Tests + 100% coverage | `uv run pytest` |
| Full Python / Django matrix | `uvx --with tox-uv tox` |

How to run linters, the matrix, and open a PR: [Contribute](https://saadmk11.github.io/redis-search-django/contributing/).

## License

[MIT](LICENSE) © Maksudul Haque
