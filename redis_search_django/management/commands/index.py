import argparse
from typing import Any

from django.core.management import BaseCommand
from redis_om import Migrator, get_redis_connection

from redis_search_django.registry import document_registry


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

        self.stdout.write(self.style.SUCCESS("Successfully migrated indices"))

        if not only_migrate:
            document_registry.index_documents(models)
            self.stdout.write(self.style.SUCCESS("Successfully indexed documents"))
