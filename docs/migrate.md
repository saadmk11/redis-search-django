---
icon: lucide/arrow-right-left
---

# Migrate from 0.1 to 1.0

0.1.0 (and the 0.2.x line) indexed Django models by **subclassing redis-om**
(`JsonDocument` / `HashDocument` / `EmbeddedJsonDocument`) and querying with
redis-om operators. **1.0 is a clean break**: redis-py only, one `Document`
class, Django-style lookups.

There is **no automated data migrator**. 1.0 writes a new key prefix. After
cutover, drop the old redis-om indexes.

## Should you upgrade?

| You are on | Path |
| --- | --- |
| **0.1.0** | Jump straight to 1.0. Do not stop on 0.2 unless you only need Python/Django bumps on the old API |
| **0.2.x** | Same 1.0 rewrite. 0.2 already dropped EOL Python/Django but still used redis-om |
| **New project** | Start on 1.0 |

!!! note "Requirements jump"

    0.1.0 ran on older Python/Django. 1.0 needs **Python 3.10–3.15**
    (free-threaded from **3.15t**) and **Django 5.2 / 6.0 / 6.1**, plus
    **Redis 8+** (Query Engine + JSON).

## What changed

``` mermaid
flowchart LR
    subgraph old["0.1 / 0.2"]
        A[JsonDocument / HashDocument]
        B[redis-om Field]
        C["find(name % q)"]
        D["manage.py index"]
        E[REDIS_OM_URL]
    end
    subgraph new["1.0"]
        F[Document]
        G[redis_search_django.fields]
        H["objects.filter(name__search=q)"]
        I["manage.py redisearch"]
        J["REDIS_SEARCH dict"]
    end
    A --> F
    B --> G
    C --> H
    D --> I
    E --> J
```

| Area | 0.1.0 | 1.0 |
| --- | --- | --- |
| Redis client | redis-om + Pydantic | redis-py only |
| Document classes | `JsonDocument`, `HashDocument`, `EmbeddedJsonDocument` | one `Document` + `embedded=True` / `Index.storage` |
| Field declaration | `name: str = Field(index=True, full_text_search=True)` | `fields.Text(...)` or `Django.fields` |
| Nested models | type annotations + `EmbeddedJsonDocument` | `fields.Object` / `fields.Nested` |
| Query | `find()`, `%`, `<<`, `>=` | `Document.objects.filter()`, `Q`, lookups |
| Pagination | `RediSearchPaginator` | stock Django `Paginator` |
| Views | `mixins.RediSearchListViewMixin` | `views.SearchListViewMixin` |
| Command | `manage.py index` | `manage.py redisearch …` |
| Settings | `REDIS_OM_URL`, `REDIS_SEARCH_AUTO_INDEX` | `REDIS_SEARCH = {…}` |
| Booleans | stored as `int` (redis-om NUMERIC) | TAG `true`/`false` |
| Dates | redis-om / ISO depending on field | UTC unix timestamp (`float`) |
| Signals | global handlers on **all** models | per registered sender |
| Aggregations | bolted-on `build_aggregate_request` + `reducers` | `facets()` / `Aggregate` |
| `import redis` | common in views | re-export from `redis_search_django` |

## Checklist

- [ ] Python ≥ 3.10, Django ≥ 5.2, Redis 8+ with Query Engine + JSON
- [ ] Replace `redis-om` with `redis-search-django` 1.0 (`redis` comes in transitively)
- [ ] Add `REDIS_SEARCH` and delete `REDIS_OM_URL` / `REDIS_SEARCH_AUTO_INDEX`
- [ ] Rewrite every `documents.py`
- [ ] Replace `find()` / `%` / `<<` with `objects.filter` / `Q`
- [ ] Replace `manage.py index` in scripts and docs
- [ ] Point views at `SearchListViewMixin` and `get_search_queryset()`
- [ ] Replace facet code with `.facets(...)` or `Aggregate`
- [ ] `redisearch rebuild` (new prefix — old keys are not reused)
- [ ] `FT.DROPINDEX` old redis-om indexes; optionally `SCAN`/`UNLINK` `redis_search:*`
- [ ] Confirm `QuerySet.update` / `bulk_*` still need a manual `populate`

## Dependencies and settings

=== "0.1"

    ``` text
    redis-om
    REDIS_OM_URL=redis://localhost:6379/0
    ```

    ``` python
    REDIS_SEARCH_AUTO_INDEX = True
    ```

=== "1.0"

    ``` toml
    # pyproject / requirements — do not list redis-om
    dependencies = [
        "redis-search-django>=1.0.0",
    ]
    ```

    ``` python
    REDIS_SEARCH = {
        "URL": "redis://localhost:6379/0",
        "AUTO_INDEX": True,
    }
    ```

| Removed | Use instead |
| --- | --- |
| `REDIS_OM_URL` | `REDIS_SEARCH["URL"]` or `CONNECTION` |
| `REDIS_SEARCH_AUTO_INDEX` | `REDIS_SEARCH["AUTO_INDEX"]` |
| `from redis import Redis` | `from redis_search_django import Redis` |
| `from redis.commands.search import reducers` | `from redis_search_django import reducers` |

