from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from redis_search_django.index import IndexManager

from .helpers import is_redis_running
from .models import Category, Product


def test_redisearch_action_must_be_string():
    from redis_search_django.management.commands.redisearch import Command

    with pytest.raises(CommandError, match="action must be a string"):
        Command().handle(action=1)


def test_redisearch_models_filter(document_class):
    document_class("CategoryDocument", Category, ["name"])
    with pytest.raises(CommandError, match="No Document registered"):
        call_command("redisearch", "info", "--models", "tests.Missing")


def test_redisearch_no_documents():
    with pytest.raises(CommandError, match="No matching Document"):
        call_command("redisearch", "info")


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
def test_redisearch_create_skips_when_index_exists(document_class, capsys):
    doc = document_class("CategoryDocument", Category, ["name"])
    manager = IndexManager(doc)
    try:
        call_command("redisearch", "create")
        assert manager.exists()
        assert "created" in capsys.readouterr().out
        call_command("redisearch", "create")
        assert "already exists" in capsys.readouterr().out
    finally:
        manager.drop(delete_docs=True)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
def test_redisearch_update_prints_follow_up(document_class, capsys):
    slim = document_class("ProdSlim", Product, ["name"])
    manager = IndexManager(slim)
    try:
        call_command("redisearch", "update")
        assert "Index created" in capsys.readouterr().out
        call_command("redisearch", "update")
        assert "up to date" in capsys.readouterr().out

        wide = document_class("ProdWide", Product, ["name", "price"])
        wide._meta.index_alias = slim._meta.index_alias
        wide._meta.key_prefix = slim._meta.key_prefix
        with pytest.raises(SystemExit):
            call_command("redisearch", "update")
        assert "FT.ALTER" in capsys.readouterr().out
    finally:
        manager.drop(delete_docs=True)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
def test_redisearch_populate_rebuild_and_drop(document_class, capsys):
    category = Category.objects.create(name="LiveCmd")
    doc = document_class("CategoryDocument", Category, ["name"])
    manager = IndexManager(doc)
    try:
        call_command("redisearch", "populate")
        assert "populated" in capsys.readouterr().out
        assert doc.objects.get(pk=category.pk).name == "LiveCmd"

        call_command("redisearch", "rebuild")
        assert "rebuilt" in capsys.readouterr().out
        assert doc.objects.get(pk=category.pk).name == "LiveCmd"

        call_command("redisearch", "info")
        assert "CategoryDocument" in capsys.readouterr().out

        call_command("redisearch", "check")
        assert "ok" in capsys.readouterr().out

        call_command("redisearch", "drop", "--dd")
        assert "dropped" in capsys.readouterr().out
        assert manager.exists() is False
    finally:
        if manager.exists():
            manager.drop(delete_docs=True)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
def test_redisearch_info_missing_and_check_drift(document_class, capsys):
    doc = document_class("CategoryDocument", Category, ["name"])
    with pytest.raises(SystemExit):
        call_command("redisearch", "info")
    assert "missing" in capsys.readouterr().out

    manager = IndexManager(doc)
    second = None
    try:
        manager.create()
        second = document_class("CategoryDocumentTwo", Category, ["name"])
        IndexManager(second).create()
        call_command("redisearch", "info")
        out = capsys.readouterr().out
        assert "CategoryDocument" in out
        assert "CategoryDocumentTwo" in out

        doc._meta.language = "french"
        with pytest.raises(SystemExit):
            call_command("redisearch", "check")
        assert "drifted" in capsys.readouterr().out

        ghost = document_class("GhostDocument", Category, ["name"])
        ghost._meta.model = None
        call_command("redisearch", "info", "--models", "tests.Category")
        filtered = capsys.readouterr().out
        assert "CategoryDocument" in filtered
        assert "GhostDocument" not in filtered
    finally:
        manager.drop(delete_docs=True)
        if second is not None:
            IndexManager(second).drop(delete_docs=True)
