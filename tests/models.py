from __future__ import annotations

import uuid

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=30)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=30)
    flags = GenericRelation("Flag")

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Vendor(models.Model):
    name = models.CharField(max_length=30)
    establishment_date = models.DateField()

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=256)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, blank=True, null=True
    )
    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, blank=True)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Author(models.Model):
    name = models.CharField(max_length=80)
    email = models.EmailField()
    website = models.URLField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Publisher(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=40)
    founded = models.DateField(null=True, blank=True)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Genre(models.Model):
    name = models.CharField(max_length=40)
    slug = models.SlugField(max_length=40)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    isbn = models.SlugField(max_length=20)
    sku = models.UUIDField(default=uuid.uuid4, editable=False)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    pages = models.IntegerField(default=0)
    weight = models.FloatField(default=0.0)
    available = models.BooleanField(default=True)
    featured = models.BooleanField(null=True, blank=True)
    published_on = models.DateField()
    listed_at = models.DateTimeField()
    shop_opens = models.TimeField(null=True, blank=True)
    cover = models.FileField(upload_to="covers", blank=True)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    publisher = models.ForeignKey(
        Publisher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="books",
    )
    genres = models.ManyToManyField(Genre, blank=True, related_name="books")

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.title


class BookExtra(models.Model):
    book = models.OneToOneField(Book, on_delete=models.CASCADE, related_name="extra")
    notes = models.TextField(blank=True)
    edition = models.PositiveIntegerField(default=1)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return f"extra:{self.book_id}"


class Flag(models.Model):
    """Generic relation target used to exercise non-FK/O2O/M2M inference."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    label = models.CharField(max_length=20)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.label
