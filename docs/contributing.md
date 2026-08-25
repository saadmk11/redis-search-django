---
icon: lucide/git-pull-request
---

# Contribute

Patches are welcome. This page is how to run the suite, keep coverage at 100%,
and match the checks that run on a pull request.

You need [uv](https://docs.astral.sh/uv/), Docker, and a clone of this repo.

``` console
$ git clone git@github.com:saadmk11/redis-search-django.git
$ cd redis-search-django
$ uv sync --group dev
$ docker compose up -d
$ uv run pytest
```

Redis is the Compose service on `6379` (`redis/redis-stack`). Tests that talk
to Query Engine skip if Redis is down; run Compose before a full suite.

## Checks

| What | Command |
| --- | --- |
| Tests + 100% coverage | `uv run pytest` |
| Full matrix | `uvx --with tox-uv tox` |
| Lint / format | `uv run ruff check` and `uv run ruff format` |
| Types | `uv run pre-commit run mypy --all-files` |
| Docs preview | `uv sync --group docs && uv run zensical serve` |

`pytest` is configured with `--cov-fail-under=100`. A patch that leaves a
branch untested will fail locally and in CI.

The GitHub workflow runs every tox env: Python **3.10–3.15** and free-threaded
**3.15t**, against Django **5.2**, **6.0**, and **6.1** (3.10 / 3.11 only with
5.2). `pre-commit.ci` runs Ruff and mypy on the PR.

Install hooks so those fail on your machine first:

``` console
$ uv run pre-commit install
```

One env, or one test:

``` console
$ uvx --with tox-uv tox -e py314-django61
$ uv run pytest tests/test_query.py -k filter
```

## Code

- Keep the public API Django-shaped (`Document.objects.filter`, stock
  `Paginator`, `Q`). Do not import `redis` from application examples; re-export
  types from `redis_search_django`.
- New behavior needs a test that would fail without the change. Prefer a
  real Redis path over a mock when the code talks to Query Engine.
- Process-local caches (Redis clients, live index prefixes) must stay safe
  if two threads hit them at once. Free-threaded **3.15t** is in the matrix.
- Do not drop coverage below 100%. Do not add `# type: ignore` to silence a
  stub if a small helper can wrap the redis-py gap.
- Match the surrounding file: Ruff format, no drive-by refactors, short
  comments only for non-obvious constraints.

The [demo app](demo.md) is the manual lab. After a query or indexing change,
load the catalog and click the page that exercises that API.

## Docs

User-facing pages live in `docs/` and are built with Zensical
(`zensical.toml`). Preview at http://127.0.0.1:8002. If you change a public
name, a setting, or a command flag, update the matching page in the same
patch.

GitHub Actions (`.github/workflows/docs.yml`) builds the site and deploys it
to GitHub Pages on push to `main` or `1.0`. The published URL is
https://saadmk11.github.io/redis-search-django/. In the repository
**Settings → Pages**, set **Source** to **GitHub Actions** (once). That is a
repo setting, not something the workflow can flip.

## Pull requests

Open a PR against `main` (or the branch the maintainers name for the
release). Describe the user-visible change and how you tested it. Keep the
diff scoped to the request.

The project is [MIT](https://github.com/saadmk11/redis-search-django/blob/1.0/LICENSE).
