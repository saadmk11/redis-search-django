---
icon: lucide/layout-dashboard
---

# Demo app

The repository ships a small catalog in [`example/`](https://github.com/saadmk11/redis-search-django/tree/1.0/example).
Plain HTML, no extra frontend. Use it to **try every public feature** and to
**manually test** new APIs — live pages print the call, `raw()` / `explain()`,
and the result.

## Run it

You need [uv](https://docs.astral.sh/uv/), Docker, and a clone of this repo.

``` console
$ git clone git@github.com:saadmk11/redis-search-django.git
$ cd redis-search-django
$ uv sync --group example
$ docker compose up -d
$ uv run python example/manage.py migrate
$ uv run python example/manage.py loaddata catalog
$ uv run python example/manage.py redisearch rebuild
$ uv run python example/manage.py runserver
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

!!! tip "What each command does"

    | Step | Why |
    | --- | --- |
    | `uv sync --group example` | Installs Django, the package, and Pillow |
    | `docker compose up -d` | Starts Redis Stack on `6379` (Insight on `8001`) |
    | `migrate` | Creates the SQLite schema |
    | `loaddata catalog` | Loads dummy rows into **SQLite only** |
    | `redisearch rebuild` | Creates both Redis indexes and writes every available product |
    | `runserver` | Serves the demo at port 8000 |

`loaddata` never writes Redis. Always follow it with `redisearch rebuild` (or
`populate` if the indexes already exist).

After you change `example/core/documents.py` (new fields or a new Document),
run `redisearch rebuild` again. Indexes that embed `Object(..., required=False)`
must be rebuilt so `{alias}_pk` is created with `INDEXMISSING` (needed for
`category__isnull`).

Create a superuser if you prefer Django admin:

``` console
$ uv run python example/manage.py createsuperuser
```

## Dummy data

The `catalog` fixture is:

| Model | Count | Notes |
| --- | --- | --- |
| Categories | 5 | Electronics, Clothing, Books, Home, Sports |
| Tags | 9 | HASH + JSON copies |
| Vendors | 42 | One per product (O2O) |
| Products | 42 | 38 available, 4 hidden, 2 with `category=null` |

Hidden products (`available=False`) stay in SQLite and are **omitted from
Redis** (`get_queryset` / `should_index`). The two products with no category
are there so you can try `__isnull`.

There is **no embedding column** on `Product`. On save, `prepare_embedding`
joins name + description and a local `embed()` function turns that text into
32 floats. Redis stores the vector. `knn()` searches it. No API key and no
local model.

Other `prepare_*` hooks in the demo:

| Hook | Stored value |
| --- | --- |
| `prepare_name` | Title **UPPERCASE** |
| `prepare_sku` | `SKU-0001` style tag |
| `prepare_department` | Category slug, or omitted when there is no category |
| `prepare_location` | `lon,lat` Geo point from the category (or omitted) |

`TagHashDocument` is a second index of `Tag` with `Index.storage = "hash"` so
HASH vs JSON can be compared without changing the product graph.

## What each page is

| URL | What it shows |
| --- | --- |
| [`/`](http://127.0.0.1:8000/) | Keyword search, meaning (`knn()`), facets, sort, paginator, `to_queryset()`, aggregates, highlight |
| [`/aggregations/`](http://127.0.0.1:8000/aggregations/) | `Aggregate().group_by` with COUNT / AVG / MIN / MAX / SUM / TOLIST |
| [`/async/`](http://127.0.0.1:8000/async/) | `acount` / `aexists` / `afirst` / `alast` / `aget_by_pk` / `afacets` / `aaggregate` / `aexplain` / `ato_queryset` |
| [`/async/search/`](http://127.0.0.1:8000/async/search/) | Same filters as `/`, served through `SearchListViewMixin.aget` |
| [`/lab/`](http://127.0.0.1:8000/lab/) | Feature matrix: every public API, what it is, how to test it |
| [`/lab/query/`](http://127.0.0.1:8000/lab/query/) | Playground + canned demos: lookups, `extra()`, `none()`, HASH, Geo |
| [`/lab/index/`](http://127.0.0.1:8000/lab/index/) | Registry, `IndexManager` info/check/drift, `REDIS_SEARCH`, `apply_index_action` |
| [`/catalog/`](http://127.0.0.1:8000/catalog/) | CRUD for products, categories, tags, vendors (`AUTO_INDEX`) |
| [`/catalog/products/<id>/`](http://127.0.0.1:8000/catalog/products/1/) | Django row vs `objects.get(pk=)` (sku, Geo, embedding, full payload) |
| [`/admin/`](http://127.0.0.1:8000/admin/) | Same models; saves still fire `AUTO_INDEX` |

Search, aggregations, lab, async, product detail, and catalog CRUD
use `SearchDebugMixin`. A pill in the bottom-left corner lists Redis search /
aggregate / get **and** index writes (`JSON.SET` / `HSET` / `DEL`) on that
page. Click it or press ++alt+r++. See [debug overlay](debug.md).

Category and Vendor documents are embedded, so creating an unused category or
vendor does not write a Redis key (the overlay stays at 0 queries). Saving a
product, tag, or a category/vendor that already has products does write.

## Things to try

### Keyword + meaning

On `/`, use the **Meaning** box (leave keyword empty):

| Query | Nearby products |
| --- | --- |
| `wireless headphones` | earbuds, speaker, microphone |
| `trail running` | trail shoes, running shorts, hiking pants |
| `redis search` | Redis Internals, Search Handbook, Indexing Strategies |
| `kitchen coffee` | pour-over, mug, skillet |

Add a price or category filter first — Redis applies that, then ranks by
meaning.

### Query lab

On `/lab/query/`:

| Demo | Expected |
| --- | --- |
| `objects.search("coffee")` | kitchen / mug / pour-over |
| `name__exact="REDIS INTERNALS"` | one hit (stored uppercase) |
| `department__isnull=True` | Gift Card + Mystery Sample (no category) |
| `category__isnull=True` | same two products |
| `available=False` | count `0` (keys never written) |
| `extra()` then `filter()` | `ValueError` |
| HASH `TagHashDocument` | 9 tags, `storage=hash` |

### CRUD / signals

1. **Catalog → Add vendor**, then **Add product**. The new product appears in
   search after save (`AUTO_INDEX`).
2. Edit the product, set **available** off, save. Redis drops the key
   (`should_index`).
3. Rename a category. Related products reindex with the new name and a new
   Geo coordinate.
4. Add or rename a tag. The HASH document updates; products with that tag
   reindex.
5. Delete a product. The Redis document is removed.
6. On `/lab/index/`, POST `upsert` / `delete` / `reindex_related` to run
   `apply_index_action` without going through a model form.

After a create / update / delete, the next opted-in page still shows the POST
writes in the overlay.
