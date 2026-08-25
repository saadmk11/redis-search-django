from __future__ import annotations

import pytest
from django.core.exceptions import FieldError
from django.core.paginator import Paginator

from redis_search_django.exceptions import MissingQueryParams, NotSupportedError
from redis_search_django.indexer import Indexer
from redis_search_django.query.aggregate import Aggregate, _rows_from_aggregate
from redis_search_django.query.compiler import ensure_query_params, param_names
from redis_search_django.query.results import SearchHit, SearchResult

from .conftest import make_document
from .helpers import alive_index, live_index
from .models import Category


def test_search_result_to_queryset_empty():
    doc = make_document("CatEmpty", Category, ["name"])
    result = SearchResult(hits=[], total=0, document_cls=doc)
    assert list(result.to_queryset()) == []


@pytest.mark.django_db
def test_search_result_to_queryset_preserves_order(category_obj):
    other = Category.objects.create(name="Other")
    doc = make_document("CatOrder", Category, ["name"])
    result = SearchResult(
        hits=[SearchHit(pk=str(other.pk)), SearchHit(pk=str(category_obj.pk))],
        total=2,
        document_cls=doc,
    )
    assert list(result.to_queryset()) == [other, category_obj]


def test_order_by_requires_one_sortable_field(document_class):
    doc = document_class("CatSort", Category, ["name"])
    with pytest.raises(NotSupportedError):
        doc.objects.order_by("name", "pk")
    qs = doc.objects.order_by("-name")
    assert qs._sort == "name"
    assert qs._sort_desc is True


def test_order_by_unknown_field(document_class):
    doc = document_class("CatBadSort", Category, ["name"])
    with pytest.raises(FieldError):
        doc.objects.order_by("missing")


def test_filter_after_extra_raises(document_class):
    doc = document_class("CatExtra", Category, ["name"])
    with pytest.raises(ValueError, match="replaces the compiled Q tree"):
        doc.objects.extra(query="*").filter(name="x")


def test_highlight_and_values_conflict(document_class):
    doc = document_class("CatHV", Category, ["name"])
    with pytest.raises(ValueError):
        doc.objects.highlight("name").values("name")
    with pytest.raises(ValueError):
        doc.objects.values("name").highlight("name")


def test_knn_requires_vector_field(document_class):
    doc = document_class("CatKnn", Category, ["name"])
    with pytest.raises(TypeError):
        doc.objects.knn()
    with pytest.raises(FieldError, match="no Vector field"):
        doc.objects.knn("red")


def test_raw_and_search_clone(document_class):
    doc = document_class("CatRaw", Category, ["name"])
    qs = doc.objects.search("trail")
    query, params = qs.raw()
    assert "name" in query
    assert params


def test_param_names_and_ensure_query_params():
    assert param_names("@name:($p1 $p2)") == frozenset({"p1", "p2"})
    assert param_names("@name:$p1*") == frozenset({"p1"})
    assert param_names("@available:{true}") == frozenset()
    ensure_query_params("@name:($p1)", {"p1": "shoes"})
    with pytest.raises(MissingQueryParams, match=r"\$p2") as exc:
        ensure_query_params("@name:($p1 $p2)", {"p1": "shoes"})
    assert exc.value.missing == ["p2"]


def test_raw_aggregate_request_missing_params_raises_before_redis(document_class):
    from redis_search_django import AggregateRequest, reducers

    doc = document_class("CatAggParams", Category, ["name"])
    request = AggregateRequest("@name:($p1)").group_by(
        ["@name"], reducers.count().alias("n")
    )
    with pytest.raises(MissingQueryParams, match=r"\$p1"):
        doc.objects.aggregate(request)


def test_extra_with_unbound_params_raises_before_redis(document_class):
    doc = document_class("CatExtraParams", Category, ["name"])
    qs = doc.objects.extra(query="@name:($p1)")
    with pytest.raises(MissingQueryParams, match=r"\$p1"):
        qs.count()
    with pytest.raises(MissingQueryParams, match=r"\$p1"):
        qs.explain()


