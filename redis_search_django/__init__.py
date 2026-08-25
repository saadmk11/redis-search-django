from __future__ import annotations

from . import fields
from .actions import aapply_index_action, apply_index_action
from .client import get_async_redis_connection, get_redis_connection
from .documents import Document
from .embeddings import Embedder
from .enums import IndexAction, Lookup, Storage
from .query import Aggregate, Q
from .redis import AggregateRequest, AsyncRedis, Redis, reducers
from .registry import document_registry

__version__ = "1.0.0"

__all__ = [
    "Aggregate",
    "AggregateRequest",
    "AsyncRedis",
    "Document",
    "Embedder",
    "IndexAction",
    "Lookup",
    "Q",
    "Redis",
    "Storage",
    "__version__",
    "aapply_index_action",
    "apply_index_action",
    "document_registry",
    "fields",
    "get_async_redis_connection",
    "get_redis_connection",
    "reducers",
]
