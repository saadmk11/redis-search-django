from __future__ import annotations

from django.core.exceptions import FieldError, ImproperlyConfigured


class RedisSearchError(Exception):
    """Base error for redis-search-django."""


class SchemaDrift(RedisSearchError):
    """Local document schema does not match the live Redis index."""


class ReindexInProgress(RedisSearchError):
    """Another reindex, rebuild, or drop holds the per-index lock."""


class DocumentNotFound(RedisSearchError):
    """A document key was not found in Redis.

    ``Document.DoesNotExist`` subclasses this, like Django's
    ``ObjectDoesNotExist`` / ``Model.DoesNotExist``.
    """


class NotSupportedError(RedisSearchError):
    """A requested feature is not supported in this version."""


class UnsupportedLookup(FieldError):
    """A Django-style lookup is not implemented for this field type."""


class ConfigurationError(ImproperlyConfigured, RedisSearchError):
    """Invalid Document / Index / settings configuration."""


class UnknownIndexAction(RedisSearchError):
    """``apply_index_action`` was called with an unknown action name."""


class MissingQueryParams(FieldError, RedisSearchError):
    """A query string references ``$name`` PARAMS that were not supplied."""

    def __init__(self, missing: list[str], query: str) -> None:
        self.missing = missing
        self.query = query
        names = ", ".join(f"${name}" for name in missing)
        verb = "is" if len(missing) == 1 else "are"
        super().__init__(
            f"Query references {names} but {verb} not in params. "
            "Pass extra(..., params={{...}}) or call aggregate() on the "
            "queryset that compiled this query."
        )
