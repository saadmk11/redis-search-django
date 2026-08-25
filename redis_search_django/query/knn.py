"""Compile dialect-2 KNN clauses that wrap a RediSearch pre-filter."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..fields import Vector
from .compiler import QueryParams

_SCORE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
KNN_K_PARAM = "rsd_knn_k"
KNN_VEC_PARAM = "rsd_knn_vec"


@dataclass(frozen=True)
class KnnClause:
    """Resolved KNN clause attached to a ``DocumentQuerySet``."""

    alias: str
    field: Vector
    blob: bytes
    k: int
    ef_runtime: int | None
    score_name: str


def validate_score_name(name: str) -> str:
    if not _SCORE_NAME.fullmatch(name):
        raise ValueError(f"knn() score_name must be a simple identifier, got {name!r}.")
    return name


def wrap_knn_query(
    query: str, knn: KnnClause, params: QueryParams | None
) -> tuple[str, QueryParams]:
    """Wrap a filter expression as ``(filter)=>[KNN ...]``."""
    merged: QueryParams = dict(params or {})
    merged[KNN_K_PARAM] = str(knn.k)
    merged[KNN_VEC_PARAM] = knn.blob
    parts = [f"KNN ${KNN_K_PARAM} @{knn.alias} ${KNN_VEC_PARAM}"]
    if knn.ef_runtime is not None:
        parts.append(f"EF_RUNTIME {knn.ef_runtime}")
    parts.append(f"AS {knn.score_name}")
    clause = "[" + " ".join(parts) + "]"
    if query == "*":
        return f"*=>{clause}", merged
    return f"({query})=>{clause}", merged
