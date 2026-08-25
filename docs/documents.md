---
icon: lucide/file-text
---

# Documents

A `Document` is a **mapping class** (like a `ModelForm`), not a Redis row and
not a Django model. It says which model fields land in Redis and how related
objects are nested.

``` python title="shop/documents.py"
from redis_search_django import fields
from redis_search_django.documents import Document


class ProductDocument(Document):
    vendor = fields.Object(VendorDocument)
    tags = fields.Nested(TagDocument)

    class Django:
        model = Product
        fields = ["name", "price", "available"]
        auto_index = True
        select_related_fields = ["vendor"]
        prefetch_related_fields = ["tags"]

    class Index:
        storage = "json"          # or "hash"
        search_fields = ["name"]  # optional; default = every TEXT field
```

## `class Django`

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `model` | `type[Model]` | required unless `abstract=True` | Django model this Document indexes |
| `fields` | sequence of `str` | `()` | Auto-mapped model fields. **FK / O2O / M2M are rejected** — use `Object` / `Nested` |
| `auto_index` | `bool` | `True` | Connect realtime signals for this Document |
| `select_related_fields` | sequence of `str` | `()` | Applied in `get_queryset()` and related reindex |
| `prefetch_related_fields` | sequence of `str` | `()` | Same |
| `related_models` | `None` or `dict` | `None` | `None` = infer from `Object`/`Nested`. `{}` = no related signals. A non-empty dict **merges** with inference |
| `embedded` | `bool` | `False` | No Redis index, no primary signals; only an `Object`/`Nested` target |
| `abstract` | `bool` | `False` | Contribute fields to subclasses; not registered |

!!! note

    `related_models` must be a dict (or `None`). A list raises
    `ImproperlyConfigured`.

## `class Index`

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | `idx:{app}.{model}.{document}` | Stable **alias** |
| `prefix` | `str` | `{PREFIX}:{app}.{model}.{document}:` | Redis key prefix (trailing `:` added if missing) |
| `storage` | `"json"` \| `"hash"` | `"json"` | `ON JSON` vs `ON HASH`. Hash cannot use `Nested` |
| `language` | `str` | Redis default | `FT.CREATE LANGUAGE` |
| `stopwords` | sequence or `None` | `None` | `None` = Redis default; `()` = `STOPWORDS 0` |
| `score` | `float` | `1.0` | `FT.CREATE SCORE` |
| `dialect` | `int` | `2` | Forced on every search / aggregate |
| `search_fields` | sequence of lookups | all TEXT fields | Fields `objects.search()` queries |

`app` is `model._meta.app_label`, `model` is `model._meta.model_name`,
`document` is the class name lowercased (`ProductDocument` → `productdocument`).

Two non-embedded Documents must not share an **alias** or a **prefix**.

## Field types

Declare a field on the Document to override the default mapping.

| Class | RediSearch | Notes |
| --- | --- | --- |
| `fields.Text` | TEXT | Full-text. `sortable`, `weight`, `no_stem`, `phonetic` |
| `fields.Tag` | TAG | Exact match. `separator`, `case_sensitive`, `suffix_trie` |
| `fields.Numeric` | NUMERIC | Numbers, dates, datetimes. Sortable by default |
| `fields.Boolean` | TAG | Query `{true}` / `{false}`. JSON stores a JSON bool; Hash stores the strings |
| `fields.Geo` | GEO | Schema only. `__geo_distance` is `NotSupportedError` |
| `fields.Vector` | VECTOR | Nearest neighbors via `knn()`. See [vector search](vector.md) |
| `fields.Object(OtherDocument)` | nested object | FK / O2O. `required=False` allows `null` |
| `fields.Nested(OtherDocument)` | nested array | M2M / reverse FK lists. Illegal on Hash storage |

### Default Django → Field

