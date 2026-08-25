from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.utils.html import escape

if TYPE_CHECKING:
    from redis_search_django.debug.toolbar import DebugToolbar


class Panel:
    """One toolbar tab. Register subclasses in ``REDIS_SEARCH_DEBUG['PANELS']``."""

    title = "Panel"
    panel_id = "panel"
    template = "redis_search_django/debug/panels/empty.html"

    def __init__(self, toolbar: DebugToolbar) -> None:
        self.toolbar = toolbar
        self.stats: dict[str, Any] = {}
        self.error: str | None = None

    def enabled(self) -> bool:
        return True

    def nav_subtitle(self) -> str:
        return ""

    def generate_stats(self, request: HttpRequest, response: HttpResponse) -> None:
        return None

    def get_context(self) -> dict[str, Any]:
        return {"panel": self, "stats": self.stats, "toolbar": self.toolbar}

    def content(self) -> str:
        if self.error:
            return f'<p class="rsd-error">{escape(self.error)}</p>'
        try:
            return render_to_string(self.template, self.get_context())
        except Exception as exc:
            return (
                f'<p class="rsd-error">{escape(type(exc).__name__)}: '
                f"{escape(str(exc))}</p>"
            )
