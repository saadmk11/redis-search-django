# redis-search-django

[![Pypi Version](https://img.shields.io/pypi/v/redis-search-django.svg?style=flat-square)](https://pypi.org/project/redis-search-django/)
[![Supported Python Versions](https://img.shields.io/pypi/pyversions/redis-search-django?style=flat-square)](https://pypi.org/project/redis-search-django/)
[![Supported Django Versions](https://img.shields.io/pypi/frameworkversions/django/redis-search-django?color=darkgreen&style=flat-square)](https://pypi.org/project/redis-search-django/)
[![License](https://img.shields.io/github/license/saadmk11/redis-search-django?style=flat-square)](https://github.com/saadmk11/redis-search-django/blob/main/LICENSE)

![Django Tests](https://img.shields.io/github/workflow/status/saadmk11/redis-search-django/Django%20Tests?label=Test&style=flat-square)
![Codecov](https://img.shields.io/codecov/c/github/saadmk11/redis-search-django?style=flat-square&token=ugjHXbEKib)
![pre-commit.ci](https://img.shields.io/badge/pre--commit.ci-enabled-brightgreen?logo=pre-commit&logoColor=white&style=flat-square)
![Changelog-CI](https://img.shields.io/github/workflow/status/saadmk11/redis-search-django/Changelog%20CI?label=Changelog%20CI&style=flat-square)
![Code Style](https://img.shields.io/badge/Code%20Style-Black-black?style=flat-square)

# About

A Django package that provides **auto indexing** and **searching** capabilities for Django model instances using **[RediSearch](https://redis.io/docs/stack/search/)**.

# Features

- Management Command to create, update and populate the RediSearch Index.
- Auto Index on Model object Create, Update and Delete.
- Auto Index on Related Model object Add, Update, Remove and Delete.
- Easy to create Document classes (Uses Django Model Form Class like structure).
- Index nested models (e.g: `OneToOneField`, `ForeignKey` and `ManyToManyField`).
- Search documents using `redis-om`.
- Search Result Pagination.
- Search Result Sorting.
- RediSearch Result to Django QuerySet.
- Faceted Search.

# Requirements

- Python: 3.7, 3.8, 3.9, 3.10
- Django: 3.2, 4.0, 4.1
- redis-om: >= 0.0.27

# Redis

## Downloading Redis

The latest version of Redis is available from [Redis.io](https://redis.io/). You can also install Redis with your operating system's package manager.

## RediSearch and RedisJSON

`redis-search-django` relies on the [RediSearch](https://redis.io/docs/stack/search/) and [RedisJSON](https://redis.io/docs/stack/json/) Redis modules to support rich queries and embedded models.
You need these Redis modules to use `redis-search-django`.

The easiest way to run these Redis modules during local development is to use the [redis-stack](https://hub.docker.com/r/redis/redis-stack) Docker image.

## Docker Compose

There is a `docker-compose.yaml` file provided in the project's root directory.
This file will run Redis with RedisJSON and RediSearch modules during development.

Run the following command to start the Redis container:

```bash
docker compose up -d
```

# Example Project

There is an example project available at [Example Project](https://github.com/saadmk11/redis-search-django/tree/main/example).


# Documentation

## Installation

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

## Usage

### Document Types

There are **3 types** of documents class available:

- **JsonDocument:** This uses `RedisJSON` to store the document. If you want to use Embedded Documents (Required For `OneToOneField`, `ForeignKey` and `ManyToManyField`) then use `JsonDocument`.
- **EmbeddedJsonDocument:** If the document will be embedded inside another document class then use this. Embedded Json Documents are used for `OneToOneField`, `ForeignKey` and `ManyToManyField` or any types of nested documents.
- **HashDocument:** This uses `RedisHash` to store the documents. It can not be used for nested documents.

### Creating Document Classes

You need to inherit from The Base Document Classes mentioned above to build a document class.

#### Simple Example

**1. For Django Model:**

```python
# models.py

from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=30)
    slug = models.SlugField(max_length=30)

    def __str__(self) -> str:
        return self.name
```

**2. You can create a document class like this:**

**Note:** Document classes must be stored in `documents.py` file.

```python
# documents.py

from redis_search_django.documents import JsonDocument

from .models import Category


class CategoryDocument(JsonDocument):
    class Django:
        model = Category
        fields = ["name", "slug"]
```

**3. Run Index Django Management Command to create the index on Redis:**

```bash
python manage.py index
```

**Note:** This will also populate the index with existing data from the database

Now category objects will be indexed on create/update/delete.

#### More Complex Example

**1. For Django Models:**

```python
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

**2. You can create a document classes like this:**

**Note:** Document classes must be stored in `documents.py` file.

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
- You need to inherit from `EmbeddedJsonDocument` for document classes that will be embedded inside another document class.
- You need to explicitly add `OneToOneField`, `ForeignKey` or `ManyToManyField` (e.g: `tags: List[TagDocument]`) with an embedded document class if you want to index them.
  you can not add it in the `Django.fields` option.
- For `related_models` option, you need to specify the fields `related_name` and if it is a `ManyToManyField` or a `ForeignKey` Field then specify `"many": True`.
- `related_models` will be used when a related object is saved that contributes to the document.
- You can define `prepare_{field_name}` method to update the value of a field before indexing.
- If it is a custom field (not a model field) you must define a `prepare_{field_name}` method that returns the value of the field.
- You can override `get_queryset` method to provide more filtering. This will be used while indexing a queryset.
- Field names must match model field names or define a `prepare_{field_name}` method.


**3. Run Index Django Management Command to create the index on Redis:**

```bash
python manage.py index
```

**Note:** This will also populate the index with existing data from the database


### Management Command

This package comes with `index` management command that can be used to index all the model instances to Redis index if it has a Document class defined.

**Note:** Make sure that Redis is running before running the command.

Run the following command to index **all** models that have Document classes defined:

```bash
python manage.py index
```

You can use `--migrate-only` option to **only** update the **index schema**.

```bash
python manage.py index --migrate-only
```

You can use `--models` to **specify** which models to index (models must have a Document class defined to be indexed).

```bash
python manage.py index --models app_name.ModelName app_name2.ModelName2
```

### Views

You can use the `redis_search_django.mixin.RediSearchListViewMixin` with a Django Generic View to search for documents.
`RediSearchPaginator` which helps paginate `ReadiSearch` results is also added to this mixin.

#### Example

```python
# views.py

from django.utils.functional import cached_property
from django.views.generic import ListView
from redis.commands.search import reducers

from redis_search_django.mixins import RediSearchListViewMixin

from .documents import ProductDocument
from .models import Product


class SearchView(RediSearchListViewMixin, ListView):
    paginate_by = 20
    model = Product
    template_name = "core/search.html"
    document_class = ProductDocument

    @cached_property
    def search_query_expression(self):
        query = self.request.GET.get("query")
        query_expression = None

        if query:
            query_expression = (
                self.document_class.name % query
                | self.document_class.description % query
            )

        return query_expression

    @cached_property
    def sort_by(self):
        return self.request.GET.get("sort")

    def facets(self):
        if self.search_query_expression:
            request = self.document_class.build_aggregate_request(
                self.search_query_expression
            )
        else:
            request = self.document_class.build_aggregate_request()

        result = self.document_class.aggregate(
            request.group_by(
                ["@tags_name"],
                reducers.count().alias("count"),
            )
        )
        return result
```

### Search

This package uses `redis-om` to search for documents.

#### Example

```python
from .documents import ProductDocument


categories = ["category1", "category2"]
tags = ["tag1", "tag2"]

# Search For Products That Match The Search Query (name or description)
query_expression = (
    ProductDocument.name % "Some search query"
    | ProductDocument.description % "Some search query"
)

# Search For Products That Match The Price Range
query_expression = (
    ProductDocument.price >= float(10) & ProductDocument.price <= float(100)
)

# Search for Products that include following Categories
query_expression = ProductDocument.category.name << ["category1", "category2"]

# Search for Products that include following Tags
query_expression = ProductDocument.tags.name << ["tag1", "tag2"]

# Query expression can be passed on the `find` method
result = ProductDocument.find(query_expression).sort_by("-price").execute()
```

For more details checkout [redis-om docs](https://github.com/redis/redis-om-python/blob/main/docs/getting_started.md)

### RediSearch Aggregation / Faceted Search

`redis-om` does not support faceted search (RediSearch Aggregation). So this package uses `redis-py` to do faceted search.

#### Example

```python
from redis.commands.search import reducers

from .documents import ProductDocument


query_expression = (
    ProductDocument.name % "Some search query"
    | ProductDocument.description % "Some search query"
)

# First we need to build the aggregation request
request1 = ProductDocument.build_aggregate_request(query_expression)
request2 = ProductDocument.build_aggregate_request(query_expression)

# Get the number of products for each category
ProductDocument.aggregate(
    request1.group_by(
        ["@category_name"],
        reducers.count().alias("count"),
    )
)
# >> [{"category_name": "Shoes", "count": "112"}, {"category_name": "Cloths", "count": "200"}]


# Get the number of products for each tag
ProductDocument.aggregate(
    request2.group_by(
        ["@tags_name"],
        reducers.count().alias("count"),
    )
)
# >> [{"tags_name": "Blue", "count": "14"}, {"tags_name": "Small", "count": "57"}]
```

For more details checkout [redis-py docs](https://redis.readthedocs.io/en/stable/examples/search_json_examples.html?highlight=aggregate#Aggregation) and
[RediSearch Aggregation docs](https://redis.io/docs/stack/search/reference/aggregations/)

### Settings

#### Environment Variables

- **`REDIS_OM_URL`** (Default: `redis://localhost:6379`): This environment variable follows the `redis-py` URL format. If you are using external redis server
You need to set this variable with the URL of the redis server following this pattern: `redis://[[username]:[password]]@[host]:[post]/[database number]`

**Example:** `redis://redis_user:password@some.other.part.cloud.redislabs.com:6379/0`

For more details checkout [redis-om docs](https://github.com/redis/redis-om-python/blob/main/docs/getting_started.md#setting-the-redis-url-environment-variable)


#### Django Document Options

You can add these options on the `Django` class of each Document class:

```python
# documents.py

from redis_search_django.documents import JsonDocument

from .models import Category, Product, Tag, Vendor


class ProductDocument(JsonDocument):
    class Django:
        model = Product
        fields = ["name", "description", "price", "created_at"]
        select_related_fields = ["vendor", "category"]
        prefetch_related_fields = ["tags"]
        auto_index = True
        related_models = {
            Vendor: {
                "related_name": "product",
                "many": False,
            },
            Category: {
                "related_name": "product_set",
                "many": True,
            },
            Tag: {
                "related_name": "product_set",
                "many": True,
            },
        }
```

- **`model`** (Required): Django Model class to index.
- **`auto_index`** (Default: `True`, Optional): If True, the model instances will be indexed on create/update/delete.
- **`fields`** (Default: `[]`, Optional): List of model fields to index. (Do not add `OneToOneField`, `ForeignKey` or `ManyToManyField` here. These need to be explicitly added to the Document class using `EmbeddedJsonDocument`.)
- **`select_related_fields`** (Default: `[]`, Optional): List of fields to use on `queryset.select_related()`.
- **`prefetch_related_fields`** (Default: `[]`, Optional): List of fields to use on `queryset.prefetch_related()`.
- **`related_models`** (Default: `{}`, Optional): Dictionary of related models.
  You need to specify the fields `related_name` and if it is a `ManyToManyField` or a `ForeignKey` Field then specify `"many": True`.
  These are used to update the document data if any of the related model instances are updated.
  `related_models` will be used when a related object is saved/added/removed/deleted that contributes to the document.

For `redis-om` specific options checkout [redis-om docs](https://github.com/redis/redis-om-python/blob/main/docs/models.md)

#### Global Options

You can add these options to your Django `settings.py` File:

- **`REDIS_SEARCH_AUTO_INDEX`** (Default: `True`): Enable or Disable Auto Index when model instance is created/updated/deleted for all document classes.


# Example Application Screenshot

![RediSearch Django](https://user-images.githubusercontent.com/24854406/185760315-4e12d02b-68a2-499a-a6d6-88d8162b5447.png)


# License

The code in this project is released under the [MIT License](LICENSE).
