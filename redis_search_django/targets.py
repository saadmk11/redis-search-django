"""Live read/write key prefixes for a Document.

``Index.prefix`` is the **logical** family prefix (used in the schema
fingerprint and as generation 1). Blue/green reindex writes a new generation
as a *sibling* (``{logical.rstrip(':')}.g{n}:``) so ``FT.DROPINDEX DD`` on the
old prefix cannot delete the new keys.

During backfill, Redis meta lists every prefix that live upserts must write.
This module caches that list briefly so workers pick up dual-write without a
GET on every ``save()``.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, NamedTuple

from .client import get_redis_connection
from .conf import setting_str
from .types import IndexMeta

if TYPE_CHECKING:
    from .documents import Document

CACHE_TTL = 1.0
_UNSET_FLOAT = -1.0
_cache_lock = threading.Lock()
_cache_epoch = 0


class WriteTargets(NamedTuple):
    read_prefix: str
    write_prefixes: tuple[str, ...]
    generation: int
    physical_name: str
    reindex: dict[str, str] | None


_cache: dict[str, tuple[float, WriteTargets]] = {}


def meta_key_for(document_cls: type[Document]) -> str:
    return f"{setting_str('PREFIX')}:meta:{document_cls._meta.index_alias}"


def generation_prefix(base: str, generation: int) -> str:
    """Physical key prefix for *generation*.

    Generation 1 is the logical prefix (backward compatible). Later
    generations are siblings, not children, of that prefix.
    """
    if generation <= 1:
        return base if base.endswith(":") else f"{base}:"
    core = base[:-1] if base.endswith(":") else base
    return f"{core}.g{generation}:"


def physical_name_for(alias: str, fingerprint: str, generation: int) -> str:
    short = fingerprint.rsplit(":", 1)[-1][:8]
    if generation <= 1:
        return f"{alias}:{short}"
    return f"{alias}:g{generation}:{short}"


def invalidate_targets(document_cls: type[Document] | None = None) -> None:
    """Drop cached prefixes. Called after meta writes in this process."""
    global _cache_epoch
    with _cache_lock:
        _cache_epoch += 1
        if document_cls is None:
            _cache.clear()
            return
        _cache.pop(document_cls._meta.index_alias, None)


def load_targets(document_cls: type[Document], *, fresh: bool = False) -> WriteTargets:
    """Return the live read prefix and write prefix list."""
    logical = document_cls._meta.key_prefix
    alias = document_cls._meta.index_alias
    with _cache_lock:
        epoch = _cache_epoch
        cached = _cache.get(alias)
        if not fresh and cached is not None:
            expires, targets = cached
            if expires < 0 or expires > time.monotonic():
                return targets
    try:
        raw = get_redis_connection().get(meta_key_for(document_cls))
        meta = _decode_meta(raw)
    except Exception:
        with _cache_lock:
            cached = _cache.get(alias)
            if cached is not None:
                return cached[1]
        return WriteTargets(
            read_prefix=logical,
            write_prefixes=(logical,),
            generation=1,
            physical_name="",
            reindex=None,
        )
    targets = _targets_from_meta(meta, logical)
    expires = _UNSET_FLOAT if CACHE_TTL <= 0 else time.monotonic() + CACHE_TTL
    with _cache_lock:
        if _cache_epoch == epoch:
            _cache[alias] = (expires, targets)
    return targets


def read_prefix(document_cls: type[Document]) -> str:
    return load_targets(document_cls).read_prefix


def write_prefixes(document_cls: type[Document]) -> tuple[str, ...]:
    prefixes = load_targets(document_cls).write_prefixes
    return prefixes or (document_cls._meta.key_prefix,)


def _targets_from_meta(meta: Mapping[str, object], logical: str) -> WriteTargets:
    physical = meta.get("physical_prefix")
    if not isinstance(physical, str) or not physical:
        stored = meta.get("prefix")
        physical = stored if isinstance(stored, str) and stored else logical
    writes_raw = meta.get("write_prefixes")
    writes: list[str] = []
    if isinstance(writes_raw, list):
        writes = [item for item in writes_raw if isinstance(item, str) and item]
    if not writes:
        writes = [physical]
    generation_raw = meta.get("generation")
    generation = 1
    if isinstance(generation_raw, int) and not isinstance(generation_raw, bool):
        generation = generation_raw
    elif isinstance(generation_raw, str) and generation_raw.isdigit():
        generation = int(generation_raw)
    physical_name = meta.get("physical_name")
    name = physical_name if isinstance(physical_name, str) else ""
    session = _reindex_session(meta.get("reindex"))
    return WriteTargets(
        read_prefix=physical,
        write_prefixes=tuple(writes),
        generation=generation,
        physical_name=name,
        reindex=session,
    )


def _decode_meta(raw: object) -> IndexMeta:
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str):
        return {}
    loaded: IndexMeta = json.loads(raw)
    return loaded


def _reindex_session(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    session: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            session[key] = value
    return session or None