| Django field | Field | Stored JSON |
| --- | --- | --- |
| `CharField`, `TextField`, `EmailField` | `Text` (`sortable=True` if `CharField` and `max_length≤256`) | string |
| `SlugField`, `UUIDField`, `URLField` | `Tag` | string |
| `Integer*`, `AutoField*`, `FloatField`, `DecimalField` | `Numeric` | number (`Decimal` → `float`) |
| `DateField`, `DateTimeField` | `Numeric` | UTC unix timestamp (`float`) |
| `TimeField` | `Tag` | `HH:MM:SS` |
| `BooleanField` | `Boolean` | JSON `true`/`false` |
| `FileField`, `ImageField`, `FilePathField` | `Tag` | storage path (`.name`) |
| `ForeignKey`, `OneToOneField` | **not auto-mapped** | use `Object(...)` |
| `ManyToManyField` | **not auto-mapped** | use `Nested(...)` |

`None` values are **omitted** from the JSON document so `__isnull`
(`ismissing(@field)`) works. `index_missing` is set automatically when the
Django field is `null=True`.

Dates: aware datetimes go through `timezone.localtime` then UTC epoch. Naive
datetimes are treated as UTC. `DateField` is midnight UTC of that date.

## Nested relations

Related models are **not** listed in `Django.fields`. Declare them with
`Object` (FK / O2O) or `Nested` (M2M):

``` python
class VendorDocument(Document):
    class Django:
        model = Vendor
        fields = ["name"]
        embedded = True


class ProductDocument(Document):
    vendor = fields.Object(VendorDocument)
    category = fields.Object(CategoryDocument, required=False)
    tags = fields.Nested(TagDocument)
```

- `embedded = True` means no top-level Redis index and no primary signals. Use
  those classes only as `Object` / `Nested` targets.
- Lookups and facets use Django paths: `category__name`, `tags__name`. The
  compiler maps them to RediSearch aliases (`category_name`, `tags_name`).
- A missing optional relation is `category__isnull=True`
  (`ismissing(@category_pk)`). Redis marks `INDEXMISSING` on the object's `pk`,
  not on child TEXT/TAG fields. `category__name__isnull=True` is rewritten to
  the same query when `name` itself is not nullable.

Related-model signals are **inferred** from `Object` / `Nested` unless you set
`related_models`:

| `related_models` | Effect |
| --- | --- |
| `None` (default) | Infer every `Object`/`Nested` target |
| `{}` | No related signals (primary model still indexes) |
| `{Vendor: {"related_name": "product", "many": False}, ...}` | Those models replace inference; **unlisted** `Object`/`Nested` still infer |

Ambiguous graphs (two FKs to the same model) raise at register time. Fix with a
dict entry for that model or `get_instances_from_related`.

On related **delete**:

- `CASCADE` on the parent’s FK/O2O → do **not** upsert the parent; the parent’s
  `post_delete` removes the Redis key
- `SET_NULL` / M2M → reindex the parent without the deleted object

## Hooks

| Hook | When | Role |
| --- | --- | --- |
| `get_queryset()` | `populate` / `rebuild` / `reindex` / `index_all()` | Bulk filter + `select_related` / `prefetch_related` |
| `should_index(instance)` | Realtime upsert | If `False`, the Redis key is **deleted** |
| `get_index_version(instance, payload)` | Each write | Optional custom stamp for `verify`. Default is automatic |
| `prepare_{field}(instance)` | Each field | Raw Python value (then coerced). On a Vector field: source text or the finished vector |
| `embed_{field}(value)` | Vector fields | Turns the source value into floats if `embedder=` is not set |
| `embedder` (class attr) | Vector fields | Default embedder for every Vector field |
| `prepare(instance)` | After all fields | If not `None`, **replaces** the whole payload |
| `get_instances_from_related(related)` | Related save/delete | Parent instance(s) to reindex |

!!! warning "`get_queryset()` does not run on `Model.save()`"

    Use `should_index` so unpublished rows drop out of the index when they are
    saved. Live writes still apply the Document's `select_related` /
    `prefetch_related`.

``` python
class ProductDocument(Document):
    ...

    @classmethod
    def get_queryset(cls):
        return super().get_queryset().filter(available=True)

    @classmethod
    def should_index(cls, instance):
        return instance.available

    @classmethod
    def prepare_name(cls, obj):
        return obj.name.upper()
```

After a blue/green swap, `objects.get(pk=)` retries once if the process still
has the old key prefix cached.
