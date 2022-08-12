import argparse
from typing import Any

from django.core.management import BaseCommand
from redis_om import Migrator, get_redis_connection

from django_redis_search.registry import document_registry


class Command(BaseCommand):
    help = "Index Documents to Redis Search"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--models",
            type=str,
            nargs="*",
            help="Django Models to index to Redis. e.g. 'app_name.ModelName'",
        )
        parser.add_argument(
            "--only-migrate",
            action="store_true",
            dest="only_migrate",
            help="Only update the indices schema.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        models = options["models"]
        only_migrate = options["only_migrate"]

        get_redis_connection()
        Migrator().run()

        if only_migrate:
            return

        for django_model, document_class in document_registry.django_model_map.items():
            if models:
                if django_model._meta.label in models:
                    document_class.index_all()
            else:
                document_class.index_all()
