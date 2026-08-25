"""Internal ``_v`` stamp used by verify to detect stale documents."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from .exceptions import ConfigurationError
from .types import DocumentPayload, IndexValue

if TYPE_CHECKING:
    from django.db import models

    from .documents import Document

STAMP = "_v"


def check_version_field(document_cls: type[Document]) -> None:
    if STAMP in document_cls._meta.fields:
        raise ConfigurationError(
            f"{document_cls.__name__} declares a field named {STAMP!r}, "
            "which is reserved for the index version stamp. Rename the field."
        )


def default_version(document_cls: type[Document], payload: DocumentPayload) -> str:
    from .schema import build_schema

    body = {key: value for key, value in payload.items() if key != STAMP}
    blob = json.dumps(
        {"fp": build_schema(document_cls).fingerprint(), "doc": body},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def stamp_payload(
    document_cls: type[Document],
    instance: models.Model,
    payload: DocumentPayload,
) -> DocumentPayload:
    version = document_cls.get_index_version(instance, payload)
    if version is None or version == "":
        return payload
    stamped = dict(payload)
    stamped[STAMP] = version
    return stamped


def public_payload(payload: DocumentPayload) -> DocumentPayload:
    """Copy *payload* without the internal stamp."""
    if STAMP not in payload:
        return payload
    return {key: value for key, value in payload.items() if key != STAMP}


def payload_version(payload: DocumentPayload | None) -> str | None:
    if not payload:
        return None
    value: IndexValue | None = payload.get(STAMP)
    if value is None:
        return None
    return str(value)
