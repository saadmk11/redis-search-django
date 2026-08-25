from __future__ import annotations

from unittest import mock

import pytest

from redis_search_django.actions import (
    aapply_index_action,
    apply_index_action,
    document_label,
)
from redis_search_django.documents import Document
from redis_search_django.enums import IndexAction
from redis_search_django.exceptions import UnknownIndexAction
from redis_search_django.signals import BaseSignalProcessor, RealtimeSignalProcessor

from .helpers import alive_index, live_index
from .models import Category, Vendor


@pytest.fixture
def processor():
    proc = RealtimeSignalProcessor()
    proc.setup()
    yield proc
    proc.teardown()


@pytest.mark.django_db(transaction=True)
def test_iter_save_skips_disabled_auto_index(document_class, category_obj):
    from redis_search_django.actions import iter_save_payloads

    document_class("CatOff", Category, ["name"], auto_index=False)
    assert list(iter_save_payloads(category_obj)) == []


@pytest.mark.django_db(transaction=True)
def test_iter_save_skips_disabled_related_auto_index(
    nested_document_class, category_obj
):
    from redis_search_django.actions import iter_save_payloads

    product_doc, _ = nested_document_class
    product_doc._meta.auto_index = False
    assert list(iter_save_payloads(category_obj)) == []


@pytest.mark.django_db(transaction=True)
def test_save_upserts_primary_document(document_class, processor):
    doc = document_class("CategoryDocument", Category, ["name"])
    processor.setup()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        category = Category.objects.create(name="Test")
        apply.assert_called_once_with(
            IndexAction.UPSERT,
            {"document": document_label(doc), "pk": category.pk},
        )


@pytest.mark.django_db(transaction=True)
def test_related_pre_delete_schedules_parent_upsert(
    nested_document_class, product_obj, category_obj, processor
):
    _product_doc, _ = nested_document_class
    product_obj.category = category_obj
    product_obj.save()
    processor.setup()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        category_obj.delete()
        assert any(call.args[0] == IndexAction.UPSERT for call in apply.call_args_list)


@pytest.mark.django_db(transaction=True)
def test_delete_removes_primary_document(document_class, processor):
    doc = document_class("CategoryDocument", Category, ["name"])
    category = Category.objects.create(name="Test")
    pk = category.pk
    processor.setup()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        category.delete()
        apply.assert_called_once_with(
            IndexAction.DELETE,
            {"document": document_label(doc), "pk": pk},
        )


@pytest.mark.django_db(transaction=True)
def test_related_save_reindexes_parent(nested_document_class, product_obj, processor):
    product_doc, _ = nested_document_class
    processor.setup()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        product_obj.vendor.name = "Updated"
        product_obj.vendor.save()
        apply.assert_called_with(
            IndexAction.REINDEX_RELATED,
            {
                "document": document_label(product_doc),
                "related": Vendor._meta.label,
                "pk": product_obj.vendor.pk,
            },
        )


@pytest.mark.django_db(transaction=True)
def test_m2m_add_reindexes_product(
    nested_document_class, product_obj, tag_obj, processor
):
    product_doc, _ = nested_document_class
    processor.setup()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        product_obj.tags.add(tag_obj)
        apply.assert_any_call(
            IndexAction.UPSERT,
            {"document": document_label(product_doc), "pk": product_obj.pk},
        )


@pytest.mark.django_db
def test_auto_index_false_is_not_connected(document_class):
    document_class("CategoryDocument", Category, ["name"], auto_index=False)
    processor = RealtimeSignalProcessor()
    processor.setup()
    try:
        with mock.patch("redis_search_django.signals.apply_index_action") as apply:
            Category.objects.create(name="NoIndex")
            apply.assert_not_called()
    finally:
        processor.teardown()


