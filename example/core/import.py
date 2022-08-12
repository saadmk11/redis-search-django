import csv
import datetime
import random

from core.models import Category, Product, Tag, Vendor
from django.utils.text import slugify

cat_list = [
    # "Foods",
    # "Electronics",
    "Clothes",
    # "Books",
    "Toys",
    # "Sports",
    "Shoes",
]
cat_ids = []
for cat in cat_list:
    category = Category.objects.create(name=cat, slug=slugify(cat))
    cat_ids.append(category.id)

vendor_list = [
    "Company-1",
    "Company-2",
    "Company-3",
]

tag_list = [
    "Small",
    # "Medium",
    "Large",
    # "Red",
    "Black",
    "Blue",
    "Brand 1",
    # "Brand 2",
    # "Brand 3",
    "Model 1",
    # "Model 2",
    # "Model 3",
]
tag_ids = []
for t in tag_list:
    tag = Tag.objects.create(name=t)
    tag_ids.append(tag)

counter = 0
with open("product_data.csv", newline="", encoding="ISO-8859-1") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        if counter >= 1000:
            break
        vendor_name = random.choice(vendor_list)
        vendor = Vendor.objects.create(
            name=vendor_name,
            email=f"{slugify(vendor_name)}@example.com",
            establishment_date=datetime.date.today(),
        )
        p = Product.objects.create(
            name=row["name"],
            category_id=random.choice(cat_ids),
            vendor=vendor,
            description=row["description"],
            price=float(row["price"].replace("$", "").replace(",", "") or 10.0),
            available=random.choice([True, False]),
        )
        p.tags.set(random.sample(tag_ids, random.randint(1, 4)))
        counter += 1