If `REDIS_OM_URL` is still set, 1.0 logs a one-time warning and **ignores**
it.

## Rewrite documents

=== "0.1"

    ``` python
    from redis_om import Field

    from redis_search_django.documents import EmbeddedJsonDocument, JsonDocument

    from .models import Category, Product, Tag, Vendor


    class CategoryDocument(EmbeddedJsonDocument):
        custom_field: str = Field(index=True, full_text_search=True)

        class Django:
            model = Category
            fields = ["name", "slug"]

        @classmethod
        def prepare_custom_field(cls, obj: Category) -> str:
            return "CUSTOM FIELD VALUE"


    class TagDocument(EmbeddedJsonDocument):
        class Django:
            model = Tag
            fields = ["name"]


    class VendorDocument(EmbeddedJsonDocument):
        class Django:
            model = Vendor
            fields = ["name"]


    class ProductDocument(JsonDocument):
        vendor: VendorDocument
        category: CategoryDocument | None = None
        tags: list[TagDocument] = []

        class Django:
            model = Product
            fields = ["name", "description", "price"]
            related_models = {
                Vendor: {"related_name": "product", "many": False},
                Category: {"related_name": "product_set", "many": True},
                Tag: {"related_name": "product_set", "many": True},
            }

        @classmethod
        def get_queryset(cls):
            return super().get_queryset().filter(available=True)
    ```

=== "1.0"

    ``` python
    from django.db import models

    from redis_search_django import fields
    from redis_search_django.documents import Document

    from .models import Category, Product, Tag, Vendor


    class CategoryDocument(Document):
        custom_field = fields.Text()

        class Django:
            model = Category
            fields = ["name", "slug"]
            embedded = True

        @classmethod
        def prepare_custom_field(cls, obj: Category) -> str:
            return "CUSTOM FIELD VALUE"


    class TagDocument(Document):
        class Django:
            model = Tag
            fields = ["name"]
            embedded = True


    class VendorDocument(Document):
        class Django:
            model = Vendor
            fields = ["name"]
            embedded = True


    class ProductDocument(Document):
        vendor = fields.Object(VendorDocument)
        category = fields.Object(CategoryDocument, required=False)
        tags = fields.Nested(TagDocument)

        class Django:
            model = Product
            fields = ["name", "description", "price", "available"]
            select_related_fields = ["vendor", "category"]
            prefetch_related_fields = ["tags"]
            # related_models omitted → inferred from Object/Nested

        @classmethod
        def get_queryset(cls) -> models.QuerySet[Product]:
            return super().get_queryset().filter(available=True)

        @classmethod
        def should_index(cls, instance: models.Model) -> bool:
            return instance.available
    ```

| 0.1.0 | 1.0 |
| --- | --- |
| `JsonDocument` | `Document` (`Index.storage` defaults to `"json"`) |
| `HashDocument` | `Document` + `class Index: storage = "hash"` |
| `EmbeddedJsonDocument` | `Document` + `class Django: embedded = True` |
| `field: Type = Field(...)` | `fields.Text` / `Tag` / `Numeric` / … or list the name in `Django.fields` |
| `vendor: VendorDocument` | `vendor = fields.Object(VendorDocument)` |
| `tags: list[TagDocument] = []` | `tags = fields.Nested(TagDocument)` |
| `category: CategoryDocument \| None = None` | `category = fields.Object(..., required=False)` |
| required `related_models` dict | optional; inferred by default |
| `get_queryset()` only on bulk index | add `should_index()` for **save()** or unavailable products stay in Redis |

Hash documents **cannot** declare `Nested` fields.

## Rewrite queries

=== "0.1"

    ``` python
    result = (
        ProductDocument.find(
            ProductDocument.name % "shoes" | ProductDocument.description % "shoes"
        )
        .sort_by("-price")
        .execute()
    )
    qs = result.to_queryset()
    ```

=== "1.0"

    ``` python
    from redis_search_django import Q

    qs = (
        ProductDocument.objects.filter(
            Q(name__search="shoes") | Q(description__search="shoes")
        )
        .order_by("-price")
        .to_queryset()
    )

    for hit in ProductDocument.objects.filter(name__search="shoes")[:20]:
        hit.pk, hit.score, hit.name
    ```

| 0.1 redis-om | 1.0 lookup |
| --- | --- |
| `Document.name % "q"` | `name__search="q"` |
| `Document.name == "Acme"` | `name="Acme"` or `name__exact="Acme"` |
| `Document.price >= 10` | `price__gte=10` |
| `Document.price <= 100` | `price__lte=100` |
| `Document.category.name << ["Shoes"]` | `category__name__in=["Shoes"]` |
| `Document.tags.name << tags` | `tags__name__in=tags` |
| `Document.find()` | `Document.objects.all()` |
| `.sort_by("-price")` | `.order_by("-price")` (one field) |
| `.execute()` | iterate / slice / `list()` |
| `RediSearchPaginator` | `django.core.paginator.Paginator` |

Use **`redis_search_django.Q`**, not `django.db.models.Q`.

Inspect the compiled query without hitting Redis:

