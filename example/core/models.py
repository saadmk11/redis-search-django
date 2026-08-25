import uuid

from django.db import models
from django.urls import reverse


class Tag(models.Model):
    name = models.CharField(max_length=30)

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("tag-update", args=[self.pk])


class Category(models.Model):
    name = models.CharField(max_length=30)
    slug = models.SlugField(max_length=30)

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("category-update", args=[self.pk])


class Vendor(models.Model):
    logo = models.ImageField(upload_to="core/vendor_logo", blank=True)
    identifier = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=30)
    email = models.EmailField()
    establishment_date = models.DateField()

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("vendor-update", args=[self.pk])


class Product(models.Model):
    name = models.CharField(max_length=256)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, blank=True, null=True
    )
    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, blank=True)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    available = models.BooleanField(default=True)
    quantity = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("product-detail", args=[self.pk])
