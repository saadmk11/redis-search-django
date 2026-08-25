from __future__ import annotations

import pytest
from django.db.models import Q as DjangoQ

from redis_search_django import Q


def test_q_rejects_django_q_as_second_positional():
    with pytest.raises(TypeError, match=r"redis_search_django\.Q"):
        Q(Q(name="a"), DjangoQ(name="b"))
