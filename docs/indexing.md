---
icon: lucide/database
---

# Indexing

Django is the source of truth. Redis holds a search copy of the fields you
declared on each `Document`. This page is how you **create**, **fill**,
**check**, and **rebuild** that copy.

Live `save()` / `delete()` keep Redis in sync for single-row changes. Use the
`redisearch` command for first setup, schema changes, bulk imports, and
audits.

## The command

``` console
$ python manage.py redisearch <command> [options]
```

Always pass `--models` in an app with more than one `Document`, so you do not
touch every index at once.

``` console
$ python manage.py redisearch populate --models shop.Product
```

| Command | In one sentence |
| --- | --- |
| `create` | Make missing Redis indexes. Does not write documents. |
| `update` | Change the schema only. Never writes documents. |
| `populate` | Write every row from `get_queryset()` into Redis. |
| `reindex` | Drop the index and reload it (search is down while it runs). |
| `reindex --blue-green` | Build a new copy, then switch. Search stays up. |
| `rebuild` | Same as `reindex` without `--blue-green` (drop + reload). |
| `verify` | Compare Django rows to Redis keys. |
| `verify --repair` | Same, then fix missing / stale / leftover keys. |
| `check` | Exit `1` if the schema drifted or populate is still needed. |
| `info` | Print Redis `FT.INFO`. |
| `drop` | Remove the index. `--dd` also deletes the stored documents. |

Only one `reindex`, `rebuild`, `populate`, `drop`, or `verify --repair` can
run against the same index at a time. A second command exits with an error.

### Which models

`--models` takes Django labels (`app_label.ModelName`). It is available on
**every** subcommand.

| You type | What runs |
| --- | --- |
| *(omit `--models`)* | Every registered primary `Document` |
| `--models shop.Product` | Only the Document for `Product` |
| `--models shop.Product shop.Category` | Those two |

Unknown labels raise an error. Use the model name, not the Document class
name.

## Commands

### `create`

Creates a Redis index if it does not exist yet. If it already exists, prints
`already exists` and does nothing.

``` console
$ python manage.py redisearch create
$ python manage.py redisearch create --models shop.Product
```

Does **not** load data. Follow with `populate`.

**When:** first deploy, or a new `Document` you just added.

### `update`

Looks at your `Document` schema and the live Redis index.

- Same schema → `no-op`.
- New fields only → `FT.ALTER` (adds fields). Existing documents do **not**
  get the new values until you `populate`.
- Field type change, removed field, or storage/prefix change → does **not**
  rebuild. Prints that you should run `reindex`.

``` console
$ python manage.py redisearch update --models shop.Product
```

Never writes documents. Exit code `1` means “you still have a follow-up”
(`populate` or `reindex`).

**When:** after you change a `Document` (CI or deploy).

### `populate`

Reads `Document.get_queryset()` and writes each row that `should_index`
allows into Redis. Creates the index first if it is missing.

``` console
$ python manage.py redisearch populate --models shop.Product
```

Search stays up. Existing keys are overwritten.

**When:** first load, after `update` added fields, after a bulk ORM import
(`bulk_create`, `QuerySet.update`, …).

### `reindex`

Rebuilds from Django.

**Default** (no `--blue-green`): drops the live index, creates it again, and
populates. Search is empty until it finishes. Same idea as `rebuild`.

``` console
$ python manage.py redisearch reindex --models shop.Product
```

**`--blue-green`:** builds a second index with a new key prefix. While it
backfills, live `save()` writes go to **both** copies. Then it checks the new
copy, switches the stable alias, and drops the old index.

``` console
$ python manage.py redisearch reindex --blue-green --models shop.Product
```

| Flag | What it does |
| --- | --- |
| `--blue-green` | Zero-downtime rebuild (see above). |
| `--keep-old` | After a successful swap, leave the old index and keys. Requires `--blue-green`. |
| `--abort` | Cancel a `--blue-green` that is still running (or stuck). Turns dual-write off and drops the unfinished new copy. Safe if the new copy is already live. |

