"""Lab views: every public package feature with a description and debug output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views import View
from django.views.generic import ListView

from redis_search_django import (
    Aggregate,
    IndexAction,
    apply_index_action,
    document_registry,
    fields,
)
from redis_search_django.conf import DEFAULTS, redis_search_setting
from redis_search_django.debug import SearchDebugMixin
from redis_search_django.exceptions import DocumentNotFound, NotSupportedError
from redis_search_django.index import IndexManager
from redis_search_django.views import SearchListViewMixin

from .debug import display_params, display_value, hit_payload, hits_payload, pretty
from .documents import (
    CategoryDocument,
    ProductDocument,
    TagDocument,
    TagHashDocument,
    VendorDocument,
)
from .models import Product
from .views import _unfiltered, catalog_queryset, query_data


@dataclass
class Feature:
    group: str
    name: str
    description: str
    how: str
    page: str
    url_name: str = "lab"


FEATURES: list[Feature] = [
    Feature(
        "Document & fields",
        "Document / Django / Index",
        "Mapping class: Django.model + fields, Index.storage / search_fields.",
        "Open Index lab for _meta (alias, prefix, storage).",
        "Lab -> Index",
        "lab-index",
    ),
    Feature(
        "Document & fields",
        "Text, Tag, Numeric, Boolean",
        "Auto-mapped from Char/Text, Slug/UUID, numbers/dates, Boolean.",
        "Search filters and product detail dump the stored JSON types.",
        "Search / product detail",
        "search",
    ),
    Feature(
        "Document & fields",
        "fields.Geo",
        "Schema GEO. prepare_location maps category to lon,lat.",
        "Query lab Geo demo. No category omits the field (INDEXMISSING).",
        "Lab -> Query",
        "lab-query",
    ),
    Feature(
        "Document & fields",
        "fields.Vector + embedder",
        "32-float dummy embed() on name+description. No extra Django column.",
        "Search Meaning box, or product detail embedding length.",
        "Search",
        "search",
    ),
    Feature(
        "Document & fields",
        "Object / Nested",
        "FK/O2O -> Object; M2M -> Nested. Paths like category__name.",
        "Facets and related CRUD reindex parents.",
        "Search / Catalog",
        "search",
    ),
    Feature(
        "Document & fields",
        "HASH storage",
        "TagHashDocument.Index.storage = hash. Nested is illegal on HASH.",
        "Query lab HASH section lists TagHashDocument.objects.",
        "Lab -> Query",
        "lab-query",
    ),
    Feature(
        "Document & fields",
        "prepare_* / should_index / get_queryset",
        "prepare_name uppercases; sku; location; available=False drops Redis.",
        "Hide a product in Catalog. Compare SQLite vs Redis counts.",
        "Catalog",
        "catalog",
    ),
    Feature(
        "Document & fields",
        "key_for / objects.get(pk=)",
        "JSON.GET by key. Missing raises Document.DoesNotExist.",
        "Product detail and Query lab get_by_pk / missing pk.",
        "Lab -> Query",
        "lab-query",
    ),
    Feature(
        "Query",
        "filter / exclude / Q",
        "Lazy clone-on-write. Use redis_search_django.Q, not Django Q.",
        "Search keyword uses Q(...) | Q(...). Exclude discontinued.",
        "Search",
        "search",
    ),
    Feature(
        "Query",
        "objects.search()",
        "OR of Index.search_fields (name, description) with __search.",
        "Search: set Search via to objects.search(). Query lab demo.",
        "Search",
        "search",
    ),
    Feature(
        "Query",
        "Lookups",
        "__search, __exact, __in, comparisons, __range, __isnull, __startswith.",
        "Query lab playground + canned rows. category__isnull for no category.",
        "Lab -> Query",
        "lab-query",
    ),
    Feature(
        "Query",
        "extra() / none() / reverse() / first() / last()",
        "extra replaces the filter. none is empty. reverse needs order_by.",
        "Query lab canned demos and the extra box.",
        "Lab -> Query",
        "lab-query",
    ),
    Feature(
        "Query",
        "highlight / values / return_fields",
        "HIGHLIGHT wraps matches. values() and return_fields() limit RETURN.",
        "Query lab. Highlight needs Redis hits (not to_queryset).",
        "Lab -> Query",
        "lab-query",
    ),
    Feature(
        "Query",
        "knn()",
        "Filter first, then rank by vector distance. Hits have score.",
        "Search Meaning box. Filters apply before KNN.",
        "Search",
        "search",
    ),
    Feature(
        "Query",
        "facets / aggregate",
        "One FT.AGGREGATE per facet. group_by + COUNT/AVG/MIN/MAX/SUM/TOLIST.",
        "Search facets. Aggregations page. Query lab small aggregate.",
        "Aggregations",
        "aggregations",
    ),
    Feature(
        "Query",
        "count / exists / get / slice / Paginator",
        "count is LIMIT 0 0. exists LIMIT 0 1. Stock Paginator works.",
        "Search pagination. Query lab counts.",
        "Search",
        "search",
    ),
    Feature(
        "Query",
        "to_queryset() / raw() / explain()",
        "to_queryset loads Django rows in Redis order. raw is the filter.",
        "Search compiled query panel. Redis hits skip to_queryset.",
        "Search",
        "search",
    ),
    Feature(
        "Views",
        "SearchListViewMixin",
        "document_class + get_search_queryset + facets + convert_to_queryset.",
        "Search (sync get). Async search uses aget / afacets / ato_queryset.",
        "Search / Async search",
        "search",
    ),
    Feature(
        "Views",
        "SearchDebugMixin",
        "Per-view overlay: query, time, PARAMS, on-demand FT.EXPLAIN.",
        "Bottom-left RSD pill. Catalog CRUD is opted in; POST writes carry over.",
        "Search",
        "search",
    ),
    Feature(
        "Async",
        "a-prefixed evaluation",
        "acount, aexists, afirst, alast, aget, afacets, aaggregate, aexplain.",
        "Async stats table + Async search list.",
        "Async",
        "async-stats",
    ),
    Feature(
        "Index",
        "IndexManager / document_registry",
        "exists, info, check, drift, fingerprint. Registry lists Documents.",
        "Index lab dumps every registered class and FT.INFO.",
        "Lab -> Index",
        "lab-index",
    ),
    Feature(
        "Index",
        "apply_index_action",
        "JSON-serializable upsert / delete / reindex_related.",
        "Index lab POST form. Catalog saves use AUTO_INDEX.",
        "Lab -> Index",
        "lab-index",
    ),
    Feature(
        "Index",
        "redisearch management command",
        "create, update, populate, rebuild, drop, info, check.",
        "CLI only. Index lab check() is the same drift test.",
        "Lab -> Index",
        "lab-index",
    ),
    Feature(
        "Settings",
        "REDIS_SEARCH",
        "URL, PREFIX, AUTO_INDEX, DIALECT, storage, chunk, processor.",
        "Index lab prints resolved settings vs defaults.",
        "Lab -> Index",
        "lab-index",
    ),
    Feature(
        "Signals",
        "AUTO_INDEX CRUD + related",
        "post_save upsert, delete drops the key, related/M2M reindex.",
        "Catalog: add product, toggle available, rename category, delete.",
        "Catalog",
        "catalog",
    ),
]


@dataclass
class Demo:
    id: str
    title: str
    description: str
    code: str
    result: Any = None
    raw: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    extra: str = ""
    expected: bool = False
    why: str = ""


def _safe_raw(qs: Any) -> tuple[str, dict[str, Any]]:
    try:
        query, params = qs.raw()
        return str(query), display_params(params)
    except Exception as exc:
        return "", {"error": f"{type(exc).__name__}: {exc}"}


def _run_demo(
    demo_id: str,
    title: str,
    description: str,
    code: str,
    runner: Any,
    *,
    expected: bool = False,
    why: str = "",
) -> Demo:
    demo = Demo(
        id=demo_id,
        title=title,
        description=description,
        code=code,
        expected=expected,
        why=why,
    )
    try:
        value = runner()
        if hasattr(value, "raw") and hasattr(value, "count"):
            demo.raw, demo.params = _safe_raw(value)
            window = value[:8]
            hits = list(window)
            result = getattr(window, "_result", None)
            total = getattr(result, "total", len(hits))
            if hits and hasattr(hits[0], "data"):
                rendered = hits_payload(hits)
            else:
                rendered = [display_value(item) for item in hits]
            demo.result = {"count": total, "hits": rendered}
        else:
            demo.result = display_value(value)
    except Exception as exc:
        demo.error = f"{type(exc).__name__}: {exc}"
    return demo


def _summarize_hit(hit: Any) -> dict[str, Any]:
    if hit is None:
        return {}
    if hasattr(hit, "data"):
        return hit_payload(hit)
    return {"pk": getattr(hit, "pk", None), "repr": str(hit)}


class LabHomeView(SearchDebugMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        groups: dict[str, list[Feature]] = {}
        for item in FEATURES:
            groups.setdefault(item.group, []).append(item)
        return TemplateResponse(
            request,
            "core/lab.html",
            {
                "groups": groups,
                "product_count": Product.objects.count(),
                "redis_count": ProductDocument.objects.all().count(),
                "hash_count": TagHashDocument.objects.all().count(),
                "hidden_count": Product.objects.filter(available=False).count(),
                "uncategorized": Product.objects.filter(category__isnull=True).count(),
            },
        )


class LabQueryView(SearchDebugMixin, View):
    """Playground + canned demos for the query API, HASH, and Geo."""

    def get(self, request: HttpRequest) -> HttpResponse:
        playground = self._playground(request)
        return TemplateResponse(
            request,
            "core/lab_query.html",
            {
                "playground": playground,
                "demos": self._canned(),
                "lookups": [
                    "search",
                    "exact",
                    "in",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "range",
                    "isnull",
                    "startswith",
                    "default",
                    "extra",
                ],
                "fields": [
                    "name",
                    "description",
                    "price",
                    "quantity",
                    "available",
                    "sku",
                    "department",
                    "location",
                    "category",
                    "category__name",
                    "tags__name",
                    "vendor__name",
                ],
            },
        )

    def _playground(self, request: HttpRequest) -> Demo | None:
        lookup = request.GET.get("lookup", "").strip()
        field_name = request.GET.get("field", "name").strip() or "name"
        value = request.GET.get("value", "").strip()
        extra_query = request.GET.get("extra", "").strip()
        if not lookup and not extra_query:
            return None
        demo = Demo(
            id="playground",
            title="Playground",
            description="Runs the lookup or extra() you submitted.",
            code="",
        )
        try:
            qs = ProductDocument.objects.all()
            if extra_query and lookup == "extra":
                qs = qs.extra(extra_query)
                demo.code = f"ProductDocument.objects.extra({extra_query!r})"
            elif extra_query and not lookup:
                qs = qs.extra(extra_query)
                demo.code = f"ProductDocument.objects.extra({extra_query!r})"
            else:
                parsed = _parse_lookup_value(lookup, value)
                kwargs: dict[str, Any]
                if lookup == "default":
                    kwargs = {field_name: parsed}
                    demo.code = (
                        f"ProductDocument.objects.filter({field_name}={parsed!r})"
                    )
                else:
                    key = f"{field_name}__{lookup}"
                    kwargs = {key: parsed}
                    demo.code = f"ProductDocument.objects.filter({key}={parsed!r})"
                qs = qs.filter(**kwargs)
            demo.raw, demo.params = _safe_raw(qs)
            try:
                demo.extra = qs.explain()
            except Exception as exc:
                demo.extra = f"{type(exc).__name__}: {exc}"
            hits = list(qs[:12])
            demo.result = {
                "count": qs.count(),
                "exists": qs.exists(),
                "hits": hits_payload(hits),
            }
        except Exception as exc:
            demo.error = f"{type(exc).__name__}: {exc}"
        return demo

    def _canned(self) -> list[Demo]:
        demos: list[Demo] = []

        def add(
            demo_id: str,
            title: str,
            description: str,
            code: str,
            runner: Any,
            *,
            expected: bool = False,
            why: str = "",
        ) -> None:
            demos.append(
                _run_demo(
                    demo_id,
                    title,
                    description,
                    code,
                    runner,
                    expected=expected,
                    why=why,
                )
            )

        add(
            "search",
            "objects.search()",
            "OR of Index.search_fields with __search. Token AND inside each field.",
            'ProductDocument.objects.search("coffee")',
            lambda: ProductDocument.objects.search("coffee"),
        )
        add(
            "exact",
            "name__exact (stored UPPERCASE)",
            "prepare_name uppercases. Exact phrase is the Redis value.",
            'ProductDocument.objects.filter(name__exact="REDIS INTERNALS")',
            lambda: ProductDocument.objects.filter(name__exact="REDIS INTERNALS"),
        )
        add(
            "in",
            "tags__name__in",
            "TAG union. Nested path tags__name maps to @tags_name.",
            'ProductDocument.objects.filter(tags__name__in=["sale", "wireless"])',
            lambda: ProductDocument.objects.filter(tags__name__in=["sale", "wireless"]),
        )
        add(
            "isnull_object",
            "category__isnull",
            "Object(required=False) indexes category_pk as INDEXMISSING.",
            "ProductDocument.objects.filter(category__isnull=True)",
            lambda: ProductDocument.objects.filter(category__isnull=True),
        )
        add(
            "isnull_name",
            "category__name__isnull",
            "Child __isnull on an optional Object rewrites to category__isnull.",
            "ProductDocument.objects.filter(category__name__isnull=True)",
            lambda: ProductDocument.objects.filter(category__name__isnull=True),
        )
        add(
            "location_isnull",
            "department__isnull",
            "Tag(index_missing=True). Omitted when the product has no category.",
            "ProductDocument.objects.filter(department__isnull=True)",
            lambda: ProductDocument.objects.filter(department__isnull=True),
        )
        add(
            "range",
            "price__range",
            "NUMERIC inclusive range. Dates would be coerced to UTC timestamps.",
            "ProductDocument.objects.filter(price__range=(20, 80))",
            lambda: ProductDocument.objects.filter(price__range=(20, 80)),
        )
        add(
            "gt",
            "quantity__gt / sku default TAG",
            "Numeric __gt plus default TAG exact on the prepared SKU.",
            'ProductDocument.objects.filter(quantity__gt=10, sku="SKU-0001")',
            lambda: ProductDocument.objects.filter(quantity__gt=10),
        )
        add(
            "available",
            "available Boolean",
            "Boolean is TAG {true}/{false}. should_index omits false, so count is 0.",
            "ProductDocument.objects.filter(available=False).count()",
            lambda: {
                "available_true": ProductDocument.objects.filter(
                    available=True
                ).count(),
                "available_false": ProductDocument.objects.filter(
                    available=False
                ).count(),
                "sqlite_hidden": Product.objects.filter(available=False).count(),
            },
        )
        add(
            "extra",
            "extra()",
            "Trusted raw RediSearch filter. extra() replaces the compiled Q tree.",
            'ProductDocument.objects.extra("@price:[10 30]")',
            lambda: ProductDocument.objects.extra("@price:[10 30]"),
        )
        add(
            "extra_then_filter",
            "extra() then filter()",
            "extra() is a raw filter string. It cannot be mixed with a Q tree.",
            'ProductDocument.objects.extra("*").filter(available=True)',
            lambda: ProductDocument.objects.extra("*").filter(available=True),
            expected=True,
            why=(
                "extra() replaces filter(), it does not AND with it. Put the "
                "clauses in extra() itself, or start a new queryset and call "
                "filter() first."
            ),
        )
        add(
            "highlight",
            "highlight()",
            "FT.SEARCH HIGHLIGHT on sortable TEXT (name). Keep SearchHits.",
            'ProductDocument.objects.search("redis").highlight("name")',
            lambda: ProductDocument.objects.search("redis").highlight("name"),
        )
        add(
            "values",
            "values()",
            "RETURN only the named fields. Hits still have pk.",
            'ProductDocument.objects.filter(available=True).values("name", "price")',
            lambda: ProductDocument.objects.filter(available=True).values(
                "name", "price", "sku"
            ),
        )
        add(
            "return_fields",
            "return_fields()",
            "Same RETURN list without the values() dict-mode flag.",
            'ProductDocument.objects.return_fields("name", "sku")[:5]',
            lambda: ProductDocument.objects.return_fields("name", "sku"),
        )
        add(
            "none",
            "none()",
            "No Redis I/O. Empty result, count 0, first() is None.",
            "ProductDocument.objects.none()",
            lambda: {
                "count": ProductDocument.objects.none().count(),
                "exists": ProductDocument.objects.none().exists(),
                "first": ProductDocument.objects.none().first(),
            },
        )

        def _first_last() -> dict[str, Any]:
            qs = ProductDocument.objects.all().order_by("price")
            return {
                "first": _summarize_hit(qs.first()),
                "last": _summarize_hit(qs.last()),
                "reverse_first": _summarize_hit(qs.reverse().first()),
            }

        add(
            "first_last",
            "first() / last() / reverse()",
            "last() and reverse() require order_by(). last is reverse().first().",
            'qs.order_by("price"); qs.first(); qs.last(); qs.reverse().first()',
            _first_last,
        )

        def _gets() -> dict[str, Any]:
            hit = ProductDocument.objects.get_by_pk(1)
            missing = ""
            try:
                ProductDocument.objects.get_by_pk(99999)
            except DocumentNotFound as exc:
                missing = f"{type(exc).__name__}: {exc}"
            return {
                "get_by_pk_1": hit_payload(hit),
                "key_for_1": ProductDocument.key_for(1),
                "missing_99999": missing,
            }

        add(
            "get_by_pk",
            "get_by_pk / DoesNotExist / key_for",
            "JSON.GET by key. Missing pk raises ProductDocument.DoesNotExist.",
            "ProductDocument.objects.get_by_pk(1); ProductDocument.key_for(1)",
            _gets,
        )

        def _geo() -> dict[str, Any]:
            sample = [
                {
                    "pk": hit.pk,
                    "name": hit.name,
                    "location": hit.location,
                    "category": getattr(hit.category, "name", None),
                }
                for hit in ProductDocument.objects.all()[:6]
            ]
            geo_error = ""
            try:
                list(ProductDocument.objects.filter(location__geo_distance=("x", 1)))
            except (NotSupportedError, NotImplementedError, TypeError) as exc:
                geo_error = f"{type(exc).__name__}: {exc}"
            return {"sample": sample, "geo_distance_error": geo_error}

        add(
            "geo",
            "fields.Geo (stored) + __geo_distance",
            "Coordinates come from CATEGORY_COORDS. Distance query is not in 1.0.",
            "hit.location; ProductDocument.objects.filter(location__geo_distance=...)",
            _geo,
        )
        add(
            "hash",
            "HASH storage - TagHashDocument",
            "Separate ON HASH index. Same Tag rows, no Nested. AUTO_INDEX on Tag save.",
            "list(TagHashDocument.objects.all()[:20])",
            lambda: {
                "count": TagHashDocument.objects.all().count(),
                "storage": TagHashDocument._meta.storage.value,
                "alias": TagHashDocument._meta.index_alias,
                "prefix": TagHashDocument._meta.key_prefix,
                "hits": [
                    {"pk": hit.pk, "name": hit.name, "data": display_params(hit.data)}
                    for hit in TagHashDocument.objects.all()[:20]
                ],
            },
        )
        add(
            "aggregate",
            "Aggregate on a filtered set",
            "Same builder as /aggregations/. Here: count by available.",
            'ProductDocument.objects.aggregate(Aggregate().group_by("available").count())',
            lambda: ProductDocument.objects.aggregate(
                Aggregate().group_by("available").count("n")
            ),
        )
        return demos


def _parse_lookup_value(lookup: str, raw: str) -> Any:
    if lookup == "isnull":
        return raw.lower() not in {"0", "false", "no", ""}
    if lookup == "in":
        return [part.strip() for part in raw.split(",") if part.strip()]
    if lookup == "range":
        left, _, right = raw.partition(",")
        return (_maybe_number(left.strip()), _maybe_number(right.strip()))
    if lookup in {"gt", "gte", "lt", "lte"}:
        return _maybe_number(raw)
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    return _maybe_number(raw)


def _maybe_number(raw: str) -> Any:
    if raw == "":
        return raw
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


class LabIndexView(SearchDebugMixin, View):
    """Registry, IndexManager, settings, apply_index_action."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return TemplateResponse(request, "core/lab_index.html", self._context())

    def post(self, request: HttpRequest) -> HttpResponse:
        action = request.POST.get("action", "")
        document = request.POST.get("document", "core.ProductDocument")
        raw_pk = request.POST.get("pk", "").strip()
        related = request.POST.get("related", "").strip()
        try:
            pk: int | str = int(raw_pk) if raw_pk.isdigit() else raw_pk
            payload: dict[str, Any] = {"document": document, "pk": pk}
            if action == IndexAction.REINDEX_RELATED:
                payload["related"] = related or "core.Category"
            apply_index_action(action, payload)
            messages.success(
                request,
                f"apply_index_action({action!r}, {payload!r}) completed.",
            )
        except Exception as exc:
            messages.error(request, f"{type(exc).__name__}: {exc}")
        return redirect("lab-index")

    def _context(self) -> dict[str, Any]:
        registered = []
        for document_cls in document_registry.documents:
            manager = IndexManager(document_cls)
            info: dict[str, Any] = {}
            exists = manager.exists()
            if exists:
                raw_info = manager.info()
                info = _select_info(raw_info)
            registered.append(
                {
                    "label": document_registry.label_for(document_cls),
                    "class_name": document_cls.__name__,
                    "model": (
                        document_cls._meta.model._meta.label
                        if document_cls._meta.model is not None
                        else None
                    ),
                    "alias": document_cls._meta.index_alias,
                    "prefix": document_cls._meta.key_prefix,
                    "storage": document_cls._meta.storage.value,
                    "auto_index": document_cls._meta.auto_index,
                    "embedded": document_cls._meta.embedded,
                    "search_fields": document_cls._meta.search_fields_option,
                    "language": document_cls._meta.language,
                    "exists": exists,
                    "check": manager.check() if exists else None,
                    "drift": manager.drift() if exists else None,
                    "fingerprint": manager.schema.fingerprint(),
                    "field_names": [item.alias for item in manager.schema.fields],
                    "field_types": [
                        f"{item.alias}:{item.type}"
                        + (" INDEXMISSING" if item.index_missing else "")
                        for item in manager.schema.fields
                    ],
                    "info": info,
                    "info_json": pretty(info),
                    "related": [
                        related._meta.label
                        for related in document_cls._meta.related_map
                    ],
                }
            )
        embedded = [
            {
                "class_name": cls.__name__,
                "model": cls._meta.model._meta.label if cls._meta.model else None,
                "embedded": True,
            }
            for cls in (CategoryDocument, TagDocument, VendorDocument)
        ]
        settings_rows = []
        for key, default in DEFAULTS.items():
            current = redis_search_setting(key)
            settings_rows.append(
                {
                    "key": key,
                    "value": repr(current),
                    "default": repr(default),
                    "overridden": current != default,
                }
            )
        return {
            "registered": registered,
            "embedded": embedded,
            "settings_rows": settings_rows,
            "field_classes": [
                cls.__name__
                for cls in (
                    fields.Text,
                    fields.Tag,
                    fields.Numeric,
                    fields.Boolean,
                    fields.Geo,
                    fields.Vector,
                    fields.Object,
                    fields.Nested,
                )
            ],
            "index_actions": [item.value for item in IndexAction],
        }


