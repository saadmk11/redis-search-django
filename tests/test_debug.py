from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory
from django.views import View

from redis_search_django.debug import SearchDebugMixin, search_debug
from redis_search_django.debug.conf import (
    debug_setting,
    insert_before,
    load_panels,
    redact_url,
    resolve_show_toolbar,
    should_show,
    show_toolbar,
    slow_ms,
    store_size,
)
from redis_search_django.debug.overlay import attach_overlay, can_inject
from redis_search_django.debug.panels.base import Panel
from redis_search_django.debug.panels.config import ConfigPanel
from redis_search_django.debug.panels.indexes import IndexesPanel
from redis_search_django.debug.panels.queries import QueriesPanel
from redis_search_django.debug.store import ToolbarStore, store
from redis_search_django.debug.toolbar import DebugToolbar
from redis_search_django.query.instrument import QueryEvent, RecordingCollector

from .conftest import make_document
from .models import Category


class _DisabledPanel(Panel):
    title = "Off"
    panel_id = "off"

    def enabled(self) -> bool:
        return False


class _BoomPanel(Panel):
    title = "Boom"
    panel_id = "boom"

    def generate_stats(self, request, response) -> None:
        raise RuntimeError("panel failed")


class _BadTemplatePanel(Panel):
    title = "Bad"
    panel_id = "bad"
    template = "redis_search_django/debug/does-not-exist.html"


def _request(path="/", **meta):
    return RequestFactory().get(path, **meta)


def _event(**overrides):
    data = {
        "kind": "search",
        "document": "Cat",
        "index": "idx:cat",
        "query": "@name:(x)",
        "duration_ms": 12.5,
        "params": {"q": "x"},
        "total": 3,
        "offset": 0,
        "limit": 10,
    }
    data.update(overrides)
    return QueryEvent(**data)


@pytest.fixture(autouse=True)
def _clear_store():
    store.clear()
    yield
    store.clear()


@pytest.fixture
def toolbar_on(settings):
    settings.DEBUG = True
    return settings


def test_show_toolbar_debug_and_internal_ips(settings):
    settings.DEBUG = False
    assert show_toolbar(_request()) is False
    settings.DEBUG = True
    assert show_toolbar(_request()) is True
    settings.INTERNAL_IPS = ["127.0.0.1"]
    assert show_toolbar(_request(REMOTE_ADDR="10.0.0.1")) is False
    assert show_toolbar(_request(REMOTE_ADDR="127.0.0.1")) is True


def test_resolve_show_toolbar(toolbar_on, settings):
    assert resolve_show_toolbar()(_request()) is True
    settings.REDIS_SEARCH_DEBUG = {"SHOW_TOOLBAR": lambda request: False}
    assert resolve_show_toolbar()(_request()) is False
    settings.REDIS_SEARCH_DEBUG = {
        "SHOW_TOOLBAR": "redis_search_django.debug.conf.show_toolbar"
    }
    assert resolve_show_toolbar()(_request()) is True
    settings.REDIS_SEARCH_DEBUG = {"SHOW_TOOLBAR": 1}
    with pytest.raises(TypeError):
        resolve_show_toolbar()
    settings.REDIS_SEARCH_DEBUG = {
        "SHOW_TOOLBAR": "redis_search_django.debug.conf.DEFAULTS"
    }
    with pytest.raises(TypeError):
        resolve_show_toolbar()


