from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, TypedDict, TypeGuard

from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.db import models
from django.db.models.fields.related import ForeignObjectRel

from .conf import redis_search_setting, setting_str
from .enums import Storage
from .exceptions import ConfigurationError, DocumentNotFound
from .fields import Field, Nested, Object
from .mapping import field_from_django
from .registry import document_registry
from .targets import read_prefix
from .types import DocumentPayload
from .versioning import check_version_field, default_version

if TYPE_CHECKING:
    from .query.queryset import DocumentManager


class RelatedConfig(TypedDict):
    related_name: str
    many: bool


_MANAGER_ATTR = "_rsd_manager"


class DocumentOptions:
    """Resolved Django + Index options for a Document class."""

    def __init__(
        self,
        cls: type[Document],
        bases: tuple[type[Document], ...],
        declared: dict[str, Field],
    ) -> None:
        django_opts = getattr(cls, "Django", None)
        index_opts = getattr(cls, "Index", None)

        self.abstract = bool(getattr(django_opts, "abstract", False))
        self.embedded = bool(getattr(django_opts, "embedded", False))
        self.auto_index = bool(getattr(django_opts, "auto_index", True))
        self.model: type[models.Model] | None = getattr(django_opts, "model", None)
        self.django_fields: tuple[str, ...] = tuple(
            getattr(django_opts, "fields", None) or ()
        )
        self.select_related_fields: tuple[str, ...] = tuple(
            getattr(django_opts, "select_related_fields", None) or ()
        )
        self.prefetch_related_fields: tuple[str, ...] = tuple(
            getattr(django_opts, "prefetch_related_fields", None) or ()
        )
        raw_related = getattr(django_opts, "related_models", None)
        if isinstance(raw_related, list):
            raise ConfigurationError(
                f"{cls.__name__}.Django.related_models must be None or a dict, "
                "not a list."
            )
        self.related_models_option: dict[type[models.Model], RelatedConfig] | None
        if raw_related is None:
            self.related_models_option = None
        else:
            self.related_models_option = dict(raw_related)

        raw_storage = getattr(index_opts, "storage", None) or redis_search_setting(
            "DEFAULT_STORAGE"
        )
        try:
            self.storage = Storage(raw_storage)
        except ValueError:
            raise ConfigurationError(
                f"{cls.__name__}.Index.storage must be 'json' or 'hash'."
            ) from None
        self.language: str | None = getattr(index_opts, "language", None)
        self.stopwords: tuple[str, ...] | None
        stopwords = getattr(index_opts, "stopwords", None)
        if stopwords is None:
            self.stopwords = None
        else:
            self.stopwords = tuple(stopwords)
        self.score: float = float(getattr(index_opts, "score", 1.0))
        self.dialect: int = int(getattr(index_opts, "dialect", 2))
        self.search_fields_option: tuple[str, ...] | None
        search_fields = getattr(index_opts, "search_fields", None)
        self.search_fields_option = (
            None if search_fields is None else tuple(search_fields)
        )
        self.name_override: str | None = getattr(index_opts, "name", None)
        self.prefix_override: str | None = getattr(index_opts, "prefix", None)
        self.index_alias: str = ""
        self.key_prefix: str = ""

        self.fields: dict[str, Field] = _merge_fields(cls, bases, declared)
        if self.model is None:
            for base in bases:
                base_meta = getattr(base, "_meta", None)
                if base_meta is not None and base_meta.model is not None:
                    self.model = base_meta.model
                    break

        if not self.abstract and self.model is None:
            raise ConfigurationError(f"{cls.__name__} requires Django.model.")

        if self.model is not None:
            _apply_django_fields(cls, self)

        if self.storage is Storage.HASH and any(
            isinstance(field, Nested) for field in self.fields.values()
        ):
            raise ConfigurationError(
                f"{cls.__name__} uses Hash storage and cannot declare Nested fields."
            )

        self.related_map: dict[type[models.Model], RelatedConfig] = {}
        if not self.abstract and not self.embedded and self.model is not None:
            self.related_map = _resolve_related_map(cls, self)

        _check_alias_collisions(cls, self)

    @property
    def app_label(self) -> str:
        assert self.model is not None
        return self.model._meta.app_label

    @property
    def model_name(self) -> str:
        assert self.model is not None
        return self.model._meta.model_name or self.model.__name__.lower()

    def document_slug(self, cls: type[Document]) -> str:
        return cls.__name__.lower()

    def index_alias_for(self, cls: type[Document]) -> str:
        if self.name_override:
            return self.name_override
        return f"idx:{self.app_label}.{self.model_name}.{self.document_slug(cls)}"

    def key_prefix_for(self, cls: type[Document]) -> str:
        if self.prefix_override:
            prefix = self.prefix_override
            return prefix if prefix.endswith(":") else f"{prefix}:"
        root = setting_str("PREFIX")
        return f"{root}:{self.app_label}.{self.model_name}.{self.document_slug(cls)}:"


