from __future__ import annotations

from typing import Any

from django.db import models
from django.db.models.fields.related import ForeignObjectRel

from .exceptions import ConfigurationError
from .fields import Boolean, Field, Numeric, Tag, Text

RELATED = (
    models.ForeignKey,
    models.OneToOneField,
    models.ManyToManyField,
    models.ManyToManyRel,
    models.ManyToOneRel,
    models.OneToOneRel,
)


def field_from_django(
    django_field: models.Field[Any, Any] | ForeignObjectRel,
) -> Field:
    """Return the default 1.0 Field for a Django model field."""
    if isinstance(django_field, RELATED):
        raise ConfigurationError(
            f"Related field '{django_field.name}' cannot be listed in Django.fields; "
            "declare it as fields.Object(...) or fields.Nested(...)."
        )

    if isinstance(django_field, models.BooleanField):
        return Boolean(index_missing=django_field.null)

    # Slug/URL/FilePath subclass CharField — check them before the CharField branch.
    if isinstance(
        django_field,
        (
            models.SlugField,
            models.UUIDField,
            models.URLField,
            models.FileField,
            models.FilePathField,
            models.TimeField,
        ),
    ):
        return Tag(index_missing=django_field.null)

    if isinstance(
        django_field,
        (
            models.IntegerField,
            models.AutoField,
            models.FloatField,
            models.DecimalField,
            models.DateField,
            models.DateTimeField,
        ),
    ):
        return Numeric(sortable=True, index_missing=django_field.null)

    if isinstance(django_field, (models.TextField, models.EmailField)):
        return Text(index_missing=django_field.null)

    if isinstance(django_field, models.CharField):
        sortable = (
            getattr(django_field, "max_length", None) is None
            or (django_field.max_length or 0) <= 256
        )
        return Text(sortable=sortable, index_missing=django_field.null)

    raise ConfigurationError(
        f"No default RediSearch mapping for Django field {django_field!r}."
    )