def test_debug_setting_helpers(toolbar_on, settings):
    assert debug_setting("SLOW_MS") == 10.0
    settings.REDIS_SEARCH_DEBUG = {
        "SLOW_MS": True,
        "STORE_SIZE": 0,
        "INSERT_BEFORE": "",
        "PANELS": "nope",
    }
    assert slow_ms() == 10.0
    assert store_size() == 25
    assert insert_before() == "</body>"
    settings.REDIS_SEARCH_DEBUG = {
        "INSERT_BEFORE": 1,
        "SLOW_MS": "x",
        "STORE_SIZE": True,
        "PANELS": "nope",
    }
    assert insert_before() == "</body>"
    assert slow_ms() == 10.0
    assert store_size() == 25
    with pytest.raises(TypeError):
        load_panels()
    settings.REDIS_SEARCH_DEBUG = {
        "SLOW_MS": 25,
        "STORE_SIZE": 3,
        "INSERT_BEFORE": "</html>",
        "PANELS": [
            "redis_search_django.debug.panels.queries.QueriesPanel",
            QueriesPanel,
        ],
    }
    assert slow_ms() == 25.0
    assert store_size() == 3
    assert insert_before() == "</html>"
    panels = load_panels()
    assert panels[0] is QueriesPanel
    assert panels[1] is QueriesPanel
    assert load_panels()[0] is QueriesPanel
    settings.REDIS_SEARCH_DEBUG = "STACKTRACES"
    assert debug_setting("STACKTRACES") is True


def test_should_show_uses_resolved_callback(toolbar_on, settings):
    settings.REDIS_SEARCH_DEBUG = {
        "SHOW_TOOLBAR": lambda request: request.path == "/ok/"
    }
    assert should_show(_request("/ok/")) is True
    assert should_show(_request("/no/")) is False


def test_redact_url():
    assert redact_url("redis://localhost:6379/0") == "redis://localhost:6379/0"
    assert (
        redact_url("redis://:secret@localhost:6379/0") == "redis://***@localhost:6379/0"
    )
    assert (
        redact_url("redis://user:secret@localhost:6379/0")
        == "redis://user:***@localhost:6379/0"
    )
    assert (
        redact_url("redis://user:secret@localhost/0") == "redis://user:***@localhost/0"
    )


def test_store_lru_and_get_event():
    local = ToolbarStore()
    first = local.save([_event(query="a")])
    second = local.save([_event(query="b")])
    assert local.get(first)[0].query == "a"
    assert local.get_event(second, 0).query == "b"
    assert local.get_event(second, 5) is None
    assert local.get_event("missing", 0) is None
    local.clear()
    assert local.get(first) is None


def test_store_evicts_oldest(settings):
    settings.REDIS_SEARCH_DEBUG = {"STORE_SIZE": 1}
    local = ToolbarStore()
    first = local.save([_event(query="old")])
    second = local.save([_event(query="new")])
    assert local.get(first) is None
    assert local.get(second)[0].query == "new"


def test_store_get_refreshes_lru(settings):
    settings.REDIS_SEARCH_DEBUG = {"STORE_SIZE": 2}
    local = ToolbarStore()
    first = local.save([_event(query="a")])
    second = local.save([_event(query="b")])
    assert local.get(first)[0].query == "a"
    third = local.save([_event(query="c")])
    assert local.get(first)[0].query == "a"
    assert local.get(second) is None
    assert local.get(third)[0].query == "c"


def test_queries_panel_marks_slow_and_duplicate(rf, toolbar_on):
    collector = RecordingCollector()
    collector.record(_event(duration_ms=1, params={"q": "same"}))
    collector.record(_event(duration_ms=40, params={"q": "same"}))
    collector.record(_event(kind="get", query="JSON.GET k", duration_ms=1, params={}))
    toolbar = DebugToolbar(rf.get("/"), collector)
    toolbar.store_id = store.save(collector.events)
    panel = QueriesPanel(toolbar)
    panel.generate_stats(rf.get("/"), HttpResponse("ok"))
    assert panel.stats["count"] == 3
    assert panel.stats["slow"] == 1
    assert panel.stats["duplicates"] == 2
    assert panel.nav_subtitle().startswith("3")
    rows = panel.stats["rows"]
    assert rows[0]["duplicate"] is True
    assert rows[1]["slow"] is True
    assert rows[2]["explainable"] is False
    assert panel.stats["count"] == 3
    assert {item["kind"] for item in panel.stats["kinds"]} == {"search", "get"}
    assert "_rsd_explain=" in rows[0]["explain_url"]
    assert rows[0]["location"] is None
    assert rows[0]["stack"] == []


