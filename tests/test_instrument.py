from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from redis_search_django.enums import Storage
from redis_search_django.query.instrument import (
    QueryEvent,
    RecordingCollector,
    capture_queries,
    current_listener,
    query_text,
    sanitize,
    sanitize_params,
)
from redis_search_django.query.results import SearchHit

from .conftest import make_document
from .models import Category, Tag


class _BrokenQuery:
    def query_string(self):
        raise RuntimeError("nope")


def test_query_text_variants():
    assert query_text(None) == ""
    assert query_text("@price:[1 2]") == "@price:[1 2]"

    class Q:
        def query_string(self):
            return "@name:ok"

    assert query_text(Q()) == "@name:ok"
    assert query_text(object()).startswith("<")
    fallback = query_text(_BrokenQuery())
    assert fallback
    assert "BrokenQuery" in fallback or fallback.startswith("<")


def test_query_text_broken_falls_back_to_str():
    class Both:
        def query_string(self):
            raise ValueError("boom")

        def __str__(self) -> str:
            return "as-string"

    assert query_text(Both()) == "as-string"


def test_sanitize_and_params():
    assert sanitize(b"abc") == "<3-byte blob>"
    assert sanitize(bytearray(b"xy")) == "<2-byte blob>"
    assert sanitize(memoryview(b"z")) == "<1-byte blob>"
    assert sanitize([1.0, 2.0]) == "<2-float vector>"
    assert sanitize(["a", {"b": b"c"}]) == ["a", {"b": "<1-byte blob>"}]
    assert sanitize(None) is None
    assert sanitize(True) is True
    assert sanitize(3.5) == 3.5
    assert sanitize(object()).startswith("<")
    assert sanitize_params({"q": b"ab", 1: "x"}) == {"q": "<2-byte blob>", "1": "x"}


def test_query_event_commands():
    search = QueryEvent(
        kind="search",
        document="Product",
        index="idx:p",
        query="@name:(shoes)",
        duration_ms=1.2,
        offset=0,
        limit=20,
        sort="-price",
        dialect=2,
    )
    assert "FT.SEARCH idx:p" in search.redis_command()
    assert "LIMIT 0 20" in search.redis_command()
    assert "SORTBY price DESC" in search.redis_command()
    assert "DIALECT 2" in search.redis_command()

    asc = QueryEvent(
        kind="search",
        document="P",
        index="idx",
        query="*",
        duration_ms=1,
        sort="name",
        dialect=0,
    )
    assert "SORTBY name ASC" in asc.redis_command()
    assert "DIALECT" not in asc.redis_command()

    assert (
        QueryEvent("aggregate", "P", "idx", "*", 1.0).redis_command()
        == "FT.AGGREGATE idx '*'"
    )
    assert (
        QueryEvent("explain", "P", "idx", "*", 1.0).redis_command()
        == "FT.EXPLAIN idx '*'"
    )
    get = QueryEvent("get", "P", "idx", "JSON.GET k", 1.0)
    assert get.redis_command() == "JSON.GET k"
    write = QueryEvent("write", "P", "idx", "JSON.SET k", 1.0)
    assert write.redis_command() == "JSON.SET k"
    delete = QueryEvent("delete", "P", "idx", "DEL k", 1.0)
    assert delete.redis_command() == "DEL k"


def test_reset_listener_from_other_context():
    from contextvars import copy_context

    from redis_search_django.query.instrument import reset_listener, set_listener

    holder: dict[str, object] = {}

    def install() -> None:
        holder["token"] = set_listener(RecordingCollector())

    copy_context().run(install)
    reset_listener(holder["token"])  # type: ignore[arg-type]
    assert current_listener() is None


