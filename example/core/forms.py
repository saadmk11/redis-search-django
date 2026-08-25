from __future__ import annotations

from django import forms

from .models import Category, Product, Tag, Vendor


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "category",
            "vendor",
            "tags",
            "price",
            "available",
            "quantity",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "cols": 60}),
            "tags": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        taken = Vendor.objects.filter(product__isnull=False)
        if self.instance.pk and self.instance.vendor_id:
            taken = taken.exclude(pk=self.instance.vendor_id)
        self.fields["vendor"].queryset = Vendor.objects.exclude(
            pk__in=taken.values("pk")
        )
        self.fields["vendor"].help_text = (
            "OneToOne: only vendors without a product are listed. "
            "Create a vendor first if the list is empty."
        )


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug"]


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ["name"]


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ["name", "email", "establishment_date", "logo"]