def test_queries_panel_shows_call_site(rf, toolbar_on):
    from redis_search_django.query.instrument import StackFrame

    event = _event(
        location="core/views.py:182 in get_context_data",
        stack=(StackFrame("/app/core/views.py", 182, "get_context_data"),),
    )
    collector = RecordingCollector()
    collector.record(event)
    toolbar = DebugToolbar(rf.get("/"), collector)
    toolbar.store_id = store.save(collector.events)
    panel = QueriesPanel(toolbar)
    panel.generate_stats(rf.get("/"), HttpResponse("ok"))
    row = panel.stats["rows"][0]
    assert row["location"] == "core/views.py:182 in get_context_data"
    assert row["stack"] == ["core/views.py:182 in get_context_data"]


def test_indexes_and_config_panels(rf, toolbar_on, settings):
    make_document("CatIdx", Category, ["name"])
    toolbar = DebugToolbar(rf.get("/"), RecordingCollector())
    indexes = IndexesPanel(toolbar)
    indexes.generate_stats(rf.get("/"), HttpResponse("ok"))
    assert indexes.stats["count"] >= 1
    assert indexes.nav_subtitle() == str(indexes.stats["count"])
    assert any(row["name"] == "CatIdx" for row in indexes.stats["rows"])

    settings.REDIS_SEARCH = {
        **settings.REDIS_SEARCH,
        "URL": "redis://user:pw@localhost:6379/0",
    }
    settings.REDIS_SEARCH_DEBUG = {"PANELS": [], "SHOW_TOOLBAR": show_toolbar}
    config = ConfigPanel(toolbar)
    config.generate_stats(rf.get("/"), HttpResponse("ok"))
    url_row = next(row for row in config.stats["redis_search"] if row["key"] == "URL")
    assert "***" in url_row["value"]
    assert "pw" not in url_row["value"]
    show_row = next(
        row for row in config.stats["debug"] if row["key"] == "SHOW_TOOLBAR"
    )
    assert "show_toolbar" in show_row["value"]


def test_config_display_lists():
    from redis_search_django.debug.panels.config import _display

    assert _display([]) == "[]"
    assert _display(("a", "b")) == "a, b"
    assert _display([1, "x"]) == "[1, 'x']"
    assert "show_toolbar" in _display(show_toolbar)


def test_toolbar_skips_disabled_and_records_panel_error(rf, toolbar_on, settings):
    settings.REDIS_SEARCH_DEBUG = {
        "PANELS": [
            "redis_search_django.debug.panels.queries.QueriesPanel",
            _DisabledPanel,
            _BoomPanel,
        ]
    }
    toolbar = DebugToolbar(rf.get("/"), RecordingCollector())
    ids = [panel.panel_id for panel in toolbar.panels]
    assert "off" not in ids
    assert "boom" in ids
    toolbar.generate_stats(HttpResponse("ok"))
    boom = next(panel for panel in toolbar.panels if panel.panel_id == "boom")
    assert boom.error and "panel failed" in boom.error
    assert "panel failed" in boom.content()


def test_panel_content_missing_template(rf):
    panel = _BadTemplatePanel(DebugToolbar(rf.get("/"), RecordingCollector()))
    html = panel.content()
    assert "rsd-error" in html


def test_panel_content_ok(rf, toolbar_on):
    toolbar = DebugToolbar(rf.get("/"), RecordingCollector())
    toolbar.generate_stats(HttpResponse("<html><body></body></html>"))
    html = toolbar.render()
    assert "rsd-debug" in html
    assert "Queries" in html


def _probe():
    class Probe(SearchDebugMixin, View):
        def get(self, request):
            return HttpResponse("<html><body>hi</body></html>")

    return Probe.as_view()


