from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator, Generator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from redis.commands.search.field import Field as RedisField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.exceptions import ResponseError

from .client import get_async_redis_connection, get_redis_connection
from .conf import setting_str
from .documents import Document
from .enums import MigrateOutcome, Storage
from .exceptions import ReindexInProgress, SchemaDrift
from .redis import AsyncRedis, Redis
from .schema import IndexSchema, build_schema, redis_fields
from .targets import invalidate_targets
from .types import IndexMeta, RedisInfo, as_int, as_str_list

SETTLE_SECONDS = 1.0
INDEXING_TIMEOUT = 300.0
REINDEX_LOCK_TTL = 6 * 60 * 60

_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_RENEW_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""


class IndexManager:
    """Create, migrate, and inspect RediSearch indexes for a Document."""

    def __init__(self, document_cls: type[Document]) -> None:
        self.document_cls = document_cls
        self.schema: IndexSchema = build_schema(document_cls)
        self._lock_token: str | None = None

    def _client(self) -> Redis:
        return get_redis_connection()

    def _aclient(self) -> AsyncRedis:
        return get_async_redis_connection()

    def meta_key(self) -> str:
        return f"{setting_str('PREFIX')}:meta:{self.schema.alias}"

    def load_meta(self) -> IndexMeta:
        raw = self._client().get(self.meta_key())
        return _parse_meta(raw)

    async def aload_meta(self) -> IndexMeta:
        raw = await self._aclient().get(self.meta_key())
        return _parse_meta(raw)

    def save_meta(self, meta: IndexMeta) -> None:
        self._client().set(self.meta_key(), json.dumps(meta))
        invalidate_targets(self.document_cls)

    async def asave_meta(self, meta: IndexMeta) -> None:
        await self._aclient().set(self.meta_key(), json.dumps(meta))
        invalidate_targets(self.document_cls)

    def exists(self) -> bool:
        try:
            self._client().ft(self.schema.alias).info()
            return True
        except ResponseError:
            return False

    async def aexists(self) -> bool:
        try:
            await self._aclient().ft(self.schema.alias).info()
            return True
        except ResponseError:
            return False

    def info(self) -> RedisInfo:
        info: RedisInfo = self._client().ft(self.schema.alias).info()
        return info

    async def ainfo(self) -> RedisInfo:
        info: RedisInfo = await self._aclient().ft(self.schema.alias).info()
        return info

    def create(self) -> str:
        physical = self.schema.physical_name
        self._create_physical(physical)
        try:
            self._client().ft(self.schema.alias).info()
            self._client().ft(physical).aliasupdate(self.schema.alias)
        except ResponseError:
            self._client().ft(physical).aliasadd(self.schema.alias)
        self.save_meta(self._create_meta(physical))
        return physical

    async def acreate(self) -> str:
        physical = self.schema.physical_name
        await self._acreate_physical(physical)
        try:
            await self._aclient().ft(self.schema.alias).info()
            await self._aclient().ft(physical).aliasupdate(self.schema.alias)
        except ResponseError:
            await self._aclient().ft(physical).aliasadd(self.schema.alias)
        await self.asave_meta(self._create_meta(physical))
        return physical

    def _create_meta(
        self,
        physical: str,
        *,
        prefix: str | None = None,
        generation: int = 1,
    ) -> IndexMeta:
        used = prefix or self.schema.prefix
        fingerprint = self.schema.fingerprint()
        return {
            "fingerprint": fingerprint,
            "physical_name": physical,
            "populate_required": False,
            "populated_fp": fingerprint,
            "field_names": [field.alias for field in self.schema.fields],
            "prefix": self.schema.prefix,
            "physical_prefix": used,
            "write_prefixes": [used],
            "generation": generation,
            "storage": self.schema.storage.value,
        }

    def _create_kwargs(
        self,
        *,
        prefix: str | None = None,
    ) -> dict[str, IndexDefinition | list[str]]:
        index_type = (
            IndexType.JSON if self.schema.storage is Storage.JSON else IndexType.HASH
        )
        definition = IndexDefinition(
            prefix=[prefix or self.schema.prefix],
            index_type=index_type,
            language=self.schema.language,
            score=self.schema.score,
        )
        kwargs: dict[str, IndexDefinition | list[str]] = {"definition": definition}
        if self.schema.stopwords is not None:
            kwargs["stopwords"] = list(self.schema.stopwords)
        return kwargs

    def _create_physical(
        self,
        physical: str,
        *,
        prefix: str | None = None,
        skip_initial_scan: bool = False,
    ) -> None:
        del skip_initial_scan
        kwargs = self._create_kwargs(prefix=prefix)
        definition = kwargs["definition"]
        assert isinstance(definition, IndexDefinition)
        stopwords = kwargs.get("stopwords")
        self._client().ft(physical).create_index(
            redis_fields(self.document_cls),
            definition=definition,
            stopwords=stopwords if isinstance(stopwords, list) else None,
        )

    async def _acreate_physical(
        self,
        physical: str,
        *,
        prefix: str | None = None,
        skip_initial_scan: bool = False,
    ) -> None:
        del skip_initial_scan
        kwargs = self._create_kwargs(prefix=prefix)
        definition = kwargs["definition"]
        assert isinstance(definition, IndexDefinition)
        stopwords = kwargs.get("stopwords")
        await (
            self._aclient()
            .ft(physical)
            .create_index(
                redis_fields(self.document_cls),
                definition=definition,
                stopwords=stopwords if isinstance(stopwords, list) else None,
            )
        )

    def drop(self, *, delete_docs: bool = False) -> None:
        try:
            self._client().ft(self.schema.alias).dropindex(delete_documents=delete_docs)
        except ResponseError:
            pass
        self._client().delete(self.meta_key())
        invalidate_targets(self.document_cls)

    async def adrop(self, *, delete_docs: bool = False) -> None:
        try:
            await (
                self._aclient()
                .ft(self.schema.alias)
                .dropindex(delete_documents=delete_docs)
            )
        except ResponseError:
            pass
        await self._aclient().delete(self.meta_key())
        invalidate_targets(self.document_cls)

    def drop_physical(self, name: str, *, delete_docs: bool = False) -> None:
        """Drop a physical index by name. Does not remove the stable alias."""
        try:
            self._client().ft(name).dropindex(delete_documents=delete_docs)
        except ResponseError:
            pass

    async def adrop_physical(self, name: str, *, delete_docs: bool = False) -> None:
        try:
            await self._aclient().ft(name).dropindex(delete_documents=delete_docs)
        except ResponseError:
            pass

    def serving_prefix(self, meta: IndexMeta | None = None) -> str:
        data = meta if meta is not None else self.load_meta()
        physical = data.get("physical_prefix")
        if isinstance(physical, str) and physical:
            return physical
        stored = data.get("prefix")
        if isinstance(stored, str) and stored:
            return stored
        return self.schema.prefix

    def current_generation(self, meta: IndexMeta | None = None) -> int:
        data = meta if meta is not None else self.load_meta()
        return max(1, as_int(data.get("generation"), 1))

    def reindex_lock_key(self) -> str:
        return f"{setting_str('PREFIX')}:lock:{self.schema.alias}"

    def acquire_reindex_lock(self) -> str:
        token = uuid.uuid4().hex
        acquired = self._client().set(
            self.reindex_lock_key(), token, nx=True, ex=REINDEX_LOCK_TTL
        )
        if not acquired:
            raise ReindexInProgress(
                f"{self.document_cls.__name__}: another reindex, rebuild, "
                "or drop is in progress"
            )
        self._lock_token = token
        return token

    async def aacquire_reindex_lock(self) -> str:
        token = uuid.uuid4().hex
        acquired = await self._aclient().set(
            self.reindex_lock_key(), token, nx=True, ex=REINDEX_LOCK_TTL
        )
        if not acquired:
            raise ReindexInProgress(
                f"{self.document_cls.__name__}: another reindex, rebuild, "
                "or drop is in progress"
            )
        self._lock_token = token
        return token

    def release_reindex_lock(self, token: str) -> None:
        self._client().eval(_RELEASE_LOCK, 1, self.reindex_lock_key(), token)
        if self._lock_token == token:
            self._lock_token = None

    async def arelease_reindex_lock(self, token: str) -> None:
        client: Any = self._aclient()
        await client.eval(_RELEASE_LOCK, 1, self.reindex_lock_key(), token)
        if self._lock_token == token:
            self._lock_token = None

    def force_release_reindex_lock(self) -> None:
        self._client().delete(self.reindex_lock_key())
        self._lock_token = None

    async def aforce_release_reindex_lock(self) -> None:
        await self._aclient().delete(self.reindex_lock_key())
        self._lock_token = None

    def renew_reindex_lock(self, token: str) -> None:
        kept = self._client().eval(
            _RENEW_LOCK, 1, self.reindex_lock_key(), token, str(REINDEX_LOCK_TTL)
        )
        if not kept:
            self._lock_token = None
            raise ReindexInProgress(
                f"{self.document_cls.__name__}: lost the reindex lock"
            )

    async def arenew_reindex_lock(self, token: str) -> None:
        client: Any = self._aclient()
        kept = await client.eval(
            _RENEW_LOCK, 1, self.reindex_lock_key(), token, str(REINDEX_LOCK_TTL)
        )
        if not kept:
            self._lock_token = None
            raise ReindexInProgress(
                f"{self.document_cls.__name__}: lost the reindex lock"
            )

    def heartbeat(self) -> None:
        """Refresh the lock this manager holds. No-op if it holds none."""
        token = self._lock_token
        if token is not None:
            self.renew_reindex_lock(token)

    async def aheartbeat(self) -> None:
        token = self._lock_token
        if token is not None:
            await self.arenew_reindex_lock(token)

    @contextmanager
    def holding_reindex_lock(self) -> Generator[str, None, None]:
        token = self.acquire_reindex_lock()
        try:
            yield token
        finally:
            self.release_reindex_lock(token)

    @asynccontextmanager
    async def aholding_reindex_lock(self) -> AsyncGenerator[str, None]:
        token = await self.aacquire_reindex_lock()
        try:
            yield token
        finally:
            await self.arelease_reindex_lock(token)

    def has_reindex_session(self, target_physical: str) -> bool:
        session = self.load_meta().get("reindex")
        return isinstance(session, dict) and (
            session.get("target_physical") == target_physical
        )

    async def ahas_reindex_session(self, target_physical: str) -> bool:
        session = (await self.aload_meta()).get("reindex")
        return isinstance(session, dict) and (
            session.get("target_physical") == target_physical
        )

    def begin_reindex(
        self,
        *,
        generation: int,
        target_physical: str,
        target_prefix: str,
    ) -> IndexMeta:
        """Turn dual-write on and record the green index in meta."""
        meta = self.load_meta()
        source_prefix = self.serving_prefix(meta)
        source_physical = meta.get("physical_name")
        if not isinstance(source_physical, str):
            source_physical = self.schema.physical_name
        meta["write_prefixes"] = [source_prefix, target_prefix]
        meta["reindex"] = {
            "state": "backfill",
            "target_physical": target_physical,
            "target_prefix": target_prefix,
            "source_physical": source_physical,
            "source_prefix": source_prefix,
        }
        self.save_meta(meta)
        return meta

    async def abegin_reindex(
        self,
        *,
        generation: int,
        target_physical: str,
        target_prefix: str,
    ) -> IndexMeta:
        meta = await self.aload_meta()
        source_prefix = self.serving_prefix(meta)
        source_physical = meta.get("physical_name")
        if not isinstance(source_physical, str):
            source_physical = self.schema.physical_name
        meta["write_prefixes"] = [source_prefix, target_prefix]
        meta["reindex"] = {
            "state": "backfill",
            "target_physical": target_physical,
            "target_prefix": target_prefix,
            "source_physical": source_physical,
            "source_prefix": source_prefix,
        }
        await self.asave_meta(meta)
        return meta

    def promote_reindex(
        self,
        *,
        generation: int,
        target_physical: str,
        target_prefix: str,
    ) -> None:
        """Point the stable alias at the green index and stop dual-write."""
        self._wait_until_ready(target_physical)
        self._client().ft(target_physical).aliasupdate(self.schema.alias)
        meta = self.load_meta()
        fingerprint = self.schema.fingerprint()
        meta.update(
            {
                "fingerprint": fingerprint,
                "physical_name": target_physical,
                "physical_prefix": target_prefix,
                "write_prefixes": [target_prefix],
                "generation": generation,
                "populate_required": False,
                "populated_fp": fingerprint,
                "field_names": [field.alias for field in self.schema.fields],
                "prefix": self.schema.prefix,
                "storage": self.schema.storage.value,
                "reindex": None,
            }
        )
        self.save_meta(meta)

    async def apromote_reindex(
        self,
        *,
        generation: int,
        target_physical: str,
        target_prefix: str,
    ) -> None:
        await self._await_until_ready(target_physical)
        await self._aclient().ft(target_physical).aliasupdate(self.schema.alias)
        meta = await self.aload_meta()
        fingerprint = self.schema.fingerprint()
        meta.update(
            {
                "fingerprint": fingerprint,
                "physical_name": target_physical,
                "physical_prefix": target_prefix,
                "write_prefixes": [target_prefix],
                "generation": generation,
                "populate_required": False,
                "populated_fp": fingerprint,
                "field_names": [field.alias for field in self.schema.fields],
                "prefix": self.schema.prefix,
                "storage": self.schema.storage.value,
                "reindex": None,
            }
        )
        await self.asave_meta(meta)

    def abort_reindex(self) -> str | None:
        """Drop the unfinished green index and restore single-prefix writes."""
        meta = self.load_meta()
        target, target_physical = _session_target(meta.get("reindex"))
        if target_physical and not self._alias_serves(target_physical, target):
            self.drop_physical(target_physical, delete_docs=True)
        source = self.serving_prefix(meta)
        meta["write_prefixes"] = [source]
        meta["reindex"] = None
        self.save_meta(meta)
        self.force_release_reindex_lock()
        return target

    async def aabort_reindex(self) -> str | None:
        meta = await self.aload_meta()
        target, target_physical = _session_target(meta.get("reindex"))
        if target_physical and not await self._aalias_serves(target_physical, target):
            await self.adrop_physical(target_physical, delete_docs=True)
        source = self.serving_prefix(meta)
        meta["write_prefixes"] = [source]
        meta["reindex"] = None
        await self.asave_meta(meta)
        await self.aforce_release_reindex_lock()
        return target

    def _alias_serves(self, physical: str, prefix: str | None) -> bool:
        try:
            info: Any = self._client().ft(self.schema.alias).info()
        except ResponseError:
            return False
        return _info_serves(info, physical, prefix)

    async def _aalias_serves(self, physical: str, prefix: str | None) -> bool:
        try:
            info: Any = await self._aclient().ft(self.schema.alias).info()
        except ResponseError:
            return False
        return _info_serves(info, physical, prefix)

    def settle(self) -> None:
        """Wait so other processes refresh their prefix cache."""
        if SETTLE_SECONDS > 0:
            time.sleep(SETTLE_SECONDS)

    async def asettle(self) -> None:
        if SETTLE_SECONDS > 0:
            await asyncio.sleep(SETTLE_SECONDS)

    def drift(self) -> bool:
        return _is_drifted(self.load_meta(), self.schema.fingerprint())

    async def adrift(self) -> bool:
        return _is_drifted(await self.aload_meta(), self.schema.fingerprint())

    def check(self) -> int:
        return _check_meta(self.load_meta(), self.schema.fingerprint())

    async def acheck(self) -> int:
        return _check_meta(await self.aload_meta(), self.schema.fingerprint())

    def migrate(self) -> MigrateOutcome:
        """Apply schema changes.

        Returns no-op, waiting, created, alter, reindex, or rebuild.
        """
        local_fp = self.schema.fingerprint()
        meta = self.load_meta()
        if not self.exists():
            self.create()
            return MigrateOutcome.CREATED

        if not _is_drifted(meta, local_fp):
            if meta.get("populate_required") and meta.get("populated_fp") != local_fp:
                return MigrateOutcome.WAITING
            return MigrateOutcome.NO_OP

        old_fields = set(as_str_list(meta.get("field_names") or []))
        new_fields = {field.alias for field in self.schema.fields}
        additive_only = _is_additive_only(old_fields, new_fields)
        storage_or_prefix_change = self._storage_or_prefix_changed(meta)

        if storage_or_prefix_change:
            return MigrateOutcome.REINDEX

        if additive_only:
            try:
                added_redis = self._added_redis_fields(old_fields)
                if added_redis:
                    self._client().ft(self.schema.alias).alter_schema_add(added_redis)
            except ResponseError:
                return MigrateOutcome.REINDEX
            meta.update(
                {
                    "fingerprint": local_fp,
                    "populate_required": True,
                    "field_names": list(new_fields),
                    "prefix": self.schema.prefix,
                    "storage": self.schema.storage.value,
                }
            )
            self.save_meta(meta)
            return MigrateOutcome.ALTER

        return MigrateOutcome.REINDEX

    async def amigrate(self) -> MigrateOutcome:
        """Apply schema changes using the asyncio Redis client."""
        local_fp = self.schema.fingerprint()
        meta = await self.aload_meta()
        if not await self.aexists():
            await self.acreate()
            return MigrateOutcome.CREATED

        if not _is_drifted(meta, local_fp):
            if meta.get("populate_required") and meta.get("populated_fp") != local_fp:
                return MigrateOutcome.WAITING
            return MigrateOutcome.NO_OP

        old_fields = set(as_str_list(meta.get("field_names") or []))
        new_fields = {field.alias for field in self.schema.fields}
        additive_only = _is_additive_only(old_fields, new_fields)
        storage_or_prefix_change = self._storage_or_prefix_changed(meta)

        if storage_or_prefix_change:
            return MigrateOutcome.REINDEX

        if additive_only:
            try:
                added_redis = self._added_redis_fields(old_fields)
                if added_redis:
                    await (
                        self._aclient()
                        .ft(self.schema.alias)
                        .alter_schema_add(added_redis)
                    )
            except ResponseError:
                return MigrateOutcome.REINDEX
            meta.update(
                {
                    "fingerprint": local_fp,
                    "populate_required": True,
                    "field_names": list(new_fields),
                    "prefix": self.schema.prefix,
                    "storage": self.schema.storage.value,
                }
            )
            await self.asave_meta(meta)
            return MigrateOutcome.ALTER

        return MigrateOutcome.REINDEX

    def _storage_or_prefix_changed(self, meta: IndexMeta) -> bool:
        return meta.get("prefix") not in {
            None,
            self.schema.prefix,
        } or meta.get("storage") not in {None, self.schema.storage.value}

    def _added_redis_fields(self, old_fields: set[str]) -> list[RedisField]:
        added_aliases = {
            field.alias for field in self.schema.fields if field.alias not in old_fields
        }
        return [
            field
            for field in redis_fields(self.document_cls)
            if getattr(field, "as_name", None) in added_aliases
            or (
                not getattr(field, "as_name", None)
                and str(getattr(field, "name", "")).rsplit(" AS ", 1)[-1]
                in added_aliases
            )
        ]

    def _wait_until_ready(self, physical: str, timeout: float | None = None) -> None:
        limit = INDEXING_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            info = self._client().ft(physical).info()
            indexing = info.get("indexing", 0)
            if indexing in {0, "0", None}:
                return
            time.sleep(0.2)
        raise SchemaDrift(f"Timed out waiting for {physical} to finish indexing.")

    async def _await_until_ready(
        self, physical: str, timeout: float | None = None
    ) -> None:
        limit = INDEXING_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            info = await self._aclient().ft(physical).info()
            indexing = info.get("indexing", 0)
            if indexing in {0, "0", None}:
                return
            await asyncio.sleep(0.2)
        raise SchemaDrift(f"Timed out waiting for {physical} to finish indexing.")

    def mark_populated(self) -> None:
        meta = self.load_meta()
        self.save_meta(self._populated_meta(meta))

    async def amark_populated(self) -> None:
        meta = await self.aload_meta()
        await self.asave_meta(self._populated_meta(meta))

    def _populated_meta(self, meta: IndexMeta) -> IndexMeta:
        fingerprint = self.schema.fingerprint()
        meta["populate_required"] = False
        meta["populated_fp"] = fingerprint
        meta["field_names"] = [field.alias for field in self.schema.fields]
        meta["prefix"] = self.schema.prefix
        meta["storage"] = self.schema.storage.value
        meta["fingerprint"] = fingerprint
        return meta


