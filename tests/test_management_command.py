from unittest import mock

from django.core.management import call_command

from redis_search_django.documents import JsonDocument
from redis_search_django.registry import DocumentRegistry
from tests.models import Category, Tag, Vendor


@mock.patch("redis_search_django.documents.JsonDocument.index_all")
@mock.patch("redis_search_django.management.commands.index.get_redis_connection")
@mock.patch("redis_search_django.management.commands.index.Migrator")
def test_index_command_only_migrate(
    migrator, get_redis_connection, index_all, document_class
):
    with mock.patch(
        "redis_search_django.management.commands.index.document_registry",
        DocumentRegistry(),
    ) as mocked_registry:
        DocumentClass = document_class(JsonDocument, Vendor, ["name"])
        mocked_registry.register(DocumentClass)

        call_command("index", "--only-migrate")

        get_redis_connection.assert_called_once()
        migrator().run.assert_called_once()
        index_all.assert_not_called()


@mock.patch("redis_search_django.documents.JsonDocument.index_all")
@mock.patch("redis_search_django.management.commands.index.get_redis_connection")
@mock.patch("redis_search_django.management.commands.index.Migrator")
def test_index_command(migrator, get_redis_connection, index_all, document_class):
    with mock.patch(
        "redis_search_django.management.commands.index.document_registry",
        DocumentRegistry(),
    ) as mocked_registry:
        DocumentClass = document_class(JsonDocument, Vendor, ["name"])
        mocked_registry.register(DocumentClass)

        call_command("index")

        get_redis_connection.assert_called_once()
        migrator().run.assert_called_once()
        index_all.assert_called_once()


@mock.patch("redis_search_django.documents.JsonDocument.index_all")
@mock.patch("redis_search_django.management.commands.index.get_redis_connection")
@mock.patch("redis_search_django.management.commands.index.Migrator")
def test_index_command_with_models_option(
    migrator, get_redis_connection, index_all, document_class
):
    with mock.patch(
        "redis_search_django.management.commands.index.document_registry",
        DocumentRegistry(),
    ) as mocked_registry:
        VendorDocumentClass = document_class(JsonDocument, Vendor, ["name"])
        CategoryDocumentClass = document_class(JsonDocument, Category, ["name"])
        TagDocumentClass = document_class(JsonDocument, Tag, ["name"])

        mocked_registry.register(VendorDocumentClass)
        mocked_registry.register(CategoryDocumentClass)
        mocked_registry.register(TagDocumentClass)

        call_command("index", "--models", "tests.Vendor", "tests.Category")

        get_redis_connection.assert_called_once()
        migrator().run.assert_called_once()
        assert index_all.call_count == 2
