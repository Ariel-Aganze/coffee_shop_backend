from django.contrib import admin

from .models import CashMovement, CashSession


class CashMovementInline(admin.TabularInline):
    model = CashMovement
    extra = 0
    readonly_fields = (
        "movement_type", "amount", "reason", "reference",
        "payment", "performed_by", "created_at",
    )
    can_delete = False


@admin.register(CashSession)
class CashSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "branch", "cashier", "status", "opening_balance",
        "expected_cash", "actual_cash", "difference", "opened_at", "closed_at",
    )
    list_filter = ("status", "branch", "currency_code")
    search_fields = ("cashier__username", "branch__name")
    readonly_fields = (
        "expected_cash", "actual_cash", "difference",
        "opened_at", "closed_at", "closed_by", "created_at",
    )
    inlines = [CashMovementInline]


@admin.register(CashMovement)
class CashMovementAdmin(admin.ModelAdmin):
    list_display = (
        "session", "movement_type", "amount", "performed_by", "created_at",
    )
    list_filter = ("movement_type",)
    search_fields = ("session__cashier__username", "reason", "reference")
    readonly_fields = ("created_at",)