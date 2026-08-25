from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.db import models
from redis.commands.json.path import Path

from .client import (
    get_async_redis_connection,
    get_redis_connection,
    hash_aset,
    json_aset,
    json_set,
)
from .conf import setting_int
from .documents import Document
from .enums import MigrateOutcome, Storage
from .exceptions import ReindexInProgress
from .index import IndexManager
from .query.instrument import observe_pipeline, observe_write
from .redis import Redis, hash_fields
from .serializer import Serializer
from .targets import generation_prefix, physical_name_for, write_prefixes
from .types import DocumentPayload, HashMapping, IndexMeta
from .verify import VerifyReport, verify_documents
from .versioning import stamp_payload

Heartbeat = Callable[[], None]


@dataclass
class ReindexResult:
    document: str
    generation: int
    old_physical: str
    new_physical: str
    old_prefix: str
    new_prefix: str
    count: int
    created: bool = False
    verified: bool = False
    dropped_old: bool = False
    aborted: bool = False
    blue_green: bool = False
    report: VerifyReport | None = None


class Indexer:
    """Bulk and incremental writes of Document payloads to Redis."""

    def __init__(self, serializer: Serializer | None = None) -> None:
        self.serializer = serializer or Serializer()

    def upsert(self, document_cls: type[Document], instance: models.Model) -> None:
        prepared = self._prepare_write(document_cls, instance)
        if prepared is None:
            self.delete(document_cls, instance.pk)
            return
        pk, payload = prepared
        client = get_redis_connection()
        mapping = None
        if document_cls._meta.storage is not Storage.JSON:
            mapping = self.serializer.flatten_hash(document_cls, payload)
        for key in self._keys_for(document_cls, pk):
            self._store(client, document_cls, key, payload, mapping)

    async def aupsert(
        self, document_cls: type[Document], instance: models.Model
    ) -> None:
        prepared = await sync_to_async(self._prepare_write)(document_cls, instance)
        if prepared is None:
            await self.adelete(document_cls, instance.pk)
            return
        pk, payload = prepared
        client = get_async_redis_connection()
        mapping = None
        if document_cls._meta.storage is not Storage.JSON:
            mapping = await sync_to_async(self.serializer.flatten_hash)(
                document_cls, payload
            )
        for key in self._keys_for(document_cls, pk):
            await self._astore(client, document_cls, key, payload, mapping)

    def delete(self, document_cls: type[Document], pk: object) -> None:
        client = get_redis_connection()
        for key in self._keys_for(document_cls, pk):
            self.delete_key(document_cls, key, client=client)

    async def adelete(self, document_cls: type[Document], pk: object) -> None:
        client = get_async_redis_connection()
        for key in self._keys_for(document_cls, pk):
            with observe_write(document_cls, "DEL", key):
                await client.delete(key)

    def delete_key(
        self,
        document_cls: type[Document],
        key: str,
        *,
        client: Redis | None = None,
    ) -> None:
        redis = client or get_redis_connection()
        with observe_write(document_cls, "DEL", key):
            redis.delete(key)

    def _prepare_write(
        self, document_cls: type[Document], instance: models.Model
    ) -> tuple[object, DocumentPayload] | None:
        if not document_cls.should_index(instance):
            return None
        payload = stamp_payload(
            document_cls, instance, self.serializer.to_document(document_cls, instance)
        )
        return instance.pk, payload

    def _keys_for(
        self,
        document_cls: type[Document],
        pk: object,
        prefixes: Sequence[str] | None = None,
    ) -> list[str]:
        used = tuple(prefixes) if prefixes is not None else write_prefixes(document_cls)
        return [f"{prefix}{pk}" for prefix in used]

    def _store(
        self,
        client: Any,
        document_cls: type[Document],
        key: str,
        payload: DocumentPayload,
        mapping: HashMapping | None,
    ) -> None:
        if document_cls._meta.storage is Storage.JSON:
            with observe_write(document_cls, "JSON.SET", key):
                json_set(client, key, payload, path=Path.root_path())
        else:
            assert mapping is not None
            with observe_write(document_cls, "HSET", key):
                client.hset(key, mapping=mapping)

    async def _astore(
        self,
        client: Any,
        document_cls: type[Document],
        key: str,
        payload: DocumentPayload,
        mapping: HashMapping | None,
    ) -> None:
        if document_cls._meta.storage is Storage.JSON:
            with observe_write(document_cls, "JSON.SET", key):
                await json_aset(client, key, payload, path=Path.root_path())
        else:
            assert mapping is not None
            with observe_write(document_cls, "HSET", key):
                await hash_aset(client, key, mapping)

    def upsert_queryset(
        self,
        document_cls: type[Document],
        qs: models.QuerySet[models.Model],
        *,
        prefixes: Sequence[str] | None = None,
        heartbeat: Heartbeat | None = None,
    ) -> int:
        count = 0
        chunk = setting_int("CHUNK_SIZE")
        client = get_redis_connection()
        pipe = client.pipeline(transaction=False)
        pending = 0
        resolved = (
            tuple(prefixes) if prefixes is not None else write_prefixes(document_cls)
        )
        # QuerySet.iterator() ignores prefetch_related (N+1 on Nested fields).
        for instance in _iter_records(qs, chunk):
            prepared = self._prepare_write(document_cls, instance)
            keys = self._keys_for(document_cls, instance.pk, resolved)
            if prepared is None:
                for key in keys:
                    pipe.delete(key)
                pending += len(keys)
            else:
                _pk, payload = prepared
                mapping = None
                if document_cls._meta.storage is not Storage.JSON:
                    mapping = self.serializer.flatten_hash(document_cls, payload)
                for key in keys:
                    if document_cls._meta.storage is Storage.JSON:
                        json_set(pipe, key, payload, path=Path.root_path())
                    else:
                        assert mapping is not None
                        pipe.hset(key, mapping=hash_fields(mapping))
                pending += len(keys)
            count += 1
            if pending >= chunk:
                with observe_pipeline(document_cls, pending):
                    pipe.execute()
                pipe = client.pipeline(transaction=False)
                pending = 0
                if heartbeat is not None:
                    heartbeat()
        if pending:
            with observe_pipeline(document_cls, pending):
                pipe.execute()
            if heartbeat is not None:
                heartbeat()
        return count

    async def aupsert_queryset(
        self,
        document_cls: type[Document],
        qs: models.QuerySet[models.Model],
        *,
        prefixes: Sequence[str] | None = None,
        heartbeat: Heartbeat | None = None,
    ) -> int:
        count = 0
        chunk = setting_int("CHUNK_SIZE")
        client = get_async_redis_connection()
        pipe = client.pipeline(transaction=False)
        pending = 0
        resolved = (
            tuple(prefixes) if prefixes is not None else write_prefixes(document_cls)
        )
        async for instance in _aiter_records(qs, chunk):
            prepared = await sync_to_async(self._prepare_write)(document_cls, instance)
            keys = self._keys_for(document_cls, instance.pk, resolved)
            if prepared is None:
                for key in keys:
                    pipe.delete(key)
                pending += len(keys)
            else:
                _pk, payload = prepared
                if document_cls._meta.storage is Storage.JSON:
                    for key in keys:
                        json_set(pipe, key, payload, path=Path.root_path())
                else:
                    mapping = await sync_to_async(self.serializer.flatten_hash)(
                        document_cls, payload
                    )
                    for key in keys:
                        pipe.hset(key, mapping=hash_fields(mapping))
                pending += len(keys)
            count += 1
            if pending >= chunk:
                with observe_pipeline(document_cls, pending):
                    await pipe.execute()
                pipe = client.pipeline(transaction=False)
                pending = 0
                if heartbeat is not None:
                    heartbeat()
        if pending:
            with observe_pipeline(document_cls, pending):
                await pipe.execute()
            if heartbeat is not None:
                heartbeat()
        return count

    def rebuild(self, document_cls: type[Document]) -> int:
        manager = IndexManager(document_cls)
        with manager.holding_reindex_lock():
            self._reject_open_session(document_cls, manager)
            return self._rebuild_unlocked(document_cls, manager)

    async def arebuild(self, document_cls: type[Document]) -> int:
        manager = IndexManager(document_cls)
        async with manager.aholding_reindex_lock():
            await self._areject_open_session(document_cls, manager)
            return await self._arebuild_unlocked(document_cls, manager)

    def _rebuild_unlocked(
        self, document_cls: type[Document], manager: IndexManager
    ) -> int:
        if not manager.exists():
            manager.create()
        else:
            outcome = manager.migrate()
            if outcome in {MigrateOutcome.REBUILD, MigrateOutcome.REINDEX}:
                manager.drop(delete_docs=True)
                manager.create()
        count = self.upsert_queryset(
            document_cls,
            document_cls.get_queryset(),
            heartbeat=manager.heartbeat,
        )
        manager.mark_populated()
        return count

    async def _arebuild_unlocked(
        self, document_cls: type[Document], manager: IndexManager
    ) -> int:
        if not await manager.aexists():
            await manager.acreate()
        else:
            outcome = await manager.amigrate()
            if outcome in {MigrateOutcome.REBUILD, MigrateOutcome.REINDEX}:
                await manager.adrop(delete_docs=True)
                await manager.acreate()
        count = await self.aupsert_queryset(
            document_cls,
            await self._aget_source_queryset(document_cls),
            heartbeat=manager.heartbeat,
        )
        await manager.amark_populated()
        return count

    def _reject_open_session(
        self, document_cls: type[Document], manager: IndexManager
    ) -> None:
        if manager.load_meta().get("reindex"):
            raise ReindexInProgress(
                f"{document_cls.__name__}: a blue/green reindex is in progress; "
                "run `redisearch reindex --abort` first"
            )

    async def _areject_open_session(
        self, document_cls: type[Document], manager: IndexManager
    ) -> None:
        if (await manager.aload_meta()).get("reindex"):
            raise ReindexInProgress(
                f"{document_cls.__name__}: a blue/green reindex is in progress; "
                "run `redisearch reindex --abort` first"
            )

    def reindex(
        self,
        document_cls: type[Document],
        *,
        blue_green: bool = False,
        keep_old: bool = False,
        skip_verify: bool = False,
        abort: bool = False,
        settle: bool = True,
    ) -> ReindexResult:
        """Rebuild an index. Pass ``blue_green=True`` for a zero-downtime swap."""
        manager = IndexManager(document_cls)
        if abort:
            old = manager.abort_reindex()
            return ReindexResult(
                document=document_cls.__name__,
                generation=manager.current_generation(),
                old_physical="",
                new_physical="",
                old_prefix=old or "",
                new_prefix="",
                count=0,
                aborted=True,
            )
        with manager.holding_reindex_lock():
            if not blue_green:
                self._reject_open_session(document_cls, manager)
                existed = manager.exists()
                count = self._rebuild_unlocked(document_cls, manager)
                meta = manager.load_meta()
                physical = meta.get("physical_name")
                prefix = manager.serving_prefix(meta)
                return ReindexResult(
                    document=document_cls.__name__,
                    generation=manager.current_generation(meta),
                    old_physical="",
                    new_physical=physical if isinstance(physical, str) else "",
                    old_prefix=prefix,
                    new_prefix=prefix,
                    count=count,
                    created=not existed,
                    verified=True,
                )
            if not manager.exists():
                count = self._populate_unlocked(document_cls, manager)
                meta = manager.load_meta()
                physical = meta.get("physical_name")
                prefix = manager.serving_prefix(meta)
                return ReindexResult(
                    document=document_cls.__name__,
                    generation=1,
                    old_physical="",
                    new_physical=physical if isinstance(physical, str) else "",
                    old_prefix="",
                    new_prefix=prefix,
                    count=count,
                    created=True,
                    verified=True,
                    blue_green=True,
                )
            return self._reindex_existing(
                document_cls,
                manager,
                keep_old=keep_old,
                skip_verify=skip_verify,
                settle=settle,
            )

    async def areindex(
        self,
        document_cls: type[Document],
        *,
        blue_green: bool = False,
        keep_old: bool = False,
        skip_verify: bool = False,
        abort: bool = False,
        settle: bool = True,
    ) -> ReindexResult:
        manager = IndexManager(document_cls)
        if abort:
            old = await manager.aabort_reindex()
            return ReindexResult(
                document=document_cls.__name__,
                generation=manager.current_generation(await manager.aload_meta()),
                old_physical="",
                new_physical="",
                old_prefix=old or "",
                new_prefix="",
                count=0,
                aborted=True,
            )
        async with manager.aholding_reindex_lock():
            if not blue_green:
                await self._areject_open_session(document_cls, manager)
                existed = await manager.aexists()
                count = await self._arebuild_unlocked(document_cls, manager)
                meta = await manager.aload_meta()
                physical = meta.get("physical_name")
                prefix = manager.serving_prefix(meta)
                return ReindexResult(
                    document=document_cls.__name__,
                    generation=manager.current_generation(meta),
                    old_physical="",
                    new_physical=physical if isinstance(physical, str) else "",
                    old_prefix=prefix,
                    new_prefix=prefix,
                    count=count,
                    created=not existed,
                    verified=True,
                )
            if not await manager.aexists():
                count = await self._apopulate_unlocked(document_cls, manager)
                meta = await manager.aload_meta()
                physical = meta.get("physical_name")
                prefix = manager.serving_prefix(meta)
                return ReindexResult(
                    document=document_cls.__name__,
                    generation=1,
                    old_physical="",
                    new_physical=physical if isinstance(physical, str) else "",
                    old_prefix="",
                    new_prefix=prefix,
                    count=count,
                    created=True,
                    verified=True,
                    blue_green=True,
                )
            return await self._areindex_existing(
                document_cls,
                manager,
                keep_old=keep_old,
                skip_verify=skip_verify,
                settle=settle,
            )

    def verify(
        self,
        document_cls: type[Document],
        *,
        prefix: str | None = None,
        repair: bool = False,
        heartbeat: Heartbeat | None = None,
    ) -> VerifyReport:
        return verify_documents(
            document_cls,
            prefix=prefix,
            repair=repair,
            serializer=self.serializer,
            heartbeat=heartbeat,
        )

    async def averify(
        self,
        document_cls: type[Document],
        *,
        prefix: str | None = None,
        repair: bool = False,
        heartbeat: Heartbeat | None = None,
    ) -> VerifyReport:
        return await sync_to_async(self.verify)(
            document_cls, prefix=prefix, repair=repair, heartbeat=heartbeat
        )

    def _reindex_existing(
        self,
        document_cls: type[Document],
        manager: IndexManager,
        *,
        keep_old: bool,
        skip_verify: bool,
        settle: bool,
    ) -> ReindexResult:
        meta = manager.load_meta()
        generation, old_physical, old_prefix, new_physical, new_prefix = (
            self._reindex_plan(document_cls, manager, meta)
        )
        self._ensure_green(
            manager,
            generation=generation,
            new_physical=new_physical,
            new_prefix=new_prefix,
            resume=bool(meta.get("reindex")),
        )
        if settle:
            manager.settle()
        report: VerifyReport | None = None
        verified = skip_verify
        count = 0
        try:
            count = self.upsert_queryset(
                document_cls,
                document_cls.get_queryset(),
                prefixes=(new_prefix,),
                heartbeat=manager.heartbeat,
            )
            if not skip_verify:
                report = self.verify(
                    document_cls,
                    prefix=new_prefix,
                    repair=True,
                    heartbeat=manager.heartbeat,
                )
                verified = report.ok
                if not report.ok:
                    return ReindexResult(
                        document=document_cls.__name__,
                        generation=generation,
                        old_physical=old_physical,
                        new_physical=new_physical,
                        old_prefix=old_prefix,
                        new_prefix=new_prefix,
                        count=count,
                        verified=False,
                        blue_green=True,
                        report=report,
                    )
        except ReindexInProgress:
            return ReindexResult(
                document=document_cls.__name__,
                generation=generation,
                old_physical=old_physical,
                new_physical=new_physical,
                old_prefix=old_prefix,
                new_prefix=new_prefix,
                count=count,
                verified=verified,
                aborted=True,
                blue_green=True,
                report=report,
            )
        if not manager.has_reindex_session(new_physical):
            return ReindexResult(
                document=document_cls.__name__,
                generation=generation,
                old_physical=old_physical,
                new_physical=new_physical,
                old_prefix=old_prefix,
                new_prefix=new_prefix,
                count=count,
                verified=verified,
                aborted=True,
                blue_green=True,
                report=report,
            )
        manager.promote_reindex(
            generation=generation,
            target_physical=new_physical,
            target_prefix=new_prefix,
        )
        if settle:
            manager.settle()
        dropped = False
        if not keep_old and old_physical and old_physical != new_physical:
            manager.drop_physical(old_physical, delete_docs=True)
            dropped = True
        return ReindexResult(
            document=document_cls.__name__,
            generation=generation,
            old_physical=old_physical,
            new_physical=new_physical,
            old_prefix=old_prefix,
            new_prefix=new_prefix,
            count=count,
            verified=verified,
            dropped_old=dropped,
            blue_green=True,
            report=report,
        )

    async def _areindex_existing(
        self,
        document_cls: type[Document],
        manager: IndexManager,
        *,
        keep_old: bool,
        skip_verify: bool,
        settle: bool,
    ) -> ReindexResult:
        meta = await manager.aload_meta()
        generation, old_physical, old_prefix, new_physical, new_prefix = (
            self._reindex_plan(document_cls, manager, meta)
        )
        if not meta.get("reindex"):
            await manager.abegin_reindex(
                generation=generation,
                target_physical=new_physical,
                target_prefix=new_prefix,
            )
            try:
                await manager._acreate_physical(
                    new_physical, prefix=new_prefix, skip_initial_scan=True
                )
            except Exception:
                await manager.aabort_reindex()
                raise
        if settle:
            await manager.asettle()
        report: VerifyReport | None = None
        verified = skip_verify
        count = 0
        try:
            count = await self.aupsert_queryset(
                document_cls,
                await self._aget_source_queryset(document_cls),
                prefixes=(new_prefix,),
                heartbeat=manager.heartbeat,
            )
            if not skip_verify:
                report = await self.averify(
                    document_cls,
                    prefix=new_prefix,
                    repair=True,
                    heartbeat=manager.heartbeat,
                )
                verified = report.ok
                if not report.ok:
                    return ReindexResult(
                        document=document_cls.__name__,
                        generation=generation,
                        old_physical=old_physical,
                        new_physical=new_physical,
                        old_prefix=old_prefix,
                        new_prefix=new_prefix,
                        count=count,
                        verified=False,
                        blue_green=True,
                        report=report,
                    )
        except ReindexInProgress:
            return ReindexResult(
                document=document_cls.__name__,
                generation=generation,
                old_physical=old_physical,
                new_physical=new_physical,
                old_prefix=old_prefix,
                new_prefix=new_prefix,
                count=count,
                verified=verified,
                aborted=True,
                blue_green=True,
                report=report,
            )
        if not await manager.ahas_reindex_session(new_physical):
            return ReindexResult(
                document=document_cls.__name__,
                generation=generation,
                old_physical=old_physical,
                new_physical=new_physical,
                old_prefix=old_prefix,
                new_prefix=new_prefix,
                count=count,
                verified=verified,
                aborted=True,
                blue_green=True,
                report=report,
            )
        await manager.apromote_reindex(
            generation=generation,
            target_physical=new_physical,
            target_prefix=new_prefix,
        )
        if settle:
            await manager.asettle()
        dropped = False
        if not keep_old and old_physical and old_physical != new_physical:
            await manager.adrop_physical(old_physical, delete_docs=True)
            dropped = True
        return ReindexResult(
            document=document_cls.__name__,
            generation=generation,
            old_physical=old_physical,
            new_physical=new_physical,
            old_prefix=old_prefix,
            new_prefix=new_prefix,
            count=count,
            verified=verified,
            dropped_old=dropped,
            blue_green=True,
            report=report,
        )

    def _reindex_plan(
        self,
        document_cls: type[Document],
        manager: IndexManager,
        meta: IndexMeta,
    ) -> tuple[int, str, str, str, str]:
        session = meta.get("reindex")
        old_prefix = manager.serving_prefix(meta)
        old_physical_raw = meta.get("physical_name")
        old_physical = (
            old_physical_raw
            if isinstance(old_physical_raw, str)
            else manager.schema.physical_name
        )
        if isinstance(session, dict):
            gen = manager.current_generation(meta) + 1
            target_physical = session.get("target_physical")
            target_prefix = session.get("target_prefix")
            if isinstance(target_physical, str) and isinstance(target_prefix, str):
                return gen, old_physical, old_prefix, target_physical, target_prefix
        generation = manager.current_generation(meta) + 1
        new_prefix = generation_prefix(document_cls._meta.key_prefix, generation)
        new_physical = physical_name_for(
            manager.schema.alias, manager.schema.fingerprint(), generation
        )
        return generation, old_physical, old_prefix, new_physical, new_prefix

    def _ensure_green(
        self,
        manager: IndexManager,
        *,
        generation: int,
        new_physical: str,
        new_prefix: str,
        resume: bool,
    ) -> None:
        if not resume:
            manager.begin_reindex(
                generation=generation,
                target_physical=new_physical,
                target_prefix=new_prefix,
            )
            try:
                manager._create_physical(
                    new_physical, prefix=new_prefix, skip_initial_scan=True
                )
            except Exception:
                manager.abort_reindex()
                raise
            return
        try:
            manager._client().ft(new_physical).info()
        except Exception:
            manager._create_physical(
                new_physical, prefix=new_prefix, skip_initial_scan=True
            )

    def populate(self, document_cls: type[Document]) -> int:
        manager = IndexManager(document_cls)
        with manager.holding_reindex_lock():
            return self._populate_unlocked(document_cls, manager)

    async def apopulate(self, document_cls: type[Document]) -> int:
        manager = IndexManager(document_cls)
        async with manager.aholding_reindex_lock():
            return await self._apopulate_unlocked(document_cls, manager)

    def _populate_unlocked(
        self, document_cls: type[Document], manager: IndexManager
    ) -> int:
        if not manager.exists():
            manager.create()
        count = self.upsert_queryset(
            document_cls,
            document_cls.get_queryset(),
            heartbeat=manager.heartbeat,
        )
        manager.mark_populated()
        return count

    async def _apopulate_unlocked(
        self, document_cls: type[Document], manager: IndexManager
    ) -> int:
        if not await manager.aexists():
            await manager.acreate()
        count = await self.aupsert_queryset(
            document_cls,
            await self._aget_source_queryset(document_cls),
            heartbeat=manager.heartbeat,
        )
        await manager.amark_populated()
        return count

    async def _aget_source_queryset(
        self, document_cls: type[Document]
    ) -> models.QuerySet[models.Model]:
        return await sync_to_async(document_cls.get_queryset)()

    def reindex_related(
        self,
        document_cls: type[Document],
        related: models.Model,
        *,
        exclude: models.Model | None = None,
        deleting: bool = False,
    ) -> None:
        if deleting and self._cascade_deletes_parent(document_cls, related):
            return
        parents_iter = _parent_list(document_cls, related)
        client = get_redis_connection()
        prefixes = write_prefixes(document_cls)
        for parent in parents_iter:
            prepared = self._prepare_related_write(
                document_cls, parent, exclude=exclude
            )
            if prepared is None:
                for key in self._keys_for(document_cls, parent.pk, prefixes):
                    self.delete_key(document_cls, key, client=client)
                continue
            pk, payload = prepared
            mapping = None
            if document_cls._meta.storage is not Storage.JSON:
                mapping = self.serializer.flatten_hash(document_cls, payload)
            for key in self._keys_for(document_cls, pk, prefixes):
                self._store(client, document_cls, key, payload, mapping)

    async def areindex_related(
        self,
        document_cls: type[Document],
        related: models.Model,
        *,
        exclude: models.Model | None = None,
        deleting: bool = False,
    ) -> None:
        if deleting and self._cascade_deletes_parent(document_cls, related):
            return
        parents_iter = await sync_to_async(_parent_list)(document_cls, related)
        client = get_async_redis_connection()
        prefixes = write_prefixes(document_cls)
        for parent in parents_iter:
            prepared = await sync_to_async(self._prepare_related_write)(
                document_cls, parent, exclude=exclude
            )
            if prepared is None:
                for key in self._keys_for(document_cls, parent.pk, prefixes):
                    with observe_write(document_cls, "DEL", key):
                        await client.delete(key)
                continue
            pk, payload = prepared
            mapping = None
            if document_cls._meta.storage is not Storage.JSON:
                mapping = await sync_to_async(self.serializer.flatten_hash)(
                    document_cls, payload
                )
            for key in self._keys_for(document_cls, pk, prefixes):
                await self._astore(client, document_cls, key, payload, mapping)

    def _prepare_related_write(
        self,
        document_cls: type[Document],
        parent: models.Model,
        *,
        exclude: models.Model | None,
    ) -> tuple[object, DocumentPayload] | None:
        if not document_cls.should_index(parent):
            return None
        payload = stamp_payload(
            document_cls,
            parent,
            self.serializer.to_document(document_cls, parent, exclude=exclude),
        )
        return parent.pk, payload

    def _cascade_deletes_parent(
        self, document_cls: type[Document], related: models.Model
    ) -> bool:
        model = document_cls._meta.model
        if model is None:
            return False
        related_model = related._meta.concrete_model
        for field in model._meta.get_fields():
            if not getattr(field, "concrete", False):
                continue
            is_fk = getattr(field, "many_to_one", False) or getattr(
                field, "one_to_one", False
            )
            if not is_fk:
                continue
            if getattr(field, "related_model", None) is not related_model:
                continue
            remote = getattr(field, "remote_field", None)
            on_delete = getattr(remote, "on_delete", None) or getattr(
                field, "on_delete", None
            )
            if on_delete == models.CASCADE:
                return True
        return False