def test_mixin_requires_app(toolbar_on, monkeypatch):
    monkeypatch.setattr(
        "redis_search_django.debug.overlay.apps.is_installed", lambda name: False
    )
    with pytest.raises(ImproperlyConfigured, match="INSTALLED_APPS"):
        _probe()(_request("/"))


def test_mixin_skips_when_hidden(settings):
    settings.DEBUG = False
    response = _probe()(_request("/"))
    assert b"rsd-debug" not in response.content
    assert "X-RSD-Queries" not in response


def test_mixin_renders_template_response(toolbar_on):
    from django.template import engines
    from django.template.response import SimpleTemplateResponse

    class Page(SearchDebugMixin, View):
        def get(self, request):
            template = engines["django"].from_string("<html><body>hi</body></html>")
            return SimpleTemplateResponse(template)

    response = Page.as_view()(_request("/"))
    assert response.is_rendered
    assert b"rsd-debug" in response.content


def test_mixin_injects_html_and_headers(toolbar_on):
    response = _probe()(_request("/"))
    assert b'id="rsd-debug"' in response.content
    assert response["X-RSD-Queries"] == "0"
    assert "X-RSD-Query-Time" in response


def test_decorator_injects(toolbar_on):
    @search_debug
    def view(request):
        return HttpResponse("<html><body>fn</body></html>")

    response = view(_request("/"))
    assert b"rsd-debug" in response.content


def test_decorator_skips_when_hidden(settings):
    settings.DEBUG = False

    @search_debug
    def view(request):
        return HttpResponse("<html><body>fn</body></html>")

    response = view(_request("/"))
    assert b"rsd-debug" not in response.content


def test_mixin_skips_non_html_but_sets_headers(toolbar_on):
    class JsonView(SearchDebugMixin, View):
        def get(self, request):
            return HttpResponse(b'{"a":1}', content_type="application/json")

    response = JsonView.as_view()(_request("/"))
    assert b"rsd-debug" not in response.content
    assert response["X-RSD-Queries"] == "0"


def test_attach_skips_streaming_encoded_and_duplicate(toolbar_on):
    request = _request("/")
    collector = RecordingCollector()
    streamed = StreamingHttpResponse(iter([b"<body></body>"]))
    out = attach_overlay(request, streamed, collector)
    assert out is streamed
    assert streamed.streaming is True
    assert can_inject(streamed) is False
    assert "X-RSD-Queries" not in streamed

    encoded = HttpResponse("<html><body>z</body></html>")
    encoded["Content-Encoding"] = "gzip"
    encoded_out = attach_overlay(request, encoded, collector)
    assert b"rsd-debug" not in encoded_out.content
    assert encoded_out["X-RSD-Queries"] == "0"

    already = HttpResponse('<html><body><div id="rsd-debug"></div></body></html>')
    injected = attach_overlay(request, already, collector)
    assert injected.content.count(b'id="rsd-debug"') == 1


def test_attach_skips_missing_marker_and_bad_charset(toolbar_on):
    request = _request("/")
    collector = RecordingCollector()
    unmarked = HttpResponse("<html>nope</html>")
    assert b"rsd-debug" not in attach_overlay(request, unmarked, collector).content

    broken = HttpResponse(b"\xff\xfe<body></body>")
    broken.charset = "utf-8"
    original_broken = broken.content
    attach_overlay(request, broken, collector)
    assert broken.content == original_broken
    assert b"rsd-debug" not in broken.content

    weird = HttpResponse("<body>ok</body>")
    weird.charset = "not-a-codec"
    original_weird = weird.content
    attach_overlay(request, weird, collector)
    assert weird.content == original_weird
    assert b"rsd-debug" not in weird.content


