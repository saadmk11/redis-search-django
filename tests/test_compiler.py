from __future__ import annotations

import datetime
import math

import pytest
from django.db.models import Q as DjangoQ

from redis_search_django import Q
from redis_search_django.documents import Document
from redis_search_django.exceptions import ConfigurationError, NotSupportedError
from redis_search_django.query.compiler import QueryCompiler, escape_tag, escape_text

from .models import Category


@pytest.fixture
def product_doc(nested_document_class):
    return nested_document_class[0]


def compile_q(document_cls, q):
    return QueryCompiler(document_cls).compile(q)


def test_rejects_django_q():
    with pytest.raises(TypeError, match=r"redis_search_django\.Q"):
        Q(DjangoQ(name="x"))


def test_empty_query_is_star(document_class):
    doc = document_class("Cat", Category, ["name"])
    compiled = compile_q(doc, Q())
    assert compiled.query == "*"
    assert compiled.params == {}


def test_text_search(product_doc):
    compiled = compile_q(product_doc, Q(name__search="trail running"))
    assert compiled.query == "@name:($p1 $p2)"
    assert compiled.params == {"p1": "trail", "p2": "running"}


def test_in_lookup_iterates_strings_like_django(product_doc):
    compiled = compile_q(product_doc, Q(name__in="ab"))
    assert compiled.query.count("@name") == 2
    with pytest.raises(TypeError, match="iterable"):
        compile_q(product_doc, Q(price__in=5))


def test_numeric_lookup_value_shapes(product_doc):
    from redis_search_django.exceptions import UnsupportedLookup

    with pytest.raises(UnsupportedLookup, match="two values"):
        compile_q(product_doc, Q(price__range=5))
    with pytest.raises(UnsupportedLookup, match="single numeric"):
        compile_q(product_doc, Q(price__gt=object()))
    with pytest.raises(UnsupportedLookup, match="single value"):
        compile_q(product_doc, Q(price=object()))


def test_numeric_range(product_doc):
    compiled = compile_q(product_doc, Q(price__gte=10, price__lte=100))
    assert "@price>=10" in compiled.query
    assert "@price<=100" in compiled.query


def test_numeric_exact(product_doc):
    compiled = compile_q(product_doc, Q(price=10))
    assert compiled.query == "@price:[10 10]"


def test_related_tag_in(product_doc):
    compiled = compile_q(product_doc, Q(category__name__in=["Shoes", "Clothes"]))
    assert compiled.query == '(@category_name:("Shoes")|@category_name:("Clothes"))'
    assert compiled.params == {}


def test_text_in_empty_matches_nothing(product_doc):
    compiled = compile_q(product_doc, Q(name__in=[]))
    assert compiled.query == '@name:("__rsd_in_empty__")'


def test_numeric_in_empty_matches_nothing(product_doc):
    compiled = compile_q(product_doc, Q(price__in=[]))
    assert compiled.query == "@price:[+inf +inf]"


def test_tag_in_uses_params():
    from redis_search_django.documents import Document
    from redis_search_django.fields import Tag

    class Tagged(Document):
        code = Tag()

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.tagin"
            prefix = "rsd:test.category.tagin:"

    compiled = compile_q(Tagged, Q(code__in=["a", "b"]))
    assert compiled.query == "@code:{$p1|$p2}"
    assert compiled.params == {"p1": "a", "p2": "b"}


def test_or_combination(product_doc):
    compiled = compile_q(product_doc, Q(name__search="t") | Q(description__search="t"))
    assert compiled.query == "(@name:($p1) | @description:($p2))"


def test_exclude_tag(product_doc):
    compiled = compile_q(product_doc, ~Q(tags__name="x"))
    assert compiled.query == '-@tags_name:("x")'


def test_text_in_single_value(product_doc):
    compiled = compile_q(product_doc, Q(name__in=["Shoes"]))
    assert compiled.query == '@name:("Shoes")'
    assert compiled.params == {}


def test_boolean_exact_uses_params(product_doc):
    from redis_search_django.documents import Document
    from redis_search_django.fields import Boolean

    from .models import Product

    class Flagged(Document):
        available = Boolean()

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            name = "idx:test.product.boolparam"
            prefix = "rsd:test.product.boolparam:"

    compiled = compile_q(Flagged, Q(available=True))
    assert compiled.query == "@available:{$p1}"
    assert compiled.params == {"p1": "true"}


