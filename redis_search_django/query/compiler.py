from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence

from ..documents import Document
from ..enums import NUMERIC_LOOKUPS, NUMERIC_OPS, Lookup, QConnector
from ..exceptions import (
    ConfigurationError,
    MissingQueryParams,
    NotSupportedError,
    UnsupportedLookup,
)
from ..fields import Boolean, Field, Geo, Nested, Numeric, Object, Tag, Text, Vector
from ..schema import flatten_lookup
from ..types import FieldInput, IndexValue, LookupValue, is_field_input
from .lookups import Q, split_lookup

MAX_QUERY_BYTES = 32 * 1024
_PARAM_REF = re.compile(r"\$(?P<name>[A-Za-z][A-Za-z0-9_]*)")

QueryParamValue = str | bytes | int | float
QueryParams = dict[str, QueryParamValue]


def param_names(query: str) -> frozenset[str]:
    """Return PARAMS referenced as ``$name`` in a RediSearch query string."""
    return frozenset(_PARAM_REF.findall(query))


def ensure_query_params(query: str, params: QueryParams | None) -> None:
    """Raise if ``query`` uses ``$name`` placeholders that ``params`` lacks."""
    missing = sorted(param_names(query) - set(params or ()))
    if missing:
        raise MissingQueryParams(missing, query)


def escape_text(value: str) -> str:
    specials = r',.(){}[]"\':;~@%|-'
    out = []
    for char in value:
        if char in specials:
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


def escape_tag(value: str, separator: str = ",") -> str:
    specials = {"{", "}", "\\", separator, " "}
    out = []
    for char in value:
        if char in specials:
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


class CompiledQuery:
    def __init__(self, query: str, params: QueryParams) -> None:
        self.query = query
        self.params = params