def test_attach_custom_insert_before_and_bad_charset_type(toolbar_on, settings):
    settings.REDIS_SEARCH_DEBUG = {"INSERT_BEFORE": "</html>"}
    request = _request("/")
    collector = RecordingCollector()
    out = attach_overlay(
        request, HttpResponse("<html><body>x</body></html>"), collector
    )
    assert b"rsd-debug" in out.content
    html = out.content.lower()
    assert html.index(b"rsd-debug") < html.index(b"</html>")

    typed = HttpResponse("<html><body>y</body></html>")
    typed.charset = 123  # type: ignore[assignment]
    original = typed.content
    attach_overlay(request, typed, collector)
    assert typed.content == original
    assert b"rsd-debug" not in typed.content


def test_attach_encode_failure_and_content_length(toolbar_on, monkeypatch):
    request = _request("/")
    collector = RecordingCollector()
    response = HttpResponse("<html><body>ok</body></html>")
    response.charset = "ascii"
    monkeypatch.setattr(
        "redis_search_django.debug.overlay.DebugToolbar.render",
        lambda self: '<div id="rsd-debug">café</div>',
    )
    attach_overlay(request, response, collector)
    assert b"rsd-debug" not in response.content
    assert b"<body>ok</body>" in response.content

    sized = HttpResponse("<html><body>hi</body></html>")
    sized["Content-Length"] = str(len(sized.content))
    monkeypatch.setattr(
        "redis_search_django.debug.overlay.DebugToolbar.render",
        lambda self: '<div id="rsd-debug">ok</div>',
    )
    out = attach_overlay(request, sized, collector)
    assert b"rsd-debug" in out.content
    assert out["Content-Length"] == str(len(out.content))


def test_attach_passthrough_non_response(toolbar_on):
    assert attach_overlay(_request("/"), "nope", RecordingCollector()) == "nope"  # type: ignore[arg-type]


def test_can_inject_rejects_non_bytes():
    fake = Mock()
    fake.streaming = False
    fake.get.side_effect = lambda key, default="": (
        "text/html" if key == "Content-Type" else default
    )
    fake.content = "not-bytes"
    assert can_inject(fake) is False


def test_wrap_resets_listener_on_error(toolbar_on):
    from redis_search_django.debug.mixins import wrap_request
    from redis_search_django.query.instrument import current_listener

    def boom():
        raise RuntimeError("view failed")

    with pytest.raises(RuntimeError, match="view failed"):
        wrap_request(_request("/"), boom)
    assert current_listener() is None


@pytest.mark.asyncio
async def test_async_mixin_and_decorator(toolbar_on):
    class AsyncView(SearchDebugMixin, View):
        async def get(self, request):
            return HttpResponse("<html><body>async</body></html>")

    response = await AsyncView.as_view()(_request("/"))
    assert b"rsd-debug" in response.content

    @search_debug
    async def view(request):
        return HttpResponse("<html><body>afn</body></html>")

    decorated = await view(_request("/"))
    assert b"rsd-debug" in decorated.content

    from django.test.utils import override_settings

    with override_settings(DEBUG=False):

        @search_debug
        async def hidden(request):
            return HttpResponse("<html><body>x</body></html>")

        skipped = await hidden(_request("/"))
    assert b"rsd-debug" not in skipped.content

    @search_debug
    async def explain_only(request):
        return HttpResponse("<html><body>nope</body></html>")

    explained = await explain_only(
        RequestFactory().get("/", {"_rsd_explain": "missing:0"})
    )
    assert explained.status_code == 404


@pytest.mark.asyncio
async def test_async_wrap_resets_listener_on_error(toolbar_on):
    from redis_search_django.debug.mixins import wrap_request
    from redis_search_django.query.instrument import current_listener

    async def boom():
        raise RuntimeError("async failed")

    result = wrap_request(_request("/"), boom)
    with pytest.raises(RuntimeError, match="async failed"):
        await result
    assert current_listener() is None