def test_isnull_requires_index_missing(product_doc):
    with pytest.raises(ConfigurationError, match="index_missing"):
        compile_q(product_doc, Q(name__isnull=True))


def test_isnull_on_optional_field():
    from redis_search_django.documents import Document
    from redis_search_django.fields import Text

    class NullableCategory(Document):
        name = Text(index_missing=True)

        class Django:
            model = Category

        class Index:
            name = "idx:test.category.nullable"
            prefix = "rsd:test.category.nullable:"

    compiled = compile_q(NullableCategory, Q(name__isnull=True))
    assert compiled.query == "ismissing(@name)"


def test_rejected_lookup(product_doc):
    with pytest.raises(NotSupportedError):
        compile_q(product_doc, Q(name__icontains="x"))


def test_geo_distance_not_implemented(product_doc):
    with pytest.raises(NotSupportedError, match="geo_distance"):
        compile_q(product_doc, Q(name__geo_distance=1))


def test_range_inf(product_doc):
    compiled = compile_q(product_doc, Q(price__range=(0, math.inf)))
    assert compiled.query == "@price:[0 +inf]"


def test_date_comparison_uses_timestamp():
    from redis_search_django.documents import Document
    from redis_search_django.fields import Numeric

    from .models import Vendor

    class VendorDoc(Document):
        establishment_date = Numeric()

        class Django:
            model = Vendor
            fields = ["name"]

        class Index:
            name = "idx:test.vendor.datecmp"
            prefix = "rsd:test.vendor.datecmp:"

    compiled = compile_q(
        VendorDoc, Q(establishment_date__gte=datetime.date(2020, 1, 1))
    )
    ts = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
    assert compiled.query == f"@establishment_date>={ts}"


def test_tag_startswith_requires_suffix_trie():
    from redis_search_django.documents import Document
    from redis_search_django.fields import Tag

    class Tagged(Document):
        code = Tag()

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.tagstart"
            prefix = "rsd:test.category.tagstart:"

    with pytest.raises(ConfigurationError, match="suffix_trie"):
        compile_q(Tagged, Q(code__startswith="bl"))


def test_escape_text_and_tag():
    assert "\\|" in escape_text("a|b")
    assert "\\ " in escape_tag("hello world")
    assert "\\|" in escape_tag("a|b", separator="|")
    assert "\\|" in escape_tag("c|d", separator="|")


def test_query_too_long(document_class):
    doc = document_class("CatLong", Category, ["name"])
    compiler = QueryCompiler(doc)
    node = Q()
    node.children = [("name__search", "x")] * 4000
    with pytest.raises(ValueError, match="32 KiB"):
        compiler.compile(node)


def test_search_on_non_text_and_empty_tokens(document_class):
    from redis_search_django.exceptions import UnsupportedLookup
    from redis_search_django.fields import Tag

    class Tagged(Document):
        code = Tag()

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.searchtag"
            prefix = "rsd:test.category.searchtag:"

    with pytest.raises(UnsupportedLookup, match="TEXT"):
        compile_q(Tagged, Q(code__search="x"))
    empty = document_class("CatEmptyTok", Category, ["name"])
    compiled = compile_q(empty, Q(name__search="   "))
    assert compiled.query == "*"


def test_isnull_on_optional_object(product_doc):
    compiled = compile_q(product_doc, Q(category__isnull=True))
    assert compiled.query == "ismissing(@category_pk)"
    compiled = compile_q(product_doc, Q(category__isnull=False))
    assert compiled.query == "-ismissing(@category_pk)"


def test_isnull_on_object_child_rewrites_to_parent(product_doc):
    compiled = compile_q(product_doc, Q(category__name__isnull=True))
    assert compiled.query == "ismissing(@category_pk)"
    compiled = compile_q(product_doc, Q(category__name__isnull=False))
    assert compiled.query == "-ismissing(@category_pk)"


def test_isnull_on_required_object_raises(product_doc):
    with pytest.raises(ConfigurationError, match="required Object"):
        compile_q(product_doc, Q(vendor__isnull=True))
    with pytest.raises(ConfigurationError, match="index_missing"):
        compile_q(product_doc, Q(vendor__name__isnull=True))


def test_isnull_on_nested_raises(product_doc):
    with pytest.raises(ConfigurationError, match="Nested"):
        compile_q(product_doc, Q(tags__isnull=True))