def test_raw_after_extra_drops_unused_params(document_class):
    doc = document_class("CatRawExtra", Category, ["name"])
    query, params = doc.objects.search("trail").extra(query="*").raw()
    assert query == "*"
    assert params == {}


def test_extra_params_are_bound_on_the_queryset(document_class):
    doc = document_class("CatExtraBound", Category, ["name"])
    qs = doc.objects.extra(query="@name:($p1)", params={"p1": "alpha"})
    query, params = qs.raw()
    assert query == "@name:($p1)"
    assert params == {"p1": "alpha"}


def test_none_is_empty(document_class):
    doc = document_class("CatNone", Category, ["name"])
    qs = doc.objects.filter(name="x").none()
    assert qs.count() == 0
    assert qs.exists() is False
    assert qs.first() is None
    assert list(qs) == []
    assert qs.aggregate(object()) == []
    assert qs.none().aggregate(object()) == []


@pytest.mark.django_db(transaction=True)
def test_getitem_and_first_on_live_index(document_class, category_obj):
    doc = document_class("CatGetItem", Category, ["name"])
    with live_index(doc):
        Indexer().upsert(doc, category_obj)
        hit = doc.objects.all()[0]
        assert hit.pk == str(category_obj.pk)
        assert doc.objects.first().pk == str(category_obj.pk)


async def test_first_on_empty_index_is_none(document_class):
    doc = document_class("CatFirst", Category, ["name"])
    with live_index(doc):
        assert doc.objects.first() is None
        assert list(doc.objects.all()) == []
        with pytest.raises(IndexError):
            _ = doc.objects.all()[0]
        with pytest.raises(doc.DoesNotExist):
            doc.objects.get()
        assert await doc.objects.afirst() is None
        with pytest.raises(FieldError):
            doc.objects.last()
        with pytest.raises(FieldError):
            doc.objects.reverse()
        assert list(doc.objects.iterator()) == []
        assert [hit async for hit in doc.objects.aiterator()] == []


def test_reverse_requires_order_by(document_class):
    doc = document_class("CatRev", Category, ["name"])
    with pytest.raises(FieldError, match="order_by"):
        doc.objects.all().reverse()
    qs = doc.objects.order_by("name")
    assert qs.reverse()._sort_desc is True


async def test_manager_first_last_none_iterator_and_search_blank(document_class):
    doc = document_class("CatMgrVerbs", Category, ["name"])
    assert doc.objects.none().count() == 0
    assert doc.objects.search("   ")._q.children == []
    assert list(doc.objects.none().iterator()) == []
    assert [hit async for hit in doc.objects.none().aiterator()] == []
    assert await doc.objects.none().aaggregate(object()) == []
    assert await doc.objects.none().afirst() is None
    assert doc.objects.order_by("name").none().last() is None
    assert await doc.objects.order_by("name").none().alast() is None
    with pytest.raises(FieldError):
        await doc.objects.alast()
    qs = doc.objects.order_by("name")[:1]
    with pytest.raises(TypeError, match="slice"):
        qs.reverse()
    from redis_search_django.fields import Text

    with pytest.raises(FieldError, match="sortable"):
        document_class(
            "CatUnsort",
            Category,
            extra_attrs={"body": Text()},
        ).objects.order_by("body")


def test_highlight_return_fields_and_slices(document_class):
    doc = document_class("CatHV2", Category, ["name"])
    qs = doc.objects.return_fields("name")
    assert qs._return_fields == ("name",)
    qs = doc.objects.highlight("name")
    assert qs._highlight == ("name",)
    with pytest.raises(ValueError, match="step"):
        _ = doc.objects.all()[0:4:2]
    with pytest.raises(ValueError, match="Negative"):
        _ = doc.objects.all()[0:-1]
    open_ended = doc.objects.all()[1:]
    assert open_ended._slice[0] == 1
    assert open_ended._slice[1] is None