def test_explain_via_same_view(client, toolbar_on, settings, monkeypatch):
    from redis_search_django.debug import views as debug_views

    event = _event()
    key = store.save([event, _event(kind="get", query="JSON.GET k")])
    monkeypatch.setattr(
        debug_views,
        "get_redis_connection",
        lambda: Mock(**{"ft.return_value.explain.return_value": "INTERSECT { @name }"}),
    )
    url = f"/?_rsd_explain={key}:0"
    response = client.get(url)
    assert response.status_code == 200
    assert "INTERSECT" in response.json()["explain"]

    missing = client.get("/?_rsd_explain=nope:0")
    assert missing.status_code == 404

    bad_kind = client.get(f"/?_rsd_explain={key}:1")
    assert bad_kind.status_code == 400

    settings.DEBUG = False
    forbidden = client.get(url)
    assert forbidden.status_code == 403


def test_explain_view_redis_error(client, toolbar_on, monkeypatch):
    from redis_search_django.debug import views as debug_views

    key = store.save([_event()])
    ft = Mock()
    ft.explain.side_effect = RuntimeError("no index")
    monkeypatch.setattr(
        debug_views, "get_redis_connection", lambda: Mock(ft=lambda name: ft)
    )
    response = client.get(f"/?_rsd_explain={key}:0")
    assert response.status_code == 400
    assert "no index" in response.json()["error"]


def test_explain_param_invalid(client, toolbar_on):
    assert client.get("/?_rsd_explain=nocolon").status_code == 404
    assert client.get("/?_rsd_explain=:0").status_code == 404
    assert client.get("/?_rsd_explain=abc:nope").status_code == 404


def test_explain_url_requires_store_id(rf):
    from redis_search_django.debug.panels.queries import _explain_url

    assert _explain_url(rf.get("/"), "", 0) == ""


def test_panel_defaults(rf, toolbar_on):
    panel = Panel(DebugToolbar(rf.get("/"), RecordingCollector()))
    assert panel.generate_stats(rf.get("/"), HttpResponse("ok")) is None
    assert panel.nav_subtitle() == ""
    assert panel.enabled() is True
    assert "nothing to show" in panel.content()


def test_document_row_without_model():
    from redis_search_django.debug.panels.indexes import _document_row
    from redis_search_django.enums import Storage

    class Meta:
        model = None
        index_alias = "idx"
        key_prefix = "p:"
        storage = Storage.JSON
        dialect = 2
        auto_index = True
        fields: dict = {}

    class Doc:
        __name__ = "NoModel"
        _meta = Meta()

    row = _document_row(Doc)
    assert row["model"] == ""
    assert row["label"] == "Doc"


def test_display_callable_without_module():
    from redis_search_django.debug.panels.config import _display

    def fn() -> None:
        return None

    fn.__module__ = ""
    assert _display(fn).endswith("fn")


def test_debug_ready_requires_main_app(monkeypatch):
    from django.apps import apps

    from redis_search_django.debug.apps import RedisSearchDebugConfig

    monkeypatch.setattr(
        "redis_search_django.debug.apps.apps.is_installed", lambda name: False
    )
    config = apps.get_app_config("redis_search_django_debug")
    assert isinstance(config, RedisSearchDebugConfig)
    with pytest.raises(ImproperlyConfigured, match="requires redis_search_django"):
        config.ready()
    monkeypatch.setattr(
        "redis_search_django.debug.apps.apps.is_installed",
        lambda name: name == "redis_search_django",
    )
    config.ready()


def test_empty_toolbar_does_not_evict_store(rf, toolbar_on, settings):
    settings.REDIS_SEARCH_DEBUG = {"STORE_SIZE": 1}
    first = store.save([_event(query="keep")])
    toolbar = DebugToolbar(rf.get("/"), RecordingCollector())
    toolbar.generate_stats(HttpResponse("<html><body></body></html>"))
    assert toolbar.store_id == ""
    assert store.get(first)[0].query == "keep"


