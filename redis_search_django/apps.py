from __future__ import annotations

import logging
import os

from django.apps import AppConfig
from django.conf import settings
from django.utils.module_loading import autodiscover_modules, import_string

from .conf import setting_bool, setting_str
from .types import SignalProcessor

logger = logging.getLogger("redis_search_django")


class DjangoRedisSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "redis_search_django"
    signal_processor: SignalProcessor | None = None

    def ready(self) -> None:
        autodiscover_modules("documents")
        if os.environ.get("REDIS_OM_URL") or getattr(settings, "REDIS_OM_URL", None):
            logger.warning(
                "REDIS_OM_URL is ignored in redis-search-django 1.0; "
                "set REDIS_SEARCH['URL'] instead."
            )
        from .registry import document_registry

        # ready() can run more than once (tests, autoreload). Tear down the
        # previous processor so weak signal refs are not left pointing at a
        # discarded instance while dispatch_uids block reconnect.
        previous = getattr(self, "signal_processor", None)
        if previous is not None:
            teardown = getattr(previous, "teardown", None)
            if callable(teardown):
                teardown()
            self.signal_processor = None

        processor_path = setting_str("SIGNAL_PROCESSOR")
        processor = import_string(processor_path)()
        if setting_bool("AUTO_INDEX"):
            processor.setup(document_registry)
        self.signal_processor = processor