def test_search_result_protocols_and_to_queryset_limits(
    document_class, settings, caplog
):
    doc = document_class("CatRes", Category, ["name"])
    empty = SearchResult(hits=[], total=0, document_cls=doc)
    assert list(empty) == []
    assert len(empty) == 0
    assert bool(empty) is False
    hit = SearchHit(pk="1", data={"name": "n", "tags": [{"pk": "2", "name": "t"}]})
    assert hit.missing is None
    assert hit.tags[0].name == "t"
    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "TO_QUERYSET_WARN": 1,
        "TO_QUERYSET_MAX": 1,
    }
    result = SearchResult(
        hits=[SearchHit(pk="1"), SearchHit(pk="2")],
        total=2,
        document_cls=doc,
    )
    with caplog.at_level("WARNING"), pytest.raises(ValueError, match="TO_QUERYSET_MAX"):
        result.to_queryset()


def test_does_not_exist_is_per_document_and_document_not_found(document_class):
    from redis_search_django.exceptions import DocumentNotFound

    first = document_class("CatDNE1", Category, ["name"])
    second = document_class("CatDNE2", Category, ["name"])
    assert first.DoesNotExist is not second.DoesNotExist
    assert issubclass(first.DoesNotExist, DocumentNotFound)
    with pytest.raises(first.DoesNotExist):
        try:
            raise first.DoesNotExist("missing")
        except second.DoesNotExist:
            pytest.fail("DoesNotExist leaked across document classes")


def test_negative_index_rejected(document_class):
    doc = document_class("CatSlice", Category, ["name"])
    with pytest.raises(ValueError, match="Negative"):
        _ = doc.objects.all()[-1]


@pytest.mark.django_db(transaction=True)
def test_paginator_uses_search_count(document_class):
    names = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for name in names:
        Category.objects.create(name=name)
    doc = document_class("CatPage", Category, ["name"])
    with live_index(doc):
        Indexer().upsert_queryset(doc, Category.objects.all())
        qs = doc.objects.order_by("name")
        paginator = Paginator(qs, 2)
        assert paginator.count == 5
        page = paginator.page(2)
        assert page.start_index() == 3
        assert [hit.name for hit in page.object_list] == ["delta", "epsilon"]


@pytest.mark.django_db(transaction=True)
async def test_async_count_exists_get_and_values(document_class):
    first = await Category.objects.acreate(name="alpha")
    second = await Category.objects.acreate(name="beta")
    doc = document_class("CatAsyncEval", Category, ["name"])
    async with alive_index(doc):
        indexer = Indexer()
        await indexer.aupsert(doc, first)
        await indexer.aupsert(doc, second)
        qs = doc.objects.filter(name="alpha")
        assert await qs.acount() == 1
        assert await qs.aexists() is True
        hit = await qs.aget()
        assert hit.name == "alpha"
        rows = [row async for row in doc.objects.values("name").filter(name="alpha")]
        assert rows == [{"name": "alpha"}]
        with pytest.raises(doc.MultipleObjectsReturned):
            await doc.objects.aget()
        with pytest.raises(doc.DoesNotExist):
            await doc.objects.aget(name="missing")
        assert doc.objects.get(name="alpha").name == "alpha"
        assert doc.objects.filter(name="alpha").get().name == "alpha"
        facets = await doc.objects.afacets("name")
        raw_facets = doc.objects.all()._facets_from_rows(
            ("raw_alias",),
            {"raw_alias": [{"raw_alias": "Shoes", "count": "2"}]},
        )
        assert raw_facets["raw_alias"] == [{"value": "Shoes", "count": 2}]
        assert {row["value"] for row in facets["name"]} == {"alpha", "beta"}
        grouped = await doc.objects.aaggregate(
            Aggregate().group_by("name").count("count")
        )
        assert {row["name"] for row in grouped} == {"alpha", "beta"}


@pytest.mark.django_db(transaction=True)
def test_hash_get_by_pk(document_class):
    category = Category.objects.create(name="HashGet")
    doc = document_class(
        "CatHashGet",
        Category,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "name": f"idx:test.category.hashget.{category.pk}",
                    "prefix": f"rsd:test.category.hashget.{category.pk}:",
                    "storage": "hash",
                },
            )
        },
    )
    with live_index(doc):
        Indexer().upsert(doc, category)
        assert doc.objects.get_by_pk(category.pk).name == "HashGet"


