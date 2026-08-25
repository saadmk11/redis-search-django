---
icon: lucide/radio
---

# Signals

How auto-indexing works, how to swap the processor, and how to run the
**same** write path in Celery, django-q, or RQ.

This package does **not** depend on Celery or any task queue.

## How auto-index works

On `AppConfig.ready()`:

1. Autodiscover every `documents.py`.
2. Instantiate `REDIS_SEARCH["SIGNAL_PROCESSOR"]`.
3. If `AUTO_INDEX` is `True`, call `processor.setup()`.

`setup()` connects signals **only** to:

- the Document’s primary model
- each related model in the resolved `related_map` (`Object` / `Nested`)
- each M2M `through` table for those `Nested` fields

There is no global `post_save.connect(fn)` on every model.

``` mermaid
sequenceDiagram
    participant ORM as Django ORM
    participant P as SignalProcessor
    participant D as dispatch
    participant A as apply_index_action
    participant R as Redis

    ORM->>P: post_save / m2m / delete
    P->>P: on_commit if atomic()
    P->>D: dispatch(action, payload)
    Note over D: override this for Celery
    D->>A: apply_index_action(action, payload)
    A->>R: JSON.SET / DEL
```

| Event | Action |
| --- | --- |
| `post_save` on the primary model | `upsert` that document |
| `post_save` on a related model | `reindex_related` (rewrite each parent) |
| `post_delete` on the primary model | `delete` by pk |
| `pre_delete` on a related model | Capture parent pks; `upsert` each parent after commit. Skip if the parent will `CASCADE` |
| `m2m_changed` `post_add` / `post_remove` / `post_clear` | Same as save on the instance the M2M was accessed from |

`Indexer.upsert` respects `should_index(instance)`. If it returns `False`,
the Redis key is deleted.

## When Redis is written

``` python
if transaction.get_connection().in_atomic_block:
    transaction.on_commit(run)   # enqueue / write after commit
else:
    run()                        # write now
```

| Context | `"SIGNAL_ERRORS": "raise"` | `"log"` |
| --- | --- | --- |
| Not in `atomic()` | Redis error comes out of `save()` / `delete()`. The ORM row is already written | Log; request continues |
| Inside `atomic()` | Hook runs **after** commit. Redis error can 500 the request; the **DB row stays** | Log; index may lag |

Do not treat Redis and `Model.save()` as one transaction.
`redisearch populate` heals drift.

!!! warning "Enqueue after commit"

    If a Celery worker runs before commit, it will not see the new row.

## The three layers

| Layer | Lives in | You replace? |
| --- | --- | --- |
| **Wiring** — senders, `setup` / `teardown` | `BaseSignalProcessor` | Rarely |
| **Dispatch** — in-process vs `.delay()` | `dispatch(action, payload)` | Yes |
| **Action** — load rows + `Indexer` | `apply_index_action` | No |

Override **only** `dispatch`. Related-model fan-out and CASCADE skips stay
in the processor.

## Actions and payloads

`apply_index_action(action, payload)` is JSON-serializable. Document label
is `{app_label}.{DocumentClassName}` (for example `shop.ProductDocument`).

| `action` (`IndexAction`) | `payload` | Worker does |
| --- | --- | --- |
| `IndexAction.UPSERT` (`"upsert"`) | `{"document": "shop.ProductDocument", "pk": 42}` | Load the instance; `Indexer.upsert`. If the row is gone, delete the Redis key |
| `IndexAction.DELETE` (`"delete"`) | `{"document": "shop.ProductDocument", "pk": 42}` | `Indexer.delete` |
| `IndexAction.REINDEX_RELATED` (`"reindex_related"`) | `{"document": "shop.ProductDocument", "related": "shop.Vendor", "pk": 7}` | Load the related row; reindex parent documents |

Brokers still receive the string values. Import the enum as
`from redis_search_django import IndexAction`.

Unknown `action` → `UnknownIndexAction`. Unknown document label →
`LookupError`.

``` python
from redis_search_django import apply_index_action

apply_index_action("upsert", {"document": "shop.ProductDocument", "pk": 42})
```

Workers always load **fresh** rows. Never put model instances on the broker.

## Processors

=== "Realtime (default)"

    ``` python
    REDIS_SEARCH = {
        "SIGNAL_PROCESSOR": "redis_search_django.signals.RealtimeSignalProcessor",
    }
    ```

    `dispatch` calls `apply_index_action` in the web/worker process that
    handled the ORM write.