def test_hidden_stack_frame_and_call_site():
    from redis_search_django.query.instrument import (
        StackFrame,
        capture_call_site,
        hidden_stack_frame,
    )

    assert hidden_stack_frame("") is True
    assert hidden_stack_frame("<frozen importlib>") is True
    assert hidden_stack_frame("/venv/lib/site-packages/redis/client.py") is True
    assert hidden_stack_frame("/p/redis_search_django/query/queryset.py") is True
    assert hidden_stack_frame("/p/redis_search_django/debug/mixins.py") is True
    assert hidden_stack_frame("/p/redis_search_django/indexer.py") is True
    assert hidden_stack_frame("/p/redis_search_django/signals.py") is True
    assert hidden_stack_frame("/p/redis_search_django/actions.py") is True
    assert hidden_stack_frame("/lib/contextlib.py") is True
    assert hidden_stack_frame("/venv/bin/pytest") is True
    assert hidden_stack_frame("/app/_pytest/runner.py") is True
    assert hidden_stack_frame("/app/views.py", "django.views.generic.base") is True
    assert hidden_stack_frame("/app/plugin.py", "pluggy._manager") is True
    assert hidden_stack_frame("/app/core/views.py", "core.views") is False
    assert hidden_stack_frame("/app/core/views.py", "") is False
    frame = StackFrame("/proj/core/views.py", 182, "get_context_data")
    assert frame.label() == "core/views.py:182 in get_context_data"
    assert StackFrame("views.py", 1, "fn").label() == "views.py:1 in fn"
    location, stack = capture_call_site()
    assert location
    assert "test_instrument.py" in location
    assert stack
    assert stack[0].function == "test_hidden_stack_frame_and_call_site"


def test_observe_records_filtered_location():
    from redis_search_django.query.instrument import observe

    with capture_queries() as collector:
        with observe(kind="search", document="D", index="i", query="*"):
            pass
    event = collector.events[0]
    assert event.location
    assert "query/instrument.py" not in event.location.replace("\\", "/")
    assert "test_instrument.py" in event.location
    assert event.stack
    hidden = "/redis_search_django/query/"
    assert all(hidden not in frame.filename.replace("\\", "/") for frame in event.stack)


def test_observe_skips_stack_when_disabled(settings):
    from redis_search_django.query.instrument import observe

    settings.REDIS_SEARCH_DEBUG = {"STACKTRACES": False}
    with capture_queries() as collector:
        with observe(kind="search", document="D", index="i", query="*"):
            pass
    assert collector.events[0].location is None
    assert collector.events[0].stack == ()


def _stack_info(
    filename: str, lineno: int, function: str, module: str
) -> SimpleNamespace:
    return SimpleNamespace(
        filename=filename,
        lineno=lineno,
        function=function,
        frame=SimpleNamespace(f_globals={"__name__": module}),
    )


def test_capture_call_site_empty_when_all_hidden(monkeypatch):
    from redis_search_django.query import instrument as inst

    monkeypatch.setattr(
        inst.inspect,
        "stack",
        lambda context=0: [
            _stack_info("<frozen>", 1, "hidden", "django.db"),
        ],
    )
    location, stack = inst.capture_call_site()
    assert location is None
    assert stack == ()


def test_capture_call_site_respects_stack_limit(monkeypatch):
    from redis_search_django.query import instrument as inst

    infos = [
        _stack_info(f"/app/{name}.py", index + 1, name, name)
        for index, name in enumerate(("alpha", "beta", "gamma"))
    ]
    monkeypatch.setattr(inst.inspect, "stack", lambda context=0: infos)
    monkeypatch.setattr(inst, "_STACK_LIMIT", 2)
    location, stack = inst.capture_call_site()
    assert location == "app/alpha.py:1 in alpha"
    assert [frame.function for frame in stack] == ["alpha", "beta"]


def test_stacktraces_enabled_non_dict_setting(settings):
    from redis_search_django.query.instrument import _stacktraces_enabled

    settings.REDIS_SEARCH_DEBUG = "nope"
    assert _stacktraces_enabled() is True
    settings.REDIS_SEARCH_DEBUG = {"STACKTRACES": 0}
    assert _stacktraces_enabled() is False


def test_observe_is_noop_without_listener():
    from redis_search_django.query.instrument import observe

    with observe(kind="search", document="D", index="i", query="*") as obs:
        assert obs is None
    from redis_search_django.query.instrument import observe_pipeline, observe_write

    with observe_write(object(), "JSON.SET", "k") as obs:
        assert obs is None
    with observe_pipeline(object(), 3) as obs:
        assert obs is None