``` console
$ python manage.py redisearch reindex --blue-green --keep-old --models shop.Product
$ python manage.py redisearch reindex --abort --models shop.Product
```

If the new copy fails the check, the alias is **not** switched. Dual-write
stays on until you `--abort` or fix with `verify --repair` and run
`--blue-green` again.

**When:** schema change that `update` cannot apply. Use `--blue-green` if
search must stay up. Use the default only when downtime is fine (dev,
staging, a small index).

### `rebuild`

Drop + create + populate. Brief downtime. Same outcome as `reindex` without
`--blue-green`.

``` console
$ python manage.py redisearch rebuild --models shop.Product
```

**When:** local development, or a throwaway environment.

### `verify`

Walks Django (`get_queryset()` + `should_index`) and Redis keys:

| Report | Meaning | `--repair` |
| --- | --- | --- |
| missing | Django row should be indexed, no Redis key | write it |
| stale | Redis key exists but the document is out of date | write it again |
| orphaned | Redis key with no matching Django row | delete it |

``` console
$ python manage.py redisearch verify --models shop.Product
$ python manage.py redisearch verify --repair --models shop.Product
$ python manage.py redisearch verify --json --models shop.Product
```

| Flag | What it does |
| --- | --- |
| `--repair` | Fix the problems above. |
| `--json` | Print a machine-readable report (all pks). |
| `--limit N` | How many pks to print per category in the text report (default `20`). `0` prints all. |

Exit code `1` if anything is still wrong after the run. `--repair` that
succeeds exits `0`.

Stale detection uses a small stamp written on each document. Override
`get_index_version` only if you need a custom value (for example a row
`updated_at`).

**When:** after a bulk import, after a failed deploy, or as a rare audit. It
reads the whole table — do not run it every minute on a large index.

### `check`

Compares the local schema fingerprint to Redis. Also fails if `update`
marked the index as “populate still required”.

``` console
$ python manage.py redisearch check
$ python manage.py redisearch check --models shop.Product
```

Exit `0` = in sync. Exit `1` = run `update` / `populate` / `reindex`.

**When:** CI or a deploy gate.

### `info`

Prints Redis `FT.INFO` for each index. Warns and exits `1` if an index is
missing.

``` console
$ python manage.py redisearch info --models shop.Product
```

### `drop`

Removes the index definition. Documents in Redis stay unless you pass
`--dd`.

``` console
$ python manage.py redisearch drop --models shop.Product
$ python manage.py redisearch drop --dd --models shop.Product
```

| Flag | What it does |
| --- | --- |
| `--dd` | Also delete the JSON/Hash keys (`FT.DROPINDEX DD`). |

Refuses to run if a `--blue-green` reindex is still open. `--abort` that
session first.

## Options

| Option | Commands | Default | Purpose |
| --- | --- | --- | --- |
| `--models …` | all | all Documents | Limit to those Django models |
| `--blue-green` | `reindex` | off | Rebuild without taking search down |
| `--keep-old` | `reindex` | off | Keep the previous index after a swap |
| `--abort` | `reindex` | off | Cancel an in-progress `--blue-green` |
| `--repair` | `verify` | off | Write missing/stale keys; delete orphans |
| `--json` | `verify` | off | JSON report on stdout |
| `--limit N` | `verify` | `20` | How many pks to print per category |
| `--dd` | `drop` | off | Delete stored documents with the index |

`--keep-old` without `--blue-green` is an error.

## Workflows

=== "First time"

    Empty Redis, first deploy, or a new Document.

    ``` console
    $ python manage.py redisearch create --models shop.Product
    $ python manage.py redisearch populate --models shop.Product
    $ python manage.py redisearch check --models shop.Product
    ```

    After this, `Product.save()` / `delete()` keep the index current (if
    `AUTO_INDEX` is on).

