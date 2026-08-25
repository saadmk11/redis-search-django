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

Index Django models into **[Redis Query Engine](https://redis.io/docs/latest/develop/interact/search-and-query/)** (RediSearch) and query them with Django-style lookups.

**Documentation:** [`docs/`](docs/) (Zensical). Preview locally with `uv sync --group docs && uv run zensical serve` (http://127.0.0.1:8002 — the demo app keeps 8000).

## Features

- Declarative `Document` classes, discovered from `documents.py`
- Auto-index on create / update / delete, including related FK, O2O, and M2M changes
- Nested objects via `fields.Object` and `fields.Nested`
- Django-style lookups on `Document.objects` (`Q`, `__search`, `__range`, `__isnull`, …)
- Stock Django `Paginator` and `to_queryset()` that preserves RediSearch ranking
- First-class `FT.AGGREGATE` facets and `Aggregate` builder
- Vector search: embed on save, then `knn()` (works with `filter()`)
- Optional debug overlay: Redis query, timing, `FT.EXPLAIN` on opted-in views
- Async query, index writes, and views (`acount`, `aget`, `ato_queryset`)
- Swappable signal processor (Celery / django-q / RQ — no package dependency)
- `redisearch` command: create, update, populate, reindex (incl. blue-green), verify, check

## Requirements

| Runtime | Versions |
| --- | --- |
| Python | 3.10 – 3.15 (free-threaded from 3.15t) |
| Django | 5.2, 6.0, 6.1 |
| redis-py | ≥ 5.0 |
| Redis | 8+ with Query Engine and RedisJSON |

## Install

```bash
uv add redis-search-django
# or
pip install redis-search-django
```

```python
INSTALLED_APPS = [
    ...,
    "redis_search_django",
]
```

Local Redis (this repo’s Compose file uses `redis/redis-stack`):

```bash
docker compose up -d
```

Then follow **[Getting started](docs/getting-started.md)**.

## Example

A click-through catalog lives in [`example/`](example/). How to run it, load dummy data, and what each page is: **[Demo app](docs/demo.md)**.

## Development

```bash
uv sync --group dev
docker compose up -d
uv run pytest
```

Test matrix: `uvx --with tox-uv tox`. Docs: `uv sync --group docs && uv run zensical serve` (http://127.0.0.1:8002).

## License

[MIT](LICENSE)
