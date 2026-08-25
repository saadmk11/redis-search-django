"""Optional per-view overlay for Redis Query Engine traffic.

Add ``redis_search_django.debug`` to ``INSTALLED_APPS``, then opt in on
the views you care about with :class:`SearchDebugMixin` or
:func:`search_debug`.
"""

from .mixins import SearchDebugMixin, search_debug

__all__ = ["SearchDebugMixin", "search_debug"]
