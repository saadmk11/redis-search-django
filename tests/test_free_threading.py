"""Free-threaded CPython (3.15t+) support.

1.0 supports the no-GIL build starting at Python 3.15t. Regular 3.15 is
also in the matrix. These tests run on every interpreter: the GIL check
skips unless this is a 3.15+ free-threaded build; the concurrent index /
search work is the same path threads take when the GIL is off.
"""

from __future__ import annotations

import sys
import sysconfig
from concurrent.futures import ThreadPoolExecutor

import pytest

from redis_search_django.indexer import Indexer

from .conftest import make_document
from .helpers import is_redis_running, live_index
from .models import Category

_LIVE = pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")


def _is_free_threaded_build() -> bool:
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def test_free_threaded_315t_disables_the_gil():
    """3.15t CI sets PYTHON_GIL=0. Fail if this job still has the GIL on."""
    if sys.version_info < (3, 15) or not _is_free_threaded_build():
        pytest.skip("free-threaded support starts at CPython 3.15t")
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    assert callable(is_gil_enabled)
    assert is_gil_enabled() is False


@_LIVE
@pytest.mark.django_db(transaction=True)
def test_concurrent_index_and_search():
    """Several threads share one cached client to upsert and read hits."""
    categories = [Category.objects.create(name=f"FreeThread-{i}") for i in range(8)]
    doc = make_document("FreeThreadCat", Category, ["name"])
    indexer = Indexer()
    with live_index(doc):
        indexer.upsert_queryset(
            doc, Category.objects.filter(pk__in=[c.pk for c in categories])
        )

        def round_trip(category: Category) -> str:
            indexer.upsert(doc, category)
            return doc.objects.get(pk=category.pk).name

        with ThreadPoolExecutor(max_workers=8) as pool:
            names = list(pool.map(round_trip, categories))

        assert names == [category.name for category in categories]
        hits = list(doc.objects.all())
        assert len(hits) == 8
        assert {hit.name for hit in hits} == {category.name for category in categories}
