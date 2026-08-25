from django.http import HttpResponse, HttpResponseRedirect
from django.urls import path
from django.views import View

from redis_search_django.debug import SearchDebugMixin, search_debug
from redis_search_django.query.instrument import QueryEvent, current_listener


class ProbeView(SearchDebugMixin, View):
    def get(self, request):
        return HttpResponse("<html><body>ok</body></html>")


class AsyncProbeView(SearchDebugMixin, View):
    async def get(self, request):
        return HttpResponse("<html><body>async</body></html>")


class AsyncTemplateProbeView(SearchDebugMixin, View):
    async def get(self, request):
        from django.template import engines
        from django.template.response import SimpleTemplateResponse

        template = engines["django"].from_string("<html><body>async-tpl</body></html>")
        return SimpleTemplateResponse(template)


@search_debug
def probe_fn(request):
    return HttpResponse("<html><body>fn</body></html>")


@search_debug
async def probe_async_fn(request):
    return HttpResponse("<html><body>afn</body></html>")


def plain(request):
    return HttpResponse("<html><body>plain</body></html>")


class WriteRedirectView(SearchDebugMixin, View):
    def post(self, request):
        listener = current_listener()
        if listener is not None:
            listener.record(
                QueryEvent(
                    kind="write",
                    document="Cat",
                    index="idx:cat",
                    query="JSON.SET rsd:cat:1",
                    duration_ms=1.5,
                    key="rsd:cat:1",
                )
            )
        return HttpResponseRedirect("/after/")


class AfterView(SearchDebugMixin, View):
    def get(self, request):
        return HttpResponse("<html><body>after</body></html>")


urlpatterns = [
    path("", ProbeView.as_view(), name="ok"),
    path("async/", AsyncProbeView.as_view(), name="async-ok"),
    path("async-tpl/", AsyncTemplateProbeView.as_view(), name="async-tpl"),
    path("fn/", probe_fn, name="fn"),
    path("afn/", probe_async_fn, name="afn"),
    path("plain/", plain, name="plain"),
    path("save/", WriteRedirectView.as_view(), name="save"),
    path("after/", AfterView.as_view(), name="after"),
]
