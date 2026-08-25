"""Diff Django primary keys against Redis document keys.

``missing``
    In ``get_queryset()`` and ``should_index``, but no Redis key.
``stale``
    Key exists, but the stored version stamp does not match the current
    serialized payload (missed ``QuerySet.update()``, old schema, …).
``orphaned``
    Redis key whose pk is not in the expected Django set (deleted row,
    ``should_index`` is now false, leftover generation).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db import models

from .client import get_redis_connection
from .conf import setting_int
from .enums import Storage
from .query.instrument import observe_pipeline
from .serializer import Serializer
from .targets import read_prefix
from .types import as_document_payload
from .versioning import STAMP, payload_version, stamp_payload

if TYPE_CHECKING:
    from .documents import Document
    from .redis import Redis

Heartbeat = Callable[[], None]


@dataclass
class VerifyReport:
    document: str
    prefix: str
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    repaired_missing: int = 0
    repaired_stale: int = 0
    repaired_orphaned: int = 0

    @property
    def issues(self) -> int:
        return len(self.missing) + len(self.stale) + len(self.orphaned)

    @property
    def ok(self) -> bool:
        return self.issues == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "document": self.document,
            "prefix": self.prefix,
            "checked": self.checked,
            "missing": list(self.missing),
            "stale": list(self.stale),
            "orphaned": list(self.orphaned),
            "repaired_missing": self.repaired_missing,
            "repaired_stale": self.repaired_stale,
            "repaired_orphaned": self.repaired_orphaned,
            "ok": self.ok,
        }


def verify_documents(
    document_cls: type[Document],
    *,
    prefix: str | None = None,
    repair: bool = False,
    serializer: Serializer | None = None,
    heartbeat: Heartbeat | None = None,
) -> VerifyReport:
    """Compare Django PKs to Redis keys for one Document class."""
    ser = serializer or Serializer()
    used_prefix = prefix or read_prefix(document_cls)
    report = VerifyReport(document=document_cls.__name__, prefix=used_prefix)
    expected = _expected_versions(document_cls, ser, heartbeat=heartbeat)
    found = _redis_versions(document_cls, used_prefix, heartbeat=heartbeat)
    report.checked = len(expected)

    for pk, want in expected.items():
        stored = found.get(pk)
        if pk not in found:
            report.missing.append(pk)
        elif want is not None and stored != want:
            report.stale.append(pk)

    for pk in found:
        if pk not in expected:
            report.orphaned.append(pk)

    if repair:
        _repair(document_cls, report, used_prefix)

    return report


def _expected_versions(
    document_cls: type[Document],
    serializer: Serializer,
    *,
    heartbeat: Heartbeat | None = None,
) -> dict[str, str | None]:
    from .indexer import _iter_records

    chunk = setting_int("CHUNK_SIZE")
    expected: dict[str, str | None] = {}
    seen = 0
    for instance in _iter_records(document_cls.get_queryset(), chunk):
        if not document_cls.should_index(instance):
            continue
        payload = stamp_payload(
            document_cls, instance, serializer.to_document(document_cls, instance)
        )
        expected[str(instance.pk)] = payload_version(payload)
        seen += 1
        if heartbeat is not None and seen % chunk == 0:
            heartbeat()
    return expected


def _redis_versions(
    document_cls: type[Document],
    prefix: str,
    *,
    heartbeat: Heartbeat | None = None,
) -> dict[str, str | None]:
    client = get_redis_connection()
    found: dict[str, str | None] = {}
    chunk = setting_int("CHUNK_SIZE")
    batch: list[str] = []
    for key in _scan_keys(client, prefix):
        batch.append(key)
        if len(batch) >= chunk:
            found.update(_load_versions(client, document_cls, prefix, batch))
            batch = []
            if heartbeat is not None:
                heartbeat()
    if batch:
        found.update(_load_versions(client, document_cls, prefix, batch))
        if heartbeat is not None:
            heartbeat()
    return found


def _scan_keys(client: Redis, prefix: str) -> Iterator[str]:
    pattern = f"{prefix}*"
    for raw in client.scan_iter(match=pattern, count=setting_int("CHUNK_SIZE")):
        key: object = raw
        if isinstance(key, bytes):
            key = key.decode()
        if not isinstance(key, str):
            continue
        # Sibling generations (``.g2:``) must not be treated as children.
        remainder = key[len(prefix) :]
        if ":" in remainder:
            continue
        yield key


def _load_versions(
    client: Redis,
    document_cls: type[Document],
    prefix: str,
    keys: list[str],
) -> dict[str, str | None]:
    pipe = client.pipeline(transaction=False)
    json_storage = document_cls._meta.storage is Storage.JSON
    for key in keys:
        if json_storage:
            pipe.json().get(key, "._v")
        else:
            pipe.hget(key, STAMP)
    rows = pipe.execute(raise_on_error=False)
    found: dict[str, str | None] = {}
    for key, raw in zip(keys, rows, strict=True):
        if isinstance(raw, Exception):
            found[key[len(prefix) :]] = None
            continue
        found[key[len(prefix) :]] = _stamp_from_raw(raw)
    return found


def _stamp_from_raw(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
        if raw is None:
            return None
    if isinstance(raw, dict):
        return payload_version(as_document_payload(raw))
    if isinstance(raw, bytes):
        return raw.decode()
    return str(raw)


def _repair(document_cls: type[Document], report: VerifyReport, prefix: str) -> None:
    from .indexer import Indexer

    indexer = Indexer()
    model = document_cls._meta.model
    assert model is not None
    chunk = setting_int("CHUNK_SIZE")
    rewrite = report.missing + report.stale
    rewritten: set[str] = set()
    missing_set = set(report.missing)
    for start in range(0, len(rewrite), chunk):
        part = rewrite[start : start + chunk]
        pks = _coerce_pks(model, part)
        qs = document_cls.get_queryset().filter(pk__in=pks)
        found = {str(pk) for pk in qs.values_list("pk", flat=True)}
        if found:
            indexer.upsert_queryset(document_cls, qs)
        for pk in part:
            if pk not in found:
                continue
            rewritten.add(pk)
            if pk in missing_set:
                report.repaired_missing += 1
            else:
                report.repaired_stale += 1
    report.missing = [pk for pk in report.missing if pk not in rewritten]
    report.stale = [pk for pk in report.stale if pk not in rewritten]
    if report.orphaned:
        client = get_redis_connection()
        pipe = client.pipeline(transaction=False)
        pending = 0
        for pk in report.orphaned:
            pipe.delete(document_cls.key_for(pk, prefix=prefix))
            pending += 1
            if pending >= chunk:
                with observe_pipeline(document_cls, pending):
                    pipe.execute()
                pipe = client.pipeline(transaction=False)
                pending = 0
        if pending:
            with observe_pipeline(document_cls, pending):
                pipe.execute()
        report.repaired_orphaned = len(report.orphaned)
        report.orphaned = []


def _coerce_pks(model: type[models.Model], values: list[str]) -> list[object]:
    field = model._meta.pk
    assert field is not None
    converted: list[object] = []
    for raw in values:
        try:
            converted.append(field.to_python(raw))
        except (TypeError, ValueError):
            converted.append(raw)
    return converted
