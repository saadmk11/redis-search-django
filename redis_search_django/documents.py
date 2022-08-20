import operator
from abc import ABC
from dataclasses import dataclass
from functools import reduce
from typing import Any, Dict, List, Type, Union

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from pydantic.fields import ModelField
from redis.commands.search.aggregation import AggregateRequest
from redis_om import Field, HashModel, JsonModel
from redis_om.model.model import (
    EmbeddedJsonModel,
    Expression,
    NotFoundError,
    RedisModel,
)

from .config import model_field_class_config
from .query import RediSearchQuery
from .registry import document_registry


@dataclass
class DjangoOptions:
    """Settings for a Django model."""

    model: models.Model
    fields: List[str]
    select_related_fields: List[str]
    prefetch_related_fields: List[str]
    related_models: Dict[Type[models.Model], Dict[str, Union[str, bool]]]
    auto_index: bool

    def __init__(self, options: Any = None) -> None:
        self.model = getattr(options, "model", None)

        if not self.model:
            raise ImproperlyConfigured("Django options requires field `model`.")

        # If the attributes do not exist or explicitly set to None,
        # use the default value
        self.fields = getattr(options, "fields", None) or []
        self.select_related_fields = (
            getattr(options, "select_related_fields", None) or []
        )
        self.prefetch_related_fields = (
            getattr(options, "prefetch_related_fields", None) or []
        )
        self.related_models = getattr(options, "related_models", None) or {}
        self.auto_index = getattr(options, "auto_index", True)


