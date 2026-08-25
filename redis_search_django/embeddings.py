"""Pluggable embedding hooks for ``fields.Vector``.

The package does not generate embeddings. Users supply a callable, a dotted
path, or an object with ``embed()`` (and optionally ``embed_query()``).
``Document.embed_{field}`` and ``Document.embedder`` are looked up the same
way as ``prepare_{field}``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, TypeGuard, runtime_checkable

from django.db import models
from django.utils.module_loading import import_string

from .exceptions import ConfigurationError
from .types import FieldInput, is_field_input

if TYPE_CHECKING:
    from .documents import Document
    from .fields import Vector

EmbedFn = Callable[[FieldInput], Sequence[float]]


@runtime_checkable
class Embedder(Protocol):
    """Turn a source value into a dense vector.

    Implement ``embed`` for documents. Optionally implement ``embed_query``
    when search text should be encoded differently from indexed text.
    A plain ``(value) -> Sequence[float]`` callable is also accepted.
    """

    def embed(self, value: FieldInput) -> Sequence[float]: ...


def _as_numeric_list(value: object) -> list[float]:
    """Coerce an array-like value to floats. Rejects text, bytes, and bools."""
    if value is None or isinstance(value, (str, bytes, bytearray, memoryview)):
        raise TypeError
    candidate: object = value
    tolist = getattr(candidate, "tolist", None)
    if callable(tolist) and not isinstance(candidate, (list, tuple)):
        try:
            candidate = tolist()
        except Exception:
            raise TypeError from None
    if isinstance(candidate, (str, bytes, bytearray, memoryview)) or candidate is None:
        raise TypeError
    if not isinstance(candidate, Sequence):
        raise TypeError
    items = list(candidate)
    if not items:
        raise TypeError
    out: list[float] = []
    for item in items:
        out.append(_float_item(item))
    return out


def _float_item(item: object) -> float:
    if isinstance(item, bool):
        raise TypeError
    if isinstance(item, (int, float)):
        return float(item)
    convert = getattr(item, "__float__", None)
    if callable(convert):
        return float(convert())
    raise TypeError


def is_vector(value: object) -> bool:
    """Return True if *value* looks like a dense numeric vector."""
    try:
        _as_numeric_list(value)
    except (TypeError, ValueError):
        return False
    return True


def as_floats(value: object, *, field_name: str, dims: int) -> list[float]:
    """Coerce *value* to ``dims`` floats or raise ``ConfigurationError``."""
    try:
        values = _as_numeric_list(value)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"Vector field {field_name!r} expected a numeric sequence of length "
            f"{dims}, got {type(value).__name__}."
        ) from None
    if len(values) != dims:
        raise ConfigurationError(
            f"Vector field {field_name!r} expected {dims} dimensions, "
            f"got {len(values)}."
        )
    return values


def _is_embedder(value: object) -> TypeGuard[Embedder | EmbedFn]:
    """Return True for callables or objects with ``embed()``."""
    return callable(value) or callable(getattr(value, "embed", None))


def coerce_embedder(embedder: Embedder | EmbedFn | str) -> Embedder | EmbedFn:
    """Resolve a field ``embedder=`` value to a callable or ``Embedder``."""
    if isinstance(embedder, str):
        resolved: object = import_string(embedder)
        if not _is_embedder(resolved):
            raise ConfigurationError(
                f"Embedder {embedder!r} is not callable and has no embed()."
            )
        return resolved
    if _is_embedder(embedder):
        return embedder
    raise ConfigurationError(
        f"Embedder {embedder!r} is not callable and has no embed()."
    )


def resolve_embedder(
    document_cls: type[Document], field: Vector
) -> Embedder | EmbedFn | None:
    """Pick the embedder for *field*: field, ``embed_{name}``, then class default."""
    if field.embedder is not None:
        return coerce_embedder(field.embedder)
    hook = getattr(document_cls, f"embed_{field.name}", None)
    if _is_embedder(hook):
        return hook
    default = getattr(document_cls, "embedder", None)
    if isinstance(default, str) or _is_embedder(default):
        return coerce_embedder(default)
    return None


def call_embed(embedder: Embedder | EmbedFn, value: FieldInput) -> Sequence[float]:
    if isinstance(embedder, type):
        raise ConfigurationError("Pass an Embedder instance, not a class.")
    embed = getattr(embedder, "embed", None)
    if callable(embed):
        embedded: Sequence[float] = embed(value)
        return embedded
    if callable(embedder):
        return embedder(value)
    raise ConfigurationError(
        f"Embedder {embedder!r} is not callable and has no embed()."
    )


def call_embed_query(
    embedder: Embedder | EmbedFn, value: FieldInput
) -> Sequence[float]:
    embed_query = getattr(embedder, "embed_query", None)
    if callable(embed_query):
        embedded: Sequence[float] = embed_query(value)
        return embedded
    return call_embed(embedder, value)


def resolve_source(instance: models.Model, path: str) -> FieldInput:
    """Walk a dotted attribute path. Missing names are configuration errors."""
    current: object = instance
    for part in path.split("."):
        if current is None:
            return None
        if not hasattr(current, part):
            raise ConfigurationError(
                f"Vector source {path!r} does not exist on "
                f"{instance.__class__.__name__}."
            )
        current = getattr(current, part)
    if current is None or is_field_input(current):
        return current
    return str(current)
