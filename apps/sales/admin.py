from django.contrib import admin

from .models import Sale, SaleItem


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = (
        "product", "product_name", "sku", "quantity",
        "unit_price", "discount_amount", "line_total", "created_at",
    )
    can_delete = False


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "transaction_number", "branch", "cashier",
        "status", "total_amount", "currency_code", "created_at",
    )
    list_filter = ("status", "business", "branch", "currency_code")
    search_fields = ("transaction_number", "cashier__username")
    readonly_fields = ("transaction_number", "created_at", "completed_at")
    inlines = [SaleItemInline]


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ("sale", "product_name", "quantity", "unit_price", "line_total")
    search_fields = ("product_name", "sku", "sale__transaction_number")