class QueryCompiler:
    """Compile ``Q`` trees to a dialect-2 query string and PARAMS dict."""

    def __init__(self, document_cls: type[Document]) -> None:
        self.document_cls = document_cls
        self._param_i = 0

    def compile(self, q: Q | None) -> CompiledQuery:
        self._param_i = 0
        params: QueryParams = {}
        if q is None or not q.children:
            expr = "*"
        else:
            expr = self._compile_q(q, params) or "*"
        if len(expr.encode()) > MAX_QUERY_BYTES:
            raise ValueError("Compiled RediSearch query exceeds 32 KiB.")
        return CompiledQuery(expr, params)

    def _compile_q(self, node: Q, params: QueryParams) -> str:
        parts: list[str] = []
        for child in node.children:
            if isinstance(child, Q):
                inner = self._compile_q(child, params)
                if inner:
                    parts.append(inner)
            else:
                key, value = child
                parts.append(self._compile_lookup(key, value, params))
        if not parts:
            body = ""
        elif node.connector == QConnector.OR:
            body = "(" + " | ".join(parts) + ")"
        else:
            body = " ".join(parts)
            if len(parts) > 1:
                body = f"({body})"
        if node.negated and body:
            return f"-{body}"
        return body

    def _compile_lookup(
        self,
        key: str,
        value: FieldInput | Sequence[FieldInput],
        params: QueryParams,
    ) -> str:
        path, lookup = split_lookup(key)
        if lookup is Lookup.GEO_DISTANCE:
            raise NotSupportedError("__geo_distance is not implemented in 1.0.")
        try:
            alias, field = flatten_lookup(self.document_cls, path)
        except KeyError as exc:
            raise UnsupportedLookup(f"Unknown field path {path!r}.") from exc

        if lookup is Lookup.SEARCH:
            if not isinstance(field, Text):
                raise UnsupportedLookup("__search is only valid on TEXT fields.")
            tokens = [token for token in str(value).split() if token]
            if not tokens:
                return "*"
            bits = []
            for token in tokens:
                name = self._add_param(params, escape_text(token))
                bits.append(f"${name}")
            return f"@{alias}:({' '.join(bits)})"

        if lookup is Lookup.ISNULL:
            return self._compile_isnull(path, alias, field, value)

        if lookup is Lookup.IN:
            if not isinstance(value, Iterable):
                raise TypeError(
                    f"__in lookup expected an iterable, got {type(value).__name__}."
                )
            values = list(value)
            if not values:
                if isinstance(field, Numeric):
                    return f"@{alias}:[+inf +inf]"
                if isinstance(field, Text):
                    return f'@{alias}:("__rsd_in_empty__")'
                return f"@{alias}:{{__rsd_in_empty__}}"
            if isinstance(field, Numeric):
                bits = []
                for item in values:
                    number = self._num(field.to_index_value(item))
                    bits.append(f"@{alias}:[{number} {number}]")
                return "(" + "|".join(bits) + ")"
            if isinstance(field, Text):
                bits = [f'@{alias}:("{escape_text(str(item))}")' for item in values]
                return bits[0] if len(bits) == 1 else "(" + "|".join(bits) + ")"
            tags = []
            for item in values:
                name = self._add_param(params, escape_tag(self._tag_value(field, item)))
                tags.append(f"${name}")
            return f"@{alias}:{{{'|'.join(tags)}}}"

        if lookup in NUMERIC_LOOKUPS:
            if not isinstance(field, Numeric):
                raise UnsupportedLookup(f"{lookup} is only valid on NUMERIC fields.")
            if lookup is Lookup.RANGE:
                if not (
                    isinstance(value, Sequence)
                    and not isinstance(value, (str, bytes))
                    and len(value) == 2
                    and is_field_input(value[0])
                    and is_field_input(value[1])
                ):
                    raise UnsupportedLookup("range lookup expects two values.")
                low, high = value[0], value[1]
                return (
                    f"@{alias}:["
                    f"{self._num(field.to_index_value(low))} "
                    f"{self._num(field.to_index_value(high))}]"
                )
            if not is_field_input(value):
                raise UnsupportedLookup(f"{lookup} expects a single numeric value.")
            number = self._num(field.to_index_value(value))
            return f"@{alias}{NUMERIC_OPS[lookup]}{number}"

        if lookup is Lookup.STARTSWITH:
            token = (
                escape_text(str(value))
                if isinstance(field, Text)
                else escape_tag(str(value))
            )
            name = self._add_param(params, token)
            if isinstance(field, Tag):
                if not getattr(field, "suffix_trie", False):
                    raise ConfigurationError(
                        f"{path} must set suffix_trie=True for __startswith."
                    )
                return f"@{alias}:{{${name}*}}"
            return f"@{alias}:${name}*"

        # exact / default
        if isinstance(field, Numeric):
            if not is_field_input(value):
                raise UnsupportedLookup("exact numeric lookup expects a single value.")
            indexed = field.to_index_value(value)
            return f"@{alias}:[{self._num(indexed)} {self._num(indexed)}]"
        if isinstance(field, (Tag, Boolean)):
            name = self._add_param(
                params,
                escape_tag(self._tag_value(field, value)),
            )
            return f"@{alias}:{{${name}}}"
        if isinstance(field, Text):
            # PARAMS do not substitute inside quotes; embed an escaped phrase.
            return f'@{alias}:("{escape_text(str(value))}")'
        if isinstance(field, (Geo, Vector)):
            raise UnsupportedLookup(f"No exact lookup for {field.redis_type()} fields.")
        raise UnsupportedLookup(f"Cannot compile {key}.")

    def _tag_value(self, field: Field, value: LookupValue) -> str:
        converted = field.to_index_value(value if is_field_input(value) else str(value))
        if isinstance(converted, bool):
            return "true" if converted else "false"
        return str(converted)

    def _num(self, value: IndexValue) -> str:
        if value in {math.inf, float("inf")}:
            return "+inf"
        if value in {-math.inf, float("-inf")}:
            return "-inf"
        return str(value)

    def _compile_isnull(
        self,
        path: str,
        alias: str,
        field: Field,
        value: FieldInput | Sequence[FieldInput],
    ) -> str:
        """Compile ``__isnull`` to ``ismissing(@alias)``.

        Optional ``Object`` fields store INDEXMISSING on ``{alias}_pk``, not on
        child TEXT/TAG fields. ``category__isnull`` and a child lookup such as
        ``category__name__isnull`` (when the child itself is not nullable) both
        become ``ismissing(@category_pk)`` — Django's LEFT JOIN null for a
        missing relation.
        """
        if isinstance(field, Object):
            if field.required:
                raise ConfigurationError(
                    f"{path} is a required Object; __isnull is unavailable. "
                    "Declare Object(..., required=False) so Redis can "
                    f"INDEXMISSING on {alias}_pk."
                )
            expr = f"ismissing(@{alias}_pk)"
            return expr if value else f"-{expr}"
        if isinstance(field, Nested):
            raise ConfigurationError(
                f"{path} is Nested; __isnull is not supported on lists. "
                "Filter a child field instead (e.g. tags__name)."
            )
        if not field.index_missing:
            parent = _optional_object_parent(self.document_cls, path)
            if parent is not None:
                parent_alias, _ = parent
                expr = f"ismissing(@{parent_alias}_pk)"
                return expr if value else f"-{expr}"
            raise ConfigurationError(
                f"{path} does not have index_missing=True; __isnull is unavailable. "
                "Set index_missing=True on that field, or use <object>__isnull on "
                "an Object(..., required=False) (ismissing(@<object>_pk))."
            )
        expr = f"ismissing(@{alias})"
        return expr if value else f"-{expr}"

    def _add_param(self, params: QueryParams, value: str) -> str:
        self._param_i += 1
        name = f"p{self._param_i}"
        params[name] = value
        return name


def _optional_object_parent(
    document_cls: type[Document], path: str
) -> tuple[str, Object] | None:
    parts = [part for part in path.split("__") if part]
    if len(parts) < 2:
        return None
    alias, field = flatten_lookup(document_cls, "__".join(parts[:-1]))
    if isinstance(field, Object) and not field.required:
        return alias, field
    return None
