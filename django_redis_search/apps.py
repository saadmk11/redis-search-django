from django.apps import AppConfig


class DjangoRedisSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_redis_search"

    def ready(self) -> None:
        import django_redis_search.signals  # noqa: F401
