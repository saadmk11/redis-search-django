from unittest import mock

import pytest
from django.core.paginator import EmptyPage, PageNotAnInteger

from redis_search_django.paginator import RediSearchPaginator
from redis_search_django.query import RediSearchQuery, RediSearchResult

from .models import Category


def test_paginator_validate_number():
    paginator = RediSearchPaginator(range(10), 5)
    assert paginator.validate_number(1) == 1
    assert paginator.validate_number("1") == 1

    with pytest.raises(PageNotAnInteger):
        paginator.validate_number("abc")

    with pytest.raises(EmptyPage):
        paginator.validate_number(0)

    with pytest.raises(EmptyPage):
        paginator.validate_number(-1)


@mock.patch("redis_search_django.query.RediSearchQuery.execute")
def test_paginator(execute):
    query = RediSearchQuery(mock.MagicMock(), model=mock.MagicMock())
    result = RediSearchResult([mock.MagicMock()], 100, Category)
    query._model_cache = result
    execute.return_value = result

    paginator = RediSearchPaginator(query, 5)
    paginator.page(1)

    assert paginator.count == 100
    assert paginator.num_pages == 20

    with pytest.raises(EmptyPage):
        paginator.page(50)
