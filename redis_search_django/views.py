from __future__ import annotations

from typing import TYPE_CHECKING, Any, overload

from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import InvalidPage, Paginator
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse
from django.views.generic.list import (
    MultipleObjectMixin,
    MultipleObjectTemplateResponseMixin,
)

from .documents import Document
from .query.queryset import DocumentQuerySet
from .types import FacetMap

# django-stubs marks MultipleObjectMixin as Generic[Model]; the runtime class
# is not subscriptable. Base classes are evaluated immediately, even with
# from __future__ import annotations.
if TYPE_CHECKING:
    from django.views.generic.list import _HasModel

    _MultipleObjectMixin = MultipleObjectMixin[Model]
else:
    _MultipleObjectMixin = MultipleObjectMixin


class SearchListViewMixin(MultipleObjectTemplateResponseMixin, _MultipleObjectMixin):
    """List view helper that queries ``document_class.objects``.

    One mixin for sync and async, like Django's ``ListView``. Filter
    construction (``get_search_queryset``) is synchronous. ``get()``
    evaluates with ``count`` / ``facets`` / ``to_queryset``. ``aget()``
    evaluates with ``acount`` / ``afacets`` / ``ato_queryset``.
    """

    paginator_class: type[Paginator[Model]] = Paginator
    document_class: type[Document] | None = None
    convert_to_queryset: bool = True
    kwargs: dict[str, str]
    request: HttpRequest

    def get_search_queryset(self) -> DocumentQuerySet:
        if self.document_class is None:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} requires a 'document_class' attribute."
            )
        return self.document_class.objects.all()

    def get_queryset(self) -> Any:
        cached = getattr(self, "_search_qs", None)
        if cached is not None:
            return cached
        qs = self.get_search_queryset()
        self._search_qs = qs
        return qs

    def facets(self) -> FacetMap | None:
        return None

    async def afacets(self) -> FacetMap | None:
        return None

    @overload
    def get_context_object_name(self, object_list: _HasModel) -> str: ...

    @overload
    def get_context_object_name(self, object_list: Any) -> str | None: ...

    def get_context_object_name(self, object_list: Any) -> str | None:
        if self.context_object_name:
            return self.context_object_name
        model = (
            self.document_class._meta.model if self.document_class is not None else None
        )
        if model is not None:
            return f"{model._meta.model_name}_list"
        return None

    def get_template_names(self) -> list[str]:
        if self.template_name:
            return [self.template_name]
        model = (
            self.document_class._meta.model if self.document_class is not None else None
        )
        if model is not None:
            opts = model._meta
            suffix = self.template_name_suffix
            return [f"{opts.app_label}/{opts.model_name}{suffix}.html"]
        raise ImproperlyConfigured(
            f"{self.__class__.__name__} requires a 'template_name' or a document_class."
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        object_list = context.get("object_list")
        if (
            self.convert_to_queryset
            and object_list is not None
            and hasattr(object_list, "to_queryset")
        ):
            context["object_list"] = object_list.to_queryset()
        return context

    def paginate_queryset(
        self, queryset: Any, page_size: int
    ) -> tuple[Any, Any, Any, bool]:
        """One FT.SEARCH for the page: reuse ``total`` instead of a second COUNT."""
        if not hasattr(queryset, "_search"):
            return super().paginate_queryset(queryset, page_size)
        paginator = self.get_paginator(
            queryset,
            page_size,
            orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty(),
        )
        page_number = self._page_number(paginator, queryset)
        offset = max((page_number - 1) * page_size, 0)
        result = queryset._search(offset=offset, limit=page_size)
        queryset._result = result
        queryset._total = result.total
        paginator.count = result.total
        try:
            page_obj = paginator.page(page_number)
        except InvalidPage as exc:
            raise Http404(f"Invalid page ({page_number}): {exc}") from exc
        sliced: Any = page_obj.object_list
        sliced._result = result
        sliced._total = result.total
        sliced._compiled = queryset._compiled
        return paginator, page_obj, sliced, page_obj.has_other_pages()

    def _page_number(self, paginator: Any, queryset: Any) -> int:
        page_kwarg = self.page_kwarg
        raw_page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
        if raw_page == "last":
            paginator.count = queryset.count()
            return int(paginator.num_pages)
        try:
            return int(raw_page)
        except ValueError:
            raise Http404(
                'Page is not "last", nor can it be converted to an int.'
            ) from None

    def get(self, request: HttpRequest, *args: str, **kwargs: str) -> HttpResponse:
        self.object_list = self.get_queryset()
        if not self.get_allow_empty() and not self.object_list:
            raise Http404(
                f"Empty list and {self.__class__.__name__}.allow_empty is False."
            )
        context = self.get_context_data(facets=self.facets())
        return self.render_to_response(context)

    async def aget(
        self, request: HttpRequest, *args: str, **kwargs: str
    ) -> HttpResponse:
        """Async counterpart of :meth:`get`. Wire it with ``async def get``."""
        self.object_list = self.get_queryset()
        if not self.get_allow_empty() and await self._ais_empty(self.object_list):
            raise Http404(
                f"Empty list and {self.__class__.__name__}.allow_empty is False."
            )
        context = await self.aget_context_data(facets=await self.afacets())
        return self.render_to_response(context)

    async def aget_context_data(self, **kwargs: Any) -> dict[str, Any]:
        queryset = kwargs.pop("object_list", None)
        if queryset is None:
            queryset = self.object_list
        page_size = self.get_paginate_by(queryset)
        context_object_name = self.get_context_object_name(queryset)
        if page_size:
            paginator, page, queryset, is_paginated = await self.apaginate_queryset(
                queryset, page_size
            )
            context: dict[str, Any] = {
                "paginator": paginator,
                "page_obj": page,
                "is_paginated": is_paginated,
                "object_list": queryset,
            }
        else:
            queryset = await self._amaterialize(queryset)
            context = {
                "paginator": None,
                "page_obj": None,
                "is_paginated": False,
                "object_list": queryset,
            }
        if context_object_name is not None:
            context[context_object_name] = queryset
        context.update(kwargs)
        return context

    async def apaginate_queryset(
        self, queryset: Any, page_size: int
    ) -> tuple[Any, Any, Any, bool]:
        """Same contract as ``MultipleObjectMixin.paginate_queryset``."""
        paginator = self.get_paginator(
            queryset,
            page_size,
            orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty(),
        )
        page_kwarg = self.page_kwarg
        raw_page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
        if raw_page == "last":
            if hasattr(queryset, "acount"):
                paginator.count = await queryset.acount()
            page_number = paginator.num_pages
        else:
            try:
                page_number = int(raw_page)
            except ValueError:
                raise Http404(
                    'Page is not "last", nor can it be converted to an int.'
                ) from None
        if hasattr(queryset, "_asearch"):
            offset = (page_number - 1) * page_size
            result = await queryset._asearch(offset=max(offset, 0), limit=page_size)
            queryset._result = result
            queryset._total = result.total
            paginator.count = result.total
        try:
            page_obj = paginator.page(page_number)
        except InvalidPage as exc:
            raise Http404(f"Invalid page ({page_number}): {exc}") from exc
        object_list: Any = page_obj.object_list
        cached = getattr(queryset, "_result", None)
        if hasattr(object_list, "_result") and cached is not None:
            object_list._result = cached
            object_list._total = getattr(queryset, "_total", None)
            object_list._compiled = getattr(queryset, "_compiled", None)
        object_list = await self._amaterialize(object_list)
        page_obj.object_list = object_list
        return paginator, page_obj, object_list, page_obj.has_other_pages()

    async def _ais_empty(self, object_list: Any) -> bool:
        aexists = getattr(object_list, "aexists", None)
        if callable(aexists):
            return not await aexists()
        return not object_list

    async def _amaterialize(self, object_list: Any) -> Any:
        if (
            self.convert_to_queryset
            and object_list is not None
            and hasattr(object_list, "ato_queryset")
        ):
            return await object_list.ato_queryset()
        if hasattr(object_list, "__aiter__") and hasattr(object_list, "document_cls"):
            return [hit async for hit in object_list]
        return object_list