@pytest.mark.django_db
def test_teardown_disconnects(document_class):
    document_class("CategoryDocument", Category, ["name"])
    processor = RealtimeSignalProcessor()
    processor.setup()
    processor.teardown()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        Category.objects.create(name="After")
        apply.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_custom_dispatch_is_used(document_class):
    doc = document_class("CategoryDocument", Category, ["name"])
    seen: list[tuple[str, dict]] = []

    class RecordingProcessor(BaseSignalProcessor):
        def dispatch(self, action, payload):
            seen.append((action, payload))

    processor = RecordingProcessor()
    processor.setup()
    try:
        category = Category.objects.create(name="Queued")
    finally:
        processor.teardown()
    assert seen == [
        (IndexAction.UPSERT, {"document": document_label(doc), "pk": category.pk})
    ]


@pytest.mark.django_db(transaction=True)
def test_apply_index_action_uses_instance_queryset(nested_document_class, product_obj):
    product_doc, _ = nested_document_class
    qs = product_doc.instance_queryset()
    selected = qs.query.select_related
    assert "vendor" in selected
    assert "category" in selected

    with live_index(product_doc):
        with mock.patch.object(
            product_doc,
            "instance_queryset",
            wraps=product_doc.instance_queryset,
        ) as spy:
            apply_index_action(
                IndexAction.UPSERT,
                {"document": document_label(product_doc), "pk": product_obj.pk},
            )
        spy.assert_called()
        assert product_doc.objects.get(pk=product_obj.pk).name == product_obj.name


@pytest.mark.django_db(transaction=True)
def test_apply_index_action_ignores_get_queryset_filter(document_class, category_obj):
    doc = document_class(
        "CatLiveFilter",
        Category,
        ["name"],
        auto_index=False,
        extra_attrs={
            "get_queryset": classmethod(
                lambda cls: cls.instance_queryset().filter(name="Never")
            )
        },
    )
    with live_index(doc):
        apply_index_action(
            IndexAction.UPSERT,
            {"document": document_label(doc), "pk": category_obj.pk},
        )
        assert doc.objects.get(pk=category_obj.pk).name == category_obj.name


@pytest.mark.django_db(transaction=True)
def test_apply_index_action_upsert_and_delete(document_class, category_obj):
    doc = document_class("CategoryDocument", Category, ["name"], auto_index=False)
    with live_index(doc):
        apply_index_action(
            IndexAction.UPSERT,
            {"document": document_label(doc), "pk": category_obj.pk},
        )
        assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
        apply_index_action(
            IndexAction.DELETE,
            {"document": document_label(doc), "pk": category_obj.pk},
        )
        with pytest.raises(doc.DoesNotExist):
            doc.objects.get(pk=category_obj.pk)


@pytest.mark.django_db(transaction=True)
def test_apply_index_action_upsert_missing_deletes_key(document_class, category_obj):
    doc = document_class("CategoryDocument", Category, ["name"], auto_index=False)
    with live_index(doc):
        apply_index_action(
            IndexAction.UPSERT,
            {"document": document_label(doc), "pk": category_obj.pk},
        )
        assert doc.objects.get(pk=category_obj.pk).name == category_obj.name
        pk = category_obj.pk
        category_obj.delete()
        apply_index_action(
            IndexAction.UPSERT, {"document": document_label(doc), "pk": pk}
        )
        with pytest.raises(doc.DoesNotExist):
            doc.objects.get(pk=pk)


def test_apply_index_action_unknown():
    with pytest.raises(UnknownIndexAction):
        apply_index_action("nope", {"document": "tests.Missing", "pk": 1})


def test_apply_index_action_unknown_document():
    with pytest.raises(LookupError, match="No Document registered"):
        apply_index_action(IndexAction.DELETE, {"document": "tests.Missing", "pk": 1})


@pytest.mark.django_db(transaction=True)
async def test_aapply_index_action_upsert_and_delete(document_class, category_obj):
    doc = document_class("CategoryDocument", Category, ["name"], auto_index=False)
    async with alive_index(doc):
        await aapply_index_action(
            IndexAction.UPSERT,
            {"document": document_label(doc), "pk": category_obj.pk},
        )
        hit = await doc.objects.aget(pk=category_obj.pk)
        assert hit.name == category_obj.name
        await aapply_index_action(
            IndexAction.DELETE,
            {"document": document_label(doc), "pk": category_obj.pk},
        )
        with pytest.raises(doc.DoesNotExist):
            await doc.objects.aget(pk=category_obj.pk)


