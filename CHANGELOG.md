# Version: 1.0.0

* Breaking rewrite: talk to Redis Query Engine through redis-py only. Drop redis-om and Pydantic from the runtime.
* Replace `JsonDocument` / `HashDocument` / `EmbeddedJsonDocument` with a single `Document` class. Nested data uses `fields.Object` and `fields.Nested`.
* Query with Django-style lookups on `Document.objects` (`filter`, `exclude`, `search`, `order_by`, `facets`).
* Add vector search: `fields.Vector` can encode a value on save through a pluggable embedder (`embedder=`, `embed_{field}()`, or `Document.embedder`). `knn()` is dialect-2 hybrid KNN and works with `filter()`.
* Add Django-style async evaluation and index writes (`acount`, `aget`, `aindex_all`, `apaginate`) on `redis.asyncio`.
* Add `redisearch` management command (`create`, `update`, `populate`, `rebuild`, `reindex`, `verify`, `drop`, `info`, `check`). Remove `index`.
* `redisearch reindex` rebuilds in place. Pass `--blue-green` for a zero-downtime swap (new prefix, dual-write, `FT.ALIASUPDATE`, drop the old index). `verify [--repair]` diffs Django PKs against Redis keys.
* Add optional `redis_search_django.debug` overlay: `SearchDebugMixin` / `@search_debug` record Redis Query Engine calls and index writes on opted-in views.
* Support `__isnull` on `Object(..., required=False)` as `ismissing(@{alias}_pk)`.
* Connect signals only to registered models and declared related / M2M senders.
* Store booleans as TAG `true`/`false` (JSON keeps a JSON bool). Dates and datetimes are UTC unix timestamps.
* Settings move to a single `REDIS_SEARCH` dict. `REDIS_OM_URL` and `REDIS_SEARCH_AUTO_INDEX` are ignored.
* Extract `apply_index_action` so a user `SIGNAL_PROCESSOR` can run the same writes in Celery, django-q, or any other queue without this package depending on those libraries.
* Re-export user-facing redis-py names (`Redis`, `AggregateRequest`, `reducers`, `Query`, exceptions) from `redis_search_django` so application code does not import `redis` directly.
* Map `SlugField` / `URLField` / `FilePathField` to TAG (they subclass `CharField` and were incorrectly treated as TEXT).
* Omit `None` values from JSON payloads so `INDEXMISSING` / `__isnull` works.
* Infer reverse OneToOne / FK relations for `Object` fields such as `book.extra`.
* Add user documentation under `docs/` (Zensical: guide, signals/Celery, aggregations, vector search, 0.1.0 → 1.0.0 migration).
* Use stock Django `Paginator`. Remove `RediSearchPaginator` and redis-om `FindQuery`.
* Drop support for Python 3.7–3.9 and Django 3.2–5.1. Support Python 3.10–3.15 (free-threaded from 3.15t) and Django 5.2, 6.0, and 6.1.
* Thread-safe first-use of the cached Redis clients and of the live index-prefix cache (needed when the GIL is off).

# Version: 0.2.0

* Drop support for Python 3.7–3.9 and Django 3.2–5.1 (all end of life).
* Add support for Python 3.11–3.14 and Django 5.2, 6.0, and 6.1.
* Require redis-om 1.x (Pydantic v2) and target current Redis with Query Engine and JSON.
* Manage the project with uv and move all packaging metadata into `pyproject.toml`.
* Replace deprecated Pydantic v1 APIs (`ModelField`, `__fields__`) with Pydantic v2 `model_fields`.
* Replace deprecated `Migrator` with `SchemaDetector`.
* Add modern type annotations throughout the package.
* Replace Black, isort, flake8, and pyupgrade with Ruff.

# Version: 0.1.0

* [#2](https://github.com/saadmk11/redis-search-django/pull/2): Bump actions/checkout from 2 to 3
* [#5](https://github.com/saadmk11/redis-search-django/pull/5): Create LICENSE
* [#4](https://github.com/saadmk11/redis-search-django/pull/4): Add Tests and Improvements
