from django.utils.functional import cached_property
from django.views.generic import ListView
from redis.commands.search import reducers

from redis_search_django.mixins import ListViewMixin

from .documents import ProductDocument
from .models import Product


class SearchView(ListViewMixin, ListView):
    paginate_by = 20
    model = Product
    template_name = "core/search.html"
    document_class = ProductDocument

    def get_context_data(self, *, object_list=None, **kwargs):
        return super().get_context_data(query_data=dict(self.request.GET), **kwargs)

    @cached_property
    def search_query_expression(self):
        query = self.request.GET.get("query")
        min_price = self.request.GET.get("min_price")
        max_price = self.request.GET.get("max_price")
        categories = list(filter(None, self.request.GET.getlist("category")))
        tags = list(filter(None, self.request.GET.getlist("tags")))
        query_expression = None

        if query:
            query_expression = (
                self.document_class.name % query
                | self.document_class.description % query
            )

        if min_price:
            min_price_expression = self.document_class.price >= float(min_price)
            query_expression = (
                query_expression & min_price_expression
                if query_expression
                else min_price_expression
            )

        if max_price:
            max_price_expression = self.document_class.price <= float(max_price)
            query_expression = (
                query_expression & max_price_expression
                if query_expression
                else max_price_expression
            )

        if categories:
            category_expression = self.document_class.category.name << categories
            query_expression = (
                query_expression & category_expression
                if query_expression
                else category_expression
            )

        if tags:
            tag_expression = self.document_class.tags.name << tags
            query_expression = (
                query_expression & tag_expression
                if query_expression
                else tag_expression
            )

        return query_expression

    @cached_property
    def sort_by(self):
        return self.request.GET.get("sort")

    def facets(self):
        if self.search_query_expression:
            request1 = self.document_class.build_aggregate_request(
                self.search_query_expression
            )
            request2 = self.document_class.build_aggregate_request(
                self.search_query_expression
            )
        else:
            request1 = self.document_class.build_aggregate_request()
            request2 = self.document_class.build_aggregate_request()
        result = self.document_class.aggregate(
            request1.group_by(
                ["@category_name"],
                reducers.count().alias("count"),
            )
        )
        result2 = self.document_class.aggregate(
            request2.group_by(
                ["@tags_name"],
                reducers.count().alias("count"),
            )
        )
        return [result, result2]
