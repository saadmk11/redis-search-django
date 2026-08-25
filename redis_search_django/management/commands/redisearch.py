from __future__ import annotations

import argparse
import json

from django.core.management import BaseCommand, CommandError

from redis_search_django.documents import Document
from redis_search_django.enums import CommandAction, MigrateOutcome
from redis_search_django.exceptions import ReindexInProgress
from redis_search_django.index import IndexManager
from redis_search_django.indexer import Indexer, ReindexResult
from redis_search_django.registry import document_registry
from redis_search_django.verify import VerifyReport

FOLLOW_UP = {
    MigrateOutcome.NO_OP: "Schema is up to date.",
    MigrateOutcome.WAITING: "Schema matches; run `redisearch populate` to finish.",
    MigrateOutcome.CREATED: (
        "Index created. Run `redisearch populate` to load documents."
    ),
    MigrateOutcome.ALTER: "Added fields with FT.ALTER. Run `redisearch populate`.",
    MigrateOutcome.ALIAS_SWAP: (
        "Switched to a new physical index. Run `redisearch populate`."
    ),
    MigrateOutcome.REBUILD: (
        "Prefix or storage changed. Run `redisearch reindex` "
        "or `redisearch reindex --blue-green`."
    ),
    MigrateOutcome.REINDEX: (
        "Schema needs a new physical index. Run `redisearch reindex` "
        "or `redisearch reindex --blue-green`."
    ),
}

NEEDS_FOLLOW_UP = frozenset(
    {
        MigrateOutcome.WAITING,
        MigrateOutcome.ALTER,
        MigrateOutcome.ALIAS_SWAP,
        MigrateOutcome.REBUILD,
        MigrateOutcome.REINDEX,
    }
)