=== "Added a field"

    ``` python
    class ProductDocument(Document):
        class Django:
            model = Product
            fields = ["name", "price", "sku"]  # sku is new
    ```

    ``` console
    $ python manage.py redisearch update --models shop.Product
    # prints: alter. Added fields with FT.ALTER. Run redisearch populate.
    $ python manage.py redisearch populate --models shop.Product
    $ python manage.py redisearch check --models shop.Product
    ```

    Until `populate` finishes, old documents do not have `sku` in Redis.
    Filters on `sku` will miss those rows.

=== "Schema change, stay up"

    `update` will tell you to reindex. In production, use `--blue-green`:

    ``` console
    $ python manage.py redisearch update --models shop.Product
    # prints: reindex. Schema needs a new physical index.
    $ python manage.py redisearch reindex --blue-green --models shop.Product
    $ python manage.py redisearch check --models shop.Product
    ```

    Users keep searching the old copy until the new one is filled and
    checked. Then the package switches the name and deletes the old copy.

    If the job fails halfway:

    ``` console
    $ python manage.py redisearch reindex --abort --models shop.Product
    ```

    That turns dual-write off and removes the unfinished new copy. The old
    index stays live.

    To keep the old copy around after a successful swap (for a quick
    compare):

    ``` console
    $ python manage.py redisearch reindex --blue-green --keep-old --models shop.Product
    ```

    Remember to `drop --dd` the leftover later, or Redis keeps both copies.

=== "Schema change, downtime OK"

    ``` console
    $ python manage.py redisearch reindex --models shop.Product
    ```

    or

    ``` console
    $ python manage.py redisearch rebuild --models shop.Product
    ```

    Search is down until the command finishes.

=== "After a bulk import"

    `bulk_create`, `bulk_update`, `QuerySet.update()`, and
    `QuerySet.delete()` do **not** update Redis.

    ``` console
    # after Product.objects.bulk_create(...) or .update(available=False)
    $ python manage.py redisearch populate --models shop.Product
    ```

    To only check first:

    ``` console
    $ python manage.py redisearch verify --models shop.Product
    $ python manage.py redisearch verify --repair --models shop.Product
    ```

=== "Deploy gate"

    ``` console
    $ python manage.py redisearch check --models shop.Product
    ```

    - Exit `0` → schema matches, populate is not pending.
    - Exit `1` → run `update`, then `populate` or `reindex --blue-green` as
      the message says. Run `check` again.

=== "Is Redis missing rows?"

    ``` console
    $ python manage.py redisearch verify --json --models shop.Product
    ```

    Use `--repair` only when you have read the report. `--repair` **deletes**
    Redis keys that look orphaned. If `get_queryset()` hides rows
    (soft-delete, tenant filter) those keys look orphaned even though you
    still want them.

=== "Throw away an index"

    ``` console
    $ python manage.py redisearch drop --dd --models shop.Product
    $ python manage.py redisearch create --models shop.Product
    $ python manage.py redisearch populate --models shop.Product
    ```

## What `save()` does not cover

| Happens in Django | Redis |
| --- | --- |
| `instance.save()` / `delete()` | Updated, if `AUTO_INDEX` is on |
| Related object save (vendor, tags, …) | Parent document rewritten |
| `QuerySet.update()` / `bulk_create` / `bulk_update` / `QuerySet.delete()` | **Unchanged** — run `populate` |
| `get_queryset()` filter | Used by `populate` / `reindex` / `verify` only |
| `should_index(instance)` is `False` | That key is deleted on save |

`get_queryset()` does not run on `Model.save()`. Use `should_index` so
unpublished rows leave the index when they are saved.

Live writes still use the Document’s `select_related` / `prefetch_related`,
so nested fields do not cause an extra query per relation.

To run those writes in Celery instead of the request: [signals](signals.md).
