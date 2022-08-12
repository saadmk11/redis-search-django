from typing import Any, Iterable, List, Type, Union

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.utils.functional import cached_property
from django.views.generic.list import (
    MultipleObjectMixin,
    MultipleObjectTemplateResponseMixin,
)

from .documents import Document
from .paginator import RediSearchPaginator


class RediSearchMixin:
    paginator_class = RediSearchPaginator
    document_class: Type[Document]

    @cached_property
    def search_query_expression(self) -> Any:
        return None

    @cached_property
    def sort_by(self) -> Union[str, None]:
        return None

    def facets(self) -> Any:
        return None

    def search(self) -> Document:
        if not self.document_class:
            raise ImproperlyConfigured(
                "%(cls)s requires a 'document_class' attribute"
                % {"cls": self.__class__.__name__}
            )
        if self.search_query_expression:
            search_query = self.document_class.find(self.search_query_expression)
        else:
            search_query = self.document_class.find()

        if self.sort_by:
            search_query = search_query.sort_by(self.sort_by)

        return search_query


class RediSearchTemplateResponseMixin(MultipleObjectTemplateResponseMixin):
    def get_template_names(self) -> List[str]:
        """
        Return a list of template names to be used for the request. Must return
        a list. May not be called if render_to_response() is overridden.
        """
        if self.template_name:
            return [self.template_name]

        template_names = []

        if self.document_class._django.model:
            opts = self.document_class._django.model._meta
            template_names.append(
                "%s/%s%s.html"
                % (opts.app_label, opts.model_name, self.template_name_suffix)
            )
        elif not template_names:
            raise ImproperlyConfigured(
                "%(cls)s requires either a 'template_name' attribute "
                "or a get_queryset() method that returns a QuerySet."
                % {
                    "cls": self.__class__.__name__,
                }
            )
        return template_names


class RediSearchMultipleObjectMixin(MultipleObjectMixin):
    def get_context_object_name(self, object_list: Iterable[Any]) -> Union[str, None]:
        """Get the name of the item to be used in the context."""
        if self.context_object_name:
            return self.context_object_name
        elif self.document_class._django.model:
            return "%s_list" % self.document_class._django.model._meta.model_name
        else:
            return None


class ListViewMixin(
    RediSearchMixin, RediSearchTemplateResponseMixin, RediSearchMultipleObjectMixin
):
    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> TemplateResponse:
        self.object_list = self.search()
        context = self.get_context_data(facets=self.facets())
        return self.render_to_response(context)
