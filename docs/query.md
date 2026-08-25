---
icon: lucide/search
---

# Query

`DocumentQuerySet` is **lazy**, clone-on-write, like Django.

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

for hit in qs[:20]:
    hit.pk, hit.score, hit.name
```

Use **`redis_search_django.Q` only**. Passing `django.db.models.Q` is a
`TypeError`.

## Lookups

| Lookup | Field types | Dialect 2 |
| --- | --- | --- |
| (default) | TAG, Boolean | `@available:{true}` |
| (default) | TEXT | phrase exact |
| (default) | NUMERIC | `@price:[10 10]` |
| `__search` | TEXT | `@name:($p1 $p2)` (token AND, PARAMS) |
| `__exact` | TEXT / TAG | phrase or `{tag}` |
| `__in` | TEXT, TAG, Boolean, NUMERIC | TEXT: phrase union; TAG/Boolean: `{a\|b}`; NUMERIC: exact ranges |
| `__gt` `__gte` `__lt` `__lte` | NUMERIC | `@price>=10` (dates coerced to timestamps) |
| `__range` | NUMERIC | `@price:[10 100]` (`inf` → `+inf`) |
| `__isnull` | field with `index_missing=True` | `ismissing(@field)` |
| `__isnull` | `Object(..., required=False)` | `ismissing(@category_pk)` |
| `__startswith` | TEXT | `@name:$p1*` |
| `__startswith` | TAG | requires `suffix_trie=True` |
| `__geo_distance` | GEO | `NotSupportedError` |

Use `knn()` for Vector fields; `__exact` on a Vector is `UnsupportedLookup`.

Rejected Django lookups (`__contains`, `__icontains`, `__year`, …) raise
`NotSupportedError`.

## Evaluation

| Operation | Redis |
| --- | --- |
| `filter` / `exclude` / `search` / `order_by` / `qs[a:b]` | none (clone) |
| `list(qs)` / `for hit in qs` | `FT.SEARCH` (unsliced pages at `CHUNK_SIZE`) |
| `qs[n]` | `LIMIT n 1` |
| `count()` / `len(qs)` | `LIMIT 0 0` (hit **total**). After `knn()`: neighbor count, at most `k` |
| `exists()` / `bool(qs)` | `LIMIT 0 1` |
| `get()` | `LIMIT 0 2` |
| `objects.get(pk=)` | `JSON.GET` (JSON) or `HGETALL` (Hash) by key; missing → `Document.DoesNotExist` |
| `first()` / `last()` | `LIMIT 0 1` (or reversed); empty → `None` |
| `none()` | no I/O; empty result |
| `raw()` | no I/O — `(query_string, params)` |
| `explain()` | `FT.EXPLAIN` (same compiled query + `PARAMS` as search) |
| `knn(query, k=10)` | `FT.SEARCH` KNN — [vector search](vector.md) |

`order_by` accepts **exactly one** sortable field. Extra names raise
`NotSupportedError`. `last()` / `reverse()` require `order_by()`.

`extra(query="...", params={...})` replaces the compiled filter (trusted).
`filter()` after `extra()` is a `ValueError` — put extra clauses in the raw
string, or call `filter()` first.

`raw()` is the analog of `str(qs.query)` for SQL, except it returns only the
filter expression — not `SORTBY` / `LIMIT`. After `knn()` it also includes the
`=>[KNN …]` clause.

Each `Document` subclass gets its own `DoesNotExist` / `MultipleObjectsReturned`,
like Django models. `DoesNotExist` subclasses `DocumentNotFound`.

Every evaluation method has an `a`-prefixed counterpart
(`acount`, `aget`, `ato_queryset`, …). Filter construction stays sync. See
[async](async.md).

## Pagination and `to_queryset()`

Stock Django `Paginator` works: it calls `count()` then slices.

``` python
from django.core.paginator import Paginator

page = Paginator(ProductDocument.objects.filter(name__search=q), 20).get_page(n)
```

`to_queryset()` loads Django rows for the **current** hits, preserving
RediSearch order with `Case`/`When`:

``` python
products = ProductDocument.objects.filter(name__search=q)[:20].to_queryset()
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `TO_QUERYSET_WARN` | `1000` | Log a warning above this many pks |
| `TO_QUERYSET_MAX` | `5000` | Raise `ValueError` above this (`0` disables) |

Always slice before `to_queryset()` on large result sets.

## Views

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

    def get_search_queryset(self):
        qs = ProductDocument.objects.all()
        query = self.request.GET.get("query")
        if query:
            qs = qs.filter(Q(name__search=query) | Q(description__search=query))
        sort = self.request.GET.get("sort")
        if sort in {"price", "-price", "name", "-name"}:
            qs = qs.order_by(sort)
        return qs

    def facets(self):
        return self.get_search_queryset().facets("category__name", "tags__name")
```

By default the mixin converts the current page to a Django queryset
(`convert_to_queryset = True`) so templates can use `product.vendor.name`.

The same mixin is used on ASGI. Write `async def get` that returns
`await self.aget(...)`, and override `afacets()` if you overrode `facets()`.
See [async](async.md).
