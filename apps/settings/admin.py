from django.contrib import admin

from .models import BusinessSettings, Currency, CurrencyChangeLog


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "decimal_places", "is_active")
    search_fields = ("code", "name", "country_hint")
    list_filter = ("is_active",)


@admin.register(BusinessSettings)
class BusinessSettingsAdmin(admin.ModelAdmin):
    list_display = ("business", "primary_currency", "timezone", "language", "updated_at")
    search_fields = ("business__name", "primary_currency__code")


@admin.register(CurrencyChangeLog)
class CurrencyChangeLogAdmin(admin.ModelAdmin):
    list_display = ("business", "old_currency", "new_currency", "changed_by", "changed_at")
    list_filter = ("old_currency", "new_currency")
    readonly_fields = ("changed_at",)