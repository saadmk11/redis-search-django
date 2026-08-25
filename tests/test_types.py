from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from redis_search_django.types import (
    as_document_payload,
    as_float,
    as_hash_mapping,
    as_int,
    as_str_list,
    is_field_input,
    is_index_value,
    is_primary_key,
    is_setting_value,
)


def test_is_primary_key_rejects_bool_and_accepts_scalars():
    assert is_primary_key("a")
    assert is_primary_key(1)
    assert is_primary_key(uuid4())
    assert not is_primary_key(True)
    assert not is_primary_key(None)


def test_is_field_input_and_index_value():
    assert is_field_input(None)
    assert is_field_input("x")
    assert is_field_input([1.0, 2.0])
    assert is_field_input(Decimal("1.5"))
    assert not is_field_input(object())
    assert is_index_value(None)
    assert is_index_value({"a": 1})
    assert is_index_value([1, "x"])
    assert not is_index_value(object())


def test_is_setting_value():
    assert is_setting_value(None)
    assert is_setting_value("s")
    assert is_setting_value(1)
    assert is_setting_value(True)
    assert is_setting_value(lambda: None)
    assert not is_setting_value(object())


def test_as_int_and_as_float():
    assert as_int(3) == 3
    assert as_int(4.2) == 4
    assert as_int("9") == 9
    assert as_int("nope") == 0
    assert as_int(True) == 0
    assert as_int(None, default=7) == 7
    assert as_int([1]) == 0
    assert as_float(1.5) == 1.5
    assert as_float(2) == 2.0
    assert as_float("3.25") == 3.25
    assert as_float("nope") is None
    assert as_float(True) is None
    assert as_float(None) is None
    assert as_float([1]) is None


def test_parse_helpers_used_by_index_and_hash():
    from redis_search_django.index import _parse_meta
    from redis_search_django.query.queryset import _pairs_to_hash

    assert _parse_meta(None) == {}
    assert _parse_meta(b'{"fingerprint": "x"}') == {"fingerprint": "x"}
    assert _parse_meta(1) == {}
    assert _pairs_to_hash("nope") == {}
    assert _pairs_to_hash({1: object(), "ok": 1}) == {"ok": 1}


def test_as_str_list_and_payloads():
    assert as_str_list(["a", 1, "b"]) == ["a", "b"]
    assert as_str_list("nope") == []
    assert as_document_payload("x") is None
    payload = as_document_payload({"pk": "1", 2: "skip", "ok": True})
    assert payload == {"pk": "1", "ok": True}
    assert as_hash_mapping("x") == {}
    assert as_hash_mapping({"a": 1}) == {"a": 1}
