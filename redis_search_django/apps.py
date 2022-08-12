from django.apps import AppConfig


class DjangoRedisSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "redis_search_django"

    def ready(self) -> None:
        import redis_search_django.signals  # noqa: F401
