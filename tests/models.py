from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=30)

    class Meta:
        app_label = "tests"

    def __str__(self) -> str:
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=30)

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