=== "Celery"

    This package does not import Celery. Copy the following into **your**
    project.

    ``` python title="myapp/search.py"
    from redis_search_django.signals import BaseSignalProcessor

    from myapp.tasks import redis_search_action


    class CelerySignalProcessor(BaseSignalProcessor):
        def dispatch(self, action: str, payload: dict) -> None:
            redis_search_action.delay(action, payload)
    ```

    ``` python title="myapp/tasks.py"
    from celery import shared_task

    from redis_search_django import apply_index_action


    @shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
    def redis_search_action(action: str, payload: dict) -> None:
        apply_index_action(action, payload)
    ```

    ``` python title="settings.py"
    REDIS_SEARCH = {
        "SIGNAL_PROCESSOR": "myapp.search.CelerySignalProcessor",
        "SIGNAL_ERRORS": "log",  # do not 500 the request if the broker blips
    }
    ```

    Recommended extras (in **your** app, not this package):

    - `acks_late=True` if a killed worker should retry
    - Bind `document` + `pk` into the task name / logging
    - `redisearch populate` already rebuilds from the ORM after a broker outage

    ??? tip "Retry only on Redis errors"

        ``` python
        from celery import shared_task

        from redis_search_django import apply_index_action
        from redis_search_django.redis import ConnectionError, TimeoutError


        @shared_task(
            autoretry_for=(ConnectionError, TimeoutError),
            retry_backoff=True,
            retry_kwargs={"max_retries": 8},
        )
        def redis_search_action(action: str, payload: dict) -> None:
            apply_index_action(action, payload)
        ```

        Import `ConnectionError` / `TimeoutError` from
        `redis_search_django.redis`, not from `redis`.

=== "django-q"

    ``` python title="myapp/search.py"
    from django_q.tasks import async_task

    from redis_search_django import apply_index_action
    from redis_search_django.signals import BaseSignalProcessor


    class DjangoQSignalProcessor(BaseSignalProcessor):
        def dispatch(self, action: str, payload: dict) -> None:
            async_task(apply_index_action, action, payload)
    ```

    ``` python
    REDIS_SEARCH = {
        "SIGNAL_PROCESSOR": "myapp.search.DjangoQSignalProcessor",
    }
    ```

=== "RQ / Huey / other"

    Same contract: enqueue `(action, payload)`, call `apply_index_action` in
    the worker.

    If the worker is itself async (ASGI background task, asyncio consumer),
    call `aapply_index_action` instead. That loads rows with Django's async
    ORM and writes Redis through `redis.asyncio`. Signal `dispatch()` stays
    synchronous — only the worker needs to be async. See [async](async.md).

    ``` python
    from django_rq import enqueue

    from redis_search_django import apply_index_action
    from redis_search_django.signals import BaseSignalProcessor


    class RQSignalProcessor(BaseSignalProcessor):
        def dispatch(self, action: str, payload: dict) -> None:
            enqueue(apply_index_action, action, payload)
    ```

## Testing without a broker

The test suite’s `tests/dummy_celery.py` is the same pattern with an
in-memory queue:

``` python
from tests.dummy_celery import DummyCelerySignalProcessor, DummyTask

task = DummyTask()
processor = DummyCelerySignalProcessor(task)
processor.setup()
Product.objects.create(...)   # queues payloads, no Redis write yet
assert task.calls[0][0] == "upsert"
task.apply()                  # worker: apply_index_action(...)
processor.teardown()
```

`DummyTask.delay` JSON-round-trips the payload so tests fail if you
accidentally queue a model instance.

## Error handling

| Setting | In-process (`RealtimeSignalProcessor`) | Celery / django-q |
| --- | --- | --- |
| `SIGNAL_ERRORS = "raise"` | Redis errors propagate from `dispatch` | Broker/`delay` errors propagate; worker errors stay on the worker |
| `SIGNAL_ERRORS = "log"` | Logged; request continues | Same for `delay` failures |

For async processors, prefer `"log"` in the web process and retries on the
worker.

## Turning auto-index off

``` python
# everything
REDIS_SEARCH = {"AUTO_INDEX": False}

# one Document
class ProductDocument(Document):
    class Django:
        model = Product
        auto_index = False
```

Then index only with `redisearch populate` / `rebuild`.
