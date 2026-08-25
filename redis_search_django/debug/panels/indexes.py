from __future__ import annotations

from typing import Any

from redis_search_django.registry import document_registry

from .base import Panel


class IndexesPanel(Panel):
    title = "Indexes"
    panel_id = "indexes"
    template = "redis_search_django/debug/panels/indexes.html"

    def nav_subtitle(self) -> str:
        count = self.stats.get("count", 0)
        return f"{count}"

    def generate_stats(self, request: object, response: object) -> None:
        rows = [_document_row(cls) for cls in document_registry.documents]
        self.stats = {"count": len(rows), "rows": rows}


def _document_row(document_cls: type[Any]) -> dict[str, Any]:
    meta = document_cls._meta
    model = meta.model
    if model is not None:
        model_label = f"{model._meta.app_label}.{model.__name__}"
        label = document_registry.label_for(document_cls)
    else:
        model_label = ""
        label = document_cls.__name__
    fields = [
        {
            "name": name,
            "type": type(field).__name__,
            "alias": field.as_name(),
            "sortable": bool(getattr(field, "sortable", False)),
        }
        for name, field in meta.fields.items()
    ]
    return {
        "label": label,
        "name": document_cls.__name__,
        "model": model_label,
        "alias": meta.index_alias,
        "prefix": meta.key_prefix,
        "storage": meta.storage.value,
        "dialect": meta.dialect,
        "auto_index": meta.auto_index,
        "fields": fields,
    }
