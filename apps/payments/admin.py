from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "sale", "method", "amount", "currency_code",
        "is_refund", "received_by", "created_at",
    )
    list_filter = ("method", "is_refund", "currency_code")
    search_fields = ("sale__transaction_number", "reference")
    readonly_fields = ("created_at",)