def _select_info(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"raw": display_params({"value": raw})}
    keep = (
        "index_name",
        "num_docs",
        "num_records",
        "num_terms",
        "hashing_policy",
        "max_doc_id",
        "total_indexing_time",
        "percent_indexed",
        "index_definition",
        "attributes",
        "dialect",
    )
    out: dict[str, Any] = {}
    for key in keep:
        if key in raw:
            out[key] = display_params({key: raw[key]}).get(key, raw[key])
    return out


class AsyncSearchView(SearchDebugMixin, SearchListViewMixin, ListView):
    """Same catalog filters as Search, evaluated with aget / afacets / ato_queryset."""

    paginate_by = 8
    model = Product
    template_name = "core/search.html"
    document_class = ProductDocument

    def get_search_queryset(self) -> Any:
        return catalog_queryset(self.request)

    async def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        self.convert_to_queryset = not bool(
            request.GET.get("knn") or request.GET.get("redis_hits")
        )
        return await self.aget(request, *args, **kwargs)

    async def afacets(self) -> dict[str, list[dict[str, Any]]]:
        return await self.get_queryset().afacets(
            "category__name", "tags__name", "vendor__name"
        )

    async def aget_context_data(self, **kwargs: Any) -> dict[str, Any]:
        qs = self.get_queryset()
        raw_query, raw_params = qs.raw()
        data = query_data(self.request)
        context = await super().aget_context_data(query_data=data, **kwargs)
        context["knn_mode"] = bool(data["knn"]) or bool(data.get("redis_hits"))
        context["async_mode"] = True
        context["raw_query"] = raw_query
        context["raw_params"] = display_params(raw_params)
        context["explain"] = await qs.aexplain()
        hidden = Product.objects.filter(available=False)
        context["sqlite_hidden"] = await hidden.acount()
        paginator = context.get("paginator")
        if _unfiltered(qs) and paginator is not None:
            context["redis_count"] = paginator.count
        else:
            context["redis_count"] = await ProductDocument.objects.all().acount()
        context["price_stats"] = await qs.aaggregate(
            Aggregate()
            .group_by("available")
            .count("count")
            .avg("price", "avg_price")
            .min("price", "min_price")
            .max("price", "max_price")
            .sum("price", "sum_price")
        )
        return context
