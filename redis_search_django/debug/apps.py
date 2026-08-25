from __future__ import annotations

from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured


class RedisSearchDebugConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "redis_search_django.debug"
    label = "redis_search_django_debug"
    verbose_name = "Redis Search Debug"

    def ready(self) -> None:
        if not apps.is_installed("redis_search_django"):
            raise ImproperlyConfigured(
                "redis_search_django.debug requires redis_search_django "
                "in INSTALLED_APPS."
            )
        from .conf import load_panels

        load_panels()