def _merge_fields(
    cls: type[Document],
    bases: tuple[type[Document], ...],
    declared: dict[str, Field],
) -> dict[str, Field]:
    merged: dict[str, Field] = {}
    for base in reversed(bases):
        base_meta = getattr(base, "_meta", None)
        if base_meta is None:
            continue
        if getattr(base_meta, "abstract", False) or getattr(base, "__name__", "") == (
            "Document"
        ):
            for name, field in base_meta.fields.items():
                merged[name] = field.copy()
    merged.update(declared)
    for name, field in merged.items():
        field.bind(name, cls)
    return merged


def _apply_django_fields(cls: type[Document], opts: DocumentOptions) -> None:
    assert opts.model is not None
    for field_name in opts.django_fields:
        if field_name in opts.fields or field_name in {"id", "pk"}:
            continue
        django_field = opts.model._meta.get_field(field_name)
        mapped = field_from_django(django_field)
        if django_field.null and not isinstance(mapped, Object):
            mapped.index_missing = True
        mapped.bind(field_name, cls)
        opts.fields[field_name] = mapped


def _resolve_related_map(
    cls: type[Document], opts: DocumentOptions
) -> dict[type[models.Model], RelatedConfig]:
    assert opts.model is not None
    resolved: dict[type[models.Model], RelatedConfig] = {}
    option = opts.related_models_option
    if option == {}:
        return resolved
    if option:
        for model, cfg in option.items():
            if "related_name" not in cfg or "many" not in cfg:
                raise ConfigurationError(
                    f"{cls.__name__}.related_models[{model.__name__}] "
                    "must include 'related_name' and 'many'."
                )
            resolved[model] = {
                "related_name": cfg["related_name"],
                "many": bool(cfg["many"]),
            }

    infer_all = option is None
    for field in opts.fields.values():
        if not isinstance(field, (Object, Nested)):
            continue
        related_model = field.target._meta.model
        if related_model is None:
            continue
        if related_model is opts.model and isinstance(field, Object):
            raise ConfigurationError(
                f"{cls.__name__}.{field.name} cannot nest the same Document class."
            )
        if related_model in resolved and not infer_all:
            continue
        if related_model in resolved and infer_all:
            existing = resolved[related_model]
            inferred = _infer_relation(opts.model, field)
            if inferred["related_name"] != existing["related_name"]:
                raise ConfigurationError(
                    f"{cls.__name__} has two relations to {related_model.__name__}: "
                    f"{existing['related_name']!r} and {inferred['related_name']!r}. "
                    "Set Django.related_models for that model or implement "
                    "get_instances_from_related."
                )
            continue
        resolved[related_model] = _infer_relation(opts.model, field)
    return resolved