def test_observe_write_records_with_listener(document_class):
    from redis_search_django.query.instrument import observe_pipeline, observe_write

    doc = document_class("CatObsWrite", Category, ["name"])
    with capture_queries() as collector:
        with observe_write(doc, "JSON.SET", "rsd:x") as obs:
            assert obs is not None
        with observe_write(doc, "DEL", "rsd:x"):
            pass
        with observe_pipeline(doc, 2):
            pass
    assert collector.events[0].kind == "write"
    assert collector.events[0].index == doc._meta.index_alias
    assert collector.events[1].kind == "delete"
    assert collector.events[2].query == "PIPELINE 2"


def test_capture_queries_resets_listener():
    assert current_listener() is None
    with capture_queries() as collector:
        assert current_listener() is collector
        collector.record(
            QueryEvent(kind="search", document="D", index="i", query="*", duration_ms=1)
        )
        assert collector.total_ms == 1
        collector.record(
            QueryEvent(
                kind="write",
                document="D",
                index="i",
                query="JSON.SET k",
                duration_ms=4,
                carried=True,
            )
        )
        assert collector.total_ms == 1
        assert len(collector.live_events()) == 1
    assert current_listener() is None


def test_none_queryset_is_not_recorded(document_class):
    doc = document_class("CatNone", Category, ["name"])
    with capture_queries() as collector:
        assert list(doc.objects.none()) == []
        assert doc.objects.none().count() == 0
    assert collector.events == []


def _fake_search_client(total=0, docs=None, plan="INTERSECT {}", rows=None):
    result = Mock()
    result.total = total
    result.docs = docs or []
    agg = Mock()
    agg.rows = rows if rows is not None else []
    ft = Mock()
    ft.search.return_value = result
    ft.explain.return_value = plan
    ft.aggregate.return_value = agg
    client = Mock()
    client.ft.return_value = ft
    return client, ft


def test_observed_search_records_sort_label(document_class, monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatSortObs", Category, ["name"])
    client, _ft = _fake_search_client(total=1)
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: client)
    with capture_queries() as collector:
        doc.objects.filter(name="X").count()
        doc.objects.order_by("name").count()
        doc.objects.order_by("-name").count()
    sorts = [event.sort for event in collector.events if event.kind == "search"]
    assert None in sorts
    assert "name" in sorts
    assert "-name" in sorts


def test_search_and_explain_are_recorded(document_class, monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatRec", Category, ["name"])
    client, _ft = _fake_search_client(total=2)
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: client)

    with capture_queries() as collector:
        assert doc.objects.filter(name="X")[:10].count() == 2
        assert doc.objects.filter(name="X").explain() == "INTERSECT {}"
    kinds = [event.kind for event in collector.events]
    assert "search" in kinds
    assert "explain" in kinds
    search = next(event for event in collector.events if event.kind == "search")
    assert search.document == "CatRec"
    assert search.total == 2
    assert search.offset == 0
    assert search.limit == 0


def test_search_records_error(document_class, monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatErr", Category, ["name"])
    client, ft = _fake_search_client()
    ft.search.side_effect = RuntimeError("down")
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: client)

    with capture_queries() as collector, pytest.raises(RuntimeError, match="down"):
        list(doc.objects.all()[:1])
    assert collector.events
    assert "RuntimeError" in (collector.events[0].error or "")