def test_carry_helpers(rf, toolbar_on):
    from django.core import signing
    from django.http import HttpResponseRedirect

    from redis_search_django.debug.carry import (
        CARRY_COOKIE,
        CARRY_SALT,
        clear_carry_cookie,
        is_redirect,
        persist_redirect,
        pop_carried_events,
        request_origin,
    )

    request = rf.post("/save/")
    assert request_origin(request) == "POST /save/"
    assert is_redirect(HttpResponseRedirect("/x/")) is True
    assert is_redirect(HttpResponse("ok")) is False

    collector = RecordingCollector()
    redirect = HttpResponseRedirect("/after/")
    assert persist_redirect(request, redirect, collector) is None
    assert CARRY_COOKIE not in redirect.cookies

    collector.record(
        _event(kind="write", query="JSON.SET k", key="k", origin="POST /already/")
    )
    store_id = persist_redirect(request, redirect, collector)
    assert store_id
    assert CARRY_COOKIE in redirect.cookies

    nxt = rf.get("/after/")
    nxt.COOKIES[CARRY_COOKIE] = redirect.cookies[CARRY_COOKIE].value
    carried = pop_carried_events(nxt)
    assert len(carried) == 1
    assert carried[0].carried is True
    assert carried[0].origin == "POST /already/"

    nxt.COOKIES[CARRY_COOKIE] = "not-a-signature"
    assert pop_carried_events(nxt) == []
    nxt.COOKIES[CARRY_COOKIE] = signing.dumps(1, salt=CARRY_SALT)
    assert pop_carried_events(nxt) == []
    nxt.COOKIES[CARRY_COOKIE] = signing.dumps("missing-id", salt=CARRY_SALT)
    assert pop_carried_events(nxt) == []

    html = HttpResponse("ok")
    html.set_cookie(CARRY_COOKIE, "x")
    clear_carry_cookie(html)
    assert html.cookies[CARRY_COOKIE]["max-age"] == 0
    assert html.cookies[CARRY_COOKIE]["samesite"] == "Lax"


def test_finalize_response_redirect_and_carried(rf, toolbar_on):
    from django.http import HttpResponseRedirect

    from redis_search_django.debug.overlay import finalize_response

    collector = RecordingCollector()
    collector.record(_event(kind="write", query="JSON.SET k"))
    redirect = finalize_response(
        rf.post("/save/"), HttpResponseRedirect("/x/"), collector
    )
    assert redirect.status_code == 302
    assert redirect["X-RSD-Queries"] == "1"

    carried = [
        _event(kind="write", query="JSON.SET k", carried=True, origin="POST /save/")
    ]
    page = finalize_response(
        rf.get("/x/"),
        HttpResponse("<html><body>ok</body></html>"),
        RecordingCollector(),
        carried,
    )
    assert b'<pre class="rsd-code">JSON.SET k' in page.content
    assert b"previous request" in page.content
    assert page["X-RSD-Queries"] == "0"
    assert finalize_response(rf.get("/"), "nope", RecordingCollector()) == "nope"


def test_queries_panel_write_and_carried(rf, toolbar_on):
    collector = RecordingCollector()
    collector.record(
        _event(
            kind="write",
            query="JSON.SET k",
            duration_ms=1,
            carried=True,
            origin="POST /",
        )
    )
    toolbar = DebugToolbar(rf.get("/"), collector)
    toolbar.store_id = store.save(collector.events)
    panel = QueriesPanel(toolbar)
    panel.generate_stats(rf.get("/"), HttpResponse("ok"))
    assert panel.stats["count"] == 0
    assert panel.stats["carried"] == 1
    assert panel.stats["kinds"] == []
    assert panel.stats["rows"][0]["explainable"] is False
    assert panel.stats["rows"][0]["origin"] == "POST /"


def test_wrap_passthrough_non_response(toolbar_on):
    from redis_search_django.debug.mixins import wrap_request

    assert wrap_request(_request("/"), lambda: "raw") == "raw"
