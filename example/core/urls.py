from django.urls import path

from . import lab, views

urlpatterns = [
    path("", views.SearchView.as_view(), name="search"),
    path("aggregations/", views.AggregationsView.as_view(), name="aggregations"),
    path("async/", views.AsyncStatsView.as_view(), name="async-stats"),
    path("async/search/", lab.AsyncSearchView.as_view(), name="async-search"),
    path("lab/", lab.LabHomeView.as_view(), name="lab"),
    path("lab/query/", lab.LabQueryView.as_view(), name="lab-query"),
    path("lab/index/", lab.LabIndexView.as_view(), name="lab-index"),
    path("catalog/", views.CatalogHomeView.as_view(), name="catalog"),
    path("catalog/products/", views.ProductListView.as_view(), name="product-list"),
    path(
        "catalog/products/add/",
        views.ProductCreateView.as_view(),
        name="product-create",
    ),
    path(
        "catalog/products/<int:pk>/",
        views.ProductDetailView.as_view(),
        name="product-detail",
    ),
    path(
        "catalog/products/<int:pk>/edit/",
        views.ProductUpdateView.as_view(),
        name="product-update",
    ),
    path(
        "catalog/products/<int:pk>/delete/",
        views.ProductDeleteView.as_view(),
        name="product-delete",
    ),
    path("catalog/categories/", views.CategoryListView.as_view(), name="category-list"),
    path(
        "catalog/categories/add/",
        views.CategoryCreateView.as_view(),
        name="category-create",
    ),
    path(
        "catalog/categories/<int:pk>/edit/",
        views.CategoryUpdateView.as_view(),
        name="category-update",
    ),
    path(
        "catalog/categories/<int:pk>/delete/",
        views.CategoryDeleteView.as_view(),
        name="category-delete",
    ),
    path("catalog/tags/", views.TagListView.as_view(), name="tag-list"),
    path("catalog/tags/add/", views.TagCreateView.as_view(), name="tag-create"),
    path(
        "catalog/tags/<int:pk>/edit/", views.TagUpdateView.as_view(), name="tag-update"
    ),
    path(
        "catalog/tags/<int:pk>/delete/",
        views.TagDeleteView.as_view(),
        name="tag-delete",
    ),
    path("catalog/vendors/", views.VendorListView.as_view(), name="vendor-list"),
    path(
        "catalog/vendors/add/", views.VendorCreateView.as_view(), name="vendor-create"
    ),
    path(
        "catalog/vendors/<int:pk>/edit/",
        views.VendorUpdateView.as_view(),
        name="vendor-update",
    ),
    path(
        "catalog/vendors/<int:pk>/delete/",
        views.VendorDeleteView.as_view(),
        name="vendor-delete",
    ),
]