@pytest.mark.django_db(transaction=True)
async def test_hash_aget_by_pk(document_class):
    category = await Category.objects.acreate(name="AHashGet")
    doc = document_class(
        "CatAHashGet",
        Category,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "name": f"idx:test.category.ahashget.{category.pk}",
                    "prefix": f"rsd:test.category.ahashget.{category.pk}:",
                    "storage": "hash",
                },
            )
        },
    )
    async with alive_index(doc):
        await Indexer().aupsert(doc, category)
        hit = await doc.objects.aget_by_pk(category.pk)
        assert hit.name == "AHashGet"
        with pytest.raises(doc.DoesNotExist):
            await doc.objects.aget_by_pk(999_999)


def test_search_result_parses_json_blob_and_unknown_fields(document_class):
    doc = document_class("CatBlob", Category, ["name"])
    qs = doc.objects.return_fields("not_a_field").highlight("also_missing")

    class Raw:
        total = 1
        docs = [
            type(
                "D",
                (),
                {
                    "__dict__": {
                        "id": "idx:1",
                        "json": '{"name": "from-json"}',
                        "payload": "x",
                        "score": 1.0,
                    },
                    "score": 1.0,
                },
            )()
        ]

    query, _params = qs._search_args(offset=0, limit=1)
    assert query is not None
    parsed = qs._result_from_raw(Raw())
    assert parsed.hits[0].pk == "1"
    assert parsed.hits[0].name == "from-json"

    class Broken:
        total = 1
        docs = [
            type(
                "D",
                (),
                {
                    "__dict__": {"id": "idx:9", "json": "{not-json", "score": 0.0},
                    "score": 0.0,
                },
            )()
        ]

    broken = qs._result_from_raw(Broken())
    assert broken.hits[0].pk == "9"
    assert broken.hits[0].name is None


def test_aggregate_rows_from_dict_and_pairs():
    class DictRaw:
        rows = [{"@name": "Shoes", "count": "3"}]

    assert _rows_from_aggregate(DictRaw()) == [{"name": "Shoes", "count": "3"}]

    class PairRaw:
        rows = [["name", "Shoes", "count", "3"]]

    assert _rows_from_aggregate(PairRaw()) == [{"name": "Shoes", "count": "3"}]


@pytest.mark.django_db(transaction=True)
def test_aggregate_without_group_sorts_and_limits(document_class):
    Category.objects.create(name="Zebra")
    Category.objects.create(name="Apple")
    doc = document_class("CatAggNoGroup", Category, ["name"])
    with live_index(doc):
        Indexer().upsert_queryset(doc, Category.objects.all())
        rows = doc.objects.aggregate(Aggregate().sort_by("name").limit(1).load("name"))
        assert rows
        assert rows[0]["name"] == "Apple"


@pytest.mark.django_db(transaction=True)
async def test_async_evaluate_reuses_cache(document_class):
    category = await Category.objects.acreate(name="Cached")
    doc = document_class("CatAex", Category, ["name"])
    async with alive_index(doc):
        await Indexer().aupsert(doc, category)
        qs = doc.objects.all()
        first = await qs._aevaluate()
        second = await qs._aevaluate()
        assert first is second
        assert first.hits[0].name == "Cached"
        sync_qs = doc.objects.all()
        cached = sync_qs._evaluate()
        assert sync_qs._evaluate() is cached


@pytest.mark.django_db(transaction=True)
async def test_async_exhaust_warns_when_unsliced_total_exceeds_chunk(
    document_class, settings, caplog
):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "CHUNK_SIZE": 1}
    doc = document_class("CatAexWarn", Category, ["name"])
    await Category.objects.acreate(name="One")
    await Category.objects.acreate(name="Two")
    async with alive_index(doc):
        await Indexer().aupsert_queryset(doc, Category.objects.all())
        with caplog.at_level("WARNING"):
            result = await doc.objects.all()._aevaluate()
        assert result.total >= 2
        assert "Unsliced search exhausted" in caplog.text
