from __future__ import annotations

import pytest

from redis_search_django import fields
from redis_search_django.documents import Document
from redis_search_django.exceptions import ConfigurationError
from redis_search_django.registry import document_registry
from redis_search_django.schema import build_schema, flatten_lookup, redis_fields

from .conftest import make_document
from .models import Category, Product, Tag, Vendor


def test_document_meta_rejects_unrelated_class():
    from redis_search_django.documents import DocumentMeta

    with pytest.raises(TypeError, match="not a Document class"):

        class Weird(metaclass=DocumentMeta):
            pass


def test_django_fields_are_mapped(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])
    assert "name" in doc._meta.fields
    assert doc._meta.model is Category
    assert doc in document_registry.documents


def test_embedded_documents_are_not_registered():
    doc = make_document("CategoryEmbedded", Category, ["name"], embedded=True)
    assert doc._meta.embedded is True
    assert doc not in document_registry.documents


def test_related_fields_cannot_be_in_django_fields():
    with pytest.raises(ConfigurationError, match="Related field"):
        make_document("BadProduct", Product, ["name", "vendor"])


def test_related_models_list_is_rejected():
    with pytest.raises(ConfigurationError, match="not a list"):

        class Bad(Document):
            class Django:
                model = Category
                fields = ["name"]
                related_models = [Vendor]


def test_nested_inference(nested_document_class):
    product_doc, _embedded = nested_document_class
    related = product_doc._meta.related_map
    assert Vendor in related
    assert related[Vendor]["related_name"] == "product"
    assert related[Vendor]["many"] is False
    assert Category in related
    assert related[Category]["many"] is True
    assert Tag in related
    assert related[Tag]["many"] is True


def test_related_models_empty_disables_inference():
    vendor_doc = make_document("VendorEmb", Vendor, ["name"], embedded=True)

    class ProductDoc(Document):
        vendor = fields.Object(vendor_doc)

        class Django:
            model = Product
            fields = ["name"]
            related_models = {}

        class Index:
            name = "idx:test.product.emptyrel"
            prefix = "rsd:test.product.emptyrel:"

    assert ProductDoc._meta.related_map == {}


def test_hash_rejects_nested():
    tag_doc = make_document("TagEmb", Tag, ["name"], embedded=True)
    with pytest.raises(ConfigurationError, match="Hash storage"):

        class ProductHash(Document):
            tags = fields.Nested(tag_doc)

            class Django:
                model = Product
                fields = ["name"]

            class Index:
                storage = "hash"
                name = "idx:test.product.hashnested"
                prefix = "rsd:test.product.hashnested:"


@pytest.mark.django_db
def test_should_index_default_true(document_class, category_obj):
    doc = document_class("CatDoc", Category, ["name"])
    assert doc.should_index(category_obj) is True


@pytest.mark.django_db
def test_get_queryset_applies_select_related(nested_document_class, product_obj):
    product_doc, _ = nested_document_class
    qs = product_doc.get_queryset()
    assert product_obj in list(qs)
    selected = qs.query.select_related
    assert "vendor" in selected
    assert "category" in selected


