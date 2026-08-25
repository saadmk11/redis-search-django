from __future__ import annotations

from enum import Enum


class Lookup(str, Enum):
    """Supported Django-style lookup suffixes."""

    SEARCH = "search"
    EXACT = "exact"
    IN = "in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    RANGE = "range"
    ISNULL = "isnull"
    STARTSWITH = "startswith"
    GEO_DISTANCE = "geo_distance"


class QConnector(str, Enum):
    AND = "AND"
    OR = "OR"


class Storage(str, Enum):
    JSON = "json"
    HASH = "hash"


class IndexAction(str, Enum):
    UPSERT = "upsert"
    DELETE = "delete"
    REINDEX_RELATED = "reindex_related"


class MigrateOutcome(str, Enum):
    NO_OP = "no-op"
    WAITING = "waiting"
    CREATED = "created"
    ALTER = "alter"
    ALIAS_SWAP = "alias-swap"
    REBUILD = "rebuild"
    REINDEX = "reindex"


class CommandAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    POPULATE = "populate"
    REBUILD = "rebuild"
    REINDEX = "reindex"
    DROP = "drop"
    INFO = "info"
    CHECK = "check"
    VERIFY = "verify"


class SignalErrorMode(str, Enum):
    RAISE = "raise"
    LOG = "log"


class ReducerKind(str, Enum):
    COUNT = "count"
    AVG = "avg"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    TOLIST = "tolist"


class M2MAction(str, Enum):
    POST_ADD = "post_add"
    POST_REMOVE = "post_remove"
    POST_CLEAR = "post_clear"


NUMERIC_LOOKUPS = frozenset(
    {Lookup.GT, Lookup.GTE, Lookup.LT, Lookup.LTE, Lookup.RANGE}
)
NUMERIC_OPS = {
    Lookup.GT: ">",
    Lookup.GTE: ">=",
    Lookup.LT: "<",
    Lookup.LTE: "<=",
}
M2M_REINDEX_ACTIONS = frozenset(
    {M2MAction.POST_ADD, M2MAction.POST_REMOVE, M2MAction.POST_CLEAR}
)
