from django.contrib import admin

from .models import (
    BranchProductAvailability,
    Category,
    Product,
    ProductPrice,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "is_active", "created_at")
    list_filter = ("is_active", "business")
    search_fields = ("name",)


class ProductPriceInline(admin.TabularInline):
    model = ProductPrice
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "business", "category", "product_type", "is_active")
    list_filter = ("is_active", "product_type", "business", "category")
    search_fields = ("name", "sku")
    inlines = [ProductPriceInline]


@admin.register(ProductPrice)
class ProductPriceAdmin(admin.ModelAdmin):
    list_display = ("product", "selling_price", "cost_price", "effective_from", "created_by")
    list_filter = ("product__business",)
    search_fields = ("product__sku", "product__name")
    readonly_fields = ("created_at",)


@admin.register(BranchProductAvailability)
class BranchProductAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "is_available", "updated_at")
    list_filter = ("is_available", "branch")
    search_fields = ("product__sku", "product__name", "branch__name")