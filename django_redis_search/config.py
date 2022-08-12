import datetime
import uuid
from decimal import Decimal
from typing import Any, Dict, Type

from django.db import models

# A mapping for Django model fields and Redis OM fields data
model_field_class_config: Dict[Type[models.Field], Dict[str, Any]] = {
    models.AutoField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.BigAutoField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.BigIntegerField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.BooleanField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.CharField: {
        "type": str,
        "full_text_search": True,
        "index": True,
        "sortable": True,
    },
    models.DateField: {
        "type": datetime.date,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.DateTimeField: {
        "type": datetime.datetime,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.DecimalField: {
        "type": Decimal,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.EmailField: {
        "type": str,
        "full_text_search": True,
        "index": True,
        "sortable": False,
    },
    models.FileField: {
        "type": str,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.FilePathField: {
        "type": str,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.FloatField: {
        "type": float,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.ImageField: {
        "type": str,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.IntegerField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.PositiveIntegerField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.PositiveSmallIntegerField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.SlugField: {
        "type": str,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.SmallIntegerField: {
        "type": int,
        "full_text_search": False,
        "index": True,
        "sortable": True,
    },
    models.TextField: {
        "type": str,
        "full_text_search": True,
        "index": True,
        "sortable": False,
    },
    models.TimeField: {
        "type": datetime.time,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.URLField: {
        "type": str,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
    models.UUIDField: {
        "type": uuid.UUID,
        "full_text_search": False,
        "index": True,
        "sortable": False,
    },
}
