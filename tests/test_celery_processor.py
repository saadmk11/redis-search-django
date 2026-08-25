from __future__ import annotations

import json

import pytest

from redis_search_django.actions import document_label
from redis_search_django.enums import IndexAction

from .catalog import create_sample_book, make_catalog_documents
from .dummy_celery import DummyCelerySignalProcessor, DummyTask
from .helpers import is_redis_running
from .models import Author


@pytest.mark.django_db(transaction=True)
def test_dummy_celery_queues_json_payloads_then_applies():
    docs = make_catalog_documents()
    book_doc = docs["book"]
    task = DummyTask()
    processor = DummyCelerySignalProcessor(task)
    processor.setup()
    try:
        book = create_sample_book()
        assert task.calls
        assert all(isinstance(payload, dict) for _, payload in task.calls)
        json.dumps(task.calls[0][1])
        upserts = [c for c in task.calls if c[0] == IndexAction.UPSERT]
        assert any(
            call[1]["document"] == document_label(book_doc)
            and str(call[1]["pk"]) == str(book.pk)
            for call in upserts
        )
        applied = task.apply()
        assert applied >= 1
        assert task.calls == []
    finally:
        processor.teardown()


@pytest.mark.django_db(transaction=True)
def test_dummy_celery_related_save_enqueues_reindex():
    docs = make_catalog_documents()
    book_doc = docs["book"]
    task = DummyTask()
    processor = DummyCelerySignalProcessor(task)
    processor.setup()
    try:
        book = create_sample_book()
        task.calls.clear()
        author = book.author
        author.name = "Ada King"
        author.save()
        related = [c for c in task.calls if c[0] == IndexAction.REINDEX_RELATED]
        assert related
        assert related[0][1]["document"] == document_label(book_doc)
        assert related[0][1]["related"] == Author._meta.label
        assert str(related[0][1]["pk"]) == str(author.pk)
    finally:
        processor.teardown()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
def test_dummy_celery_does_not_write_until_apply(document_class):
    from redis_search_django.client import get_redis_connection
    from redis_search_django.index import IndexManager

    from .models import Category

    doc = document_class("CategoryDocument", Category, ["name"])
    manager = IndexManager(doc)
    manager.create()
    task = DummyTask()
    processor = DummyCelerySignalProcessor(task)
    processor.setup()
    try:
        category = Category.objects.create(name="queued")
        client = get_redis_connection()
        assert not client.exists(doc.key_for(category.pk))
        task.apply()
        assert client.exists(doc.key_for(category.pk))
    finally:
        processor.teardown()
        manager.drop(delete_docs=True)