class Command(BaseCommand):
    help = "Create, migrate, populate, reindex, verify, and inspect RediSearch indexes."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="action", required=True)

        create = sub.add_parser("create", help="FT.CREATE missing indexes")
        update = sub.add_parser("update", help="Migrate schema only; never populate")
        populate = sub.add_parser(
            "populate", help="Write documents from get_queryset()"
        )
        rebuild = sub.add_parser(
            "rebuild", help="Drop, create, and populate (downtime)"
        )
        reindex = sub.add_parser(
            "reindex",
            help="Rebuild from Django (drop+reload, or --blue-green)",
        )
        drop = sub.add_parser("drop", help="Drop indexes")
        info = sub.add_parser("info", help="Print FT.INFO")
        check = sub.add_parser(
            "check", help="Exit 1 if schema drifted or populate is required"
        )
        verify = sub.add_parser(
            "verify",
            help="Diff Django PKs against Redis keys (missing / stale / orphaned)",
        )

        drop.add_argument(
            "--dd",
            action="store_true",
            dest="delete_docs",
            help="Also delete indexed Redis keys (FT.DROPINDEX DD).",
        )
        reindex.add_argument(
            "--blue-green",
            action="store_true",
            dest="blue_green",
            help="Zero-downtime rebuild: new prefix, dual-write, FT.ALIASUPDATE.",
        )
        reindex.add_argument(
            "--keep-old",
            action="store_true",
            dest="keep_old",
            help="With --blue-green, leave the old index and keys after the swap.",
        )
        reindex.add_argument(
            "--abort",
            action="store_true",
            dest="abort",
            help="Cancel an in-progress --blue-green reindex.",
        )
        verify.add_argument(
            "--repair",
            action="store_true",
            dest="repair",
            help="Upsert missing/stale documents and delete orphans.",
        )
        verify.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Print a machine-readable report.",
        )
        verify.add_argument(
            "--limit",
            type=int,
            default=None,
            dest="limit",
            help="Max primary keys to print per category (default 20).",
        )
        for item in (
            create,
            update,
            populate,
            rebuild,
            reindex,
            drop,
            info,
            check,
            verify,
        ):
            item.add_argument(
                "--models",
                nargs="*",
                help="Limit to Django models, e.g. shop.Product",
            )

    def handle(self, *args: str, **options: object) -> None:
        action_name = options["action"]
        if not isinstance(action_name, str):
            raise CommandError("Management command action must be a string.")
        action = CommandAction(action_name)
        models_opt = options.get("models")
        labels: list[str] | None = models_opt if isinstance(models_opt, list) else None
        documents = self._documents(labels)
        if not documents:
            raise CommandError("No matching Document classes are registered.")

        indexer = Indexer()
        exit_code = 0
        verify_reports: list[VerifyReport] = []
        for document_cls in documents:
            manager = IndexManager(document_cls)
            label = document_cls.__name__
            if action is CommandAction.CREATE:
                if manager.exists():
                    self.stdout.write(f"{label}: already exists")
                else:
                    manager.create()
                    self.stdout.write(self.style.SUCCESS(f"{label}: created"))
            elif action is CommandAction.UPDATE:
                outcome = manager.migrate()
                needs_follow_up = outcome in NEEDS_FOLLOW_UP
                style = self.style.WARNING if needs_follow_up else self.style.SUCCESS
                self.stdout.write(style(f"{label}: {outcome}. {FOLLOW_UP[outcome]}"))
                if needs_follow_up:
                    exit_code = 1
            elif action is CommandAction.POPULATE:
                try:
                    count = indexer.populate(document_cls)
                except ReindexInProgress as exc:
                    raise CommandError(str(exc)) from exc
                self.stdout.write(
                    self.style.SUCCESS(f"{label}: populated {count} document(s)")
                )
            elif action is CommandAction.REBUILD:
                try:
                    count = indexer.rebuild(document_cls)
                except ReindexInProgress as exc:
                    raise CommandError(str(exc)) from exc
                self.stdout.write(
                    self.style.SUCCESS(f"{label}: rebuilt {count} document(s)")
                )
            elif action is CommandAction.REINDEX:
                blue_green = bool(options.get("blue_green"))
                keep_old = bool(options.get("keep_old"))
                abort = bool(options.get("abort"))
                if keep_old and not blue_green:
                    raise CommandError("--keep-old requires --blue-green.")
                try:
                    result = indexer.reindex(
                        document_cls,
                        blue_green=blue_green,
                        keep_old=keep_old,
                        abort=abort,
                    )
                except ReindexInProgress as exc:
                    raise CommandError(str(exc)) from exc
                self._write_reindex(result)
                if result.aborted:
                    continue
                if not result.created and not result.verified:
                    exit_code = 1
            elif action is CommandAction.DROP:
                try:
                    with manager.holding_reindex_lock():
                        if manager.load_meta().get("reindex"):
                            raise CommandError(
                                f"{label}: a blue/green reindex is in progress; "
                                "run `redisearch reindex --abort` first"
                            )
                        manager.drop(delete_docs=bool(options.get("delete_docs")))
                except ReindexInProgress as exc:
                    raise CommandError(str(exc)) from exc
                self.stdout.write(self.style.SUCCESS(f"{label}: dropped"))
            elif action is CommandAction.INFO:
                if not manager.exists():
                    self.stdout.write(self.style.WARNING(f"{label}: missing"))
                    exit_code = 1
                    continue
                info = manager.info()
                self.stdout.write(f"{label}: {info}")
            elif action is CommandAction.VERIFY:
                repair = bool(options.get("repair"))
                try:
                    if repair:
                        with manager.holding_reindex_lock():
                            report = indexer.verify(
                                document_cls,
                                repair=True,
                                heartbeat=manager.heartbeat,
                            )
                    else:
                        report = indexer.verify(document_cls)
                except ReindexInProgress as exc:
                    raise CommandError(str(exc)) from exc
                verify_reports.append(report)
                if not report.ok:
                    exit_code = 1
            else:
                code = manager.check()
                if code:
                    self.stdout.write(
                        self.style.ERROR(f"{label}: drifted or populate required")
                    )
                    exit_code = 1
                else:
                    self.stdout.write(self.style.SUCCESS(f"{label}: ok"))
        if action is CommandAction.VERIFY:
            self._write_verify(
                verify_reports,
                as_json=bool(options.get("as_json")),
                limit=options.get("limit"),
            )
        if exit_code:
            raise SystemExit(exit_code)

    def _write_reindex(self, result: ReindexResult) -> None:
        label = result.document
        if result.aborted:
            self.stdout.write(
                self.style.WARNING(f"{label}: reindex aborted; dual-write off")
            )
            return
        if result.created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{label}: created and populated {result.count} document(s)"
                )
            )
            return
        if not result.verified:
            self.stdout.write(
                self.style.ERROR(
                    f"{label}: reindex backfill wrote {result.count} "
                    f"document(s) but verify failed; alias not swapped. "
                    f"Fix with `redisearch verify --repair` or "
                    f"`redisearch reindex --abort`."
                )
            )
            reports = [result.report] if result.report else []
            self._write_verify(reports, as_json=False, limit=None)
            return
        if not result.blue_green:
            self.stdout.write(
                self.style.SUCCESS(f"{label}: reindexed {result.count} document(s)")
            )
            return
        extra = " (old index kept)" if not result.dropped_old else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{label}: reindexed {result.count} document(s) "
                f"{result.old_prefix} → {result.new_prefix}{extra}"
            )
        )

    def _write_verify(
        self,
        reports: list[VerifyReport],
        *,
        as_json: bool,
        limit: object,
    ) -> None:
        if as_json:
            self.stdout.write(
                json.dumps(
                    {
                        "ok": all(report.ok for report in reports),
                        "documents": [report.as_dict() for report in reports],
                    },
                    indent=2,
                )
            )
            return
        shown = limit if isinstance(limit, int) and not isinstance(limit, bool) else 20
        for report in reports:
            if report.ok:
                repaired = (
                    report.repaired_missing
                    + report.repaired_stale
                    + report.repaired_orphaned
                )
                extra = f", repaired {repaired}" if repaired else ""
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{report.document}: ok ({report.checked} checked{extra})"
                    )
                )
                continue
            self.stdout.write(
                self.style.ERROR(
                    f"{report.document}: {len(report.missing)} missing, "
                    f"{len(report.stale)} stale, "
                    f"{len(report.orphaned)} orphaned "
                    f"({report.checked} checked)"
                )
            )
            self._write_pk_list("missing", report.missing, shown)
            self._write_pk_list("stale", report.stale, shown)
            self._write_pk_list("orphaned", report.orphaned, shown)
            repaired = (
                report.repaired_missing
                or report.repaired_stale
                or report.repaired_orphaned
            )
            if repaired:
                self.stdout.write(
                    f"  repaired: missing={report.repaired_missing} "
                    f"stale={report.repaired_stale} "
                    f"orphaned={report.repaired_orphaned}"
                )
            else:
                self.stdout.write("  run `redisearch verify --repair` to fix")

    def _write_pk_list(self, title: str, pks: list[str], limit: int) -> None:
        if not pks:
            return
        sample = ", ".join(pks[:limit]) if limit else ", ".join(pks)
        more = f" … +{len(pks) - limit}" if limit and len(pks) > limit else ""
        self.stdout.write(f"  {title}: {sample}{more}")

    def _documents(self, labels: list[str] | None) -> list[type[Document]]:
        if not labels:
            return document_registry.primary_documents()
        found: list[type[Document]] = []
        wanted = {label.lower() for label in labels}
        for document_cls in document_registry.primary_documents():
            model = document_cls._meta.model
            if model is None:
                continue
            key = f"{model._meta.app_label}.{model.__name__}"
            if key.lower() in wanted:
                found.append(document_cls)
        missing = wanted - {
            f"{doc._meta.model._meta.app_label}.{doc._meta.model.__name__}".lower()
            for doc in found
            if doc._meta.model is not None
        }
        if missing:
            raise CommandError(
                "No Document registered for: " + ", ".join(sorted(missing))
            )
        return found
