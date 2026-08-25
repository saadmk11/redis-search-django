# Example project

A small catalog you can click through to try every public feature of
redis-search-django. Plain HTML, no extra frontend.

**Full walkthrough** (run, dummy data, what each page is, things to try):
[Demo app](../docs/demo.md).

```bash
uv sync --group example
docker compose up -d
uv run python example/manage.py migrate
uv run python example/manage.py loaddata catalog
uv run python example/manage.py redisearch rebuild
uv run python example/manage.py runserver
```

Open http://127.0.0.1:8000/
