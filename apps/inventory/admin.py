from django.contrib import admin

from .models import Inventory, StockMovement


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "quantity", "minimum_threshold", "updated_at")
    list_filter = ("branch",)
    search_fields = ("product__sku", "product__name", "branch__name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "branch", "product", "movement_type",
        "delta", "quantity_after", "performed_by",
    )
    list_filter = ("movement_type", "branch")
    search_fields = ("product__sku", "product__name", "reference", "reason")
    readonly_fields = (
        "branch", "product", "movement_type", "delta", "quantity_after",
        "reason", "reference", "sale", "performed_by", "created_at",
    )

    def has_add_permission(self, request):
        return False  # immutable

    def has_change_permission(self, request, obj=None):
        return False  # immutable

    def has_delete_permission(self, request, obj=None):
        return False  # immutable