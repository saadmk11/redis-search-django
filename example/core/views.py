from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from redis_search_django import Aggregate, Q
from redis_search_django.debug import SearchDebugMixin
from redis_search_django.exceptions import DocumentNotFound
from redis_search_django.views import SearchListViewMixin

from .debug import display_params, hit_payload, pretty
from .documents import ProductDocument, TagHashDocument
from .forms import CategoryForm, ProductForm, TagForm, VendorForm
from .models import Category, Product, Tag, Vendor

KNN_K_MAX = 50


def query_data(request: HttpRequest) -> dict[str, Any]:
    knn = request.GET.get("knn", "")
    raw_k = request.GET.get("k", "10")
    try:
        k = int(raw_k)
    except (TypeError, ValueError):
        k = 10
    if k < 1:
        k = 10
    k = min(k, KNN_K_MAX)
    return {
        "query": request.GET.get("query", ""),
        "search_via": request.GET.get("search_via", "q"),
        "prefix": request.GET.get("prefix", ""),
        "exact": request.GET.get("exact", ""),
        "min_price": request.GET.get("min_price", ""),
        "max_price": request.GET.get("max_price", ""),
        "qty_min": request.GET.get("qty_min", ""),
        "qty_max": request.GET.get("qty_max", ""),
        "available": request.GET.get("available", ""),
        "uncategorized": request.GET.get("uncategorized", ""),
        "sort": request.GET.get("sort", ""),
        "exclude_discontinued": request.GET.get("exclude_discontinued", ""),
        "redis_hits": request.GET.get("redis_hits", ""),
        "highlight": request.GET.get("highlight", ""),
        "category": request.GET.getlist("category"),
        "tags": request.GET.getlist("tags"),
        "vendor": request.GET.getlist("vendor"),
        "knn": knn,
        "k": k,
    }


def catalog_queryset(request: HttpRequest) -> Any:
    qs = ProductDocument.objects.all()
    data = query_data(request)
    if data["query"]:
        if data["search_via"] == "search":
            qs = qs.search(data["query"])
        else:
            qs = qs.filter(
                Q(name__search=data["query"]) | Q(description__search=data["query"])
            )
    if data["prefix"]:
        qs = qs.filter(name__startswith=data["prefix"].upper())
    if data["exact"]:
        qs = qs.filter(name__exact=data["exact"].upper())
    if data["min_price"]:
        qs = qs.filter(price__gte=float(data["min_price"]))
    if data["max_price"]:
        qs = qs.filter(price__lte=float(data["max_price"]))
    if data["qty_min"] and data["qty_max"]:
        qs = qs.filter(quantity__range=(int(data["qty_min"]), int(data["qty_max"])))
    elif data["qty_min"]:
        qs = qs.filter(quantity__gte=int(data["qty_min"]))
    elif data["qty_max"]:
        qs = qs.filter(quantity__lte=int(data["qty_max"]))
    if data["available"] == "true":
        qs = qs.filter(available=True)
    elif data["available"] == "false":
        qs = qs.filter(available=False)
    if data["uncategorized"]:
        qs = qs.filter(department__isnull=True)
    if data["category"]:
        qs = qs.filter(category__name__in=data["category"])
    if data["tags"]:
        qs = qs.filter(tags__name__in=data["tags"])
    if data["vendor"]:
        qs = qs.filter(vendor__name__in=data["vendor"])
    if data["exclude_discontinued"]:
        qs = qs.exclude(tags__name="discontinued")
    if data["highlight"] and data["query"]:
        qs = qs.highlight("name")
    if data["knn"]:
        qs = qs.knn(data["knn"], k=data["k"])
    elif data["sort"] in {
        "price",
        "-price",
        "name",
        "-name",
        "quantity",
        "-quantity",
    }:
        qs = qs.order_by(data["sort"])
    return qs


def _unfiltered(qs: Any) -> bool:
    return (
        not getattr(qs, "_none", False)
        and not getattr(qs, "_extra", None)
        and not getattr(qs, "_knn", None)
        and not getattr(getattr(qs, "_q", None), "children", True)
    )


def _cast_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = dict(row)
        for key in ("count", "n"):
            if key in item and item[key] is not None:
                item[key] = int(item[key])
        for key in ("avg_price", "min_price", "max_price", "sum_price"):
            if key in item and item[key] is not None:
                item[key] = float(item[key])
        out.append(item)
    return out


