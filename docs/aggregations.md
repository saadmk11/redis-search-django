---
icon: lucide/chart-bar
---

# Aggregations

`FT.AGGREGATE` is a first-class API. You never pass raw `@category_name`
unless you opt into `AggregateRequest`.

## Facets

One `FT.AGGREGATE` **per** facet (RediSearch groups one way per request).
Arguments are Django lookups.

``` python
from shop.documents import ProductDocument

ProductDocument.objects.filter(name__search="shoes").facets(
    "category__name",
    "tags__name",
)
# async: await qs.afacets("category__name", "tags__name")
```

Returns:

``` python
{
    "category__name": [
        {"value": "Shoes", "count": 112},
        {"value": "Clothes", "count": 40},
    ],
    "tags__name": [{"value": "Blue", "count": 14}],
}
```

| Input lookup | Grouped RediSearch alias |
| --- | --- |
| `category__name` | `@category_name` |
| `tags__name` | `@tags_name` |
| `vendor__name` | `@vendor_name` |
| `available` | `@available` |

The current queryset’s filter (and `extra()`) is the aggregate query string.
Unfiltered:

``` python
ProductDocument.objects.facets("category__name")
```

In a list view:

``` python
def facets(self):
    return self.get_search_queryset().facets("category__name", "tags__name")
```

``` django
{% for row in facets.category__name %}
  <label>
    <input type="checkbox" name="category" value="{{ row.value }}" />
    {{ row.value }} ({{ row.count }})
  </label>
{% endfor %}
```

## `Aggregate`

Fluent builder. Lookups are Django names; the compiler maps them to `AS`
aliases.

``` python
from redis_search_django import Aggregate
from shop.documents import ProductDocument

ProductDocument.objects.filter(available=True).aggregate(
    # async: await qs.aaggregate(Aggregate()...)
    Aggregate()
    .group_by("vendor__name")
    .count("count")
    .avg("price", "avg_price")
    .sum("price", "sum_price")
    .min("price", "min_price")
    .max("price", "max_price")
    .sort_by("-count")
    .limit(10)
    .load("name", "price")
)
```

Typical row:

``` python
{"vendor_name": "Acme", "count": "12", "avg_price": "84.5", ...}
```

Redis returns reducer values as strings. Cast in your app
(`int(row["count"])`, `float(row["avg_price"])`).

### Builder methods

| Method | Meaning |
| --- | --- |
| `group_by(lookup)` | `GROUPBY 1 @alias` |
| `count(alias="count")` | `REDUCE COUNT` |
| `avg(lookup, alias)` | `REDUCE AVG` |
| `sum(lookup, alias)` | `REDUCE SUM` |
| `min(lookup, alias)` | `REDUCE MIN` |
| `max(lookup, alias)` | `REDUCE MAX` |
| `tolist(lookup, alias)` | `REDUCE TOLIST` |
| `sort_by(field)` | One field; leading `-` = descending |
| `limit(n)` | `LIMIT 0 n` |
| `load(*lookups)` | `LOAD` those aliases |

`sort_by` is single-field, same as search.

## How lookups become aliases

Flattening is not configurable:

| User lookup / facet arg | JSON path | `AS` alias |
| --- | --- | --- |
| `pk` | `$.pk` | `pk` |
| `name` | `$.name` | `name` |
| `vendor__name` | `$.vendor.name` | `vendor_name` |
| `category__name` | `$.category.name` | `category_name` |
| `tags__name` | `$.tags[*].name` | `tags_name` |

A concrete field named `category_name` plus `Object` `category.name` is a
register-time `ImproperlyConfigured` (alias collision). Rename with
`fields.Text(as_name="...")`.

## Raw `AggregateRequest`

For `APPLY`, `FILTER`, `WITHCURSOR`, timeouts, or reducers this package does
not wrap, pass a request from **this** package (not `from redis.commands...`):

``` python
from redis_search_django import AggregateRequest, reducers
from shop.documents import ProductDocument

request = (
    AggregateRequest("@available:{true}")
    .group_by(["@vendor_name"], reducers.count().alias("count"))
    .sort_by("@count", desc=True)
    .limit(0, 10)
)

ProductDocument.objects.aggregate(request)
```

!!! warning "Trusted raw RediSearch"

    You are responsible for aliases (`@vendor_name`, not `vendor__name`) and
    for not interpolating unsanitized user strings.

The queryset’s compiled filter is **not** applied when you pass a complete
`AggregateRequest` — that object carries its own query string.

To combine a Django-filtered queryset with a raw request, compile first and
either call `aggregate()` on **that same** queryset or pass `query_params`
explicitly:

``` python
qs = ProductDocument.objects.filter(name__search="shoes")
query, params = qs.raw()
qs.aggregate(
    AggregateRequest(query).group_by(["@category_name"], reducers.count().alias("n"))
)
# or, on a different queryset:
ProductDocument.objects.aggregate(
    AggregateRequest(query).group_by(["@category_name"], reducers.count().alias("n")),
    query_params=params,
)
```

If the request query still has `$p1` / `$p2` / … and those names are not
bound, `aggregate()` raises `MissingQueryParams` (a `FieldError`)
immediately. Bind them with `extra(query, params=...)`, call `aggregate()`
on the queryset that compiled the filter, or pass `query_params`. Prefer
`Aggregate()` when the filter uses `__search` — it always pairs the compiled
query with its PARAMS.

## Reducers

| `Aggregate` method | redis-py `reducers` |
| --- | --- |
| `count` | `reducers.count()` |
| `avg` | `reducers.avg` |
| `sum` | `reducers.sum` |
| `min` | `reducers.min` |
| `max` | `reducers.max` |
| `tolist` | `reducers.tolist` |

Anything else: build an `AggregateRequest` with `reducers` imported from
`redis_search_django`.

``` python
from redis_search_django import AggregateRequest, reducers

AggregateRequest("*").group_by(
    ["@category_name"],
    reducers.count().alias("n"),
    reducers.avg("@price").alias("avg_price"),
)
```

## Dialect and filters

Every aggregate the queryset builds uses **dialect 2**. The filter is the
same compiled string as `FT.SEARCH` for that queryset (`Q` tree or
`extra()`).

``` python
# These share the same filter expression
qs = ProductDocument.objects.filter(price__gte=10, available=True)
hits = list(qs[:20])
facets = qs.facets("category__name")
stats = qs.aggregate(Aggregate().group_by("vendor__name").avg("price", "avg_price"))
```

## What is not wrapped

| Feature | Status |
| --- | --- |
| `APPLY` | Use `AggregateRequest` |
| `FILTER` (post-group) | Use `AggregateRequest` |
| `WITHCURSOR` | Use `AggregateRequest` |
| Query timeout | Use `AggregateRequest` |
| Multi-field `SORTBY` | Not on `Aggregate.sort_by`; raw request only |
| Hybrid / KNN aggregate | Out of scope |

`facets()` always uses `COUNT`. For average price per facet, use
`Aggregate().group_by(...).avg(...)`.
