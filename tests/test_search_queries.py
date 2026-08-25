from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import pytest

from redis_search_django import Q
from redis_search_django.exceptions import NotSupportedError, UnsupportedLookup
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer

from .catalog import create_sample_book, make_catalog_documents
from .helpers import is_redis_running
from .models import Author, Book

pytestmark = [
    pytest.mark.skipif(not is_redis_running(), reason="Redis is not running"),
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture
def live_book_doc():
    docs = make_catalog_documents()
    book_doc = docs["book"]
    manager = IndexManager(book_doc)
    manager.create()
    yield book_doc
    manager.drop(delete_docs=True)


def _index(book_doc, *books: Book) -> None:
    indexer = Indexer()
    for book in books:
        indexer.upsert(book_doc, book)


def test_text_search_and_exact(live_book_doc):
    book = create_sample_book()
    other = create_sample_book(
        title="Quiet Room",
        summary="nothing matching",
        isbn="978-0002",
        author=Author.objects.create(name="Other", email="o@example.com"),
    )
    _index(live_book_doc, book, other)
    hits = list(live_book_doc.objects.search("trail running"))
    assert [hit.pk for hit in hits] == [str(book.pk)]
    exact = list(live_book_doc.objects.filter(title="Quiet Room"))
    assert [hit.pk for hit in exact] == [str(other.pk)]


def test_tag_slug_uuid_and_in(live_book_doc):
    book = create_sample_book()
    _index(live_book_doc, book)
    assert live_book_doc.objects.filter(isbn="978-0001").count() == 1
    assert (
        live_book_doc.objects.filter(sku="11111111-1111-1111-1111-111111111111").count()
        == 1
    )
    assert live_book_doc.objects.filter(isbn__in=["978-0001", "nope"]).count() == 1
    assert live_book_doc.objects.filter(genres__slug="fiction").count() == 1
    assert live_book_doc.objects.filter(genres__slug__in=["history", "x"]).count() == 1


def test_numeric_comparisons_and_range(live_book_doc):
    cheap = create_sample_book(price=Decimal("9.99"), pages=100, isbn="n-cheap")
    mid = create_sample_book(
        price=Decimal("19.50"),
        pages=240,
        isbn="n-mid",
        author=Author.objects.create(name="B", email="b@example.com"),
    )
    pricey = create_sample_book(
        price=Decimal("40.00"),
        pages=500,
        isbn="n-high",
        author=Author.objects.create(name="C", email="c@example.com"),
    )
    _index(live_book_doc, cheap, mid, pricey)
    assert live_book_doc.objects.filter(price__gte=19).count() == 2
    assert live_book_doc.objects.filter(price__lt=10).count() == 1
    assert live_book_doc.objects.filter(price__range=(10, 20)).count() == 1
    assert live_book_doc.objects.filter(pages__in=[100, 500]).count() == 2
    ordered = list(live_book_doc.objects.order_by("-price"))
    assert [hit.pk for hit in ordered] == [
        str(pricey.pk),
        str(mid.pk),
        str(cheap.pk),
    ]


def test_boolean_and_nullable(live_book_doc):
    on = create_sample_book(available=True, featured=True, isbn="b-on")
    off = create_sample_book(
        available=False,
        featured=None,
        isbn="b-off",
        author=Author.objects.create(name="D", email="d@example.com"),
    )
    _index(live_book_doc, on, off)
    assert live_book_doc.objects.filter(available=True).count() == 1
    assert live_book_doc.objects.filter(available=False).count() == 1
    assert live_book_doc.objects.filter(featured__isnull=True).count() == 1
    assert live_book_doc.objects.filter(featured__isnull=False).count() == 1


def test_optional_object_isnull(live_book_doc):
    with_pub = create_sample_book(isbn="obj-pub")
    without = create_sample_book(
        isbn="obj-nopub",
        publisher=None,
        author=Author.objects.create(name="Solo", email="solo@example.com"),
        sku=uuid.UUID("44444444-4444-4444-4444-444444444444"),
    )
    _index(live_book_doc, with_pub, without)
    missing = live_book_doc.objects.filter(publisher__isnull=True)
    present = live_book_doc.objects.filter(publisher__isnull=False)
    assert {hit.pk for hit in missing} == {str(without.pk)}
    assert {hit.pk for hit in present} == {str(with_pub.pk)}
    rewritten = live_book_doc.objects.filter(publisher__name__isnull=True)
    assert {hit.pk for hit in rewritten} == {str(without.pk)}


async def test_optional_object_isnull_async(live_book_doc):
    from asgiref.sync import sync_to_async

    with_pub = await sync_to_async(create_sample_book)(isbn="aobj-pub")
    without = await sync_to_async(create_sample_book)(
        isbn="aobj-nopub",
        publisher=None,
        author=await Author.objects.acreate(
            name="AsyncSolo", email="asolo@example.com"
        ),
        sku=uuid.UUID("55555555-5555-5555-5555-555555555555"),
    )
    indexer = Indexer()
    await indexer.aupsert(live_book_doc, with_pub)
    await indexer.aupsert(live_book_doc, without)

    missing = live_book_doc.objects.filter(publisher__isnull=True)
    present = live_book_doc.objects.filter(publisher__isnull=False)
    rewritten = live_book_doc.objects.filter(publisher__name__isnull=True)
    assert await missing.acount() == 1
    assert await present.acount() == 1
    assert await rewritten.acount() == 1
    assert {hit.pk async for hit in missing} == {str(without.pk)}
    assert {hit.pk async for hit in present} == {str(with_pub.pk)}
    assert {hit.pk async for hit in rewritten} == {str(without.pk)}
    assert await missing.aexists() is True


def test_date_datetime_and_time(live_book_doc):
    book = create_sample_book(
        published_on=datetime.date(2020, 5, 1),
        listed_at=datetime.datetime(2024, 6, 1, 12, 0, tzinfo=datetime.timezone.utc),
        shop_opens=datetime.time(9, 30),
    )
    _index(live_book_doc, book)
    after = datetime.date(2020, 1, 1)
    assert live_book_doc.objects.filter(published_on__gte=after).count() == 1
    assert (
        live_book_doc.objects.filter(
            listed_at__lt=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
        ).count()
        == 1
    )
    assert live_book_doc.objects.filter(shop_opens="09:30:00").count() == 1


def test_nested_and_boolean_q_combinations(live_book_doc):
    ada = Author.objects.create(name="Ada Lovelace", email="ada2@example.com")
    book = create_sample_book(author=ada, title="Engines", isbn="q-1")
    other = create_sample_book(
        title="Silence",
        summary="zzz",
        isbn="q-2",
        author=Author.objects.create(name="Eve", email="eve@example.com"),
    )
    _index(live_book_doc, book, other)
    qs = live_book_doc.objects.filter(
        Q(title__search="Engines") | Q(summary__search="zzz"),
        available=True,
        author__name="Ada Lovelace",
    ).exclude(isbn="q-2")
    hits = list(qs)
    assert [hit.pk for hit in hits] == [str(book.pk)]
    assert live_book_doc.objects.filter(publisher__slug="analytical").count() == 2


def test_facets_and_extra(live_book_doc):
    book = create_sample_book()
    _index(live_book_doc, book)
    facets = live_book_doc.objects.facets("genres__name")
    names = {row["value"] for row in facets["genres__name"]}
    assert "Fiction" in names or "fiction" in {str(v).lower() for v in names}
    extra = list(live_book_doc.objects.extra(query="*"))
    assert extra


def test_facets_pass_search_params(live_book_doc):
    book = create_sample_book(title="Trail Shoes", isbn="978-facet-1")
    other = create_sample_book(
        title="Quiet Night",
        isbn="978-facet-2",
        sku=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        author=Author.objects.create(name="Eve", email="eve-facet@example.com"),
        genres=[],
    )
    _index(live_book_doc, book, other)
    facets = live_book_doc.objects.filter(title__search="Trail").facets("genres__name")
    names = {row["value"] for row in facets["genres__name"]}
    assert names
    assert "Fiction" in names or "fiction" in {str(v).lower() for v in names}


def _other_book(**overrides: object) -> Book:
    overrides.setdefault(
        "author",
        Author.objects.create(
            name="Other Author", email=f"{uuid.uuid4().hex}@example.com"
        ),
    )
    overrides.setdefault("sku", uuid.uuid4())
    overrides.setdefault("isbn", uuid.uuid4().hex[:12])
    return create_sample_book(**overrides)


def _assert_search_aggregate_explain(qs, *, count: int, facet: str = "genres__name"):
    assert qs.count() == count
    assert qs.exists() is (count > 0)
    assert bool(qs) is (count > 0)
    assert len(list(qs)) == count
    plan = qs.explain()
    assert isinstance(plan, str) and plan
    grouped = qs.facets(facet)
    assert facet in grouped
    if count == 0:
        assert grouped[facet] == []
    return grouped


def test_filtered_queryset_runs_search_aggregate_and_explain(live_book_doc):
    trail = create_sample_book(title="Trail Shoes", isbn="m-trail", available=True)
    quiet = _other_book(
        title="Quiet Night",
        isbn="m-quiet",
        available=False,
        genres=[],
    )
    _index(live_book_doc, trail, quiet)

    cases = [
        (live_book_doc.objects.all(), 2),
        (live_book_doc.objects.extra(query="*"), 2),
        (live_book_doc.objects.filter(title__search="Trail"), 1),
        (live_book_doc.objects.filter(available=True), 1),
        (live_book_doc.objects.filter(available=False), 1),
        (live_book_doc.objects.filter(title__in=["Trail Shoes", "Nope"]), 1),
        (live_book_doc.objects.filter(title__in=["Trail Shoes"]), 1),
        (live_book_doc.objects.filter(title__in=[]), 0),
        (live_book_doc.objects.filter(isbn__in=[]), 0),
        (live_book_doc.objects.filter(pages__in=[]), 0),
        (live_book_doc.objects.filter(genres__name__in=["Fiction"]), 1),
        (live_book_doc.objects.filter(isbn__in=["m-trail", "missing"]), 1),
        (live_book_doc.objects.filter(pages__in=[240, 999]), 2),
        (live_book_doc.objects.filter(title__startswith="Trail"), 1),
        (live_book_doc.objects.exclude(available=True), 1),
        (live_book_doc.objects.exclude(isbn="m-quiet"), 1),
        (
            live_book_doc.objects.filter(title__search="Trail").exclude(isbn="missing"),
            1,
        ),
        (live_book_doc.objects.filter(title__search="missing-term"), 0),
        (
            live_book_doc.objects.filter(title__search="Trail").extra(query="*"),
            2,
        ),
    ]
    for qs, expected in cases:
        _assert_search_aggregate_explain(qs, count=expected)


def test_unsliced_iteration_warns(live_book_doc, settings, caplog):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    book = create_sample_book(title="Page One", isbn="978-page-1")
    other = _other_book(title="Page Two", isbn="978-page-2")
    _index(live_book_doc, book, other)
    with caplog.at_level("WARNING"):
        hits = list(live_book_doc.objects.all())
    assert len(hits) >= 2
    assert "Unsliced search exhausted" in caplog.text


def test_first_last_highlight_values_and_search_fields(live_book_doc):
    book = create_sample_book(title="First Last", isbn="978-fl-1")
    _index(live_book_doc, book)
    assert live_book_doc.objects.first() is not None
    assert live_book_doc.objects.order_by("title").last() is not None
    highlighted = list(live_book_doc.objects.highlight("title")[:1])
    assert highlighted
    values = list(live_book_doc.objects.values("title")[:1])
    assert isinstance(values[0], dict)
    limited = live_book_doc.objects.all()
    limited.document_cls._meta.search_fields_option = ("title",)
    hits = list(limited.search("First"))
    assert hits
    returned = list(live_book_doc.objects.return_fields("title")[:1])
    assert returned
    assert hasattr(returned[0], "title")


def test_aggregate_reducers_load_and_limit(live_book_doc):
    from redis_search_django import Aggregate

    book = create_sample_book(title="Agg Full", isbn="978-agg-full")
    _index(live_book_doc, book)
    rows = live_book_doc.objects.aggregate(
        Aggregate()
        .group_by("genres__name")
        .count("count")
        .avg("price", "avg_price")
        .sum("price", "sum_price")
        .min("price", "min_price")
        .max("price", "max_price")
        .tolist("title", "titles")
        .sort_by("count")
        .limit(5)
        .load("title")
    )
    assert rows
    assert "count" in rows[0]


def test_aggregate_sort_by_reducer_alias(live_book_doc):
    from redis_search_django import Aggregate

    on = create_sample_book(title="On One", isbn="978-agg-sort-1", available=True)
    also_on = _other_book(title="On Two", isbn="978-agg-sort-2", available=True)
    off = _other_book(title="Off", isbn="978-agg-sort-3", available=False)
    _index(live_book_doc, on, also_on, off)
    rows = live_book_doc.objects.aggregate(
        Aggregate().group_by("available").count("count").sort_by("-count")
    )
    by_flag = {str(row["available"]): int(row["count"]) for row in rows}
    assert by_flag == {"1": 2, "0": 1}
    assert [int(row["count"]) for row in rows] == [2, 1]


def test_aggregate_request_requires_params_unless_passed(live_book_doc):
    from redis_search_django import AggregateRequest, reducers
    from redis_search_django.exceptions import MissingQueryParams

    book = create_sample_book(title="Param Shoes", isbn="978-agg-p2")
    _index(live_book_doc, book)
    filtered = live_book_doc.objects.filter(title__search="Param")
    query, params = filtered.raw()
    request = AggregateRequest(query).group_by(
        ["@genres_name"], reducers.count().alias("n")
    )
    with pytest.raises(MissingQueryParams, match=r"\$p"):
        live_book_doc.objects.aggregate(request)
    rows = live_book_doc.objects.aggregate(request, query_params=params)
    assert rows
    assert filtered.aggregate(request)


def test_geo_and_vector_filters_fail_before_redis(live_book_doc):
    with pytest.raises(NotSupportedError, match="geo_distance"):
        live_book_doc.objects.filter(location__geo_distance=1).count()
    with pytest.raises(UnsupportedLookup):
        live_book_doc.objects.filter(location="1,2").count()
    with pytest.raises(UnsupportedLookup):
        live_book_doc.objects.filter(embedding=[0, 0, 0, 0]).count()