``` python
query, params = ProductDocument.objects.filter(name__search="shoes").raw()
```

That is the closest equivalent to `str(django_qs.query)`.

## Rewrite aggregations

=== "0.1"

    ``` python
    from redis.commands.search import reducers

    request = ProductDocument.build_aggregate_request(expr)
    ProductDocument.aggregate(
        request.group_by(["@category_name"], reducers.count().alias("count"))
    )
    ```

=== "1.0"

    ``` python
    ProductDocument.objects.filter(name__search="shoes").facets("category__name")

    from redis_search_django import Aggregate

    ProductDocument.objects.filter(available=True).aggregate(
        Aggregate().group_by("vendor__name").count("count").avg("price", "avg_price")
    )
    ```

Import `reducers` / `AggregateRequest` from `redis_search_django` if you
still need a raw request. See [aggregations](aggregations.md).

## Rewrite views

=== "0.1"

    ``` python
    from redis_search_django.mixins import RediSearchListViewMixin

    class SearchView(RediSearchListViewMixin, ListView):
        document_class = ProductDocument

        @cached_property
        def search_query_expression(self):
            q = self.request.GET.get("query")
            if q:
                return self.document_class.name % q | self.document_class.description % q
            return None
    ```

=== "1.0"

    ``` python
    from redis_search_django import Q
    from redis_search_django.views import SearchListViewMixin

    class SearchView(SearchListViewMixin, ListView):
        document_class = ProductDocument

        def get_search_queryset(self):
            qs = ProductDocument.objects.all()
            q = self.request.GET.get("query")
            if q:
                qs = qs.filter(Q(name__search=q) | Q(description__search=q))
            return qs

        def facets(self):
            return self.get_search_queryset().facets("category__name", "tags__name")
    ```

| 0.1 | 1.0 |
| --- | --- |
| `redis_search_django.mixins` | `redis_search_django.views` |
| `search_query_expression` | `get_search_queryset()` |
| `RediSearchPaginator` | stock `Paginator` |

## Management command

| 0.1 | 1.0 |
| --- | --- |
| `python manage.py index` | `python manage.py redisearch rebuild` (dev) or `create` + `populate` |
| `python manage.py index --only-migrate` | `python manage.py redisearch update` |
| `python manage.py index --models app.Model` | `python manage.py redisearch populate --models app.Model` |

`update` **never** writes documents. After an `ALTER`, run `populate`. After
an incompatible schema change, run `redisearch reindex --blue-green`
(zero-downtime) or `rebuild`. `check` exits `1` until that finishes.

## Signals

| 0.1 | 1.0 |
| --- | --- |
| Handlers connected to **every** model | Only registered models + inferred related / M2M through tables |
| Writes inside the signal (ahead of rollback) | `transaction.on_commit` when inside `atomic()` |
| No extension point | `REDIS_SEARCH["SIGNAL_PROCESSOR"]` + `dispatch()` |

If you need Celery, do **not** look for an in-tree class. Subclass
`BaseSignalProcessor` and call `apply_index_action` from a task —
[signals](signals.md).

Documents imported after `ready()` no longer pick up signals automatically.

## Redis keys

| Kind | 0.1 (redis-om) | 1.0 |
| --- | --- | --- |
| Document | `redis_search:{module}.{Class}:{pk}` | `{PREFIX}:{app}.{model}.{document}:{pk}` |
| Index | redis-om generated name | Alias `idx:{app}.{model}.{document}` → physical `{alias}:{fingerprint}` |
| Schema meta | none | `{PREFIX}:meta:{alias}` |

JSON shape changes too: booleans are JSON bools (not `0`/`1`); dates are UTC
timestamps (not ISO unless you `prepare_*`).

## Behavioral changes

1. **`get_queryset()` is bulk-only.** 0.1 `save()` still indexed rows that
   `get_queryset()` would exclude. 1.0 uses `should_index()` on incremental
   writes.
2. **`None` is omitted** from JSON so `__isnull` works. Optional relations
   are missing keys, not `"category": null`.
3. **Related delete + CASCADE** no longer upserts a parent that Django is
   about to delete.
4. **Bulk ORM** still does not update Redis (`update`, `bulk_create`,
   `QuerySet.delete`).
5. **Composite primary keys** are rejected.

## Cutover

``` console
# 1. Deploy 1.0 code (indexes are empty / new prefix)
$ python manage.py redisearch create
$ python manage.py redisearch populate

# 2. Smoke-test search
$ python manage.py redisearch check
$ python manage.py redisearch info

# 3. After you are happy, drop old redis-om indexes (names vary)
# redis-cli FT._LIST
# redis-cli FT.DROPINDEX <old-name> DD
```

Keep 0.1 and 1.0 indexes side by side only if you dual-write during a
rollout; they do **not** share prefixes. Dual-write is not built in — cut
over in a maintenance window or run `populate` immediately after deploy.

??? note "Scan leftover 0.1 keys"

    ``` console
    $ redis-cli --scan --pattern 'redis_search:*' | head
    # when sure:
    # redis-cli --scan --pattern 'redis_search:*' | xargs -L 100 redis-cli UNLINK
    ```