@pytest.mark.asyncio
async def test_async_search_and_explain_recorded(document_class, monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatA", Category, ["name"])
    result = Mock()
    result.total = 1
    result.docs = []
    ft = AsyncMock()
    ft.search.return_value = result
    ft.explain.return_value = "plan"
    client = Mock()
    client.ft.return_value = ft
    monkeypatch.setattr(qs_mod, "get_async_redis_connection", lambda: client)

    with capture_queries() as collector:
        assert await doc.objects.all()[:5].acount() == 1
        assert await doc.objects.all().aexplain() == "plan"
    assert {event.kind for event in collector.events} >= {"search", "explain"}


def test_get_by_pk_recorded(document_class, monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatGet", Category, ["name"])
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: Mock())
    monkeypatch.setattr(qs_mod, "json_get", lambda client, key: {"name": "X"})

    with capture_queries() as collector:
        hit = doc.objects.get_by_pk(1)
    assert isinstance(hit, SearchHit)
    assert collector.events[0].kind == "get"
    assert collector.events[0].total == 1
    assert "JSON.GET" in collector.events[0].query


def test_get_by_pk_missing_still_records(document_class, monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatMiss", Category, ["name"])
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: Mock())
    monkeypatch.setattr(qs_mod, "json_get", lambda client, key: None)

    with capture_queries() as collector, pytest.raises(doc.DoesNotExist):
        doc.objects.get_by_pk(9)
    assert collector.events[0].total == 0


def test_hash_get_by_pk_recorded(monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    uid = uuid.uuid4().hex[:8]
    doc = make_document(
        "TagHashGet",
        Tag,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "storage": "hash",
                    "name": f"idx:hashget.{uid}",
                    "prefix": f"rsd:hashget.{uid}:",
                },
            ),
        },
    )
    assert doc._meta.storage is Storage.HASH
    monkeypatch.setattr(qs_mod, "get_redis_connection", lambda: Mock())
    monkeypatch.setattr(qs_mod, "_load_hash", lambda client, key, cls: {"name": "t"})

    with capture_queries() as collector:
        doc.objects.get_by_pk(1)
    assert "HGETALL" in collector.events[0].query


@pytest.mark.asyncio
async def test_async_get_by_pk_recorded(document_class, monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    doc = document_class("CatAG", Category, ["name"])
    monkeypatch.setattr(qs_mod, "get_async_redis_connection", lambda: Mock())

    async def _aget(client, key):
        return {"name": "Y"}

    monkeypatch.setattr(qs_mod, "json_aget", _aget)
    with capture_queries() as collector:
        await doc.objects.aget_by_pk(2)
    assert collector.events[0].kind == "get"


@pytest.mark.asyncio
async def test_async_hash_get_by_pk_recorded(monkeypatch):
    from redis_search_django.query import queryset as qs_mod

    uid = uuid.uuid4().hex[:8]
    doc = make_document(
        "TagHashAGet",
        Tag,
        ["name"],
        extra_attrs={
            "Index": type(
                "Index",
                (),
                {
                    "storage": "hash",
                    "name": f"idx:hashaget.{uid}",
                    "prefix": f"rsd:hashaget.{uid}:",
                },
            ),
        },
    )

    async def _aload(client, key, cls):
        return {"name": "t"}

    monkeypatch.setattr(qs_mod, "get_async_redis_connection", lambda: Mock())
    monkeypatch.setattr(qs_mod, "_aload_hash", _aload)
    with capture_queries() as collector:
        await doc.objects.aget_by_pk(3)
    assert "HGETALL" in collector.events[0].query


def test_aggregate_recorded(document_class, monkeypatch):
    from redis_search_django.query import aggregate as agg_mod
    from redis_search_django.query.aggregate import Aggregate

    doc = document_class("CatAgg", Category, ["name"])
    client, _ft = _fake_search_client(rows=[{"name": "A", "count": 1}])
    monkeypatch.setattr(agg_mod, "get_redis_connection", lambda: client)

    with capture_queries() as collector:
        rows = doc.objects.aggregate(Aggregate().group_by("name").count("count"))
    assert rows
    assert collector.events[0].kind == "aggregate"
    assert collector.events[0].total == 1


@pytest.mark.asyncio
async def test_aaggregate_recorded(document_class, monkeypatch):
    from redis_search_django.query import aggregate as agg_mod
    from redis_search_django.query.aggregate import Aggregate

    doc = document_class("CatAAgg", Category, ["name"])
    ft = AsyncMock()
    result = Mock()
    result.rows = []
    ft.aggregate.return_value = result
    client = Mock()
    client.ft.return_value = ft
    monkeypatch.setattr(agg_mod, "get_async_redis_connection", lambda: client)

    with capture_queries() as collector:
        rows = await doc.objects.aaggregate(Aggregate().group_by("name").count("count"))
    assert rows == []
    assert collector.events[0].kind == "aggregate"
    assert collector.events[0].total == 0


def test_fingerprint_groups_duplicates():
    a = QueryEvent("search", "D", "i", "*", 1.0, params={"q": "x"})
    b = QueryEvent("search", "D", "i", "*", 2.0, params={"q": "x"})
    c = QueryEvent("search", "D", "i", "*", 2.0, params={"q": "y"})
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()