class Document(RedisModel, ABC):
    """Base class for all documents."""

    _query_class = RediSearchQuery

    class Meta:
        global_key_prefix = "redis_search"

    @classmethod
    def find(
        cls, *expressions: Union[Any, Expression], **kwargs: Any
    ) -> RediSearchQuery:
        """Query Redis Search Index for documents."""
        return cls._query_class(
            expressions=expressions, django_model=cls._django.model, model=cls, **kwargs
        )

    @classmethod
    def build_aggregate_request(
        cls, *expressions: Union[Any, Expression]
    ) -> AggregateRequest:
        """Build an aggregation request using the given expressions"""
        search_query_expression = (
            cls._query_class.resolve_redisearch_query(
                reduce(operator.and_, expressions)
            )
            if expressions
            else "*"
        )
        return AggregateRequest(search_query_expression)

    @classmethod
    def aggregate(cls, aggregate_request: AggregateRequest) -> List[Dict[str, Any]]:
        """Aggregate data and return a list of dictionaries containing the results"""
        results = cls.db().ft(cls._meta.index_name).aggregate(aggregate_request)

        return [
            {
                decode_string(result[i]): decode_string(result[i + 1])
                for i in range(0, len(result), 2)
            }
            for result in results.rows
        ]

    @classmethod
    def data_from_model_instance(
        cls, instance: models.Model, exclude_obj: Union[models.Model, None] = None
    ) -> Dict[str, Any]:
        """Build a document data dictionary from a Django Model instance"""
        data = {}

        for field_name, field in cls.__fields__.items():
            field_type = field.type_
            # Check if Document is embedded in another Document
            is_embedded = issubclass(field_type, EmbeddedJsonDocument)
            # Get Target Field Value
            target = getattr(instance, field_name, None)
            # If the target field is a ManyToManyField,
            # get the related objects and build data using the objects
            if (
                is_embedded
                and target
                and hasattr(target, "all")
                and callable(target.all)
            ):
                data[field_name] = [
                    field_type.data_from_model_instance(obj, exclude_obj=exclude_obj)
                    for obj in target.all()
                    if obj != exclude_obj
                ]
            # If the target field is a ForeignKey or OneToOneField,
            # get the related object to build data
            elif is_embedded and target:
                if target != exclude_obj:
                    data[field_name] = field_type.data_from_model_instance(
                        target, exclude_obj=exclude_obj
                    )
            else:
                # Check if the Document class has a `prepare_{field_name}` class method
                # and use it to prepare the field value
                prepare_func = getattr(cls, f"prepare_{field_name}", None)
                value = prepare_func(instance) if prepare_func else target

                # If the field is required and the value is None, raise an error
                if value is None and field.required:
                    raise ValueError(
                        f"Field '{field_name}' is required, either use a Django "
                        f"model field or define 'prepare_{field_name}' class "
                        f"method on the '{cls.__name__}' class "
                        f"that returns a value of type {field_type}"
                    )

                if field_name == "pk":
                    value = str(value)

                # This is added to convert models.BooleanField value to int
                # as redis-om creates schema for the field as NUMERIC field.
                # see https://github.com/redis/redis-om-python/issues/193
                data[field_name] = int(value) if isinstance(value, bool) else value
        return data

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> RedisModel:
        """Build a document from a data dictionary"""
        return cls(**data)

    @classmethod
    def from_model_instance(
        cls,
        instance: models.Model,
        exclude_obj: Union[models.Model, None] = None,
        save: bool = True,
    ) -> RedisModel:
        """Build a document from a Django Model instance"""
        assert instance._meta.model == cls._django.model

        obj = cls.from_data(
            cls.data_from_model_instance(instance, exclude_obj=exclude_obj)
        )

        if save:
            obj.save()

        return obj

    @classmethod
    def update_from_model_instance(
        cls, instance: models.Model, create: bool = True
    ) -> RedisModel:
        """Update a document from a Django Model instance"""
        try:
            obj = cls.get(instance.pk)
            obj.update(**cls.data_from_model_instance(instance))
        except NotFoundError:
            if not create:
                raise
            # Create the Document if not found.
            obj = cls.from_model_instance(instance, save=True)
        return obj

    @classmethod
    def update_from_related_model_instance(
        cls, instance: models.Model, exclude: models.Model = None
    ) -> None:
        """Update a document from a Django Related Model instance"""
        related_model = instance.__class__
        related_model_config = cls._django.related_models.get(related_model)

        # If the related model is not configured, return
        if not related_model_config:
            return

        related_name = related_model_config["related_name"]
        attribute = getattr(instance, related_name, None)

        # If the related name attribute is not found, return
        if not attribute:
            return

        if related_model_config["many"]:
            cls.index_queryset(attribute.all(), exclude_obj=exclude)
        else:
            # If the related model instance will delete
            # the document's Django model instance
            # Then we need to skip re-indexing the document
            if (
                exclude
                and related_model._meta.get_field(related_name).on_delete
                == models.CASCADE
            ):
                exclude = None
            cls.from_model_instance(attribute, exclude_obj=exclude, save=True)

    @classmethod
    def index_queryset(
        cls,
        queryset: models.QuerySet,
        exclude_obj: Union[models.Model, None] = None,
    ) -> None:
        """Index all items in the Django model queryset"""
        obj_list = []

        for instance in queryset.iterator(chunk_size=2000):
            obj_list.append(
                cls.from_model_instance(instance, exclude_obj=exclude_obj, save=False)
            )

        cls.add(obj_list)

    @classmethod
    def index_all(cls) -> None:
        """Index all instances of the model"""
        cls.index_queryset(cls.get_queryset())

    @classmethod
    def get_queryset(cls) -> models.QuerySet:
        """Get Django model queryset, can be overridden to filter queryset"""
        queryset = cls._django.model._default_manager.all()

        if cls._django.select_related_fields:
            queryset = queryset.select_related(*cls._django.select_related_fields)

        if cls._django.prefetch_related_fields:
            queryset = queryset.prefetch_related(*cls._django.prefetch_related_fields)
        return queryset

    @classmethod
    def add_django_fields(cls, field_names: List[str]) -> None:
        """Dynamically add fields to the document"""
        fields: Dict[str, ModelField] = {}
        type_annotations: Dict[str, type] = {}
        is_embedded = issubclass(cls, EmbeddedJsonDocument)

        for field_name in field_names:
            if field_name in cls.__fields__ or field_name in ["id", "pk"]:
                continue

            field_type = cls._django.model._meta.get_field(field_name)

            field_config = model_field_class_config.get(field_type.__class__)

            if not field_config:
                raise ImproperlyConfigured(
                    f"Either the field '{field_type}' is not a Django model field or "
                    "is a Related Model Field (OneToOneField, ForeignKey, ManyToMany) "
                    f"which needs to be explicitly added to the '{cls.__name__}' "
                    f"document class using 'EmbeddedJsonDocument'"
                )

            field_config = field_config.copy()
            annotation = field_config.pop("type")

            if is_embedded:
                field_config["full_text_search"] = False

                if annotation == str:
                    field_config["sortable"] = False

            required = not (field_type.null or field_type.blank)
            field_info = Field(**field_config)
            type_annotations[field_name] = annotation

            # Create a new Field object with the field_info dict
            fields[field_name] = ModelField(
                name=field_name,
                class_validators={},
                model_config=cls.__config__,
                type_=annotation,
                required=required,
                field_info=field_info,
            )

        cls.__fields__.update(fields)
        cls.__annotations__.update(type_annotations)

    @property
    def id(self) -> Union[int, str]:
        """Alias for the primary key of the document"""
        return self.pk


def decode_string(value: Union[str, bytes]) -> str:
    """Decode a string from bytes to str"""

    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


class JsonDocument(Document, JsonModel, ABC):
    """A Document that uses Redis JSON storage"""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Initialize the Subclass class with proper Django options and fields"""
        cls._django = DjangoOptions(getattr(cls, "Django", None))

        if cls._django.fields:
            cls.add_django_fields(cls._django.fields)

        super().__init_subclass__(**kwargs)
        document_registry.register(cls)


class EmbeddedJsonDocument(Document, EmbeddedJsonModel, ABC):
    """An Embedded Document that uses Redis JSON storage"""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Initialize the Subclass class with proper Django options and fields"""
        cls._django = DjangoOptions(getattr(cls, "Django", None))

        if cls._django.fields:
            cls.add_django_fields(cls._django.fields)

        super().__init_subclass__(**kwargs)
        document_registry.register(cls)


class HashDocument(Document, HashModel, ABC):
    """A Document that uses Redis Hash storage"""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Initialize the Subclass class with proper Django options and fields"""
        cls._django = DjangoOptions(getattr(cls, "Django", None))

        if cls._django.fields:
            cls.add_django_fields(cls._django.fields)

        super().__init_subclass__(**kwargs)
        document_registry.register(cls)
