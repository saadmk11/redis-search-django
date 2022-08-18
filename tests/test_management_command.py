from unittest import mock

from django.core.management import call_command


@mock.patch("redis_search_django.management.commands.index.get_redis_connection")
@mock.patch("redis_search_django.management.commands.index.Migrator")
@mock.patch(
    "redis_search_django.management.commands.index.document_registry.index_documents"
)
def test_index_command_only_migrate(
    index_documents, migrator, get_redis_connection, document_class
):
    call_command("index", "--only-migrate")

    get_redis_connection.assert_called_once()
    migrator().run.assert_called_once()
    index_documents.assert_not_called()


@mock.patch("redis_search_django.management.commands.index.get_redis_connection")
@mock.patch("redis_search_django.management.commands.index.Migrator")
@mock.patch(
    "redis_search_django.management.commands.index.document_registry.index_documents"
)
def test_index_command(index_documents, migrator, get_redis_connection, document_class):
    call_command("index")

    get_redis_connection.assert_called_once()
    migrator().run.assert_called_once()
    index_documents.assert_called_once()


@mock.patch("redis_search_django.management.commands.index.get_redis_connection")
@mock.patch("redis_search_django.management.commands.index.Migrator")
@mock.patch(
    "redis_search_django.management.commands.index.document_registry.index_documents"
)
def test_index_command_with_models_option(
    index_documents, migrator, get_redis_connection, document_class
):
    call_command("index", "--models", "tests.Vendor", "tests.Category")

    get_redis_connection.assert_called_once()
    migrator().run.assert_called_once()
    index_documents.assert_called_once_with(["tests.Vendor", "tests.Category"])