def test_isnull_on_nullable_object_child_uses_child_field(document_class):
    from redis_search_django.fields import Object, Text

    from .models import Product

    inner = document_class(
        "CatChildNull",
        Category,
        [],
        extra_attrs={"name": Text(index_missing=True)},
        embedded=True,
    )
    outer = document_class(
        "ProdChildNull",
        Product,
        ["name"],
        extra_attrs={"category": Object(inner, required=False)},
    )
    compiled = compile_q(outer, Q(category__name__isnull=True))
    assert compiled.query == "ismissing(@category_name)"


def test_isnull_false_and_unknown_path(document_class):
    from redis_search_django.documents import Document
    from redis_search_django.exceptions import UnsupportedLookup
    from redis_search_django.fields import Text

    class Nullable(Document):
        name = Text(index_missing=True)

        class Django:
            model = Category

        class Index:
            name = "idx:test.category.isnullf"
            prefix = "rsd:test.category.isnullf:"

    compiled = compile_q(Nullable, Q(name__isnull=False))
    assert compiled.query == "-ismissing(@name)"
    with pytest.raises(UnsupportedLookup, match="Unknown field"):
        compile_q(Nullable, Q(missing__exact="x"))


def test_empty_tag_in_and_numeric_in_and_neg_inf(product_doc):
    from redis_search_django.documents import Document
    from redis_search_django.fields import Tag

    class Tagged(Document):
        code = Tag()

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.tagemptyin"
            prefix = "rsd:test.category.tagemptyin:"

    compiled = compile_q(Tagged, Q(code__in=[]))
    assert compiled.query == "@code:{__rsd_in_empty__}"
    compiled = compile_q(product_doc, Q(price__in=[1, 2]))
    assert "@price:[1 1]" in compiled.query
    compiled = compile_q(product_doc, Q(price__range=(-math.inf, 0)))
    assert compiled.query == "@price:[-inf 0]"


def test_gt_on_text_rejected(product_doc):
    from redis_search_django.exceptions import UnsupportedLookup

    with pytest.raises(UnsupportedLookup, match="NUMERIC"):
        compile_q(product_doc, Q(name__gt=1))


def test_tag_startswith_and_text_startswith(product_doc):
    from redis_search_django.documents import Document
    from redis_search_django.fields import Tag

    class Tagged(Document):
        code = Tag(suffix_trie=True)

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.tagpre"
            prefix = "rsd:test.category.tagpre:"

    compiled = compile_q(Tagged, Q(code__startswith="ab"))
    assert compiled.query.startswith("@code:{$")
    compiled = compile_q(product_doc, Q(name__startswith="Tr"))
    assert compiled.query.startswith("@name:$")


def test_geo_vector_exact_rejected():
    from redis_search_django.documents import Document
    from redis_search_django.exceptions import UnsupportedLookup
    from redis_search_django.fields import Geo, Vector

    from .models import Product

    class Spatial(Document):
        loc = Geo()
        emb = Vector(dims=4)

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            name = "idx:test.product.spatial"
            prefix = "rsd:test.product.spatial:"

    with pytest.raises(UnsupportedLookup, match="exact"):
        compile_q(Spatial, Q(loc="1,2"))
    with pytest.raises(UnsupportedLookup, match="exact"):
        compile_q(Spatial, Q(emb=[0, 0, 0, 0]))


def test_custom_field_exact_is_unsupported(document_class):
    from redis_search_django.exceptions import UnsupportedLookup
    from redis_search_django.fields import Field

    class Weird(Field):
        def redis_type(self):
            return "WEIRD"

    class Odd(Document):
        extra = Weird()

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.weird"
            prefix = "rsd:test.category.weird:"

    with pytest.raises(UnsupportedLookup, match="Cannot compile"):
        compile_q(Odd, Q(extra="x"))


def test_nested_empty_q_and_q_add():
    from redis_search_django.enums import QConnector
    from redis_search_django.query.lookups import Q as SearchQ

    node = SearchQ(name="a")
    node.add(("name", "b"), QConnector.OR)
    assert node.connector is QConnector.OR
    empty = SearchQ()
    empty.add(("name", "c"))
    assert empty.children
    with pytest.raises(TypeError):
        SearchQ(name="a") & DjangoQ(name="b")
    with pytest.raises(TypeError):
        Q(DjangoQ(name="x"), name="y")
    nested = SearchQ(SearchQ())
    compiled = compile_q(
        __import__("tests.conftest", fromlist=["make_document"]).make_document(
            "CatQ", Category, ["name"]
        ),
        nested,
    )
    assert compiled.query == "*"