@pytest.mark.django_db(transaction=True)
async def test_aapply_index_action_upsert_missing_deletes_key(document_class):
    doc = document_class("CategoryDocument", Category, ["name"], auto_index=False)
    category = await Category.objects.acreate(name="Gone")
    async with alive_index(doc):
        await aapply_index_action(
            IndexAction.UPSERT,
            {"document": document_label(doc), "pk": category.pk},
        )
        assert (await doc.objects.aget(pk=category.pk)).name == "Gone"
        pk = category.pk
        await category.adelete()
        await aapply_index_action(
            IndexAction.UPSERT, {"document": document_label(doc), "pk": pk}
        )
        with pytest.raises(doc.DoesNotExist):
            await doc.objects.aget(pk=pk)


async def test_aapply_index_action_unknown():
    with pytest.raises(UnknownIndexAction):
        await aapply_index_action("nope", {"document": "tests.Missing", "pk": 1})


@pytest.mark.django_db(transaction=True)
async def test_aapply_index_action_reindex_related(
    nested_document_class, product_obj, category_obj
):
    product_doc, _ = nested_document_class
    product_obj.category = category_obj
    await product_obj.asave()
    async with alive_index(product_doc):
        from redis_search_django.indexer import Indexer

        await Indexer().aupsert(product_doc, product_obj)
        category_obj.name = "Relabeled"
        await category_obj.asave()
        await aapply_index_action(
            IndexAction.REINDEX_RELATED,
            {
                "document": document_label(product_doc),
                "related": "tests.Category",
                "pk": category_obj.pk,
            },
        )
        hit = await product_doc.objects.aget(pk=product_obj.pk)
        assert hit.category.name == "Relabeled"


@pytest.mark.django_db(transaction=True)
def test_apply_index_action_related_missing_is_noop(document_class, product_obj):
    from .models import Product

    product_doc = document_class("ProdRelMiss", Product, ["name"])
    with live_index(product_doc):
        from redis_search_django.indexer import Indexer

        Indexer().upsert(product_doc, product_obj)
        apply_index_action(
            IndexAction.REINDEX_RELATED,
            {
                "document": document_label(product_doc),
                "related": "tests.Category",
                "pk": 999_999,
            },
        )
        assert product_doc.objects.get(pk=product_obj.pk).name == product_obj.name


@pytest.mark.django_db(transaction=True)
async def test_aapply_index_action_related_missing_is_noop(document_class, product_obj):
    from .models import Product

    product_doc = document_class("ProdARelMiss", Product, ["name"])
    async with alive_index(product_doc):
        from redis_search_django.indexer import Indexer

        await Indexer().aupsert(product_doc, product_obj)
        await aapply_index_action(
            IndexAction.REINDEX_RELATED,
            {
                "document": document_label(product_doc),
                "related": "tests.Category",
                "pk": 999_999,
            },
        )
        hit = await product_doc.objects.aget(pk=product_obj.pk)
        assert hit.name == product_obj.name


@pytest.mark.django_db(transaction=True)
def test_signal_errors_log_mode(document_class, settings, caplog):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "SIGNAL_ERRORS": "log"}
    document_class("CatLogErr", Category, ["name"])
    processor = RealtimeSignalProcessor()
    processor.setup()
    with mock.patch(
        "redis_search_django.signals.apply_index_action",
        side_effect=RuntimeError("boom"),
    ):
        with caplog.at_level("ERROR"):
            Category.objects.create(name="logged")
    assert "signal handler failed" in caplog.text
    processor.teardown()


def test_processor_skips_when_auto_index_off(settings, document_class):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "AUTO_INDEX": False}
    document_class("CatNoAuto", Category, ["name"])
    processor = RealtimeSignalProcessor()
    processor.setup()
    assert processor._connections == []