def _infer_relation(model: type[models.Model], field: Object | Nested) -> RelatedConfig:
    source = field.model_attr or field.name or ""
    try:
        django_field = model._meta.get_field(source.split(".", 1)[0])
    except FieldDoesNotExist as exc:
        raise ConfigurationError(
            f"Cannot infer relation for {model.__name__}.{source}."
        ) from exc
    if isinstance(django_field, ForeignObjectRel):
        forward = getattr(django_field, "field", None)
        name = getattr(forward, "name", None)
        if not name:
            raise ConfigurationError(
                f"Cannot infer reverse relation for {model.__name__}.{source}."
            )
        reverse_many = bool(
            getattr(django_field, "one_to_many", False)
            or getattr(django_field, "many_to_many", False)
        )
        return {"related_name": name, "many": reverse_many}

    remote = getattr(django_field, "remote_field", None)
    if remote is None:
        raise ConfigurationError(
            f"{model.__name__}.{source} is not a related field; "
            "use Object/Nested with a related Django field name."
        )
    accessor = remote.get_accessor_name()
    if isinstance(django_field, (models.ForeignKey, models.OneToOneField)):
        reverse_many = not isinstance(django_field, models.OneToOneField)
        return {"related_name": accessor, "many": reverse_many}
    if isinstance(django_field, models.ManyToManyField):
        return {"related_name": accessor, "many": True}
    many = bool(
        getattr(django_field, "one_to_many", False)
        or getattr(django_field, "many_to_many", False)
    )
    return {"related_name": accessor, "many": many}


def _check_alias_collisions(cls: type[Document], opts: DocumentOptions) -> None:
    aliases: dict[str, str] = {}
    for field in opts.fields.values():
        if isinstance(field, (Object, Nested)):
            parent = field.as_name()
            for child in field.target._meta.fields.values():
                if isinstance(child, (Object, Nested)):
                    continue
                alias = child.as_name(parent)
                if alias in aliases:
                    raise ConfigurationError(
                        f"{cls.__name__} alias collision {alias!r} "
                        f"({aliases[alias]} vs {field.name}.{child.name})."
                    )
                aliases[alias] = f"{field.name}.{child.name}"
        else:
            alias = field.as_name()
            if alias in aliases:
                raise ConfigurationError(f"{cls.__name__} alias collision {alias!r}.")
            aliases[alias] = field.name or ""


def _is_document_class(cls: object, *, name: str) -> TypeGuard[type[Document]]:
    return isinstance(cls, type) and (name == "Document" or issubclass(cls, Document))


class DocumentMeta(type):
    def __new__(
        mcs,
        name: str,
        bases: tuple[type[Document], ...],
        attrs: dict[str, Field | str | bool | None],
    ) -> type[Document]:
        declared: dict[str, Field] = {
            key: value for key, value in list(attrs.items()) if isinstance(value, Field)
        }
        for key in declared:
            attrs.pop(key)
        created = super().__new__(mcs, name, bases, attrs)
        if not _is_document_class(created, name=name):
            raise TypeError(f"{name} is not a Document class")
        cls = created
        if name == "Document" and attrs.get("__module__") == (
            "redis_search_django.documents"
        ):
            return cls
        _attach_exception_classes(cls)
        opts = DocumentOptions(cls, bases, declared)
        cls._meta = opts
        if opts.model is not None:
            cls._meta_index_alias = opts.index_alias_for(cls)
            cls._meta_key_prefix = opts.key_prefix_for(cls)
            opts.index_alias = cls._meta_index_alias
            opts.key_prefix = cls._meta_key_prefix
        else:
            cls._meta_index_alias = ""
            cls._meta_key_prefix = ""
        if not opts.abstract and not opts.embedded:
            check_version_field(cls)
            document_registry.register(cls)
        return cls


def _attach_exception_classes(cls: type[Document]) -> None:
    """Per-Document ``DoesNotExist`` / ``MultipleObjectsReturned``, like ModelBase."""
    target: Any = cls
    if "DoesNotExist" not in cls.__dict__:
        target.DoesNotExist = type(
            "DoesNotExist",
            (Document.DoesNotExist,),
            {
                "__module__": cls.__module__,
                "__qualname__": f"{cls.__name__}.DoesNotExist",
            },
        )
    if "MultipleObjectsReturned" not in cls.__dict__:
        target.MultipleObjectsReturned = type(
            "MultipleObjectsReturned",
            (Document.MultipleObjectsReturned,),
            {
                "__module__": cls.__module__,
                "__qualname__": f"{cls.__name__}.MultipleObjectsReturned",
            },
        )


