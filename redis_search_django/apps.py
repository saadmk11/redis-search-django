from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class DjangoRedisSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "redis_search_django"

    def ready(self) -> None:
        # Auto Discover Document modules
        # Required for Document classes to be registered
        autodiscover_modules("documents")
        import redis_search_django.signals  # noqa: F401