@pytest.mark.django_db
def test_prepare_hook(category_obj):
    class UpperCategory(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.upper"
            prefix = "rsd:test.category.upper:"

        @classmethod
        def prepare_name(cls, obj):
            return obj.name.upper()

    from redis_search_django.serializer import Serializer

    payload = Serializer().to_document(UpperCategory, category_obj)
    assert payload["name"] == "TEST"
    assert payload["pk"] == str(category_obj.pk)


def test_abstract_fields_are_copied_not_shared():
    class BaseDoc(Document):
        title = fields.Text()

        class Django:
            abstract = True

    class One(BaseDoc):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.inherit1"
            prefix = "rsd:test.category.inherit1:"

    class Two(BaseDoc):
        class Django:
            model = Vendor
            fields = ["name"]

        class Index:
            name = "idx:test.vendor.inherit2"
            prefix = "rsd:test.vendor.inherit2:"

    assert One._meta.fields["title"] is not Two._meta.fields["title"]
    assert One._meta.fields["title"].document_cls is One
    assert Two._meta.fields["title"].document_cls is Two


def test_missing_model_raises():
    with pytest.raises(ConfigurationError, match=r"requires Django\.model"):

        class NoModel(Document):
            class Django:
                fields = ["name"]


def test_invalid_storage_and_stopwords_and_inherited_model():
    with pytest.raises(ConfigurationError, match="storage"):

        class BadStorage(Document):
            class Django:
                model = Category
                fields = ["name"]

            class Index:
                storage = "xml"
                name = "idx:test.category.badstore"
                prefix = "rsd:test.category.badstore:"

    class WithStops(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.stops"
            prefix = "rsd:test.category.stops:"
            stopwords = ("the", "a")

    assert WithStops._meta.stopwords == ("the", "a")

    class BaseCat(Document):
        class Django:
            abstract = True
            model = Category

    class ChildCat(BaseCat):
        class Django:
            fields = ["name"]

        class Index:
            name = "idx:test.category.childmodel"
            prefix = "rsd:test.category.childmodel:"

    assert ChildCat._meta.model is Category


def test_related_models_must_include_keys():
    with pytest.raises(ConfigurationError, match="related_name"):

        class BadRel(Document):
            class Django:
                model = Product
                fields = ["name"]
                related_models = {Vendor: {"many": False}}

            class Index:
                name = "idx:test.product.badrel"
                prefix = "rsd:test.product.badrel:"


def test_cannot_nest_same_document():
    class SelfDoc(Document):
        class Django:
            model = Category
            fields = ["name"]
            embedded = True

    with pytest.raises(ConfigurationError, match="cannot nest"):

        class Loop(Document):
            inner = fields.Object(SelfDoc)

            class Django:
                model = Category
                fields = ["name"]

            class Index:
                name = "idx:test.category.loop"
                prefix = "rsd:test.category.loop:"


def test_ambiguous_related_and_alias_collision():
    vendor_doc = make_document("VendEmb", Vendor, ["name"], embedded=True)
    with pytest.raises(ConfigurationError):

        class TwoVendors(Document):
            vendor = fields.Object(vendor_doc)
            extra = fields.Object(vendor_doc, model_attr="category")

            class Django:
                model = Product
                fields = ["name"]

            class Index:
                name = "idx:test.product.twovendors"
                prefix = "rsd:test.product.twovendors:"

    with pytest.raises(ConfigurationError, match="Cannot infer"):

        class Boom(Document):
            vendor = fields.Object(vendor_doc)
            also = fields.Object(vendor_doc, model_attr="missing")

            class Django:
                model = Product
                fields = ["name"]

            class Index:
                name = "idx:test.product.ambig"
                prefix = "rsd:test.product.ambig:"


def test_alias_collision_on_declared_fields():
    with pytest.raises(ConfigurationError, match="alias collision"):

        class Clash(Document):
            name = fields.Text()
            other = fields.Text(as_name="name")

            class Django:
                model = Category

            class Index:
                name = "idx:test.category.clash"
                prefix = "rsd:test.category.clash:"


def test_infer_non_related_model_attr():
    vendor_doc = make_document("VendEmb2", Vendor, ["name"], embedded=True)
    with pytest.raises(ConfigurationError, match="not a related field"):

        class BadAttr(Document):
            vendor = fields.Object(vendor_doc, model_attr="name")

            class Django:
                model = Product
                fields = ["name"]

            class Index:
                name = "idx:test.product.badattr"
                prefix = "rsd:test.product.badattr:"


@pytest.mark.django_db
def test_get_instances_from_related_unknown_returns_none(document_class, category_obj):
    doc = document_class("CatRelNone", Category, ["name"])
    assert doc.get_instances_from_related(category_obj) is None


@pytest.mark.django_db
def test_index_all_rebuilds(document_class, category_obj):
    from redis_search_django.index import IndexManager

    from .helpers import is_redis_running

    if not is_redis_running():
        pytest.skip("Redis is not running")
    doc = document_class("CatIndexAll", Category, ["name"])
    assert doc.index_all() >= 1
    assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
    IndexManager(doc).drop(delete_docs=True)


def test_concrete_base_fields_are_not_copied():
    class Parent(Document):
        extra = fields.Text()

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.parentconc"
            prefix = "rsd:test.category.parentconc:"

    class Child(Parent):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.childconc"
            prefix = "rsd:test.category.childconc:"

    assert "extra" not in Child._meta.fields
    assert "name" in Child._meta.fields


def test_django_fields_skips_declared_and_pk():
    class Declared(Document):
        name = fields.Text(as_name="title")

        class Django:
            model = Category
            fields = ["name", "id", "pk"]

        class Index:
            name = "idx:test.category.declpk"
            prefix = "rsd:test.category.declpk:"

    assert Declared._meta.fields["name"].as_name() == "title"


def test_explicit_related_models_skips_inference():
    vendor_doc = make_document("VendSkip", Vendor, ["name"], embedded=True)
    category_doc = make_document("CatSkip", Category, ["name"], embedded=True)

    class ProductDoc(Document):
        vendor = fields.Object(vendor_doc)
        category = fields.Object(category_doc, required=False)

        class Django:
            model = Product
            fields = ["name"]
            related_models = {
                Vendor: {"related_name": "product", "many": False},
            }

        class Index:
            name = "idx:test.product.relskip"
            prefix = "rsd:test.product.relskip:"

    assert ProductDoc._meta.related_map[Vendor]["related_name"] == "product"
    assert Category in ProductDoc._meta.related_map


def test_object_target_without_model_is_skipped():
    class Emb(Document):
        title = fields.Text()

        class Django:
            abstract = True
            embedded = True

    class ProductDoc(Document):
        inner = fields.Object(Emb)

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            name = "idx:test.product.nomodelobj"
            prefix = "rsd:test.product.nomodelobj:"

    assert ProductDoc._meta.related_map == {}


def test_duplicate_object_same_relation_is_ok():
    vendor_doc = make_document("VendDup", Vendor, ["name"], embedded=True)

    class ProductDoc(Document):
        vendor = fields.Object(vendor_doc)
        also = fields.Object(vendor_doc, model_attr="vendor")

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            name = "idx:test.product.samerel"
            prefix = "rsd:test.product.samerel:"

    assert ProductDoc._meta.related_map[Vendor]["related_name"] == "product"


def test_infer_relation_rejects_reverse_without_field_name():
    from redis_search_django.documents import _infer_relation

    from .models import Author, Book

    book_doc = make_document("BookRev", Book, ["title"], embedded=True)
    field = fields.Object(book_doc)
    field.bind("books", object)
    rel = Author._meta.get_field("books")
    old_name = rel.field.name
    rel.field.name = ""
    try:
        with pytest.raises(ConfigurationError, match="reverse relation"):
            _infer_relation(Author, field)
    finally:
        rel.field.name = old_name


def test_generic_relation_inference():
    from .models import Flag

    flag_doc = make_document("FlagEmb", Flag, ["label"], embedded=True)

    class CategoryDoc(Document):
        flags = fields.Object(flag_doc, model_attr="flags")

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.flags"
            prefix = "rsd:test.category.flags:"

    assert Flag in CategoryDoc._meta.related_map
    assert CategoryDoc._meta.related_map[Flag]["many"] is True


def test_nested_object_child_is_skipped_in_alias_check():
    tag_doc = make_document("TagDeep", Tag, ["name"], embedded=True)

    class CategoryEmb(Document):
        name = fields.Text()
        tag = fields.Object(tag_doc)

        class Django:
            model = Category
            embedded = True

    class ProductDoc(Document):
        category = fields.Object(CategoryEmb)

        class Django:
            model = Product
            fields = ["name"]
            related_models = {}

        class Index:
            name = "idx:test.product.deepobj"
            prefix = "rsd:test.product.deepobj:"

    assert "category" in ProductDoc._meta.fields


def test_nested_alias_collision_across_objects():
    vendor_doc = make_document("VendAlias", Vendor, ["name"], embedded=True)
    category_doc = make_document("CatAlias", Category, ["name"], embedded=True)
    with pytest.raises(ConfigurationError, match="alias collision"):

        class ProductDoc(Document):
            vendor = fields.Object(vendor_doc, as_name="rel")
            category = fields.Object(category_doc, as_name="rel")

            class Django:
                model = Product
                fields = ["name"]
                related_models = {}

            class Index:
                name = "idx:test.product.aliasnest"
                prefix = "rsd:test.product.aliasnest:"


def test_custom_exception_classes_are_kept():
    class Custom(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.customboth"
            prefix = "rsd:test.category.customboth:"

        class DoesNotExist(Exception):
            pass

        class MultipleObjectsReturned(Exception):
            pass

    assert Custom.DoesNotExist is not Document.DoesNotExist
    assert Custom.MultipleObjectsReturned is not Document.MultipleObjectsReturned


@pytest.mark.django_db
def test_get_instances_none_attribute_and_missing_reverse(document_class):
    vendor = Vendor.objects.create(
        name="Orphan", establishment_date=__import__("datetime").date.today()
    )
    vendor_doc = make_document("VEmb", Vendor, ["name"], embedded=True)
    product_doc = document_class(
        "ProdOrphan",
        Product,
        ["name"],
        extra_attrs={"vendor": fields.Object(vendor_doc)},
    )
    assert product_doc.get_instances_from_related(vendor) is None

    class Named(Document):
        class Django:
            model = Product
            fields = ["name"]
            related_models = {
                Category: {"related_name": "parent_product", "many": False},
            }

        class Index:
            name = "idx:test.product.noneattr"
            prefix = "rsd:test.product.noneattr:"

    category = Category.objects.create(name="NoParent")
    category.parent_product = None
    assert Named.get_instances_from_related(category) is None


@pytest.mark.django_db
def test_get_instances_many_without_select_or_prefetch(product_with_tag):
    product, tag = product_with_tag
    tag_doc = make_document("TagMany", Tag, ["name"], embedded=True)

    class ProductDoc(Document):
        tags = fields.Nested(tag_doc)

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            name = "idx:test.product.manynosel"
            prefix = "rsd:test.product.manynosel:"

    assert ProductDoc._meta.select_related_fields == ()
    assert ProductDoc._meta.prefetch_related_fields == ()
    parents = list(ProductDoc.get_instances_from_related(tag))
    assert product in parents


def test_schema_weight_vector_and_flatten_lookup_error(document_class):
    from redis_search_django.fields import Vector

    class Weighted(Document):
        name = fields.Text(weight=2.0, no_stem=True)
        emb = Vector(dims=4)

        class Django:
            model = Category

        class Index:
            name = "idx:test.category.weighted"
            prefix = "rsd:test.category.weighted:"

    schema = build_schema(Weighted)
    types = {field.alias: field.type for field in schema.fields}
    assert types["name"] == "TEXT"
    assert types["emb"] == "VECTOR"
    name_field = Weighted._meta.fields["name"]
    assert name_field.weight == 2.0
    assert name_field.no_stem is True
    assert flatten_lookup(Weighted, "name")[0] == "name"
    assert flatten_lookup(Weighted, "name")[0] == "name"
    with pytest.raises(KeyError):
        flatten_lookup(Weighted, "missing")
    with pytest.raises(KeyError):
        flatten_lookup(Weighted, "")
    with pytest.raises(KeyError):
        flatten_lookup(Weighted, "__")


def test_optional_object_pk_is_index_missing(nested_document_class):
    product_doc, _embedded = nested_document_class
    schema = build_schema(product_doc)
    by_alias = {field.alias: field for field in schema.fields}
    assert by_alias["category_pk"].index_missing is True
    assert by_alias["category_pk"].extra == {"ismissing": True}
    assert by_alias["vendor_pk"].index_missing is False
    assert by_alias["vendor_pk"].extra == {}
    created = {
        field.as_name: list(field.args_suffix) for field in redis_fields(product_doc)
    }
    assert "INDEXMISSING" in created["category_pk"]
    assert "INDEXMISSING" not in created["vendor_pk"]


def test_build_schema_reuses_cached_schema_until_meta_changes(document_class):
    doc = document_class("CatSchemaCache", Category, ["name"])
    first = build_schema(doc)
    second = build_schema(doc)
    assert first is second
    assert first.fingerprint() is first.fingerprint()
    doc._meta.key_prefix = "rsd:test.category.schemacache-b:"
    rebuilt = build_schema(doc)
    assert rebuilt is not first
    assert rebuilt.prefix == "rsd:test.category.schemacache-b:"
    assert build_schema(doc) is rebuilt