class _ObjectsDescriptor:
    def __get__(self, obj: Document | None, owner: type[Document]) -> DocumentManager:
        cached = owner.__dict__.get(_MANAGER_ATTR)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        from .query.queryset import DocumentManager

        manager = DocumentManager(owner)
        setattr(owner, _MANAGER_ATTR, manager)
        return manager


class Document(metaclass=DocumentMeta):
    """Declarative mapping from a Django model to a RediSearch index."""

    if TYPE_CHECKING:
        objects: ClassVar[DocumentManager]
        _meta: ClassVar[DocumentOptions]
    else:
        objects = _ObjectsDescriptor()
        _meta = None

    _meta_index_alias: ClassVar[str]
    _meta_key_prefix: ClassVar[str]

    class Django:
        abstract = True

    class DoesNotExist(DocumentNotFound):
        pass

    class MultipleObjectsReturned(Exception):
        pass

    @classmethod
    def _config_error(cls, message: str) -> ConfigurationError:
        return ConfigurationError(message)

    @classmethod
    def _with_related(
        cls, qs: models.QuerySet[models.Model]
    ) -> models.QuerySet[models.Model]:
        if cls._meta.select_related_fields:
            qs = qs.select_related(*cls._meta.select_related_fields)
        if cls._meta.prefetch_related_fields:
            qs = qs.prefetch_related(*cls._meta.prefetch_related_fields)
        return qs

    @classmethod
    def instance_queryset(cls) -> models.QuerySet[models.Model]:
        """Default manager plus this Document's select/prefetch.

        Used by live writes so Nested/Object fields do not N+1. Unlike
        :meth:`get_queryset`, this is not a filter hook — override
        ``should_index`` to drop a row from the index on save.
        """
        assert cls._meta.model is not None
        return cls._with_related(cls._meta.model._default_manager.all())

    @classmethod
    def get_queryset(cls) -> models.QuerySet[models.Model]:
        return cls.instance_queryset()

    @classmethod
    def should_index(cls, instance: models.Model) -> bool:
        return True

    @classmethod
    def get_instances_from_related(
        cls, related: models.Model
    ) -> models.QuerySet[models.Model] | models.Model | None:
        cfg = cls._meta.related_map.get(related.__class__)
        if cfg is None:
            concrete = related._meta.concrete_model
            cfg = cls._meta.related_map.get(concrete) if concrete is not None else None
        if cfg is None:
            return None
        try:
            attribute = getattr(related, cfg["related_name"])
        except ObjectDoesNotExist:
            return None
        if attribute is None:
            return None
        if cfg["many"]:
            qs = cls._with_related(attribute.all())
            queryset: models.QuerySet[models.Model] = qs
            return queryset
        related_obj: models.Model = attribute
        return related_obj

    @classmethod
    def key_for(cls, pk: object, *, prefix: str | None = None) -> str:
        """Redis key for *pk*.

        Uses the live serving prefix from index meta so ``objects.get(pk=)``
        keeps working after a blue/green reindex. Pass *prefix* to target a
        specific generation (reindex / verify).
        """
        if prefix is None:
            prefix = read_prefix(cls)
        return f"{prefix}{pk}"

    @classmethod
    def get_index_version(
        cls, instance: models.Model, payload: DocumentPayload
    ) -> str | None:
        """Internal stamp for stale detection. Override only if needed."""
        return default_version(cls, payload)

    @classmethod
    def prepare(cls, instance: models.Model) -> DocumentPayload | None:
        """Return a full replacement payload, or None to keep default serialization."""
        return None

    @classmethod
    def index_all(cls) -> int:
        from .indexer import Indexer

        return Indexer().rebuild(cls)

    @classmethod
    async def aindex_all(cls) -> int:
        from .indexer import Indexer

        return await Indexer().arebuild(cls)
