# redis-search-django

[![Pypi Version](https://img.shields.io/pypi/v/redis-search-django.svg?style=flat-square)](https://pypi.org/project/redis-search-django/)
[![Supported Python Versions](https://img.shields.io/pypi/pyversions/redis-search-django?style=flat-square)](https://pypi.org/project/redis-search-django/)
[![Supported Django Versions](https://img.shields.io/pypi/frameworkversions/django/redis-search-django?color=darkgreen&style=flat-square)](https://pypi.org/project/redis-search-django/)
[![GitHub stars](https://img.shields.io/github/stars/saadmk11/redis-search-django?color=success&style=flat-square)](https://github.com/saadmk11/redis-search-django/stargazers)
![Django Tests](https://img.shields.io/github/workflow/status/saadmk11/redis-search-django/Django%20Tests?label=Test&style=flat-square)
![Codecov](https://img.shields.io/codecov/c/github/saadmk11/redis-search-django?style=flat-square&token=ugjHXbEKib)
![pre-commit.ci](https://img.shields.io/badge/pre--commit.ci-enabled-brightgreen?logo=pre-commit&logoColor=white&style=flat-square)
![Changelog-CI](https://img.shields.io/github/workflow/status/saadmk11/redis-search-django/Changelog%20CI?label=Changelog%20CI&style=flat-square)
![Code Style](https://img.shields.io/badge/Code%20Style-Black-black?style=flat-square)
[![License](https://img.shields.io/github/license/saadmk11/redis-search-django?style=flat-square)](https://github.com/saadmk11/redis-search-django/blob/main/LICENSE)

## Description

`redis-search-django` is a Django package that provides **indexing** and **searching** capabilities for Django model instances utilizing **RediSearch**.

## Features

- Management Command to create, update and populate the RediSearch Index.
- Auto Sync Index on Model object Create, Update and Delete.
- Auto Sync Index on Related Model object Add, Update, Remove and Delete.
- Easy to crate Document Classes with Django Form Class like structure.
- Indexing of models with `OneToOneField`, `ForeignKey` and `ManyToManyField`.
- Searching of Model instances using `redis-om`.
- Search Result Pagination.
- Faceted Searching of Model instances.


## Requirements

- Python: 3.7, 3.8, 3.9, 3.10
- Django: 3.2, 4.0, 4.1
- redis-om: >= 0.0.27

## Redis Requirements

### Downloading Redis

The latest version of Redis is available from [Redis.io](https://redis.io/). You can also install Redis with your operating system's package manager.

### RediSearch and RedisJSON

`redis-search-django` relies on the [RediSearch](https://redis.io/docs/stack/search/) and [RedisJSON](https://redis.io/docs/stack/json/) Redis modules to support rich queries and embedded models.
You need these Redis modules to use `redis-search-django`.

The easiest way to run these Redis modules during local development is to use the [redis-stack](https://hub.docker.com/r/redis/redis-stack) Docker image.

#### Docker Compose

There is a `docker-compose.yaml` file provided in the projects root directory.
This file will run Redis, RedisJSON and RediSearch during development.

Run the following command to start the containers:

```bash
docker compose up -d
```

## Example Project

There is an example project available at [Example Project](https://github.com/saadmk11/redis-search-django/tree/main/example).


## Documentation

### Installation

```bash
pip install redis-search-django
```

Then add `redis_search_django` to your `INSTALLED_APPS`:

```bash
INSTALLED_APPS = [
    ...
    'redis_search_django',
]
```

### Usage

#### Document Types

There are 3 types of documents class available:

- **JsonDocument:** This uses `RedisJSON` to store the document. If you want to use Embedded Documents (Required For `OneToOneField`, `ForeignKey` and `ManyToManyField`) then use `JsonDocument`.
- **EmbeddedJsonDocument:** Embedded Json Documents are used for `OneToOneField`, `ForeignKey` and `ManyToManyField` or any types of nested documents.
- **HashDocument:** This uses `RedisHash` to store the documents. It can not be used for nested documents.

#### Creating Document Classes

You need to inherit from Base Document Classes mentioned above to build a document class.

**Simple Example**

For Django Model:

```python
# models.py

from django.db import models

class Category(models.Model):
    name = models.CharField(max_length=30)
    slug = models.SlugField(max_length=30)

    def __str__(self) -> str:
        return self.name
```

You can create a document class like this:

```python
# documents.py

from redis_search_django.documents import JsonDocument

from .models import Category

class CategoryDocument(JsonDocument):

    class Django:
        model = Category
        fields = ["name", "slug"]
```

Now category objects will be indexed on create/update/delete.

**More Complex Example**

For Django Models:

```
# models.py

from django.db import models

class Tag(models.Model):
    name = models.CharField(max_length=30)

    def __str__(self) -> str:
        return self.name


class Vendor(models.Model):
    name = models.CharField(max_length=30)
    email = models.EmailField()
    establishment_date = models.DateField()

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=256)
    description = models.TextField(blank=True)
    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, blank=True)
    price = models.DecimalField(max_digits=6, decimal_places=2)

    def __str__(self) -> str:
        return self.name
```

You can create a document classes like this:

```python
# documents.py

from typing import List

from django.db import models
from redis_om import Field

from redis_search_django.documents import EmbeddedJsonDocument, JsonDocument

from .models import Product, Tag, Vendor

class TagDocument(EmbeddedJsonDocument):
    custom_field: str = Field(index=True, full_text_search=True)

    class Django:
        model = Tag
        # Model Fields
        fields = ["name"]

    @classmethod
    def prepare_custom_field(cls, obj):
        return "CUSTOM FIELD VALUE"


class VendorDocument(EmbeddedJsonDocument):

    class Django:
        model = Vendor
        # Model Fields
        fields = ["name", "establishment_date"]


class ProductDocument(JsonDocument):
    # OnetoOneField, with null=False
    vendor: VendorDocument
    # ManyToManyField
    tags: List[TagDocument]

    class Django:
        model = Product
        # Model Fields
        fields = ["name", "description", "price"]
        # Related Model Options
        related_models = {
            Vendor: {
                "related_name": "product",
                "many": False,
            },
            Tag: {
                "related_name": "product_set",
                "many": True,
            },
        }

    @classmethod
    def get_queryset(cls) -> models.QuerySet:
        """Override Queryset to filter out available products."""
        return super().get_queryset().filter(available=True)

    @classmethod
    def prepare_name(cls, obj):
        """Use this to update field value."""
        return obj.name.upper()
```

**Note:**

- You can not inherit from `HashDocument` for documents that include nested fields.
- You need to inherit from `EmbeddedJsonDocument` for nested documents.
- You need to explicitly add `OneToOneField`, `ForeignKey` or `ManyToManyField` with a nested document class if you want to index them.
  you can not add it in the `Django.fields` option.
- For `related_models` option, you need to specify the fields related name and if it is a `ManyToManyField` or a `ForeignKey` Field then specify `"many": True`.
- `related_models` will be used when a related object is saved that contributes to the document.
- You can define `prepare_{field_name}` method to update the value of the field for indexing.
- If it is a custom field you must define a `prepare_{field_name}` method that returns the value of the field.
- You can override `get_queryset` method to provide more filtering. This will be used while indexing a queryset.
- Field names must match model field names or define a `prepare_{field_name}` method.

#### Management Command

You can use the `index` management command to index all the models in the database to redis index if it has a Document class defined.

**Note:** Make sure that redis is running.

Just run the following command to index all models that have Document classes defined:

```bash
python manage.py index
```

You can use `--migrate-only` option to only update the index schema.

```bash
python manage.py index --migrate-only
```

You can use `--models` to specify which models to index (models must have a Document class defined).

```bash
python manage.py index --models app_name.ModelName app_name2.ModelName2
```

# License

The code in this project is released under the [MIT License](LICENSE).
