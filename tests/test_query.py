from unittest import mock

import pytest
from pytest_django.asserts import assertQuerysetEqual

from redis_search_django.query import RediSearchQuery, RediSearchResult
from tests.models import Category


def test_search_result_str():
    result = RediSearchResult([], 10, None)
    assert str(result) == "<RediSearchResult 0 of 10>"


def test_search_result_repr():
    result = RediSearchResult([], 10, None)
    assert repr(result) == "<RediSearchResult 0 of 10>"


def test_search_result_iter():
    item_1 = mock.MagicMock()
    item_2 = mock.MagicMock()
    result = RediSearchResult([item_1, item_2], 10, None)
    assert list(result) == [item_1, item_2]


def test_search_result_len():
    result = RediSearchResult([mock.MagicMock(), mock.MagicMock()], 10, None)
    assert len(result) == 2


def test_search_result_getitem():
    item_1 = mock.MagicMock()
    item_2 = mock.MagicMock()
    result = RediSearchResult([item_1, item_2], 10, None)

    assert result[0] == item_1
    assert result[1] == item_2


def test_search_result_setitem():
    item = mock.MagicMock()
    result = RediSearchResult([mock.MagicMock()], 10, None)
    result[0] = item

    assert result[0] == item


def test_search_result_contains():
    item = mock.MagicMock()
    result = RediSearchResult([item], 10, None)
    assert item in result


def test_search_result_bool():
    result = RediSearchResult([mock.MagicMock()], 10, None)
    assert bool(result)


def test_search_result_clear():
    item = mock.MagicMock()
    result = RediSearchResult([item], 10, None)
    assert len(result) == 1
    assert result.hit_count == 10

    result.clear()

    assert len(result) == 0
    assert result.hit_count == 0


def test_search_result_add():
    item = mock.MagicMock()
    result = RediSearchResult([], 10, None)

    assert len(result) == 0

    result.add(item)

    assert len(result) == 1
    assert result.hit_count == 10

    result.add([item, item])

    assert len(result) == 3


def test_search_result_count():
    result = RediSearchResult([], 10, None)

    assert result.count() == 10

    result.clear()

    assert result.count() == 0

    result.add([mock.MagicMock(), mock.MagicMock()])

    assert result.count() == 0


def test_search_result_exists():
    result = RediSearchResult([mock.MagicMock()], 10, None)
    assert result.exists()
    result.clear()
    assert not result.exists()


@pytest.mark.django_db
def test_search_result_to_queryset(get_category):
    category = get_category
    item = mock.MagicMock(name="Item", pk=category.pk)

    assertQuerysetEqual(
        RediSearchResult([item], 10, Category).to_queryset(),
        Category.objects.all(),
    )
    assertQuerysetEqual(
        RediSearchResult([], 10, Category).to_queryset(), Category.objects.none()
    )


@pytest.mark.django_db
def test_search_result_to_queryset_no_model_set():
    with pytest.raises(ValueError):
        RediSearchResult([], 10, None).to_queryset()


@pytest.mark.django_db
def test_search_query_to_queryset(get_category):
    category = get_category
    item = mock.MagicMock(name="Item", pk=category.pk)

    query = RediSearchQuery(mock.MagicMock(), model=mock.MagicMock())
    query._model_cache = RediSearchResult([item], 10, Category)

    assertQuerysetEqual(query.to_queryset(), Category.objects.all())


@pytest.mark.django_db
def test_search_query_all():
    query = RediSearchQuery(mock.MagicMock(), model=mock.MagicMock())
    assert isinstance(query.all(), RediSearchQuery)
    new_query = query.all(batch_size=1000)
    assert isinstance(new_query, RediSearchQuery)
    assert new_query.limit == 1000


def test_search_query_count():
    query = RediSearchQuery(mock.MagicMock(), model=mock.MagicMock())
    query._model_cache = RediSearchResult([], 10, None)

    assert query.count() == 10


def test_search_query_exists():
    query = RediSearchQuery(mock.MagicMock(), model=mock.MagicMock())
    query._model_cache = RediSearchResult([mock.MagicMock()], 10, None)
    assert query.exists()


def test_search_query_copy():
    query = RediSearchQuery(mock.MagicMock(), model=mock.MagicMock())
    new_query = query.copy()
    assert query is not new_query


def test_search_query_paginate():
    query = RediSearchQuery(mock.MagicMock(), model=mock.MagicMock())
    offset = 10
    limit = 20
    query.paginate(offset=offset, limit=limit)
    assert query.offset == offset
    assert query.limit == limit


def test_search_query_execute():
    model = mock.MagicMock()
    model.db().execute_command.return_value = [1, [mock.MagicMock()]]
    model.from_redis.return_value = [mock.MagicMock()]
    query = RediSearchQuery([], model=model)

    result = query.execute()

    assert isinstance(result, RediSearchResult)
    assert len(result) == 1
    assert result.hit_count == 1
