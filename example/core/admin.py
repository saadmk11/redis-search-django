from django.contrib import admin

from .models import Category, Product, Tag, Vendor


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "establishment_date")
    search_fields = ("name", "email")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "available", "price", "category", "vendor")
    list_filter = ("available", "category", "tags")
    search_fields = ("name", "description")
    filter_horizontal = ("tags",)