def _session_target(raw: object) -> tuple[str | None, str]:
    if not isinstance(raw, dict):
        return None, ""
    target: str | None = None
    target_physical = ""
    raw_physical = raw.get("target_physical")
    raw_prefix = raw.get("target_prefix")
    if isinstance(raw_physical, str):
        target_physical = raw_physical
    if isinstance(raw_prefix, str):
        target = raw_prefix
    return target, target_physical


def _info_serves(info: Mapping[str, object], physical: str, prefix: str | None) -> bool:
    name = info.get("index_name")
    if isinstance(name, bytes):
        name = name.decode()
    if name == physical:
        return True
    served = _info_prefix(info)
    return bool(prefix) and served == prefix


def _info_prefix(info: Mapping[str, object]) -> str | None:
    definition = info.get("index_definition")
    if isinstance(definition, Mapping):
        prefixes = definition.get("prefixes", definition.get("prefix"))
        return _first_prefix(prefixes)
    if isinstance(definition, (list, tuple)):
        for index, item in enumerate(definition):
            label = item.decode() if isinstance(item, bytes) else item
            if label not in {"prefixes", "prefix"} or index + 1 >= len(definition):
                continue
            return _first_prefix(definition[index + 1])
    return None


def _first_prefix(raw: object) -> str | None:
    if isinstance(raw, (list, tuple)) and raw:
        raw = raw[0]
    if isinstance(raw, bytes):
        return raw.decode()
    if isinstance(raw, str) and raw:
        return raw
    return None


def _parse_meta(raw: object) -> IndexMeta:
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str):
        return {}
    loaded: IndexMeta = json.loads(raw)
    return loaded


def _is_drifted(meta: IndexMeta, fingerprint: str) -> bool:
    if not meta:
        return True
    return meta.get("fingerprint") != fingerprint


def _is_additive_only(old_fields: set[str], new_fields: set[str]) -> bool:
    return bool(old_fields) and new_fields > old_fields and old_fields <= new_fields


def _check_meta(meta: IndexMeta, fingerprint: str) -> int:
    if _is_drifted(meta, fingerprint):
        return 1
    if meta.get("populate_required") and meta.get("populated_fp") != fingerprint:
        return 1
    return 0