@pytest.mark.django_db(transaction=True)
def test_connect_skips_auto_index_false(document_class):
    doc = document_class("CatNoSig", Category, ["name"], auto_index=False)
    processor = RealtimeSignalProcessor()
    processor.connect_document(doc)
    assert processor._connections == []


@pytest.mark.django_db(transaction=True)
def test_signal_errors_raise_mode(document_class, settings):
    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "SIGNAL_ERRORS": "raise"}
    document_class("CatRaiseErr", Category, ["name"])
    processor = RealtimeSignalProcessor()
    processor.setup()
    with mock.patch(
        "redis_search_django.signals.apply_index_action",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            Category.objects.create(name="raised")
    processor.teardown()


def test_m2m_through_without_related_map():
    from redis_search_django.signals import _m2m_through_models

    class Bare(Document):
        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.m2mnone"
            prefix = "rsd:test.category.m2mnone:"

    assert _m2m_through_models(Bare) == []


def test_connect_document_skips_when_model_missing():
    class NoModel(Document):
        class Django:
            abstract = True
            embedded = True

    processor = RealtimeSignalProcessor()
    processor.connect_document(NoModel)
    assert processor._connections == []


@pytest.mark.django_db(transaction=True)
def test_handle_delete_skips_disabled_auto_index(document_class, category_obj):
    document_class("CatOffDel", Category, ["name"], auto_index=False)
    processor = RealtimeSignalProcessor()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        processor.handle_delete(Category, category_obj)
        apply.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_handle_pre_delete_skips_disabled_and_missing_parents(
    nested_document_class, product_obj, category_obj
):
    product_doc, _ = nested_document_class
    product_doc._meta.auto_index = False
    processor = RealtimeSignalProcessor()
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        processor.handle_pre_delete(Category, category_obj)
        apply.assert_not_called()

    product_doc._meta.auto_index = True
    product_doc.get_instances_from_related = classmethod(  # type: ignore[method-assign]
        lambda cls, related: None
    )
    with mock.patch("redis_search_django.signals.apply_index_action") as apply:
        processor.handle_pre_delete(Category, category_obj)
        apply.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_handle_pre_delete_schedules_single_parent():
    from .catalog import create_sample_book, make_catalog_documents
    from .models import BookExtra

    make_catalog_documents()
    book = create_sample_book()
    extra = book.extra
    processor = RealtimeSignalProcessor()
    processor.setup()
    try:
        with mock.patch("redis_search_django.signals.apply_index_action") as apply:
            processor.handle_pre_delete(BookExtra, extra)
            assert any(
                call.args[0] == IndexAction.UPSERT and call.args[1]["pk"] == book.pk
                for call in apply.call_args_list
            )
    finally:
        processor.teardown()


def test_m2m_through_skips_unrelated_and_missing_fields():
    from redis_search_django import fields
    from redis_search_django.signals import _m2m_through_models

    from .conftest import make_document
    from .models import Product, Tag

    class NoModelEmb(Document):
        name = fields.Text()

        class Django:
            abstract = True
            embedded = True

    tag_doc = make_document("TagThrough", Tag, ["name"], embedded=True)
    category_doc = make_document("CatThrough", Category, ["name"], embedded=True)

    class ProductDoc(Document):
        mystery = fields.Nested(NoModelEmb)
        missing = fields.Nested(tag_doc, model_attr="not_a_field")
        category = fields.Nested(category_doc, model_attr="category")
        tags = fields.Nested(tag_doc)

        class Django:
            model = Product
            fields = ["name"]
            related_models = {
                Tag: {"related_name": "product_set", "many": True},
                Category: {"related_name": "product_set", "many": True},
            }

        class Index:
            name = "idx:test.product.m2mskip"
            prefix = "rsd:test.product.m2mskip:"

    throughs = _m2m_through_models(ProductDoc)
    assert Product.tags.through in throughs
