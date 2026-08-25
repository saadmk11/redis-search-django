from __future__ import annotations

from unittest import mock

import pytest
from django.apps import apps


def _restore_processor(app, original) -> None:
    current = app.signal_processor
    if current is not None and hasattr(current, "teardown"):
        current.teardown()
    app.signal_processor = original
    if original is not None and hasattr(original, "setup"):
        from redis_search_django.registry import document_registry

        original.setup(document_registry)


@pytest.mark.django_db
def test_ready_tears_down_previous_processor():
    app = apps.get_app_config("redis_search_django")
    previous = mock.Mock()
    original = app.signal_processor
    app.signal_processor = previous
    try:
        app.ready()
        previous.teardown.assert_called_once()
        assert app.signal_processor is not previous
    finally:
        _restore_processor(app, original)


def test_ready_warns_on_redis_om_url(settings, caplog):
    from django.apps import apps

    app = apps.get_app_config("redis_search_django")
    original = app.signal_processor
    settings.REDIS_OM_URL = "redis://legacy"
    try:
        with caplog.at_level("WARNING"):
            app.ready()
        assert "REDIS_OM_URL" in caplog.text
    finally:
        _restore_processor(app, original)


def test_ready_previous_processor_without_teardown():
    from django.apps import apps

    app = apps.get_app_config("redis_search_django")
    original = app.signal_processor
    previous = object()
    app.signal_processor = previous
    try:
        app.ready()
        assert app.signal_processor is not previous
        assert app.signal_processor is not None
    finally:
        _restore_processor(app, original)


def test_ready_skips_auto_index(settings):
    from django.apps import apps

    settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "AUTO_INDEX": False}
    app = apps.get_app_config("redis_search_django")
    original = app.signal_processor
    try:
        app.ready()
        processor = app.signal_processor
        assert processor is not None
        assert getattr(processor, "_connections", None) == []
    finally:
        settings.REDIS_SEARCH = {**settings.REDIS_SEARCH, "AUTO_INDEX": True}
        _restore_processor(app, original)
