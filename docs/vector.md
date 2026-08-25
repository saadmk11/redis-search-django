---
icon: lucide/waypoints
---

# Vector search

Find documents that are **close in meaning**, not just ones that share the
same words.

## What Redis does

A **vector** (also called an embedding) is a list of numbers. Similar text
produces similar lists.

Redis Query Engine can store that list on each document as a `VECTOR` field
and run **KNN** — *K nearest neighbors*:

> Given a query vector, return the *K* stored vectors that sit closest to it.

That search is a normal `FT.SEARCH` (dialect 2). Redis can also **filter
first**, then run KNN only on the rows that remain:

``` text
(@available:{true} @price<=150)=>[KNN 10 @embedding $vec AS vector_score]
```

| Part | Meaning |
| --- | --- |
| `(@available:{true} @price<=150)` | Keep only matching documents |
| `=>[KNN 10 @embedding $vec …]` | Among those, find the 10 closest to `$vec` |
| `AS vector_score` | Return the distance as `vector_score` (lower is closer) |

Without a filter the left side is just `*`.

This package builds that query for you. You do not write the Redis syntax.

## What you plug in

Redis stores and compares vectors. It does **not** turn text into numbers.

You supply a function that does:

``` python
def embed(text: str) -> list[float]:
    return your_model.encode(text)   # `dims` floats, or a numpy array
```

Use any model or API you already have. The package never calls one for you.

## Add a Vector field

``` python
from redis_search_django import fields
from redis_search_django.documents import Document

from .models import Product


class ProductDocument(Document):
    embedding = fields.Vector(
        dims=384,
        source="description",
        embedder=embed,
    )

    class Django:
        model = Product
        fields = ["name", "description", "price", "available"]
```

| Argument | Meaning |
| --- | --- |
| `dims` | Length of the list your embedder returns (must match the model) |
| `source` | Django attribute to encode on save (`vendor.name` is fine) |
| `embedder` | Your function, a dotted path, or an object with `embed()` |

On each save, the package reads `source`, calls `embedder`, and writes the
vector to Redis.

There is no default Django mapping. You always declare `fields.Vector`
yourself.

Then rebuild so existing rows get vectors:

``` console
$ python manage.py redisearch rebuild --models shop.Product
```

Later saves update the vector on their own.

## Search with `knn()`

``` python
from shop.documents import ProductDocument

# closest 10 products to this phrase
ProductDocument.objects.knn("comfortable running shoes", k=10)

# same idea, but you already have a vector
ProductDocument.objects.knn([0.12, -0.04, ...], field="embedding", k=5)
```

| Argument | Meaning |
| --- | --- |
| `query` | Text (run through the embedder) or a list of `dims` floats |
| `k` | How many neighbors to return (default `10`) |
| `field` | Vector field name. Needed only if the document has more than one |
| `ef_runtime` | Optional HNSW probe size for this query |
| `score_name` | Name of the distance field (default `vector_score`) |

If the query is a string and no embedder is configured, you get a
`ConfigurationError`. Pass a float list instead, or set an embedder.

### Filters and KNN in one query

`filter()`, `search()`, and `exclude()` become the Redis pre-filter.
KNN then runs only on those rows. `extra()` cannot be combined with `knn()`.

``` python
ProductDocument.objects.filter(available=True, price__lte=150).knn(
    "comfortable running shoes", k=10
)
```

That is one Redis command. Out-of-stock or expensive products never enter
the neighbor list.

`facets()` and `aggregate()` ignore `knn()`. They use the filter only.

### Read the results

``` python
for hit in ProductDocument.objects.filter(available=True).knn("trail shoes", k=10):
    hit.pk
    hit.score     # distance; 0 is identical (COSINE / L2)
    hit.name
```

Results are sorted closest first. `order_by()` after `knn()` replaces that
sort.

`count()`, slices, `first()`, `Paginator`, and `to_queryset()` work as usual.
`count()` is how many neighbors came back (at most `k`), not the size of
the filter. The count query does not load document bodies.

## Other embedder hooks

`embedder=` is the usual path. If you omit it, the document is checked for:

1. `embed_{field}(value)` — same naming style as `prepare_{field}`
2. `embedder` on the class — used by every Vector field

``` python
class ProductDocument(Document):
    embedding = fields.Vector(dims=384, source="description")

    class Django:
        model = Product
        fields = ["name", "description"]

    @classmethod
    def embed_embedding(cls, value: str) -> list[float]:
        return embed(value)
```

`prepare_{field}` still runs first. Return source text (the embedder runs
next) or the finished vector (the embedder is skipped). Return `None` to
omit the field.

Some models encode queries differently from documents. Optional:

``` python
class SplitEmbedder:
    def embed(self, value: str) -> list[float]:
        return model.encode(value, prompt_name="document")

    def embed_query(self, value: str) -> list[float]:
        return model.encode(value, prompt_name="query")
```

Pass an **instance**, not the class. Indexing calls `embed`. `knn()` calls
`embed_query` if it exists.

`Embedder` in this package is only a typing protocol. A plain function is
enough.

## Field options

| Option | Values | Default |
| --- | --- | --- |
| `algorithm` | `HNSW` or `FLAT` | `HNSW` (use `FLAT` for tiny indexes and tests) |
| `distance` | `COSINE`, `L2`, `IP` | `COSINE` |
| `type` | `FLOAT32`, `FLOAT64` | `FLOAT32` |

JSON (the default) stores a list of floats. Hash stores a binary blob.
`objects.get(pk=)` always returns a list of floats. Hash search hits unpack
the blob when Redis can deliver it intact; otherwise the field is omitted.

Changing `dims`, `algorithm`, `distance`, or `type` is a schema change.
Changing `source` or `embedder` is not — but you still need `populate` or
`rebuild` so old rows get new vectors.

## Notes

- `__exact` on a Vector field raises `UnsupportedLookup`. Use `knn()`.
- `extra()` and `knn()` together raise `ValueError`. Use one or the other.
- Async works: `await qs.knn("…", k=10).acount()`, `async for hit in qs`.