class SearchView(SearchDebugMixin, SearchListViewMixin, ListView):
    paginate_by = 8
    model = Product
    template_name = "core/search.html"
    document_class = ProductDocument

    def get_search_queryset(self) -> Any:
        return catalog_queryset(self.request)

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        data = query_data(request)
        self.convert_to_queryset = not bool(
            data["knn"] or data["redis_hits"] or data["highlight"]
        )
        return super().get(request, *args, **kwargs)

    def facets(self) -> dict[str, list[dict[str, Any]]]:
        return self.get_queryset().facets(
            "category__name", "tags__name", "vendor__name"
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        qs = self.get_queryset()
        raw_query, raw_params = qs.raw()
        data = query_data(self.request)
        context = super().get_context_data(query_data=data, **kwargs)
        object_list = context.get("object_list")
        if hasattr(object_list, "select_related"):
            context["object_list"] = object_list.select_related(
                "vendor", "category"
            ).prefetch_related("tags")
        context["knn_mode"] = bool(
            data["knn"] or data["redis_hits"] or data["highlight"]
        )
        context["async_mode"] = False
        context["price_stats"] = _cast_rows(
            qs.aggregate(
                Aggregate()
                .group_by("available")
                .count("count")
                .avg("price", "avg_price")
                .min("price", "min_price")
                .max("price", "max_price")
                .sum("price", "sum_price")
            )
        )
        context["raw_query"] = raw_query
        context["raw_params"] = display_params(raw_params)
        context["explain"] = qs.explain()
        context["sqlite_hidden"] = Product.objects.filter(available=False).count()
        paginator = context.get("paginator")
        if _unfiltered(qs) and paginator is not None:
            context["redis_count"] = paginator.count
        else:
            context["redis_count"] = ProductDocument.objects.all().count()
        return context


class AggregationsView(SearchDebugMixin, TemplateView):
    template_name = "core/aggregations.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        qs = catalog_queryset(self.request)
        context = super().get_context_data(**kwargs)
        context["query_data"] = query_data(self.request)
        context["vendor_stats"] = _cast_rows(
            qs.aggregate(
                Aggregate()
                .group_by("vendor__name")
                .count("count")
                .avg("price", "avg_price")
                .min("price", "min_price")
                .max("price", "max_price")
                .sum("price", "sum_price")
                .sort_by("-count")
                .limit(15)
            )
        )
        context["category_stats"] = _cast_rows(
            qs.aggregate(
                Aggregate()
                .group_by("category__name")
                .count("count")
                .avg("price", "avg_price")
                .min("price", "min_price")
                .max("price", "max_price")
                .tolist("name", "titles")
                .sort_by("-count")
            )
        )
        context["tag_stats"] = _cast_rows(
            qs.aggregate(
                Aggregate().group_by("tags__name").count("count").sort_by("-count")
            )
        )
        raw_query, raw_params = qs.raw()
        context["raw_query"] = raw_query
        context["raw_params"] = display_params(raw_params)
        return context


class AsyncStatsView(SearchDebugMixin, View):
    """Same filters as search, evaluated with the a-prefixed queryset API."""

    async def get(self, request: HttpRequest) -> HttpResponse:
        qs = catalog_queryset(request)
        data = query_data(request)
        first = await qs.afirst()
        ordered = qs
        if (
            data["sort"]
            not in {
                "price",
                "-price",
                "name",
                "-name",
                "quantity",
                "-quantity",
            }
            and not data["knn"]
        ):
            ordered = qs.order_by("price")
        last = None
        last_error = ""
        try:
            last = await ordered.alast()
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        pk = request.GET.get("pk", "1")
        by_pk = None
        by_pk_error = ""
        try:
            by_pk = await ProductDocument.objects.aget_by_pk(pk)
        except (DocumentNotFound, Exception) as exc:
            by_pk_error = f"{type(exc).__name__}: {exc}"
        django_page = None
        django_page_error = ""
        try:
            orm_qs = await qs[:5].ato_queryset()
            django_page = [{"pk": obj.pk, "name": obj.name} async for obj in orm_qs]
        except Exception as exc:
            django_page_error = f"{type(exc).__name__}: {exc}"
        context = {
            "query_data": data,
            "redis_count": await ProductDocument.objects.all().acount(),
            "filtered_count": await qs.acount(),
            "exists": await qs.aexists(),
            "first": first,
            "last": last,
            "last_error": last_error,
            "by_pk": by_pk,
            "by_pk_error": by_pk_error,
            "facets": await qs.afacets("category__name"),
            "aggregate_rows": await qs.aaggregate(
                Aggregate().group_by("available").count("count")
            ),
            "explain": await qs.aexplain(),
            "django_page": django_page,
            "django_page_error": django_page_error,
            "raw_query": qs.raw()[0],
            "raw_params": display_params(qs.raw()[1]),
        }
        return TemplateResponse(request, "core/async_stats.html", context)


class CatalogHomeView(SearchDebugMixin, TemplateView):
    template_name = "core/catalog.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["product_count"] = Product.objects.count()
        context["category_count"] = Category.objects.count()
        context["tag_count"] = Tag.objects.count()
        context["vendor_count"] = Vendor.objects.count()
        context["redis_count"] = ProductDocument.objects.all().count()
        context["hash_count"] = TagHashDocument.objects.all().count()
        context["hidden_count"] = Product.objects.filter(available=False).count()
        context["uncategorized_count"] = Product.objects.filter(
            category__isnull=True
        ).count()
        return context


class ProductListView(SearchDebugMixin, ListView):
    model = Product
    template_name = "core/product_list.html"
    paginate_by = 20

    def get_queryset(self) -> Any:
        return Product.objects.select_related("vendor", "category").prefetch_related(
            "tags"
        )


class ProductDetailView(SearchDebugMixin, DetailView):
    model = Product
    template_name = "core/product_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        try:
            hit = ProductDocument.objects.get(pk=self.object.pk)
        except DocumentNotFound:
            hit = None
        context["redis_hit"] = hit
        context["redis_payload"] = hit_payload(hit)
        context["redis_payload_json"] = pretty(hit_payload(hit))
        context["redis_key"] = ProductDocument.key_for(self.object.pk)
        context["embedding_dims"] = (
            len(hit.embedding) if hit is not None and hit.embedding else 0
        )
        return context


class _SavedMixin(SearchDebugMixin):
    success_message = "Saved. AUTO_INDEX writes the Redis document after commit."

    def form_valid(self, form: Any) -> HttpResponse:
        messages.success(self.request, self.success_message)
        return super().form_valid(form)  # type: ignore[misc]


class _DeletedMixin(SearchDebugMixin):
    def form_valid(self, form: Any) -> HttpResponse:
        messages.success(
            self.request,
            "Deleted. AUTO_INDEX removes the Redis key (or reindexes parents).",
        )
        return super().form_valid(form)  # type: ignore[misc]


class ProductCreateView(_SavedMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = "core/form.html"
    extra_context = {
        "title": "Add product",
        "hint": (
            "Creates a Django row. The signal processor indexes it if available=True."
        ),
    }


class ProductUpdateView(_SavedMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = "core/form.html"
    extra_context = {
        "title": "Edit product",
        "hint": (
            "Save to reindex. Set available=False to drop the Redis key (should_index)."
        ),
    }


class ProductDeleteView(_DeletedMixin, DeleteView):
    model = Product
    template_name = "core/confirm_delete.html"
    success_url = reverse_lazy("product-list")
    extra_context = {"title": "Delete product"}


class CategoryListView(SearchDebugMixin, ListView):
    model = Category
    template_name = "core/simple_list.html"
    extra_context = {
        "title": "Categories",
        "create_url": "category-create",
        "update_url": "category-update",
        "delete_url": "category-delete",
        "hint": "Rename a category to see related products reindexed.",
    }


class CategoryCreateView(_SavedMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "core/form.html"
    success_url = reverse_lazy("category-list")
    extra_context = {"title": "Add category"}


class CategoryUpdateView(_SavedMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "core/form.html"
    success_url = reverse_lazy("category-list")
    extra_context = {
        "title": "Edit category",
        "hint": "Related Product documents are reindexed on save.",
    }


class CategoryDeleteView(_DeletedMixin, DeleteView):
    model = Category
    template_name = "core/confirm_delete.html"
    success_url = reverse_lazy("category-list")
    extra_context = {"title": "Delete category"}


class TagListView(SearchDebugMixin, ListView):
    model = Tag
    template_name = "core/simple_list.html"
    extra_context = {
        "title": "Tags",
        "create_url": "tag-create",
        "update_url": "tag-update",
        "delete_url": "tag-delete",
        "hint": "M2M changes reindex the parent product.",
    }


class TagCreateView(_SavedMixin, CreateView):
    model = Tag
    form_class = TagForm
    template_name = "core/form.html"
    success_url = reverse_lazy("tag-list")
    extra_context = {"title": "Add tag"}


class TagUpdateView(_SavedMixin, UpdateView):
    model = Tag
    form_class = TagForm
    template_name = "core/form.html"
    success_url = reverse_lazy("tag-list")
    extra_context = {"title": "Edit tag"}


class TagDeleteView(_DeletedMixin, DeleteView):
    model = Tag
    template_name = "core/confirm_delete.html"
    success_url = reverse_lazy("tag-list")
    extra_context = {"title": "Delete tag"}


class VendorListView(SearchDebugMixin, ListView):
    model = Vendor
    template_name = "core/vendor_list.html"
    queryset = Vendor.objects.prefetch_related("product")


class VendorCreateView(_SavedMixin, CreateView):
    model = Vendor
    form_class = VendorForm
    template_name = "core/form.html"
    success_url = reverse_lazy("vendor-list")
    extra_context = {"title": "Add vendor"}


class VendorUpdateView(_SavedMixin, UpdateView):
    model = Vendor
    form_class = VendorForm
    template_name = "core/form.html"
    success_url = reverse_lazy("vendor-list")
    extra_context = {
        "title": "Edit vendor",
        "hint": "Related Product documents are reindexed on save.",
    }


class VendorDeleteView(_DeletedMixin, DeleteView):
    model = Vendor
    template_name = "core/confirm_delete.html"
    success_url = reverse_lazy("vendor-list")
    extra_context = {
        "title": "Delete vendor",
        "hint": "CASCADE also deletes the linked product and its Redis key.",
    }