def _parent_list(
    document_cls: type[Document], related: models.Model
) -> list[models.Model]:
    parents = document_cls.get_instances_from_related(related)
    if parents is None:
        return []
    if isinstance(parents, models.Model):
        return [parents]
    return list(parents)


def _iter_records(
    qs: models.QuerySet[models.Model], chunk: int
) -> Iterator[models.Model]:
    # iterator() ignores prefetch_related. Prefetch in CHUNK_SIZE batches
    # instead of materializing the whole queryset.
    lookups = getattr(qs, "_prefetch_related_lookups", ())
    if not lookups:
        yield from qs.iterator(chunk_size=chunk)
        return
    from django.db.models import prefetch_related_objects

    batch: list[models.Model] = []
    for instance in qs.prefetch_related(None).iterator(chunk_size=chunk):
        batch.append(instance)
        if len(batch) >= chunk:
            prefetch_related_objects(batch, *lookups)
            yield from batch
            batch = []
    if batch:
        prefetch_related_objects(batch, *lookups)
        yield from batch


async def _aiter_records(
    qs: models.QuerySet[models.Model], chunk: int
) -> AsyncIterator[models.Model]:
    lookups = getattr(qs, "_prefetch_related_lookups", ())
    if not lookups:
        async for instance in qs.aiterator(chunk_size=chunk):
            yield instance
        return
    from django.db.models import aprefetch_related_objects

    batch: list[models.Model] = []
    async for instance in qs.prefetch_related(None).aiterator(chunk_size=chunk):
        batch.append(instance)
        if len(batch) >= chunk:
            await aprefetch_related_objects(batch, *lookups)
            for item in batch:
                yield item
            batch = []
    if batch:
        await aprefetch_related_objects(batch, *lookups)
        for item in batch:
            yield item
